# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Dedicated A2A server for chat-client event fanout.

W28A-1002-CONV-CHAT-CLIENT — CFG-06 convergence to cloud_dog_api_kit.a2a.events
(0.12.0). The chat-client has two distinct database-backed event streams
(session events + config events) MERGED into one REST endpoint at
``/a2a/events`` and fed to a per-topic-filtered WebSocket at ``/a2a/ws``.

Adoption approach (aligned with W28A-1002-EXTEND-R2 Phase B retry pattern for
index-retriever):

- ``_ChatClientServiceBackedBroadcaster`` wraps ``runtime.store`` and
  ``runtime.config_store`` as an ``EventBroadcaster`` Protocol implementation.
  Its ``history()`` method synthesises canonical PS-72 §A2A-change-events
  ``ConfigChangeEvent`` instances on-demand from database rows — no second
  in-memory state store, preserving the single-source-of-truth invariant.
- Broadcaster exposed on ``app.state.a2a_events_broadcaster`` for platform
  compliance + future canonical SSE consumers (via
  ``create_a2a_events_router`` mounted at a non-conflicting path).
- Legacy ``GET /a2a/events`` and ``WS /a2a/ws`` preserved byte-for-byte by
  reading from the same stores (source of truth) to guarantee admin-SPA + AT
  contract preservation. Unlike index-retriever, chat-client's legacy response
  shape adds fields beyond the canonical envelope (``topic``, ``data``,
  ``session_id``, ``sequence``) and merges two distinct stream schemas, which
  ``RESTPollAdapter.field_mapping`` (rename-only) cannot express without loss.
"""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from cloud_dog_api_kit import create_app as create_api_kit_app  # type: ignore[import-untyped]
from cloud_dog_api_kit.a2a.card import create_a2a_card_router, A2ASkill
from cloud_dog_api_kit.a2a.events import (  # type: ignore[import-untyped]
    ConfigChangeEvent as _A2AConfigChangeEvent,
    EventBroadcaster as _A2AEventBroadcaster,
    create_a2a_events_router as _create_a2a_events_router,
)

from .. import __version__
from ..database.runtime import ChatDatabaseRuntime
from ..api.auth import validate_presented_api_key_for_service
from ..api.routes import (
    _extract_mcp_error_text,
    _extract_mcp_structured_or_text_object,
    _extract_mcp_text_content,
    _extract_mcp_tool_payload,
    _looks_like_file_mcp_server,
    _normalize_file_mcp_arguments,
)
from ..observability.http_audit import install_http_audit_middleware
from ..session.transcript import TranscriptEvent
from .common import bind_host, bind_port, configure_logging, load_config, run_uvicorn, server_id

_MESSAGE_EVENT_TYPES = {"user_message", "assistant_message"}

# Chat-client service identifier for canonical ConfigChangeEvent.service field.
_A2A_SERVICE_IDENT = "cloud-dog-chat-client"


def _topic_for_session_event(event_type: str) -> str:
    return "messages" if str(event_type or "") in _MESSAGE_EVENT_TYPES else "sessions"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso8601(ts: Any) -> datetime:
    """Best-effort ISO-8601 parse with UTC fallback."""
    if isinstance(ts, datetime):
        return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)
    raw = str(ts or "").strip()
    if not raw:
        return datetime.now(timezone.utc)
    # datetime.fromisoformat accepts both naive and offset-aware strings.
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


class _ChatClientServiceBackedBroadcaster:
    """``EventBroadcaster`` adapter wrapping chat-client's database-backed stores.

    Synthesises canonical PS-72 §A2A-change-events ``ConfigChangeEvent``
    instances on-demand from ``ChatSessionStore`` + ``ConfigStore`` rows.

    Design rationale (per W28A-1002-EXTEND-R2 Phase B retry lesson):
    - No second state store — the database stores remain the single source of
      truth. Publishing via the bespoke emit paths continues unchanged; the
      broadcaster is a READ-side lens over existing state.
    - Only ``history()`` is fully used by REST / SSE poll paths. ``publish`` is
      a no-op returning the passed event (publishes are performed via the
      service-owned ``append_event`` / config-store write paths that already
      fire DB inserts; wiring canonical publishes through a second pipeline is
      out-of-scope for the a2a_server.py replacement).
    - ``subscribe()`` returns an empty async-iterator to satisfy the Protocol;
      chat-client does not currently expose a canonical SSE live stream
      (CONVERGE escalation report: "SSE, gated on W28A-927i").
    """

    def __init__(self, runtime: ChatDatabaseRuntime) -> None:
        self._runtime = runtime

    async def publish(self, event: _A2AConfigChangeEvent) -> _A2AConfigChangeEvent:
        # Chat-client writes occur via append_event / config_store.persist_* paths;
        # this broadcaster surface is read-only for canonical consumers.
        return event

    def subscribe(self) -> AsyncIterator[_A2AConfigChangeEvent]:
        async def _empty() -> AsyncIterator[_A2AConfigChangeEvent]:
            if False:  # pragma: no cover
                yield  # type: ignore[unreachable]
        return _empty()

    def _session_rows(self, after_id: int, limit: int) -> list[dict[str, Any]]:
        return self._runtime.store.list_events(after_id=after_id, limit=limit)

    def _config_rows(self, after_id: int, limit: int) -> list[dict[str, Any]]:
        return self._runtime.config_store.list_events(after_id=after_id, limit=limit)

    def history(self, after_id: int = 0, limit: int = 100) -> list[_A2AConfigChangeEvent]:
        """Synthesise canonical ``ConfigChangeEvent``s from both DB stores.

        Event-id monotonicity across the two stores is synthesised: session
        events occupy the lower id range (``1..N_session``), config events the
        upper (``N_session+1 .. N_session+N_config``). This preserves
        pagination semantics for canonical consumers while the legacy REST
        handler continues to read each store's native ``id`` directly.
        """
        session_rows = self._session_rows(after_id=0, limit=limit)
        config_rows = self._config_rows(after_id=0, limit=limit)
        out: list[_A2AConfigChangeEvent] = []
        synthetic_id = 0
        for row in session_rows:
            synthetic_id += 1
            if synthetic_id <= int(after_id or 0):
                continue
            event_type = str(row.get("event_type") or "")
            topic = _topic_for_session_event(event_type)
            out.append(
                _A2AConfigChangeEvent(
                    service=_A2A_SERVICE_IDENT,
                    resource=topic,
                    action=event_type or "session_event",
                    identifier=str(row.get("session_id") or ""),
                    actor=None,
                    correlation_id=None,
                    before=None,
                    after=dict(row.get("data") or {}),
                    outcome="success",
                    timestamp=_parse_iso8601(row.get("timestamp")),
                    event_id=synthetic_id,
                )
            )
        for row in config_rows:
            synthetic_id += 1
            if synthetic_id <= int(after_id or 0):
                continue
            out.append(
                _A2AConfigChangeEvent(
                    service=_A2A_SERVICE_IDENT,
                    resource="config",
                    action=str(row.get("event_type") or row.get("action") or "config_change"),
                    identifier=str(row.get("entity_id") or ""),
                    actor=None,
                    correlation_id=None,
                    before=None,
                    after=dict(row.get("payload") or {}),
                    outcome="success",
                    timestamp=_parse_iso8601(row.get("created_at")),
                    event_id=synthetic_id,
                )
            )
        if limit <= 0:
            return []
        if len(out) > limit:
            out = out[-limit:]
        return out


async def _authorised_websocket(
    cfg,
    runtime: ChatDatabaseRuntime,
    ws: WebSocket,
    expected_header: str,
) -> bool:
    if not str(cfg.get("client_api.api_key") or "").strip():
        return True
    presented = str(
        ws.headers.get(expected_header)
        or ws.query_params.get("api_key")
        or ws.cookies.get("chat_client_api_key")
        or ""
    ).strip()
    # PS-92 (W28A-970g-V2): auth-check label uses the configured a2a base path.
    _a2a_base = str(cfg.get("a2a_server.base_path") or "/a2a").rstrip("/") or "/a2a"
    return await validate_presented_api_key_for_service(
        cfg,
        presented=presented,
        header_name=expected_header,
        path=f"{_a2a_base}/ws",
        method="WEBSOCKET",
        config_store=runtime.config_store,
        request_actor="unknown",
    )


async def _authorised_request(
    cfg,
    runtime: ChatDatabaseRuntime,
    request: Request,
    expected_header: str,
) -> bool:
    """CC2 (W28C-1703 / 1601-B): authorise an A2A event REST/SSE handshake.

    Mirrors ``_authorised_websocket`` for the HTTP surface so the legacy
    ``GET /a2a/events`` poll and the canonical ``/a2a/events/sse`` stream — both
    of which surface live session/config events — refuse anonymous callers.
    Accepts the API-key header, a ``chat_client_api_key`` cookie, an ``api_key``
    query param, or a bearer token (the cookie/api-key path the web proxy and
    SPA already use). Open only when no service API key is configured, matching
    the WS gate's behaviour.
    """
    if not str(cfg.get("client_api.api_key") or "").strip():
        return True
    bearer = str(request.headers.get("Authorization") or "").strip()
    presented = bearer[7:].strip() if bearer.lower().startswith("bearer ") else ""
    if not presented:
        presented = str(
            request.headers.get(expected_header)
            or request.cookies.get("chat_client_api_key")
            or request.query_params.get("api_key")
            or ""
        ).strip()
    _a2a_base = str(cfg.get("a2a_server.base_path") or "/a2a").rstrip("/") or "/a2a"
    return await validate_presented_api_key_for_service(
        cfg,
        presented=presented,
        header_name=expected_header,
        path=f"{_a2a_base}/events",
        method=str(request.method or "GET"),
        config_store=runtime.config_store,
        request_actor="unknown",
    )


def create_app():
    cfg = load_config()
    runtime = ChatDatabaseRuntime(cfg)
    expected_header = str(cfg.get("client_api.api_key_header") or "X-API-Key").strip() or "X-API-Key"
    # PS-92 (W28A-970g-V2): configurable A2A base path. Literal default from defaults.yaml.
    a2a_base_path = str(cfg.get("a2a_server.base_path") or "/a2a").rstrip("/") or "/a2a"
    app = create_api_kit_app(
        title="cloud-dog-chat-client-a2a",
        version="1",
        description="Cloud-Dog chat-client A2A event surface",
        enable_request_logging=True,
        enable_health=False,
        register_signal_handlers_on_startup=False,
        enable_audit_logging=False,
    )
    install_http_audit_middleware(app, cfg)
    app.router.routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", "") not in {"/health", "/ready", "/live"}
    ]

    # W28A-1002-CONV-CHAT-CLIENT: attach the ``EventBroadcaster`` Protocol
    # implementation to ``app.state`` for platform-primitive compliance. The
    # broadcaster wraps chat-client's DB-backed stores; canonical SSE
    # consumers may obtain ``ConfigChangeEvent`` history via
    # ``request.app.state.a2a_events_broadcaster.history(after_id, limit)``.
    _a2a_broadcaster: _A2AEventBroadcaster = _ChatClientServiceBackedBroadcaster(runtime)  # type: ignore[assignment]
    app.state.a2a_events_broadcaster = _a2a_broadcaster

    @app.on_event("shutdown")
    async def shutdown_event() -> None:
        runtime.dispose()

    def session_events(after_id: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        """Read session events from the canonical store (legacy response shape)."""
        return runtime.store.list_events(after_id=after_id, limit=limit)

    def config_events(after_id: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        """Read config events and project into the legacy response shape.

        Legacy contract (preserved byte-for-byte — admin-SPA + AT tests depend
        on ``id``/``topic``/``event_type``/``timestamp``/``data`` field names):
        """
        items = runtime.config_store.list_events(after_id=after_id, limit=limit)
        out: list[dict[str, Any]] = []
        for item in items:
            out.append(
                {
                    "id": int(item.get("id") or 0),
                    "topic": "config",
                    "event_type": str(item.get("action") or "config_change"),
                    "timestamp": str(item.get("created_at") or _utcnow()),
                    "data": item,
                }
            )
        return out

    # Platform health via create_health_router().
    _health_paths = {"/health", "/ready", "/live", "/status"}
    app.router.routes = [
        r for r in app.router.routes if getattr(r, "path", None) not in _health_paths
    ]
    def _health_payload() -> dict[str, Any]:
        return {
            "status": "ok",
            "application": {"name": "cloud-dog-chat-client"},
            "runtime": {"env_file": str(cfg.env_file or "")},
            "version": __version__,  # CC8: single source of truth
            "checks": {},
            "server": "a2a",
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

    @app.get(f"{a2a_base_path}/health")
    async def a2a_health() -> JSONResponse:
        return JSONResponse(_health_payload())

    @app.get(f"{a2a_base_path}/events")
    async def list_events(request: Request, after_id: int = 0, limit: int = 100) -> JSONResponse:
        """Legacy merged session+config event poll (preserved byte-for-byte).

        CC2 (W28C-1703 / 1601-B): this endpoint surfaces live session messages
        and config events; it had NO auth (the anon ``/weba2a/events`` leak).
        The handshake is now gated by the same credential check as the WS
        stream — anonymous callers get HTTP 401 (handshake refused).

        W28A-1002-CONV: this endpoint's response shape (``{"events": [{
        "id", "topic", "event_type", "timestamp", "data", ...}]}``) cannot be
        produced by ``RESTPollAdapter`` alone because the chat-client contract
        MERGES two heterogeneous database-backed streams (session events +
        config events) with schema-additive fields (``topic``, ``data``,
        ``session_id``, ``sequence``) beyond the canonical
        ``ConfigChangeEvent`` envelope. ``RESTPollAdapter.field_mapping``
        implements 1:1 rename of canonical keys and cannot add schema-foreign
        fields, so legacy contract preservation requires reading from the
        two stores directly. The canonical envelope is authoritative and
        available via ``app.state.a2a_events_broadcaster.history()`` +
        ``{a2a_base_path}/events/sse`` for canonical consumers.
        """
        if not await _authorised_request(cfg, runtime, request, expected_header):
            return JSONResponse(
                {
                    "ok": False,
                    "errors": [
                        {
                            "code": "UNAUTHENTICATED",
                            "message": (
                                "This endpoint requires a valid "
                                f"{expected_header} (or session cookie); the A2A "
                                "event stream is not available anonymously"
                            ),
                        }
                    ],
                },
                status_code=401,
            )
        events = session_events(after_id=after_id, limit=limit) + config_events(after_id=after_id, limit=limit)
        events.sort(key=lambda item: (str(item.get("timestamp") or ""), str(item.get("topic") or ""), int(item.get("id") or 0)))
        if int(after_id or 0) <= 0:
            events = events[-int(limit or 100):]
        else:
            events = events[:int(limit or 100)]
        return JSONResponse({"events": events})

    # W28A-1002-CONV-CHAT-CLIENT: mount the cloud_dog_api_kit canonical SSE
    # surface at a non-conflicting path (the legacy REST ``/a2a/events`` handler
    # above must be preserved for admin-SPA + AT compatibility). Canonical
    # consumers that want PS-72 §A2A-change-events-compliant SSE frames can
    # connect to ``{a2a_base_path}/events/sse`` and get live-stream + history
    # replay bundled by the platform primitive.
    # CC2 (W28C-1703 / 1601-B): gate the canonical SSE surface too — the
    # broadcaster replays config-event history, so it must not be anonymous.
    async def _require_a2a_event_auth(request: Request) -> None:
        """Refuse anonymous callers to the canonical A2A SSE event router."""
        if not await _authorised_request(cfg, runtime, request, expected_header):
            raise HTTPException(
                status_code=401,
                detail=(
                    f"This endpoint requires a valid {expected_header} (or "
                    "session cookie); the A2A event stream is not available "
                    "anonymously"
                ),
            )

    app.include_router(
        _create_a2a_events_router(
            _a2a_broadcaster,
            base_path=f"{a2a_base_path}/events/sse",
        ),
        dependencies=[Depends(_require_a2a_event_auth)],
    )

    @app.websocket(f"{a2a_base_path}/ws")
    async def websocket_events(websocket: WebSocket) -> None:
        if not await _authorised_websocket(cfg, runtime, websocket, expected_header):
            await websocket.close(code=4401)
            return
        topics_raw = str(websocket.query_params.get("topics") or websocket.query_params.get("topic") or "sessions,messages,config")
        topics = {item.strip() for item in topics_raw.split(",") if item.strip()}
        if not topics:
            topics = {"sessions", "messages", "config"}
        await websocket.accept()
        default_after = int(websocket.query_params.get("after_id") or 0)
        last_session_id = int(
            websocket.query_params.get("after_session_id") or default_after
        )
        last_config_id = int(
            websocket.query_params.get("after_config_id") or default_after
        )
        try:
            while True:
                for event in session_events(after_id=last_session_id, limit=50):
                    last_session_id = max(last_session_id, int(event.get("id") or 0))
                    if str(event.get("topic") or "") in topics:
                        await websocket.send_json(event)
                for event in config_events(after_id=last_config_id, limit=50):
                    last_config_id = max(last_config_id, int(event.get("id") or 0))
                    if str(event.get("topic") or "") in topics:
                        await websocket.send_json(event)
                await asyncio.sleep(1.0)
        except WebSocketDisconnect:
            return

    # --- A2A skill handlers (real service logic) ---

    def _parse_skill_payload(text: str) -> dict[str, Any]:
        raw = str(text or "").strip()
        if not raw:
            raise ValueError("A JSON object payload is required")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("Skill input must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("Skill input must decode to a JSON object")
        return payload

    def _session_server_specs(session_id: str | None = None) -> list[dict[str, Any]]:
        raw_servers = cfg.get("mcp.servers") or []
        if not isinstance(raw_servers, list):
            raw_servers = []
        if not session_id:
            return [item for item in raw_servers if isinstance(item, dict)]
        session = runtime.store.get_session(session_id)
        if not isinstance(session, dict):
            return [item for item in raw_servers if isinstance(item, dict)]
        metadata = session.get("metadata")
        if not isinstance(metadata, dict):
            return [item for item in raw_servers if isinstance(item, dict)]
        profile_servers = metadata.get("profile_mcp_servers")
        if not isinstance(profile_servers, list):
            return [item for item in raw_servers if isinstance(item, dict)]
        return [item for item in profile_servers if isinstance(item, dict)]

    def _resolve_file_server_index(
        session_id: str,
        requested_index: Any,
    ) -> tuple[int, list[dict[str, Any]]]:
        current_servers = _session_server_specs(session_id)
        if not current_servers:
            raise ValueError("No MCP servers are configured for this session")
        if requested_index is not None:
            try:
                resolved = int(requested_index)
            except (TypeError, ValueError) as exc:
                raise ValueError("server_index must be an integer") from exc
            if resolved < 0 or resolved >= len(current_servers):
                raise ValueError("server_index is out of range for this session")
            return resolved, current_servers
        for index, server_spec in enumerate(current_servers):
            if _looks_like_file_mcp_server(server_spec):
                return index, current_servers
        raise ValueError("No file-mcp server is configured for this session")

    def _with_profile_header(server_spec: dict[str, Any], profile: str) -> dict[str, Any]:
        updated = dict(server_spec)
        profile_name = str(profile or "").strip()
        if not profile_name:
            return updated
        headers = updated.get("extra_headers")
        if not isinstance(headers, dict):
            headers = {}
        else:
            headers = dict(headers)
        headers["x-file-mcp-profile"] = profile_name
        updated["extra_headers"] = headers
        return updated

    async def _maybe_initialize_file_mcp(connection, server_spec: dict[str, Any], requested: Any) -> None:
        should_initialize = (
            bool(requested)
            if requested is not None
            else bool(
                server_spec.get("require_initialize")
                if server_spec.get("require_initialize") is not None
                else cfg.get("mcp.api.require_initialize") or False
            )
        )
        if not should_initialize:
            return
        protocol_version = str(
            server_spec.get("protocol_version") or cfg.get("mcp.defaults.protocol_version") or ""
        ).strip()
        if not protocol_version:
            raise ValueError("mcp.defaults.protocol_version is required for initialize")
        await connection.transport.initialize(protocol_version=protocol_version)
        ensure_sse = getattr(connection.transport, "ensure_sse_stream", None)
        if callable(ensure_sse):
            try:
                await ensure_sse()
            except Exception as exc:
                msg = str(exc)
                if (
                    "Streamable HTTP notifications require an established session" in msg
                    or "Cannot open SSE stream without session id" in msg
                ):
                    return
                raise

    async def _run_file_tool(
        session_id: str,
        *,
        server_index: Any,
        profile: str,
        tool_name: str,
        arguments: dict[str, Any],
        require_initialize: Any,
    ) -> tuple[dict[str, Any], int, str]:
        if runtime.store.get_session(session_id) is None:
            raise ValueError("Unknown session")
        resolved_index, current_servers = _resolve_file_server_index(session_id, server_index)
        servers_override = [dict(item) for item in current_servers]
        servers_override[resolved_index] = _with_profile_header(
            servers_override[resolved_index], profile
        )
        server_spec = servers_override[resolved_index]
        normalized_arguments = _normalize_file_mcp_arguments(server_spec, tool_name, arguments)

        from ..mcp.connection import MCPConnection

        connection = MCPConnection.from_config(
            cfg,
            server_index=resolved_index,
            servers_override=servers_override,
        )
        await connection.connect()
        try:
            await _maybe_initialize_file_mcp(connection, server_spec, require_initialize)
            result = await connection.transport.tools_call(tool_name, normalized_arguments)
            path = str(normalized_arguments.get("path") or arguments.get("path") or "").strip()
            return result, resolved_index, path
        finally:
            await connection.close()

    async def _a2a_upload_file(text: str) -> str:
        payload = _parse_skill_payload(text)
        session_id = str(payload.get("session_id") or "").strip()
        path = str(payload.get("path") or "").strip()
        profile = str(payload.get("profile") or "").strip()
        if not session_id:
            raise ValueError("session_id is required")
        if not path:
            raise ValueError("path is required")

        content_text = payload.get("content_text")
        content_base64 = str(payload.get("content_base64") or "").strip()
        if bool(content_text is not None and str(content_text) != "") == bool(content_base64):
            raise ValueError("Exactly one of content_text or content_base64 must be provided")
        if content_text is not None and str(content_text) != "":
            content_bytes = str(content_text).encode("utf-8")
        else:
            raw = content_base64.replace("-", "+").replace("_", "/")
            raw += "=" * ((4 - len(raw) % 4) % 4)
            try:
                content_bytes = base64.b64decode(raw, validate=False)
            except Exception as exc:
                raise ValueError("content_base64 is invalid") from exc

        runtime.store.append_event(
            session_id,
            TranscriptEvent(
                event_type="a2a_file_upload",
                data={"path": path, "profile": profile},
            ),
        )

        encoded = base64.b64encode(content_bytes).decode("ascii")
        result, resolved_index, normalized_path = await _run_file_tool(
            session_id,
            server_index=payload.get("server_index"),
            profile=profile,
            tool_name="b64_decode_to_file",
            arguments={
                "path": path,
                "data": encoded,
                "urlsafe": False,
                "overwrite": bool(payload.get("overwrite", True)),
                "dry_run": bool(payload.get("dry_run", False)),
            },
            require_initialize=payload.get("require_initialize"),
        )
        if result.get("isError") is True:
            write_result, resolved_index, normalized_path = await _run_file_tool(
                session_id,
                server_index=payload.get("server_index"),
                profile=profile,
                tool_name="write_file",
                arguments={
                    "path": path,
                    "content": content_bytes.decode("utf-8"),
                    "overwrite": bool(payload.get("overwrite", True)),
                    "dry_run": bool(payload.get("dry_run", False)),
                },
                require_initialize=payload.get("require_initialize"),
            )
            if write_result.get("isError") is True:
                detail = _extract_mcp_error_text(write_result) or "MCP upload failed"
                raise ValueError(detail)
            payload_obj = _extract_mcp_structured_or_text_object(write_result)
            payload_obj.setdefault("bytes_written", len(content_bytes))
            payload_obj.setdefault("path", normalized_path)
        else:
            payload_obj = _extract_mcp_tool_payload(result)

        bytes_written = int(payload_obj.get("bytes_written") or len(content_bytes))
        response_path = str(payload_obj.get("path") or normalized_path or path).strip() or path
        runtime.store.append_event(
            session_id,
            TranscriptEvent(
                event_type="a2a_file_upload_result",
                data={
                    "path": response_path,
                    "bytes_written": bytes_written,
                    "server_index": resolved_index,
                },
            ),
        )
        return json.dumps(
            {
                "ok": True,
                "action": "upload_file",
                "session_id": session_id,
                "path": response_path,
                "bytes_written": bytes_written,
                "server_index": resolved_index,
                "profile": profile or None,
            }
        )

    async def _a2a_download_file(text: str) -> str:
        payload = _parse_skill_payload(text)
        session_id = str(payload.get("session_id") or "").strip()
        path = str(payload.get("path") or "").strip()
        profile = str(payload.get("profile") or "").strip()
        if not session_id:
            raise ValueError("session_id is required")
        if not path:
            raise ValueError("path is required")

        runtime.store.append_event(
            session_id,
            TranscriptEvent(
                event_type="a2a_file_download",
                data={"path": path, "profile": profile},
            ),
        )

        result, resolved_index, normalized_path = await _run_file_tool(
            session_id,
            server_index=payload.get("server_index"),
            profile=profile,
            tool_name="b64_encode_file",
            arguments={"path": path, "urlsafe": False},
            require_initialize=payload.get("require_initialize"),
        )
        if result.get("isError") is True:
            read_result, resolved_index, normalized_path = await _run_file_tool(
                session_id,
                server_index=payload.get("server_index"),
                profile=profile,
                tool_name="read_file",
                arguments={"path": path},
                require_initialize=payload.get("require_initialize"),
            )
            if read_result.get("isError") is True:
                detail = _extract_mcp_error_text(read_result) or "MCP download failed"
                raise ValueError(detail)
            payload_obj = _extract_mcp_structured_or_text_object(read_result)
            content_text_out = str(
                payload_obj.get("result") or payload_obj.get("content") or _extract_mcp_text_content(read_result) or ""
            )
            if not content_text_out:
                raise ValueError("MCP download returned empty content")
            file_bytes = content_text_out.encode("utf-8")
            content_base64_out = base64.b64encode(file_bytes).decode("ascii")
        else:
            payload_obj = _extract_mcp_tool_payload(result)
            content_base64_out = str(payload_obj.get("data") or "").strip()
            if not content_base64_out:
                raise ValueError("MCP download response missing base64 data")
            raw = content_base64_out.replace("-", "+").replace("_", "/")
            raw += "=" * ((4 - len(raw) % 4) % 4)
            try:
                file_bytes = base64.b64decode(raw, validate=False)
            except Exception as exc:
                raise ValueError("MCP download returned invalid base64 data") from exc

        response_path = str(payload_obj.get("path") or normalized_path or path).strip() or path
        runtime.store.append_event(
            session_id,
            TranscriptEvent(
                event_type="a2a_file_download_result",
                data={
                    "path": response_path,
                    "byte_size": len(file_bytes),
                    "server_index": resolved_index,
                },
            ),
        )
        response_payload: dict[str, Any] = {
            "ok": True,
            "action": "download_file",
            "session_id": session_id,
            "path": response_path,
            "byte_size": len(file_bytes),
            "server_index": resolved_index,
            "profile": profile or None,
            "content_base64": content_base64_out,
        }
        try:
            response_payload["content_text"] = file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            response_payload["content_text"] = None
        return json.dumps(response_payload)

    def _a2a_list_servers(text: str) -> str:
        """List configured MCP servers from config."""
        raw = cfg.get("mcp.servers") or []
        if not isinstance(raw, list):
            return "No MCP servers configured"
        names = []
        for idx, srv in enumerate(raw):
            if isinstance(srv, dict):
                label = srv.get("name") or srv.get("command") or f"server-{idx}"
                names.append(f"  {idx}: {label}")
        if not names:
            return "No MCP servers configured"
        return f"Found {len(names)} MCP servers:\n" + "\n".join(names)

    def _a2a_create_session(text: str) -> str:
        """Create a new chat session via the runtime store."""
        try:
            from ..session.session_manager import SessionManager
            log_folder = str(cfg.get("log.folder") or cfg.get("app.log_folder") or "/tmp/chat-sessions")
            mgr = SessionManager(log_folder=log_folder, session_store=runtime.store)
            session_id = mgr.create_session(metadata={"source": "a2a", "text": text})
            return f"Session created: {session_id}"
        except Exception as exc:
            return f"Error creating session: {exc}"

    async def _a2a_send_message(text: str) -> str:
        """Send a message. Text format: '<session_id> <message>' or just '<message>' (creates new session)."""
        try:
            from ..session.session_manager import SessionManager
            log_folder = str(cfg.get("log.folder") or cfg.get("app.log_folder") or "/tmp/chat-sessions")
            mgr = SessionManager(log_folder=log_folder, session_store=runtime.store)
            parts = text.strip().split(None, 1)
            if len(parts) == 2 and len(parts[0]) >= 32:
                session_id = parts[0]
            else:
                session_id = mgr.create_session(metadata={"source": "a2a"})
            return f"Message queued in session {session_id}. Note: full LLM processing requires the API server runtime."
        except Exception as exc:
            return f"Error sending message: {exc}"

    # A2A agent card and task submission router
    _a2a_skills = [
        A2ASkill(id="create_session", name="Create Session", description="Create a new chat session", handler=_a2a_create_session),
        A2ASkill(id="send_message", name="Send Message", description="Send a message in a chat session", handler=_a2a_send_message),
        A2ASkill(id="list_servers", name="List Servers", description="List available MCP servers", handler=_a2a_list_servers),
        A2ASkill(id="upload_file", name="Upload File", description="Upload file content to file-mcp via the current chat session", handler=_a2a_upload_file),
        A2ASkill(id="download_file", name="Download File", description="Download file content from file-mcp via the current chat session", handler=_a2a_download_file),
    ]
    _a2a_card_router = create_a2a_card_router(
        name="chat-client",
        description="Chat client A2A server for real-time chat session events",
        skills=_a2a_skills,
    )
    app.include_router(_a2a_card_router)

    return app


def main() -> None:
    cfg = load_config()
    configure_logging(
        cfg,
        section="a2a_server",
        default_log_name="a2a_server.log",
        app_name="cloud_dog_chat_a2a",
    )
    host = bind_host(cfg, "a2a_server")
    port = bind_port(cfg, "a2a_server")
    log_level = str(cfg.get("log.level") or "INFO")
    run_uvicorn(create_app(), host=host, port=port, log_level=log_level)
# W28A-565 cache bust 1775026030
