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

import json
import time
from typing import Any

from fastapi import FastAPI, Request

from cloud_dog_logging import (
    get_audit_logger,
    get_environment,
    get_service_instance,
    get_service_name,
)
from cloud_dog_logging.audit_schema import Actor, AuditEvent, Target

from ..config import ConfigManager

_SENSITIVE_TOKENS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "private_key",
    "authorization",
    "cookie",
    "credential",
)


def _header_value(request: Request, name: str) -> str:
    return str(request.headers.get(name) or "").strip()


def _request_id(request: Request) -> str:
    return str(
        getattr(request.state, "request_id", "")
        or _header_value(request, "x-request-id")
        or _header_value(request, "x-correlation-id")
        or ""
    ).strip()


def _correlation_id(request: Request) -> str:
    return str(
        getattr(request.state, "correlation_id", "")
        or _header_value(request, "x-correlation-id")
        or _request_id(request)
        or ""
    ).strip()


def _request_ip(request: Request) -> str:
    xff = _header_value(request, "x-forwarded-for")
    if xff:
        first = xff.split(",", 1)[0].strip()
        if first:
            return first
    real_ip = _header_value(request, "x-real-ip")
    if real_ip:
        return real_ip
    client = getattr(request, "client", None)
    return str(getattr(client, "host", "") or "unknown").strip() or "unknown"


def _query_keys(request: Request) -> list[str]:
    keys: list[str] = []
    for key in request.query_params.keys():
        raw = str(key or "").strip()
        if not raw:
            continue
        if any(token in raw.lower() for token in _SENSITIVE_TOKENS):
            if "__redacted__" not in keys:
                keys.append("__redacted__")
            continue
        if raw not in keys:
            keys.append(raw)
    return keys


def _redact_value(value: Any, *, key_hint: str = "") -> Any:
    key_l = str(key_hint or "").strip().lower()
    if any(token in key_l for token in _SENSITIVE_TOKENS):
        return "***REDACTED***"
    if isinstance(value, dict):
        return {str(k): _redact_value(v, key_hint=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(item, key_hint=key_hint) for item in value]
    if isinstance(value, str):
        return value if len(value) <= 512 else value[:509] + "..."
    return value


def _body_parameters(request: Request, body: bytes) -> dict[str, Any] | None:
    if not body:
        return None
    content_type = _header_value(request, "content-type").lower()
    if "application/json" in content_type:
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            return {"body_parse_error": "invalid_json"}
        if isinstance(payload, dict) and isinstance(payload.get("params"), dict):
            return _redact_value(payload["params"], key_hint="params")
        return _redact_value(payload)
    if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        try:
            form = dict(request.query_params)
        except Exception:
            form = {}
        return _redact_value(form)
    return {"body_bytes": len(body)}


def _outcome_and_severity(status_code: int) -> tuple[str, str]:
    if status_code in {401, 403}:
        return "denied", "WARNING"
    if status_code >= 500:
        return "error", "ERROR"
    if status_code >= 400:
        return "failure", "ERROR"
    return "success", "INFO"


def _action(method: str) -> str:
    mapping = {
        "GET": "read",
        "HEAD": "read",
        "OPTIONS": "read",
        "POST": "create",
        "PUT": "update",
        "PATCH": "update",
        "DELETE": "delete",
    }
    return mapping.get(str(method or "").upper(), "execute")


def install_http_audit_middleware(
    app: FastAPI,
    config: ConfigManager,
    *,
    user_header_name: str = "X-User",
) -> None:
    audit_logger = get_audit_logger()

    @app.middleware("http")
    async def _request_audit_middleware(request: Request, call_next):
        started = time.monotonic()
        response = None
        status_code = 500
        unhandled_error = ""
        body = await request.body()
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
            action = _action(method)
            outcome, severity = _outcome_and_severity(status_code)

            principal = getattr(request.state, "principal", {}) or {}
            principal_user_id = str(principal.get("user_id") or "").strip()
            principal_role = str(principal.get("role") or "").strip()
            state_actor = str(getattr(request.state, "actor", "") or "").strip()
            header_actor = _header_value(request, user_header_name)
            actor_id = state_actor or header_actor or principal_user_id or "anonymous"
            actor_type = "user" if actor_id != "anonymous" else "system"
            actor_roles = [principal_role] if principal_role else []

            details: dict[str, Any] = {
                "http": {
                    "method": method,
                    "path": str(request.url.path or ""),
                    "route": route_template,
                    "status_code": status_code,
                    "query_keys": _query_keys(request),
                },
                "parameters": _body_parameters(request, body) or {},
            }
            if unhandled_error:
                details["error_type"] = unhandled_error

            request_id = _request_id(request)
            correlation_id = _correlation_id(request) or request_id
            if not correlation_id:
                correlation_id = f"{time.time_ns()}"
            if not request_id:
                request_id = correlation_id

            event = AuditEvent(
                event_type=f"http.{action}",
                actor=Actor(
                    type=actor_type,
                    id=actor_id,
                    roles=actor_roles,
                    ip=_request_ip(request),
                    user_agent=_header_value(request, "user-agent") or None,
                ),
                action=action,
                outcome=outcome,
                correlation_id=correlation_id,
                request_id=request_id,
                trace_id=correlation_id,
                service=str(get_service_name() or config.get("app.name") or "unknown"),
                service_instance=str(get_service_instance() or "unknown"),
                environment=str(get_environment() or config.get("app.environment") or "unknown"),
                severity=severity,
                target=Target(type="http_route", id=route_template, name=f"{method} {route_template}"),
                details=details,
                duration_ms=int(round((time.monotonic() - started) * 1000.0)),
            )
            try:
                audit_logger.emit(event)
            except Exception:
                pass
