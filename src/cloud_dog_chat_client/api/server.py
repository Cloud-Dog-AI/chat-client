# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from .. import __version__

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from cloud_dog_api_kit import APIError, create_app as create_api_kit_app  # type: ignore[import-untyped]
from cloud_dog_api_kit.middleware.timeout import TimeoutMiddleware  # type: ignore[import-untyped]
from cloud_dog_logging import (  # type: ignore[import-untyped]
    get_audit_logger,
    get_environment,
    get_service_instance,
    get_service_name,
)
from cloud_dog_logging.audit_schema import Actor, AuditEvent, Target  # type: ignore[import-untyped]
from cloud_dog_logging.middleware.fastapi import LoggingMiddleware  # type: ignore[import-untyped]

from ..api.auth import principal_has_admin_capability as has_permission  # PS-70 UM3 RBAC
from ..config import ConfigManager
from ..database.runtime import ChatDatabaseRuntime
from ..jobs import JobsRuntime
from .config_admin import build_config_router
from cloud_dog_api_kit.mcp.client_transport import MCPTransportError
from ..session import SessionManager
from .routes import build_router


def _application_release(config: ConfigManager) -> str:
    """Resolve the application release: ``app.release`` override else the
    package single-source ``__version__`` (CC8, W28C-1703)."""
    configured = str(config.get("app.release") or "").strip()
    if configured:
        return configured
    return __version__


def _status_to_error_code(status_code: int) -> str:
    """Internal helper to status to error code for this module."""
    mapping = {
        400: "INVALID_REQUEST",
        401: "UNAUTHENTICATED",
        403: "UNAUTHORISED",
        404: "NOT_FOUND",
        408: "TIMEOUT",
        409: "CONFLICT",
        422: "INVALID_REQUEST",
        429: "RATE_LIMITED",
        502: "UPSTREAM_ERROR",
        504: "TIMEOUT",
    }
    mapped = mapping.get(status_code)
    if mapped:
        return mapped
    if status_code >= 500:
        return "INTERNAL_ERROR"
    return "INVALID_REQUEST"


def _request_meta(request: Request) -> dict[str, str]:
    """Internal helper to request meta for this module."""
    request_id = str(getattr(request.state, "request_id", "") or "").strip()
    if not request_id:
        request_id = str(request.headers.get("x-request-id") or "").strip()
    if not request_id:
        request_id = uuid.uuid4().hex

    correlation_id = str(getattr(request.state, "correlation_id", "") or "").strip()
    if not correlation_id:
        correlation_id = str(
            request.headers.get("x-correlation-id") or request_id
        ).strip()

    return {
        "request_id": request_id,
        "correlation_id": correlation_id,
    }


def _runtime_server_id(config: ConfigManager) -> str:
    """Return a stable runtime server identifier."""
    return str(
        config.get("app.server_id")
        or config.get("log.service_instance")
        or "chat-client-local"
    ).strip() or "chat-client-local"


def _header_value(request: Request, name: str) -> str:
    """Internal helper to header value for this module."""
    return str(request.headers.get(name) or "").strip()


def _extract_user_ip(request: Request) -> str:
    """Internal helper to extract user ip for this module."""
    xff = _header_value(request, "x-forwarded-for")
    if xff:
        for part in xff.split(","):
            candidate = part.strip()
            if candidate:
                return candidate
    real_ip = _header_value(request, "x-real-ip")
    if real_ip:
        return real_ip
    client = getattr(request, "client", None)
    host = str(getattr(client, "host", "") or "").strip()
    if host:
        return host
    return "unknown"


def _extract_intermediary_source(request: Request) -> dict[str, str]:
    """Internal helper to extract intermediary source for this module."""
    intermediary = (
        _header_value(request, "x-cloud-dog-intermediary")
        or _header_value(request, "x-intermediary-service")
        or _header_value(request, "x-forwarded-service")
    )
    intermediary_ip = (
        _header_value(request, "x-cloud-dog-intermediary-ip")
        or _header_value(request, "x-intermediary-ip")
    )
    transport = (
        _header_value(request, "x-cloud-dog-transport")
        or _header_value(request, "x-forwarded-proto")
    )

    xff = _header_value(request, "x-forwarded-for")
    if xff and not intermediary_ip:
        chain = [part.strip() for part in xff.split(",") if part.strip()]
        if len(chain) >= 2:
            intermediary_ip = chain[1]

    source: dict[str, str] = {}
    if intermediary:
        source["intermediary"] = intermediary
    if intermediary_ip:
        source["intermediary_ip"] = intermediary_ip
    if transport:
        source["transport"] = transport
    return source


def _is_sensitive_query_key(key: str) -> bool:
    """Internal helper to is sensitive query key for this module."""
    lowered = str(key or "").strip().lower()
    if not lowered:
        return False
    for token in (
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "credential",
        "authorization",
        "cookie",
        "key",
    ):
        if token in lowered:
            return True
    return False


def _safe_query_keys(request: Request) -> list[str]:
    """Internal helper to safe query keys for this module."""
    keys: list[str] = []
    for key in request.query_params.keys():
        raw_key = str(key or "").strip()
        if not raw_key:
            continue
        if _is_sensitive_query_key(raw_key):
            if "__redacted__" not in keys:
                keys.append("__redacted__")
            continue
        if raw_key not in keys:
            keys.append(raw_key)
    return keys


def _audit_outcome_and_severity(status_code: int) -> tuple[str, str]:
    """Internal helper to audit outcome and severity for this module."""
    if status_code in {401, 403}:
        return "denied", "WARNING"
    if status_code >= 500:
        return "error", "ERROR"
    if status_code >= 400:
        return "failure", "ERROR"
    return "success", "INFO"


def _compat_error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: Optional[Any] = None,
) -> JSONResponse:
    """Internal helper to compat error response for this module."""
    meta = _request_meta(request)
    error_entry: dict[str, Any] = {
        "code": code,
        "message": str(message or "Request failed"),
    }
    if details is not None:
        error_entry["details"] = details

    body = {
        "ok": False,
        "errors": [error_entry],
        "meta": {
            "request_id": meta["request_id"],
            "correlation_id": meta["correlation_id"],
        },
    }
    return JSONResponse(status_code=status_code, content=body)


def create_app(config: ConfigManager):
    """Create app for the current runtime context."""
    app_name = str(config.get("app.name") or "cloud-dog-chat-client")
    app_release = _application_release(config)
    # PS-92 (W28A-970g-V2): configurable mcp_server.base_path for MCP runtime-error routing.
    mcp_base_path = str(config.get("mcp_server.base_path") or "/mcp").rstrip("/") or "/mcp"

    app = create_api_kit_app(
        title=app_name,
        version=app_release,
        description="Cloud-Dog chat client API",
        enable_request_logging=True,
        register_signal_handlers_on_startup=False,
        enable_audit_logging=False,
    )

    # Platform API kit defaults TimeoutMiddleware to 30s; expose this via config.
    # Long-running MCP/LLM operations in this service require a higher ceiling.
    try:
        request_timeout_seconds = float(
            config.get("client_api.request_timeout_seconds") or 300.0
        )
    except (TypeError, ValueError):
        request_timeout_seconds = 300.0
    if request_timeout_seconds < 30.0:
        request_timeout_seconds = 30.0
    for mw in app.user_middleware:
        if mw.cls is TimeoutMiddleware:
            mw.kwargs["timeout_seconds"] = request_timeout_seconds

    # Preserve existing logging correlation behaviour and configurable request-id header.
    request_id_header = str(
        config.get("client_api.request_id_header") or "X-Request-Id"
    )
    app.add_middleware(LoggingMiddleware, header_name=request_id_header)
    audit_logger = get_audit_logger()

    user_header_name = str(config.get("client_api.user_header") or "X-User")

    @app.middleware("http")
    async def _request_audit_middleware(request: Request, call_next):
        """Emit per-request AU-3 compliant audit events."""
        started = time.monotonic()
        response = None
        status_code = 500
        unhandled_error = ""
        try:
            response = await call_next(request)
            status_code = int(getattr(response, "status_code", 500) or 500)
            return response
        except Exception as exc:
            unhandled_error = exc.__class__.__name__
            status_code = 500
            raise
        finally:
            route_obj = request.scope.get("route")
            route_template = str(getattr(route_obj, "path", "") or request.url.path)
            method = str(request.method or "UNKNOWN").upper()

            principal = getattr(request.state, "principal", {}) or {}
            principal_user_id = str(principal.get("user_id") or "").strip()
            principal_role = str(principal.get("role") or "").strip()
            actor_id = (
                str(getattr(request.state, "actor", "") or "").strip()
                or _header_value(request, user_header_name)
                or principal_user_id
                or "anonymous"
            )
            actor_roles = [principal_role] if principal_role else None
            actor_ip = _extract_user_ip(request)
            actor_user_agent = _header_value(request, "user-agent")

            outcome, severity = _audit_outcome_and_severity(status_code)
            duration_ms = int(round((time.monotonic() - started) * 1000.0))
            correlation_id = _request_meta(request)["correlation_id"]

            source = _extract_intermediary_source(request)
            details: dict[str, Any] = {
                "http": {
                    "method": method,
                    "path": str(request.url.path or ""),
                    "route": route_template,
                    "status_code": status_code,
                    "query_keys": _safe_query_keys(request),
                },
                "source": source or {
                    "intermediary": "",
                    "intermediary_ip": "",
                    "transport": "",
                },
            }
            if principal_user_id:
                details["auth_user_id"] = principal_user_id
            if unhandled_error:
                details["error_type"] = unhandled_error

            event = AuditEvent(
                event_type="user_function",
                actor=Actor(
                    type="user",
                    id=actor_id,
                    roles=actor_roles,
                    ip=actor_ip or None,
                    user_agent=actor_user_agent or None,
                ),
                action="request_execute",
                outcome=outcome,
                correlation_id=correlation_id,
                service=str(get_service_name() or config.get("app.name") or "unknown"),
                service_instance=str(get_service_instance() or "unknown"),
                environment=str(get_environment() or "unknown"),
                severity=severity,
                target=Target(type="http_route", id=route_template),
                details=details,
                duration_ms=duration_ms,
            )
            try:
                audit_logger.emit(event)
            except Exception:
                # Audit sink fallback handling is inside cloud_dog_logging.
                # Never break request flow for logging failures.
                pass

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        """Internal helper to http exception handler for this module."""
        status_code = int(exc.status_code or 500)
        detail = exc.detail
        message = "Request failed"
        details: Optional[Any] = None

        if isinstance(detail, dict):
            message = str(detail.get("message") or detail.get("detail") or message)
            details = detail.get("details")
        elif isinstance(detail, list):
            details = detail
            if detail:
                message = str(detail[0])
        elif detail is not None:
            message = str(detail)

        return _compat_error_response(
            request,
            status_code=status_code,
            code=_status_to_error_code(status_code),
            message=message,
            details=details,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Internal helper to validation exception handler for this module."""
        field_errors: list[dict[str, Any]] = []
        for error in exc.errors():
            field_errors.append(
                {
                    "field": ".".join(str(part) for part in error.get("loc") or []),
                    "message": str(error.get("msg") or "Validation error"),
                }
            )

        return _compat_error_response(
            request,
            status_code=422,
            code="INVALID_REQUEST",
            message="Validation failure",
            details=field_errors,
        )

    @app.exception_handler(APIError)
    async def _api_error_handler(request: Request, exc: APIError) -> JSONResponse:
        """Internal helper to API error handler for this module."""
        return _compat_error_response(
            request,
            status_code=int(getattr(exc, "status_code", 500) or 500),
            code=str(getattr(exc, "code", "INTERNAL_ERROR") or "INTERNAL_ERROR"),
            message=str(getattr(exc, "message", "Request failed") or "Request failed"),
            details=getattr(exc, "details", None),
        )

    @app.exception_handler(MCPTransportError)
    async def _mcp_transport_error_handler(
        request: Request, exc: MCPTransportError
    ) -> JSONResponse:
        """Internal helper to MCP transport error handler for this module."""
        message = str(exc or "MCP upstream transport error")
        return _compat_error_response(
            request,
            status_code=502,
            code="UPSTREAM_ERROR",
            message=message,
        )

    @app.exception_handler(RuntimeError)
    async def _runtime_error_handler(
        request: Request, exc: RuntimeError
    ) -> JSONResponse:
        """Internal helper to runtime error handler for this module."""
        message = str(exc or "Runtime error")
        path = str(getattr(request.url, "path", "") or "")
        if f"{mcp_base_path}/" not in path:
            return _compat_error_response(
                request,
                status_code=500,
                code="INTERNAL_ERROR",
                message=message,
            )

        lower = message.lower()
        if "missing required configuration key" in lower or "mcp.servers" in lower:
            status_code = 500
            code = "INTERNAL_ERROR"
            msg = f"MCP configuration error: {message}"
        else:
            status_code = 502
            code = "UPSTREAM_ERROR"
            msg = f"MCP upstream error: {message}"

        return _compat_error_response(
            request,
            status_code=status_code,
            code=code,
            message=msg,
        )

    # Replace default health routes with platform create_health_router().
    app.router.routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", "") not in {"/health", "/ready", "/live", "/status"}
    ]

    db_runtime = ChatDatabaseRuntime(config)
    app.state.chat_db_runtime = db_runtime
    jobs_runtime = JobsRuntime.from_config(config)
    app.state.chat_jobs_runtime = jobs_runtime

    async def _db_health_probe() -> dict:
        """Translate database probe output into the platform health shape."""
        probe = db_runtime.probe() if db_runtime is not None else {"status": "disabled"}
        s = str(probe.get("status") or "")
        return {"status": "ok" if s in {"ok", "disabled"} else "error", **probe}

    async def _jobs_health_probe() -> dict:
        """Report jobs runtime readiness using the shared health contract."""
        ok = jobs_runtime is not None and jobs_runtime.health()
        return {"status": "ok" if ok else "error"}

    application_name = str(config.get("app.name") or "cloud-dog-chat-client")
    # CC8 (W28C-1703): /health + /api/status MUST report the SAME single-source
    # version as /version + /api/version. Previously this used a separate
    # `app.version` key that was unset and fell back to a hardcoded "0.1.0",
    # creating the version drift. Source it from `_application_release` (== the
    # package `__version__` unless an explicit `app.release` override is set).
    app_version = _application_release(config)
    runtime_env_file = str(config.env_file or "")
    runtime_server_id = _runtime_server_id(config)

    async def _health_payload(*, live: bool = False) -> dict[str, Any]:
        """Build the health payload for liveness and readiness style endpoints."""
        checks = {} if live else {
            "db": await _db_health_probe(),
            "jobs": await _jobs_health_probe(),
        }
        statuses = {
            str((check or {}).get("status") or "ok").lower()
            for check in checks.values()
            if isinstance(check, dict)
        }
        status = "degraded" if "error" in statuses else "ok"
        return {
            "status": "ok" if live else status,
            "application": {"name": application_name},
            "runtime": {"env_file": runtime_env_file},
            "version": app_version,
            "checks": checks,
            "server": "api",
            "server_id": runtime_server_id,
            "env_file": runtime_env_file,
        }

    @app.get("/health")
    async def health() -> JSONResponse:
        """Return service health for direct runtime probes."""
        return JSONResponse(await _health_payload())

    @app.get("/api/health")
    async def api_health() -> JSONResponse:
        """Return service health for clients expecting an `/api` prefix."""
        return JSONResponse(await _health_payload())

    @app.get("/ready")
    async def ready() -> JSONResponse:
        return JSONResponse(await _health_payload())

    @app.get("/api/ready")
    async def api_ready() -> JSONResponse:
        return JSONResponse(await _health_payload())

    @app.get("/live")
    async def live() -> JSONResponse:
        return JSONResponse(await _health_payload(live=True))

    @app.get("/api/live")
    async def api_live() -> JSONResponse:
        return JSONResponse(await _health_payload(live=True))

    @app.get("/status")
    async def status() -> JSONResponse:
        return JSONResponse(await _health_payload())

    @app.get("/api/status")
    async def api_status() -> JSONResponse:
        return JSONResponse(await _health_payload())

    log_folder = str(config.get("app.logfolder"))
    sessions = SessionManager(log_folder, session_store=db_runtime.store)
    router = build_router(
        config=config,
        sessions=sessions,
        db_runtime=db_runtime,
        jobs_runtime=jobs_runtime,
    )
    app.include_router(router)
    app.include_router(build_config_router(config=config, db_runtime=db_runtime))
    # W28A-876: mount the canonical SHARED cloud_dog_idam idam_v1_router (resource-registry +
    # rbac-bindings) so the shared @cloud-dog/idam RBAC page resolves /v1/idam/v1/*.
    from cloud_dog_idam.api.fastapi.router import idam_v1_router
    try:
        from cloud_dog_idam.api.fastapi.router import set_idam_v1_engine
    except ImportError:  # cloud-dog-idam>=0.5.1 manages router storage internally.
        set_idam_v1_engine = None
    _idam_base = str(config.get("api_server.base_path") or "/v1").rstrip("/") or "/v1"
    if set_idam_v1_engine is not None:
        set_idam_v1_engine(getattr(db_runtime, "engine", None))
    app.include_router(idam_v1_router, prefix=_idam_base)

    @app.on_event("shutdown")
    async def _dispose_db_runtime() -> None:
        """Internal helper to dispose db runtime for this module."""
        runtime = getattr(app.state, "chat_db_runtime", None)
        if runtime is not None:
            runtime.dispose()

    # CC9 (W28C-1703): document the X-Admin-Key dual-key contract in OpenAPI.
    # User-scope endpoints accept the X-API-Key user credential; admin-scope
    # mutations additionally require X-Admin-Key (defence in depth). Marking the
    # per-operation security makes the requirement visible in the api-docs panel
    # and any generated client, instead of agents discovering it via a
    # misleading "Missing X-API-Key" error.
    from fastapi.openapi.utils import get_openapi as _get_openapi

    _admin_scope_segments = (
        "/users", "/groups", "/roles", "/api-keys", "/profiles",
        "/rbac", "/rbac-bindings", "/servers",
    )
    _admin_methods = {"post", "put", "patch", "delete"}
    _http_methods = {"get", "post", "put", "patch", "delete", "options", "head"}

    def _custom_openapi():
        """Build the OpenAPI schema with dual-key security documented."""
        if app.openapi_schema:
            return app.openapi_schema
        schema = _get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        components = schema.setdefault("components", {})
        components.setdefault("securitySchemes", {}).update(
            {
                "ApiKeyAuth": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-API-Key",
                    "description": "User credential — required for every authenticated endpoint.",
                },
                "AdminKeyAuth": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-Admin-Key",
                    "description": (
                        "Admin scope — required IN ADDITION to X-API-Key for "
                        "admin-scope mutations (defence in depth)."
                    ),
                },
            }
        )
        info = schema.setdefault("info", {})
        info["description"] = str(info.get("description") or "") + (
            "\n\n**Authentication.** Every authenticated endpoint requires the "
            "`X-API-Key` user credential. Admin-scope operations (create/update/"
            "delete of users, groups, roles, api-keys, profiles, RBAC bindings "
            "and MCP servers) require BOTH `X-API-Key` (user creds) AND "
            "`X-Admin-Key` (admin scope) — a defence-in-depth pairing. Presenting "
            "only one header returns 401 with a message naming both."
        )
        for path, operations in (schema.get("paths") or {}).items():
            is_admin_path = any(segment in path for segment in _admin_scope_segments)
            for method, operation in operations.items():
                if method not in _http_methods or not isinstance(operation, dict):
                    continue
                if is_admin_path and method in _admin_methods:
                    operation["security"] = [{"ApiKeyAuth": [], "AdminKeyAuth": []}]
                else:
                    operation["security"] = [{"ApiKeyAuth": []}]
        app.openapi_schema = schema
        return schema

    app.openapi = _custom_openapi

    return app


# Default app instance used by uvicorn.
# Configuration is loaded using runtime root detection + env precedence.
_app_config = ConfigManager()
app = create_app(_app_config)
