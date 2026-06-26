# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Dedicated MCP server exposing chat-client tools to other agents."""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse
from cloud_dog_api_kit import create_app as create_api_kit_app  # type: ignore[import-untyped]
from cloud_dog_api_kit.middleware.timeout import TimeoutMiddleware  # type: ignore[import-untyped]

from cloud_dog_idam.rbac import RBACEngine  # PS-70 RBAC
from cloud_dog_logging import get_audit_logger  # PS-40 audit
from cloud_dog_logging.audit_schema import Actor, Target

from .common import (
    api_auth_header,
    api_auth_key,
    api_base_url,
    bind_host,
    bind_port,
    configure_logging,
    load_config,
    request_timeout_seconds,
    run_uvicorn,
    server_id,
)
from .. import __version__
from ..api.auth import validate_presented_api_key_for_service
from ..observability.http_audit import install_http_audit_middleware

# PS-70 — Per-tool RBAC permissions
_TOOL_PERMISSIONS = {
    "create_session": "chat:conversation:create",
    "send_message": "chat:message:send",
    "list_sessions": "chat:conversation:read",
    "get_history": "chat:conversation:read",
}

_rbac_engine = RBACEngine(role_permissions={
    "admin": set(_TOOL_PERMISSIONS.values()) | {"chat:admin:*"},
    "user": set(_TOOL_PERMISSIONS.values()),
    "viewer": {"chat:conversation:read"},
})

_audit_logger = get_audit_logger()


def _audit_tool_call(tool_name: str, actor_id: str, outcome: str, details: dict[str, Any] | None = None) -> None:
    """PS-40 tool audit — redact message content."""
    safe_details = dict(details or {})
    safe_details.pop("content", None)  # Redact chat message body
    safe_details.pop("system_prompt", None)
    _audit_logger.log_crud(
        actor=Actor(type="service", id=actor_id),
        action=f"mcp.tool.{tool_name}",
        target=Target(type="mcp_tool", id=tool_name),
        outcome=outcome,
        **({"details": safe_details} if safe_details else {}),
    )

_TOOLS = [
    {
        "name": "create_session",
        "description": "Create a chat session in chat-client.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "metadata": {"type": "object"},
            },
        },
    },
    {
        "name": "send_message",
        "description": "Send a message to an existing chat session.",
        "inputSchema": {
            "type": "object",
            "required": ["session_id", "content"],
            "properties": {
                "session_id": {"type": "string"},
                "content": {"type": "string"},
                "stream": {"type": "boolean"},
                "system_prompt": {"type": "string"},
            },
        },
    },
    {
        "name": "list_sessions",
        "description": "List known chat sessions.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_history",
        "description": "Return transcript history for one session.",
        "inputSchema": {
            "type": "object",
            "required": ["session_id"],
            "properties": {"session_id": {"type": "string"}},
        },
    },
]


def _jsonrpc_result(req_id: Any, result: Any) -> JSONResponse:
    """Return a JSON-RPC success response."""
    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})


def _jsonrpc_error(req_id: Any, code: int, message: str) -> JSONResponse:
    """Return a JSON-RPC error response."""
    return JSONResponse(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}},
        status_code=400,
    )


def _jsonrpc_unauthorised(req_id: Any, message: str) -> JSONResponse:
    """Return a JSON-RPC error with HTTP 401 for the unauthenticated/denied case.

    CC1 (W28C-1703): anonymous callers to the chat-client MCP surface MUST get an
    HTTP 401 (the unauth-negative probe asserts `%{http_code} == 401`), not the
    generic 400 used for protocol errors.
    """
    return JSONResponse(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32001, "message": message}},
        status_code=401,
    )


async def _call_api(
    cfg,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    forwarded_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Call the internal chat-client API with service authentication."""
    headers = {api_auth_header(cfg): api_auth_key(cfg)} if api_auth_key(cfg) else {}
    if forwarded_headers:
        headers.update({k: v for k, v in forwarded_headers.items() if str(v or "").strip()})
    timeout = request_timeout_seconds(cfg)
    async with httpx.AsyncClient(base_url=api_base_url(cfg).rstrip("/"), timeout=timeout) as client:
        response = await client.request(method, path, headers=headers, json=payload)
    if response.status_code >= 400:
        raise RuntimeError(f"API {method} {path} failed: {response.status_code} {response.text[:240]}")
    return response.json()


def _forwarded_api_headers(request: Request) -> dict[str, str]:
    """Extract headers that should flow through MCP-to-API calls."""
    forwarded: dict[str, str] = {}
    for header_name in ("X-User", "X-Request-Id", "X-Correlation-Id"):
        value = str(request.headers.get(header_name) or "").strip()
        if value:
            forwarded[header_name] = value
    return forwarded


async def _dispatch_tool(cfg, name: str, arguments: dict[str, Any], request: Request) -> dict[str, Any]:
    """Dispatch a chat-client MCP tool onto the internal API surface."""
    forwarded_headers = _forwarded_api_headers(request)
    if name == "create_session":
        payload = await _call_api(
            cfg,
            "POST",
            "/sessions",
            payload={"metadata": dict(arguments.get("metadata") or {})},
            forwarded_headers=forwarded_headers,
        )
    elif name == "send_message":
        session_id = str(arguments.get("session_id") or "").strip()
        if not session_id:
            raise RuntimeError("session_id is required")
        payload = await _call_api(
            cfg,
            "POST",
            f"/sessions/{session_id}/messages",
            payload={
                "content": str(arguments.get("content") or ""),
                "stream": bool(arguments.get("stream", False)),
                "system_prompt": arguments.get("system_prompt"),
            },
            forwarded_headers=forwarded_headers,
        )
    elif name == "list_sessions":
        payload = await _call_api(cfg, "GET", "/sessions", forwarded_headers=forwarded_headers)
    elif name == "get_history":
        session_id = str(arguments.get("session_id") or "").strip()
        if not session_id:
            raise RuntimeError("session_id is required")
        payload = await _call_api(
            cfg,
            "GET",
            f"/sessions/{session_id}/transcript",
            forwarded_headers=forwarded_headers,
        )
    else:
        raise RuntimeError(f"Unknown tool: {name}")

    return {
        "content": [{"type": "text", "text": json.dumps(payload)}],
        "structuredContent": payload,
        "isError": False,
    }


async def _authorised(request: Request, cfg) -> bool:
    """Validate the presented API key for MCP tool execution."""
    presented = ""
    bearer = str(request.headers.get("Authorization") or "").strip()
    if bearer.lower().startswith("bearer "):
        presented = bearer[7:].strip()
    else:
        presented = str(
            request.headers.get(api_auth_header(cfg))
            or request.cookies.get("chat_client_api_key")
            or ""
        ).strip()
    return await validate_presented_api_key_for_service(
        cfg,
        presented=presented,
        header_name=api_auth_header(cfg),
        path=str(request.url.path or ""),
        method=str(request.method or ""),
        config_store=None,
        request_actor="unknown",
    )


def create_app():
    """Create the chat-client MCP FastAPI application."""
    cfg = load_config()
    timeout = max(request_timeout_seconds(cfg), 30.0)
    # PS-92 (W28A-970g-V2): configurable MCP base path. Literal default from defaults.yaml.
    mcp_base_path = str(cfg.get("mcp_server.base_path") or "/mcp").rstrip("/") or "/mcp"
    app = create_api_kit_app(
        title="cloud-dog-chat-client-mcp",
        version="1",
        description="Cloud-Dog chat-client MCP tool surface",
        enable_request_logging=True,
        register_signal_handlers_on_startup=False,
        enable_audit_logging=False,
    )
    install_http_audit_middleware(app, cfg)
    for mw in app.user_middleware:
        if mw.cls is TimeoutMiddleware:
            mw.kwargs["timeout_seconds"] = timeout
    # Platform health via create_health_router().
    app.router.routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", "") not in {"/health", "/ready", "/live", "/status"}
    ]
    def _health_payload() -> dict[str, Any]:
        """Build the common health payload for the MCP surface."""
        return {
            "status": "ok",
            "application": {"name": "cloud-dog-chat-client"},
            "runtime": {"env_file": str(cfg.env_file or "")},
            "version": __version__,  # CC8: single source of truth
            "checks": {},
            "server": "mcp",
            "server_id": server_id(cfg),
            "env_file": str(cfg.env_file or ""),
        }

    @app.get("/health")
    @app.get(f"{mcp_base_path}/health")
    async def health() -> JSONResponse:
        """Return MCP health status."""
        return JSONResponse(_health_payload())

    @app.get("/ready")
    async def ready() -> JSONResponse:
        """Return MCP readiness status."""
        return JSONResponse(_health_payload())

    @app.get("/live")
    async def live() -> JSONResponse:
        """Return MCP liveness status."""
        return JSONResponse(_health_payload())

    @app.get("/status")
    async def status() -> JSONResponse:
        """Return MCP runtime status."""
        return JSONResponse(_health_payload())

    # CC1 (W28C-1703 / 1601-B) — SECURITY: the api-kit `register_mcp_contract`
    # mount was registered here BEFORE the bespoke `mcp_endpoint` below, so
    # FastAPI first-wins routing served `POST /mcp` + `POST /messages` from the
    # api-kit transport — which dispatches tools with NO auth — and made the
    # bespoke `_authorised` + `_TOOL_PERMISSIONS` gate DEAD CODE (the anon
    # 575-session `tools/call list_sessions` leak). It also exposed a second
    # anon execute path `POST /mcp/tools/{tool_name}`. We DROP the redundant
    # api-kit contract mount: nothing consumes chat-client's own REST/SSE MCP
    # surface (the SPA Tools page and the API `/sessions/{id}/mcp/*` proxy are
    # separate, already auth-gated paths), and the bespoke JSON-RPC endpoint
    # below implements the full contract (initialize / tools/list / tools/call)
    # with default-deny auth.

    @app.post(f"{mcp_base_path}")
    @app.post("/messages")
    async def mcp_endpoint(request: Request) -> JSONResponse:
        """Handle JSON-RPC MCP requests."""
        payload = await request.json()
        req_id = payload.get("id")
        method = str(payload.get("method") or "").strip()
        params = payload.get("params") or {}

        # CC1 (W28C-1703 / 1601-B): default-deny — EVERY JSON-RPC call on the
        # chat-client MCP surface requires a valid API key. Previously only
        # tools/call was gated, and even that gate was dead code behind the
        # api-kit transport mount, so anonymous initialize / tools/list /
        # tools/call all reached tool handlers (the 575-session leak). The gate
        # now runs first for every method and returns HTTP 401 to anon callers.
        if not await _authorised(request, cfg):
            return _jsonrpc_unauthorised(
                req_id,
                "Unauthorised: a valid X-API-Key is required for the chat-client MCP surface",
            )

        if method == "initialize":
            return _jsonrpc_result(
                req_id,
                {
                    "protocolVersion": str(params.get("protocolVersion") or "2024-11-05"),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "cloud-dog-chat-client-mcp", "version": "1"},
                },
            )
        if method == "notifications/initialized":
            return _jsonrpc_result(req_id, {"ack": True})
        if method == "tools/list":
            return _jsonrpc_result(req_id, {"tools": _TOOLS})
        if method != "tools/call":
            return _jsonrpc_error(req_id, -32601, f"Method not found: {method}")

        name = str(params.get("name") or "").strip()
        arguments = dict(params.get("arguments") or {})

        # CC1 default-deny: a tool not declared in _TOOL_PERMISSIONS is refused
        # (401) rather than falling through to the dispatcher's "Unknown tool".
        required_perm = _TOOL_PERMISSIONS.get(name)
        if required_perm is None:
            _audit_tool_call(
                name or "<unknown>",
                str(request.headers.get("X-User") or "anonymous"),
                "denied",
                {"reason": "tool_not_permitted"},
            )
            return _jsonrpc_unauthorised(
                req_id,
                f"Unauthorised: tool '{name}' is not in the permitted tool set",
            )

        # PS-70 — Per-tool RBAC check
        actor_id = str(request.headers.get("X-User") or "anonymous")
        _rbac_engine.assign_role_to_user(actor_id, "user")
        if not _rbac_engine.has_permission(actor_id, required_perm):
            _audit_tool_call(name, actor_id, "denied", {"reason": "rbac"})
            return _jsonrpc_error(req_id, -32001, f"Permission denied: {required_perm}")

        try:
            result = await _dispatch_tool(cfg, name, arguments, request)
            # PS-40 — Audit tool call success (content redacted)
            _audit_tool_call(name, str(request.headers.get("X-User") or "anonymous"), "success", {"session_id": arguments.get("session_id")})
        except Exception as exc:
            _audit_tool_call(name, str(request.headers.get("X-User") or "anonymous"), "failure", {"error": str(exc)[:200]})
            return _jsonrpc_result(
                req_id,
                {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            )
        return _jsonrpc_result(req_id, result)

    return app


def main() -> None:
    """Run the MCP server process."""
    cfg = load_config()
    configure_logging(
        cfg,
        section="mcp_server",
        default_log_name="mcp_server.log",
        app_name="cloud_dog_chat_mcp",
    )
    host = bind_host(cfg, "mcp_server")
    port = bind_port(cfg, "mcp_server")
    log_level = str(cfg.get("log.level") or "INFO")
    run_uvicorn(create_app(), host=host, port=port, log_level=log_level)
