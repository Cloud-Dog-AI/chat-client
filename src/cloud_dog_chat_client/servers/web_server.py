# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Dedicated Web server for chat-client same-origin UI proxying."""

from __future__ import annotations

import secrets
import time
from typing import Any, Iterable

import httpx
from fastapi import HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from cloud_dog_api_kit import create_app as create_api_kit_app  # type: ignore[import-untyped]
from cloud_dog_api_kit.middleware.timeout import TimeoutMiddleware  # type: ignore[import-untyped]

from .. import __version__
from ..api.auth import (
    _try_resolve_principal as resolve_api_key_principal,
    principal_has_admin_capability as has_permission,  # PS-70 UM3 RBAC
)
from ..ui_spa import (
    is_spa_document_navigation,
    is_spa_entry_path,
    serve_runtime_config,
    serve_spa_asset,
    serve_spa_icon,
    serve_spa_index,
)
from .web_flat_roles import (
    ADMIN_ROLE as FLAT_ADMIN_ROLE,
    READ_ONLY_ROLE as FLAT_READ_ONLY_ROLE,
    READ_WRITE_ROLE as FLAT_READ_WRITE_ROLE,
    is_write_gated_data_path,
    normalise_flat_role,
    permissions_for_role,
    role_can_write,
)
from ..observability.http_audit import install_http_audit_middleware
from .common import (
    api_auth_header,
    api_base_url,
    base_url,
    bind_host,
    bind_port,
    configure_logging,
    load_config,
    request_timeout_seconds,
    run_uvicorn,
    server_id,
)

_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


def _filtered_headers(headers: Iterable[tuple[str, str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in headers:
        if key.lower() in _HOP_HEADERS:
            continue
        out[key] = value
    return out


def _is_api_stream_path(path: str) -> bool:
    return path.endswith("/messages/stream")


def create_app():
    cfg = load_config()
    timeout = request_timeout_seconds(cfg)
    # PS-92 (W28A-970g-V2): configurable upstream base paths for proxy composition.
    # Literal defaults live in defaults.yaml. Env override via CLOUD_DOG__<SERVER>__BASE_PATH.
    mcp_base_path = str(cfg.get("mcp_server.base_path") or "/mcp").rstrip("/") or "/mcp"
    a2a_base_path = str(cfg.get("a2a_server.base_path") or "/a2a").rstrip("/") or "/a2a"
    try:
        ui_wait_timeout = float(cfg.get("client_api.ui_wait_timeout_seconds") or timeout)
    except (TypeError, ValueError):
        ui_wait_timeout = timeout
    timeout = max(timeout, ui_wait_timeout, 30.0)
    api_url = api_base_url(cfg).rstrip("/")
    mcp_url = base_url(cfg, "mcp_server").rstrip("/")
    a2a_url = base_url(cfg, "a2a_server").rstrip("/")
    secure_cookies = bool(cfg.get("web_server.secure_cookies"))

    app = create_api_kit_app(
        title="cloud-dog-chat-client-web",
        version="1",
        description="Cloud-Dog chat-client web proxy",
        enable_request_logging=True,
        register_signal_handlers_on_startup=False,
        enable_audit_logging=False,
    )
    install_http_audit_middleware(app, cfg)
    # Platform health via create_health_router().
    app.router.routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", "") not in {"/health", "/ready", "/live", "/status"}
    ]
    for mw in app.user_middleware:
        if mw.cls is TimeoutMiddleware:
            mw.kwargs["timeout_seconds"] = timeout

    @app.middleware("http")
    async def canonical_api_docs_alias_redirects(request: Request, call_next):
        if request.method in ("GET", "HEAD") and request.url.path in {"/api-docs", "/docs", "/openapi"}:
            target = "/developer/api-docs"
            if request.url.query:
                target = f"{target}?{request.url.query}"
            return RedirectResponse(target, status_code=308)
        return await call_next(request)

    def _health_payload() -> dict[str, Any]:
        return {
            "status": "ok",
            "application": {"name": "cloud-dog-chat-client"},
            "runtime": {"env_file": str(cfg.env_file or "")},
            "version": __version__,  # CC8: single source of truth
            "checks": {},
            "server": "web",
            "server_id": server_id(cfg),
            "env_file": str(cfg.env_file or ""),
        }

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse(_health_payload())

    @app.get("/ready")
    async def ready() -> JSONResponse:
        return JSONResponse(_health_payload())

    @app.get("/live")
    async def live() -> JSONResponse:
        return JSONResponse(_health_payload())

    @app.get("/status")
    async def status() -> JSONResponse:
        return JSONResponse(_health_payload())

    # In-memory token session store (no itsdangerous dependency).
    _sessions: dict[str, dict] = {}
    # Thread-a (W28A-727-R5) flat WebUI login accounts: the three flat roles
    # admin / read-write / read-only. The admin account keeps its historical
    # config-resolved credentials (back-compat with existing demo scripts/tests);
    # read-write and read-only are seeded so all three flat roles are demoable
    # out of the box. Credentials are config-routed/env-overridable via the same
    # ConfigManager accessor the admin account already uses (§1.4.1-compliant —
    # no direct-environment reads, all through cfg.get); the matched account's
    # flat role decides the permission set via the ONE shared cloud_dog_idam
    # guard (see web_flat_roles).
    # Defaults mirror the canonical file-mcp accounts for cross-service parity.
    _admin_username = str(cfg.get("web_login.username") or "admin").strip() or "admin"
    _admin_password = str(cfg.get("web_login.password") or "OrangeRiverTable").strip() or "OrangeRiverTable"
    _rw_username = str(cfg.get("web_login.read_write_username") or "read-write").strip() or "read-write"
    _rw_password = str(cfg.get("web_login.read_write_password") or "BlueRiverChair").strip() or "BlueRiverChair"
    _ro_username = str(cfg.get("web_login.read_only_username") or "read-only").strip() or "read-only"
    _ro_password = str(cfg.get("web_login.read_only_password") or "GreenRiverDesk").strip() or "GreenRiverDesk"
    # username -> (password, flat-role). The comparison in /auth/login is
    # constant-time per candidate (secrets.compare_digest) so a wrong username
    # and a wrong password are indistinguishable (no username enumeration).
    _flat_accounts: dict[str, tuple[str, str]] = {
        _admin_username: (_admin_password, FLAT_ADMIN_ROLE),
        _rw_username: (_rw_password, FLAT_READ_WRITE_ROLE),
        _ro_username: (_ro_password, FLAT_READ_ONLY_ROLE),
    }
    # Stable per-role user-id for the session payload.
    _role_user_id = {FLAT_ADMIN_ROLE: "1", FLAT_READ_WRITE_ROLE: "2", FLAT_READ_ONLY_ROLE: "3"}
    _cookie_name = "chat_web_session"

    def _login_cookie_api_key(flat_role: str) -> str:
        """Pick the API key the proxy forwards for a web session's flat role.

        ``admin`` forwards the admin API key (full API access); ``read-write``
        and ``read-only`` forward the user API key so the API server's own
        ``require_admin_key`` gate returns 403 for admin-config writes (defence
        in depth). The read-only role's writes never reach the proxy — they are
        gated at the web layer (403-inline) before forwarding.
        """
        user_key = str(cfg.get("client_api.api_key") or "").strip()
        admin_key = str(cfg.get("client_api.admin_api_key") or "").strip()
        if normalise_flat_role(flat_role) == FLAT_ADMIN_ROLE:
            return admin_key or user_key
        return user_key or admin_key

    def _get_session(request: Request) -> dict | None:
        token = request.cookies.get(_cookie_name)
        if token and token in _sessions:
            sess = _sessions[token]
            if time.time() - sess.get("_created", 0) < 3600:
                return sess
            del _sessions[token]
        return None

    @app.post("/auth/login")
    async def auth_login(request: Request) -> JSONResponse:
        body = await request.json()
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", "")).strip()
        if not username or not password:
            raise HTTPException(status_code=400, detail="Username and password required")
        # Thread-a flat-role credential check (W28A-727-R5). Compare against
        # EVERY account with secrets.compare_digest so a wrong username and a
        # wrong password are indistinguishable (no username enumeration). The
        # matched account decides the flat role; permissions come from the ONE
        # shared idam guard via the flat role catalog (no fork).
        matched_role: str | None = None
        for cand_user, (cand_pw, cand_role) in _flat_accounts.items():
            user_ok = secrets.compare_digest(username, cand_user)
            pw_ok = secrets.compare_digest(password, cand_pw)
            if user_ok and pw_ok:
                matched_role = cand_role
                break
        if matched_role is None:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        flat_role = normalise_flat_role(matched_role)
        permissions = permissions_for_role(flat_role)
        user_id = _role_user_id.get(flat_role, "0")
        token = secrets.token_urlsafe(32)
        _sessions[token] = {
            "user": username,
            "user_id": user_id,
            "role": flat_role,
            "permissions": permissions,
            "_created": time.time(),
        }
        login_api_key = _login_cookie_api_key(flat_role)
        resp = JSONResponse({"user": {"id": user_id, "displayName": username, "email": None, "roles": [flat_role], "permissions": permissions}})
        resp.set_cookie(
            _cookie_name,
            token,
            httponly=True,
            samesite="lax",
            secure=secure_cookies,
            max_age=3600,
            path="/",
        )
        if login_api_key:
            resp.set_cookie(
                "chat_client_api_key",
                login_api_key,
                httponly=True,
                samesite="lax",
                secure=secure_cookies,
                max_age=3600,
                path="/",
            )
        return resp

    @app.get("/auth/me")
    async def auth_me(request: Request) -> JSONResponse:
        sess = _get_session(request)
        if not sess:
            raise HTTPException(status_code=401, detail="Not authenticated")
        # Thread-a (W28A-727-R5): echo the session's own flat role +
        # shared-guard-derived permissions, NOT a hardcoded admin/"*". A
        # read-only session must report a view-only permission set so the UI
        # gates its write affordances correctly (and is never silently admin).
        role = normalise_flat_role(sess.get("role"))
        permissions = sess.get("permissions")
        if not isinstance(permissions, list):
            permissions = permissions_for_role(role)
        return JSONResponse({
            "user": {
                "id": sess["user_id"],
                "displayName": sess["user"],
                "email": None,
                "roles": [role],
                "permissions": permissions,
            }
        })

    @app.post("/auth/logout")
    async def auth_logout(request: Request) -> JSONResponse:
        token = request.cookies.get(_cookie_name)
        if token:
            _sessions.pop(token, None)
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(_cookie_name, path="/")
        resp.delete_cookie("chat_client_api_key", path="/")
        return resp

    @app.get("/auth/login", include_in_schema=False)
    async def auth_login_page(request: Request) -> RedirectResponse:
        """CC6 (W28C-1703): browser landing for the admin-gate redirect.

        The /idam/* gate 302-redirects anonymous callers to
        ``/auth/login?next=...`` (the documented contract). ``/auth/login`` is
        otherwise a POST-only credential endpoint, so a browser following the
        redirect with GET would hit 405; this GET handler forwards to the SPA
        login page (preserving ``next``) so the visitor lands on a working login.
        """
        next_target = str(request.query_params.get("next") or "/ui")
        return RedirectResponse(url=f"/login?next={next_target}", status_code=307)

    @app.get("/")
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/ui", status_code=307)

    @app.get("/mcp-console", include_in_schema=False)
    async def mcp_console_redirect(request: Request) -> RedirectResponse:
        target = "/developer/mcp-console"
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(url=target, status_code=308)

    @app.get("/files", include_in_schema=False)
    async def files_catalogue_redirect(request: Request) -> RedirectResponse:
        target = "/catalogue"
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(url=target, status_code=308)

    @app.get("/runtime-config.js", include_in_schema=False)
    async def runtime_config_js(request: Request) -> Response:
        return serve_runtime_config(cfg, request)

    @app.get("/assets/{asset_path:path}", include_in_schema=False)
    async def ui_assets(asset_path: str) -> Response:
        return serve_spa_asset(cfg, f"assets/{asset_path}")

    @app.get("/favicon.ico", include_in_schema=False)
    @app.get("/apple-touch-icon.png", include_in_schema=False)
    @app.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
    async def ui_icons(request: Request) -> Response:
        return serve_spa_icon(cfg, request.url.path)

    @app.get("/api-docs", include_in_schema=False)
    @app.get("/docs", include_in_schema=False)
    @app.get("/openapi", include_in_schema=False)
    async def api_docs_legacy_alias(request: Request) -> RedirectResponse:
        target = "/developer/api-docs"
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(target, status_code=308)

    @app.get("/jobs", include_in_schema=False)
    async def jobs_legacy_alias(request: Request) -> RedirectResponse:
        # PS-WEBUI-URL-CANONICAL WURL-002 / PS-76 JW13.1: legacy /jobs -> canonical
        # /system/jobs as a deterministic HTTP 308, preserving the query string (WURL-010).
        target = "/system/jobs"
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(target, status_code=308)

    @app.get("/a2a-console", include_in_schema=False)
    async def a2a_console_legacy_alias(request: Request) -> RedirectResponse:
        # PS-WEBUI-URL-CANONICAL WURL-DEV-A2A / PS-72 §11 (W28E-1844): legacy
        # /a2a-console -> canonical /developer/a2a-console as a deterministic HTTP
        # 308, preserving the query string (WURL-010). Mirrors mcp_console_redirect.
        target = "/developer/a2a-console"
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(target, status_code=308)

    @app.get("/index.html", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/login", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/chat", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/sessions", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/profiles", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/source-connections", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/mcp-servers", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/tools", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/developer/api-docs", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/developer/mcp-console", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/developer/a2a-console", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/system/jobs", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/settings", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/admin/rbac", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/admin/users", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/admin/groups", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/admin/api-keys", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/audit-log", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/catalogue", response_class=HTMLResponse, include_in_schema=False)
    async def spa_entry(request: Request) -> HTMLResponse:
        if request.url.path == "/sessions":
            sec_fetch_dest = str(request.headers.get("sec-fetch-dest") or "").strip().lower()
            accept = str(request.headers.get("accept") or "").lower()
            wants_document = sec_fetch_dest == "document" or "text/html" in accept
            if not wants_document:
                return await _proxy_upstream("sessions", request)
        if not is_spa_entry_path(request.url.path):
            return HTMLResponse("Not Found", status_code=404)
        return serve_spa_index(cfg)

    @app.get("/diagnostics-audit", include_in_schema=False)
    @app.get("/observability", include_in_schema=False)
    @app.get("/logs", include_in_schema=False)
    @app.get("/monitoring", include_in_schema=False)
    async def audit_log_alias(request: Request) -> RedirectResponse:
        target = "/audit-log"
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(target, status_code=308)

    # CC6 (W28C-1703 / 1601-C): explicit /idam/* SPA routes (OPT-B). W28A-876
    # mounted the /api/v1 IDAM routes and the SPA wires /idam/{users,groups,roles,
    # api-keys,rbac} to the shared @cloud-dog/idam pages, but the web shell never
    # served the SPA shell for the /idam/* prefix — so direct navigation fell
    # through to the API proxy (anon -> 401, authed -> 404). These routes serve
    # the SPA shell for an authenticated visitor and 302-redirect an anonymous
    # visitor to the login page (preserving the requested /idam/<page> as `next`).
    # The SPA's own isAdmin guard handles authed-but-non-admin users, and the
    # data XHRs at /api/v1/* are admin-gated (defence in depth).
    @app.get("/idam/users", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/idam/groups", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/idam/roles", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/idam/api-keys", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/idam/rbac", response_class=HTMLResponse, include_in_schema=False)
    async def idam_spa_entry(request: Request) -> Response:
        """Serve the SPA shell for an authed visitor; 302 to login for anon."""
        if _get_session(request) is None:
            return RedirectResponse(
                url=f"/auth/login?next={request.url.path}", status_code=302
            )
        return serve_spa_index(cfg)

    @app.get("/ui/config", include_in_schema=False)
    @app.get("/ui/config/tree", include_in_schema=False)
    async def ui_config_proxy(request: Request) -> Response:
        """Proxy runtime config JSON routes before SPA deep-link fallback."""
        return await _proxy_upstream(request.url.path.lstrip("/"), request)

    # WebApiProxy from cloud_dog_api_kit for standard API proxying (W28A-849).
    from cloud_dog_api_kit.web.proxy import WebApiProxy as _WebApiProxy

    _api_proxy = _WebApiProxy(
        api_base_url=api_url,
        api_key=str(cfg.get("client_api.api_key") or "").strip(),
        api_key_header=str(api_auth_header(cfg) or "X-API-Key").strip() or "X-API-Key",
        timeout=timeout,
    )

    # MCP protocol paths (bare /mcp or /messages) — NOT API-about-MCP paths like /mcp/servers
    _MCP_PROTOCOL_PATHS = {"mcp", "mcp/sse", "mcp/message", "messages"}

    async def _proxy_upstream(path: str, request: Request) -> Response:
        # Route to correct upstream based on path prefix
        if path == "webmcp" or path.startswith("webmcp/"):
            upstream_base = mcp_url
        elif path == "weba2a" or path.startswith("weba2a/"):
            upstream_base = a2a_url
        elif path in _MCP_PROTOCOL_PATHS:
            upstream_base = mcp_url
        else:
            upstream_base = api_url

        # Resolve upstream path — PS-92 (W28A-970g-V2) configured prefixes
        if path == "webmcp":
            upstream_path = mcp_base_path
        elif path.startswith("webmcp/"):
            upstream_path = "/" + path[len("webmcp/"):]
        elif path == "weba2a":
            upstream_path = a2a_base_path
        elif path.startswith("weba2a/"):
            upstream_path = f"{a2a_base_path}/" + path[len("weba2a/"):]
        else:
            upstream_path = "/" + path if path else "/"
        if upstream_base == mcp_url and upstream_path == f"{mcp_base_path}/health":
            upstream_path = "/health"
        elif upstream_base == a2a_url and upstream_path == f"{a2a_base_path}/health":
            upstream_path = "/health"

        # For API-bound requests, use WebApiProxy (handles auth, timeout, errors)
        if upstream_base == api_url:
            extra_headers = _filtered_headers(request.headers.items())
            # Forward session user context
            sess = _get_session(request)
            if sess:
                extra_headers.setdefault("X-Request-User", str(sess.get("user") or ""))
                extra_headers.setdefault("X-Request-Source", "webui")
                # Thread-a (W28A-727-R5): forward the session's flat role so the
                # API/audit surface reflects the real role (admin/read-write/
                # read-only), not just the forwarded API key.
                extra_headers.setdefault("X-Request-Role", normalise_flat_role(sess.get("role")))
            auth_header = str(api_auth_header(cfg) or "X-API-Key").strip() or "X-API-Key"
            configured_key = str(cfg.get("client_api.api_key") or "").strip()
            cookie_key = request.cookies.get("chat_client_api_key", "").strip()
            # W28A-F-E2E-05: Reject unauthenticated proxy requests.
            # The WebApiProxy auto-injects the configured API key on every
            # proxied request, which means callers without credentials were
            # silently authenticated.  Gate: the caller must present at least
            # one valid credential (web-session cookie, API-key cookie, or
            # API-key header) before we proxy to the API server.
            caller_has_header_key = any(
                v.strip()
                for k, v in extra_headers.items()
                if k.lower() == auth_header.lower()
            )
            if not sess and not cookie_key and not caller_has_header_key:
                if upstream_path != "/health":
                    return JSONResponse(
                        {"detail": f"Missing required header: {auth_header}"},
                        status_code=401,
                    )
            if cookie_key and auth_header not in extra_headers:
                extra_headers[auth_header] = cookie_key
            elif sess and configured_key and auth_header not in extra_headers:
                extra_headers[auth_header] = configured_key
            query_params = dict(request.query_params) if request.query_params else None
            body = await request.body()
            json_body = None
            content_type = str(request.headers.get("content-type") or "").lower()
            if body and "application/json" in content_type:
                try:
                    import json as _json
                    json_body = _json.loads(body)
                except Exception:
                    json_body = None

            if _is_api_stream_path(upstream_path):
                client = httpx.AsyncClient(timeout=timeout, follow_redirects=False)
                upstream = await client.send(
                    client.build_request(
                        request.method,
                        f"{api_url}{upstream_path}",
                        headers={**extra_headers, **_api_proxy._build_headers()},
                        params=query_params,
                        content=body or None,
                    ),
                    stream=True,
                )

                if upstream.status_code >= 400:
                    payload = await upstream.aread()
                    await upstream.aclose()
                    await client.aclose()
                    return Response(
                        content=payload,
                        status_code=upstream.status_code,
                        headers=_filtered_headers(upstream.headers.items()),
                        media_type=upstream.headers.get("content-type"),
                    )

                async def _stream_response() -> Any:
                    try:
                        async for chunk in upstream.aiter_bytes():
                            if chunk:
                                yield chunk
                    finally:
                        await upstream.aclose()
                        await client.aclose()

                return StreamingResponse(
                    _stream_response(),
                    status_code=upstream.status_code,
                    headers=_filtered_headers(upstream.headers.items()),
                    media_type=upstream.headers.get("content-type"),
                )

            # Non-JSON body with content: use raw httpx with WebApiProxy auth headers
            if json_body is None and body and request.method in {"POST", "PUT", "PATCH"}:
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                    upstream = await client.request(
                        request.method,
                        f"{api_url}{upstream_path}",
                        headers={**extra_headers, **_api_proxy._build_headers()},
                        content=body,
                    )
                return Response(content=upstream.content, status_code=upstream.status_code,
                                headers=_filtered_headers(upstream.headers.items()),
                                media_type=upstream.headers.get("content-type"))

            result = await _api_proxy.request(
                method=request.method,
                path=upstream_path,
                json=json_body if json_body is not None else None,
                params=query_params,
                headers=extra_headers,
            )
            import json as _json
            if isinstance(result.data, (dict, list)):
                return JSONResponse(content=result.data, status_code=result.status_code)
            return Response(content=str(result.data or "").encode(), status_code=result.status_code,
                            media_type="application/json")

        # For MCP/A2A, use raw httpx proxy (WebApiProxy is API-only)
        headers = _filtered_headers(request.headers.items())
        sess = _get_session(request)
        if sess:
            headers.setdefault("X-Request-User", str(sess.get("user") or ""))
            headers.setdefault("X-Request-Source", "webui")
            headers.setdefault("X-Request-Role", normalise_flat_role(sess.get("role")))
        user_header = str(api_auth_header(cfg) or "X-API-Key").strip() or "X-API-Key"
        user_key = str(cfg.get("client_api.api_key") or "").strip()
        cookie_key = request.cookies.get("chat_client_api_key", "").strip()
        # CC1 + CC2 (W28C-1703 / 1601-B): refuse anonymous /webmcp + /weba2a
        # proxy handshakes. Previously this branch fell through with NO auth and,
        # for unauthenticated callers, injected nothing — but it still proxied
        # the request, leaking the live /weba2a/events session stream and
        # reaching the MCP tool surface. Require at least one credential (web
        # session, api-key cookie, or api-key header) before proxying; only
        # health probes are allowed anonymously.
        caller_has_header_key = any(
            str(v or "").strip()
            for k, v in headers.items()
            if k.lower() == user_header.lower()
        )
        is_health_probe = upstream_path == "/health" or upstream_path.endswith("/health")
        if not sess and not cookie_key and not caller_has_header_key and not is_health_probe:
            return JSONResponse(
                {"detail": f"Missing required credential: {user_header} (or an authenticated session)"},
                status_code=401,
            )
        if cookie_key and user_header not in headers:
            headers[user_header] = cookie_key
        elif sess and user_key:
            headers[user_header] = user_key

        query = f"?{request.url.query}" if request.url.query else ""
        target_url = f"{upstream_base}{upstream_path}{query}"
        content = await request.body()
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            upstream = await client.request(request.method, target_url, headers=headers, content=content)

        return Response(content=upstream.content, status_code=upstream.status_code,
                        headers=_filtered_headers(upstream.headers.items()),
                        media_type=upstream.headers.get("content-type"))

    @app.post("/login/session")
    async def login_session(request: Request) -> JSONResponse:
        payload = await request.json()
        api_key = str((payload or {}).get("api_key") or "").strip()
        if not api_key:
            return JSONResponse(status_code=400, content={"detail": "api_key is required"})
        header_name = str(api_auth_header(cfg) or "X-API-Key").strip() or "X-API-Key"
        principal = await resolve_api_key_principal(
            cfg,
            request,
            provided=api_key,
            header_name=header_name,
            require_actor_from_request=True,
        )
        if principal is None:
            raise HTTPException(status_code=401, detail="Invalid API key")

        api_role = str(principal.role or "").strip().lower()
        scopes = set(principal.scopes or ())
        # Thread-a (W28A-727-R5): map the api-key principal onto a flat role via
        # the ONE shared guard. An admin key (or admin scope) is the admin flat
        # role; any other VALID authenticated key is read-write (an authenticated
        # operator may use the system). read-only is a web-login-only role.
        if api_role == "admin" or "*" in scopes or "admin" in scopes:
            flat_role = FLAT_ADMIN_ROLE
        else:
            flat_role = FLAT_READ_WRITE_ROLE
        permissions = permissions_for_role(flat_role)
        user_id = str(principal.user_id or "api-key-user").strip() or "api-key-user"
        display_name = str(principal.actor or "").strip()
        if not display_name or display_name == "unknown":
            display_name = "api-key-admin" if flat_role == FLAT_ADMIN_ROLE else "api-key-user"
        token = secrets.token_urlsafe(32)
        _sessions[token] = {
            "user": display_name,
            "user_id": user_id,
            "role": flat_role,
            "permissions": permissions,
            "_created": time.time(),
        }

        response = JSONResponse({
            "ok": True,
            "user": {
                "id": user_id,
                "displayName": display_name,
                "email": None,
                "roles": [flat_role],
                "permissions": permissions,
            },
        })
        response.set_cookie(
            _cookie_name,
            token,
            httponly=True,
            samesite="lax",
            secure=secure_cookies,
            max_age=3600,
            path="/",
        )
        response.set_cookie(
            "chat_client_api_key",
            api_key,
            httponly=True,
            samesite="lax",
            secure=secure_cookies,
            max_age=3600,
            path="/",
        )
        return response

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
    async def proxy(path: str, request: Request) -> Response:
        if path == "health":
            return JSONResponse({"status": "ok", "server": "web"})
        # Thread-a flat-role write-gate (W28A-727-R5). A logged-in read-only
        # visitor may VIEW every data surface but is denied mutations: any write
        # method on a data path resolves to a 403-inline (not a 401, not a blank
        # UI). admin / read-write sessions fall through. This is defence in depth
        # on top of the API server's own shared-guard RBAC, and — critically —
        # it fires BEFORE the web-proxy so a read-only web session is gated here
        # rather than forwarding the write upstream. Auth/login/logout, health,
        # and read methods are never gated (see is_write_gated_data_path).
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            _gate_sess = _get_session(request)
            if (
                _gate_sess is not None
                and not role_can_write(_gate_sess.get("role"))
                and is_write_gated_data_path("/" + path)
            ):
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": "read-only role: write operations are not permitted",
                        "role": FLAT_READ_ONLY_ROLE,
                    },
                )
        # CC-401 (W28E-1863): SPA deep-link fallback of last resort. A browser
        # hard-navigation / refresh / bookmark of a React history route that is
        # NOT in the enumerated allowlist (e.g. /system/settings, /system/about,
        # /about, /research) previously fell through to the API proxy below and
        # returned a raw 401/404 JSON body instead of the SPA shell. Serve
        # index.html for any GET/HEAD document navigation to a non-reserved path
        # so React renders the requested route — or, for an anonymous visitor, its
        # own login gate — instead of leaking an API error. API / MCP / A2A /
        # health / auth / asset paths remain reserved and are proxied unchanged
        # (see is_spa_document_navigation). This matches the sql-agent / search-mcp
        # catch-all pattern and AGENT-LESSONS §2.4.
        if request.method in {"GET", "HEAD"} and is_spa_document_navigation(path):
            return serve_spa_index(cfg)
        return await _proxy_upstream(path, request)

    return app


def main() -> None:
    cfg = load_config()
    configure_logging(
        cfg,
        section="web_server",
        default_log_name="web_server.log",
        app_name="cloud_dog_chat_web",
    )
    host = bind_host(cfg, "web_server")
    port = bind_port(cfg, "web_server")
    log_level = str(cfg.get("log.level") or "INFO")
    run_uvicorn(create_app(), host=host, port=port, log_level=log_level)
