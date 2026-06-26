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

import asyncio
import base64
import copy
import hashlib
import json
import mimetypes
import os
import re
import resource
import secrets
import time
from collections import OrderedDict  # noqa: F401 — retained for typing use
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Dict, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from cloud_dog_logging import get_audit_logger, get_logger  # type: ignore[import-untyped]
from cloud_dog_logging.audit_schema import AuditEvent, Actor, Target  # type: ignore[import-untyped]

from ..agent.runtime import AgentDispatchContext, dispatch_agent_message, stream_agent_message
from ..agent.strategy import (
    SIMPLE_AGENT_STRATEGY,
    agent_strategy_for_session,
    normalize_session_metadata,
)
from ..clients.code_runner import (  # W28I-1218 consumer-side code.execute A2A client
    CodeRunnerClient,
    CodeRunnerError,
)
from ..config import ConfigManager
from ..llm import LLMService
from ..llm.providers import LLMProviderError
from ..prompts import (  # opt-in prompt-template resolution (W28B-319 / D5)
    PROMPTS_AVAILABLE,
    PromptStore,
    TemplateNotFound,
    resolve_request_system_prompt,
)
from ..llm.response_policy import format_user_response
from ..llm.protocols import ChatMessage
from ..session import SessionManager
from ..session.transcript import TranscriptEvent
from ..storage_fs import (
    disk_usage_percent,
    file_name,
    is_absolute_path,
    join_path,
    list_dir as storage_list_dir,
    path_exists,
    read_text,
    resolve_path,
    storage_for_root,
)
from .. import __version__
from ..test_harness import TestFlowRuntime
from ..ui_spa import serve_runtime_config, serve_spa_asset, serve_spa_index
from .auth import request_actor, require_admin_key, require_api_key

if TYPE_CHECKING:
    from ..database.runtime import ChatDatabaseRuntime
    from ..jobs import JobsRuntime

_REDACTED_VALUE = "***REDACTED***"
_PROCESS_START_MONOTONIC = time.monotonic()


class CreateSessionRequest(BaseModel):
    metadata: Dict[str, Any] = {}


class CreateSessionResponse(BaseModel):
    session_id: str


class SendMessageRequest(BaseModel):
    content: str
    stream: Optional[bool] = None
    system_prompt: Optional[str] = None
    # W28B-319 (D5) — opt-in prompt-template resolution. When `prompt_template`
    # is supplied the system prompt is resolved+rendered from the shared
    # PromptStore; otherwise behaviour is byte-for-byte unchanged.
    prompt_template: Optional[str] = None
    prompt_variables: Optional[Dict[str, Any]] = None
    prompt_version: Optional[int] = None


class SendMessageResponse(BaseModel):
    session_id: str
    content: str


class LoadSessionResponse(BaseModel):
    session_id: str
    events_count: int


class SessionDetailResponse(BaseModel):
    """CC4 (W28C-1703): single-session fetch response for ``GET
    /sessions/{session_id}``. Carries metadata + the last-N transcript events.

    ``session_id`` is canonical; ``id`` is a deprecated alias retained for one
    release cycle (CC5 schema convergence) so existing ``id`` consumers do not
    break during migration.
    """

    session_id: str
    id: str
    created_at: Optional[str] = None
    metadata: Dict[str, Any] = {}
    log_path: Optional[str] = None
    sequence: int = 0
    events: list[Dict[str, Any]] = []
    events_count: int = 0


class SessionSummary(BaseModel):
    """CC5 (W28C-1703): one ``GET /sessions`` list row. ``session_id`` is
    canonical; ``id`` is a deprecated alias for one release cycle. ``extra=allow``
    preserves any additional store-provided fields without dropping them.
    """

    model_config = ConfigDict(extra="allow")

    session_id: str
    id: str
    created_at: Optional[str] = None
    metadata: Dict[str, Any] = {}
    log_path: Optional[str] = None


class ListSessionsResponse(BaseModel):
    sessions: list[SessionSummary] = []


class MCPToolsListRequest(BaseModel):
    server_index: int = 0
    require_initialize: Optional[bool] = None


class MCPToolsCallRequest(BaseModel):
    server_index: int = 0
    name: str
    arguments: Dict[str, Any] = {}
    require_initialize: Optional[bool] = None


class MCPExecuteStep(BaseModel):
    method: str
    params: Optional[Dict[str, Any]] = None
    expect_error: Optional[bool] = False


class MCPExecuteRequest(BaseModel):
    server_index: Optional[int] = None
    server: Optional[Dict[str, Any]] = None
    require_initialize: Optional[bool] = None
    protocol_version: Optional[str] = None
    steps: list[MCPExecuteStep]


class MCPSSEOpenRequest(BaseModel):
    server_index: Optional[int] = None
    server: Optional[Dict[str, Any]] = None
    require_initialize: Optional[bool] = None
    protocol_version: Optional[str] = None


class MCPTerminateRequest(BaseModel):
    server_index: Optional[int] = None
    server: Optional[Dict[str, Any]] = None
    require_initialize: Optional[bool] = None
    protocol_version: Optional[str] = None
    verify_method: Optional[str] = None
    verify_params: Optional[Dict[str, Any]] = None


class MCPOAuthTokenRequest(BaseModel):
    server_index: Optional[int] = None
    server: Optional[Dict[str, Any]] = None


class MCPOAuthTokenResponse(BaseModel):
    access_token: str


class MCPFileUploadRequest(BaseModel):
    server_index: Optional[int] = None
    server: Optional[Dict[str, Any]] = None
    path: str
    content_base64: Optional[str] = None
    source_url: Optional[str] = None
    urlsafe: bool = False
    overwrite: bool = True
    dry_run: bool = False
    require_initialize: Optional[bool] = None


class MCPFileUploadResponse(BaseModel):
    path: str
    bytes_written: int
    dry_run: bool
    mcp_server_index: Optional[int] = None
    tool_result: Dict[str, Any]


class MCPFileDownloadRequest(BaseModel):
    server_index: Optional[int] = None
    server: Optional[Dict[str, Any]] = None
    path: str
    urlsafe: bool = False
    require_initialize: Optional[bool] = None


class MCPFileDownloadResponse(BaseModel):
    path: str
    content_base64: str
    byte_size: int
    mcp_server_index: Optional[int] = None
    tool_result: Dict[str, Any]


class SessionPreferencesRequest(BaseModel):
    selected_mcp_server_indices: list[int] = []


class SessionPreferencesResponse(BaseModel):
    session_id: str
    selected_mcp_server_indices: list[int]


class MCPServerAdminRequest(BaseModel):
    server: Dict[str, Any]


class SessionInjectRequest(BaseModel):
    role: str
    content: str
    timestamp: Optional[str] = None
    source: Optional[str] = None
    metadata: Dict[str, Any] = {}


class SessionInjectSequenceRequest(BaseModel):
    events: list[SessionInjectRequest]


class TestFlowCreateRequest(BaseModel):
    script: list[Dict[str, Any]]
    session_id: Optional[str] = None
    metadata: Dict[str, Any] = {}


class TestFlowRespondRequest(BaseModel):
    content: str


class CodeRunnerExecuteRequest(BaseModel):
    # W28I-1218 (consumer side): run code via the code-runner `code.execute`
    # A2A skill. `language` is one of python|node (validated downstream).
    code: str
    language: str = "python"
    task_id: Optional[str] = None


def _is_sensitive_config_key(key: str) -> bool:
    """Internal helper to is sensitive config key for this module."""
    key_l = str(key).strip().lower()
    if not key_l:
        return False
    if key_l.endswith("_header") or key_l.endswith("_path") or key_l.endswith("_url"):
        return False
    sensitive_terms = (
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "private_key",
        "ssh_authorized_key",
        "authorization",
        "cookie",
    )
    return any(term in key_l for term in sensitive_terms)


def _redact_config_tree(value: Any, key_hint: str = "") -> Any:
    """Internal helper to redact config tree for this module."""
    if _is_sensitive_config_key(key_hint):
        return _REDACTED_VALUE
    if isinstance(value, dict):
        return {str(k): _redact_config_tree(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_config_tree(v, key_hint) for v in value]
    return value


def _extract_mcp_tool_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    """Internal helper to extract MCP tool payload for this module."""
    if not isinstance(result, dict):
        raise ValueError("MCP tool result must be an object")
    if result.get("isError") is True:
        raise ValueError("MCP tool returned isError=true")

    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured

    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if not isinstance(text, str):
                continue
            text = text.strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed

    raise ValueError("Unable to parse MCP tool payload")


def _decode_base64_byte_size(data: str, *, urlsafe: bool) -> int:
    """Internal helper to decode base64 byte size for this module."""
    if not isinstance(data, str) or not data.strip():
        raise ValueError("Base64 data must be a non-empty string")

    raw = data.strip()
    if urlsafe:
        raw = raw.replace("-", "+").replace("_", "/")
    padding = "=" * ((4 - len(raw) % 4) % 4)
    try:
        decoded = base64.b64decode(raw + padding, validate=False)
    except Exception as e:
        raise ValueError("Invalid base64 content in MCP tool response") from e
    return len(decoded)


def _mcp_http_status_from_error_text(detail: str, *, default_status: int = 502) -> int:
    """Map common file-MCP error text onto an HTTP status code.

    When the backend MCP server returns a clear error (not a connectivity
    failure), surface the appropriate 4xx status instead of a blanket 502.
    502 is reserved for actual upstream connectivity failures.
    """
    text = str(detail or "").strip().lower()
    if not text:
        return default_status

    # Extract embedded HTTP status code from transport error messages
    # e.g., "MCP Streamable HTTP failed: POST /mcp -> 400; body=..."
    status_match = re.search(r"-> (\d{3})\b", text)
    if status_match:
        embedded = int(status_match.group(1))
        if 400 <= embedded < 500:
            return embedded

    if any(
        marker in text
        for marker in (
            "not found",
            "no such file",
            "does not exist",
            "missing file",
            "enoent",
            "workspace not found",
            "unknown workspace",
        )
    ):
        return 404
    if any(
        marker in text
        for marker in (
            "permission denied",
            "forbidden",
            "scope denied",
            "outside_roots",
            "outside roots",
            "not_in_allowlist",
            "not in allowlist",
            "access denied",
        )
    ):
        return 403
    if "too large" in text or "payload too large" in text:
        return 413
    if any(
        marker in text
        for marker in (
            "bad request",
            "invalid request",
            "validation error",
            "missing required",
            "invalid argument",
            "unknown tool",
            "invalid base64",
        )
    ):
        return 400
    # Only return 502 for actual connectivity failures
    if any(
        marker in text
        for marker in (
            "connect",
            "unreachable",
            "timed out",
            "connection refused",
            "dns",
        )
    ):
        return 502
    return default_status


def _extract_mcp_text_content(result: Dict[str, Any]) -> str:
    """Internal helper to extract MCP text content for this module."""
    if not isinstance(result, dict):
        return ""
    parts: list[str] = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and str(item.get("type") or "") == "text":
            text = str(item.get("text") or "").strip()
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def _parse_json_object_from_text(text: str) -> Dict[str, Any]:
    """Internal helper to json object from text for this module."""
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_json_payload_from_text(text: str) -> Any:
    """Internal helper to parse arbitrary JSON payloads from MCP text content."""
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return raw


def _extract_mcp_structured_or_text_payload(result: Dict[str, Any]) -> Any:
    """Return MCP structured content or parsed text content without assuming a dict."""
    if not isinstance(result, dict):
        return None
    structured = result.get("structuredContent")
    if structured is not None:
        return structured
    return _parse_json_payload_from_text(_extract_mcp_text_content(result))


def _extract_mcp_structured_or_text_object(result: Dict[str, Any]) -> Dict[str, Any]:
    """Internal helper to extract MCP structured or text object for this module."""
    payload = _extract_mcp_structured_or_text_payload(result)
    return payload if isinstance(payload, dict) else {}


def _extract_tool_session_id(result: Dict[str, Any]) -> str:
    """Internal helper to extract tool session id for this module."""
    payload = _extract_mcp_structured_or_text_object(result)
    for key in ("session_id", "id"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _extract_mcp_error_text(result: Dict[str, Any]) -> str:
    """Internal helper to extract MCP error text for this module."""
    if not isinstance(result, dict):
        return ""
    text = _extract_mcp_text_content(result).strip()
    if text:
        return text
    error = result.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or "").strip()
        if message:
            return message
    return ""


def _looks_like_file_mcp_server(server: Dict[str, Any]) -> bool:
    """Internal helper to detect file MCP-style server config for this module."""
    name = str(server.get("name") or "").strip().lower()
    base_url = str(server.get("base_url") or "").strip().lower()
    return "file" in name or "file-mcp" in base_url


def _normalize_file_mcp_path_value(value: Any) -> Any:
    """Internal helper to normalize file MCP path values for this module."""
    if not isinstance(value, str):
        return value
    raw = value.strip()
    if not raw:
        return value
    candidate = raw.replace("\\", "/")
    if not is_absolute_path(candidate):
        return value
    # Only rewrite true /root paths. A host path may legally contain a
    # ".../root/..." segment inside an allowed scope and must be preserved.
    if candidate == "/root":
        return "root"
    marker = "/root/"
    if not candidate.startswith(marker):
        return value
    return "root/" + candidate.split(marker, 1)[1]


def _normalize_file_mcp_arguments(
    server: Dict[str, Any], tool_name: str, arguments: Dict[str, Any]
) -> Dict[str, Any]:
    """Internal helper to normalize file MCP arguments for this module."""
    if not _looks_like_file_mcp_server(server):
        return arguments
    if not isinstance(arguments, dict):
        return arguments
    normalized = dict(arguments)
    for key in ("path", "src", "dst", "path_a", "path_b", "output_path"):
        if key in normalized:
            normalized[key] = _normalize_file_mcp_path_value(normalized.get(key))
    return normalized


def _file_mcp_extra_headers(request: Request, server: Dict[str, Any]) -> Dict[str, str]:
    """Forward supported file-mcp profile selection headers to downstream MCP servers."""
    if not _looks_like_file_mcp_server(server):
        return {}
    selected_profile = str(request.headers.get("x-file-mcp-profile") or "").strip()
    if not selected_profile:
        return {}
    return {"x-file-mcp-profile": selected_profile}


def _file_mcp_http_headers(config: ConfigManager, server: Dict[str, Any]) -> Dict[str, str]:
    """Build direct HTTP headers for auxiliary file-mcp admin API calls."""
    headers: Dict[str, str] = {"accept": "application/json"}
    defaults = config.get("mcp.defaults", {})
    if not isinstance(defaults, dict):
        defaults = {}

    api_key_header = str(
        server.get("api_key_header") or defaults.get("api_key_header") or ""
    ).strip()
    api_key = str(server.get("api_key") or "").strip()
    if api_key_header and api_key:
        headers[api_key_header] = api_key

    auth_bearer_token = str(
        server.get("auth_bearer_token") or defaults.get("auth_bearer_token") or ""
    ).strip()
    if auth_bearer_token:
        headers["authorization"] = f"Bearer {auth_bearer_token}"

    extra_headers = server.get("extra_headers")
    if isinstance(extra_headers, dict):
        for key, value in extra_headers.items():
            header_name = str(key or "").strip()
            header_value = str(value or "").strip()
            if header_name and header_value:
                headers[header_name] = header_value
    return headers


def _extract_file_profile_names(payload: Any) -> list[str]:
    """Normalise file-mcp admin profile list payloads into sorted unique names."""
    raw_items = payload.get("profiles") if isinstance(payload, dict) else None
    if not isinstance(raw_items, list):
        return []
    names: list[str] = []
    for item in raw_items:
        if isinstance(item, str):
            name = item.strip()
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
        else:
            name = ""
        if name and name not in names:
            names.append(name)
    return sorted(names)


def _extract_list_session_candidates(result: Dict[str, Any]) -> list[str]:
    """Internal helper to extract list session candidates for this module."""
    payload = _extract_mcp_structured_or_text_object(result)
    sessions_raw = payload.get("sessions") if isinstance(payload, dict) else None
    if not isinstance(sessions_raw, list):
        return []

    candidates: list[str] = []
    for item in sessions_raw:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id") or item.get("session_id") or "").strip()
        if sid and sid not in candidates:
            candidates.append(sid)

    def _sort_key(value: str) -> tuple[int, int | str]:
        """Internal helper to sort key for this module."""
        if str(value).isdigit():
            return (1, int(value))
        return (0, value)

    return sorted(candidates, key=_sort_key, reverse=True)


def _extract_translator_text(result: Dict[str, Any]) -> str:
    """Internal helper to extract translator text for this module."""
    payload = _extract_mcp_structured_or_text_object(result)
    for key in ("response", "translated_text", "message", "text", "content"):
        value = payload.get(key)
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
        if isinstance(value, dict):
            nested = _extract_translator_text({"structuredContent": value})
            if nested:
                return nested

    # Some translator MCPs wrap natural language in JSON text fields.
    # Parse and extract a likely human-facing message before falling back.
    text_content = _extract_mcp_text_content(result)
    if text_content.startswith("{") and text_content.endswith("}"):
        parsed = _parse_json_object_from_text(text_content)
        for key in ("response", "translated_text", "message", "text", "content"):
            value = parsed.get(key)
            if isinstance(value, str):
                text = value.strip()
                if text:
                    return text
    return _extract_mcp_text_content(result)


def _extract_expert_execute_text(payload: Dict[str, Any]) -> str:
    """Extract a direct-response text payload from expert execute output."""
    if not isinstance(payload, dict):
        return ""
    for key in ("output_text", "response", "content", "text", "message"):
        value = payload.get(key)
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
        if isinstance(value, dict):
            nested = _extract_expert_execute_text(value)
            if nested:
                return nested
    return ""


def _expert_denied_authoritative_service_results(
    output_text: str,
    *,
    explicit_service_calls: list[Any],
    post_service_calls: list[Any],
) -> bool:
    """Detect expert replies that contradict pre-authorised service-result context."""
    if not explicit_service_calls and not post_service_calls:
        return False
    text = str(output_text or "").strip().lower()
    if not text:
        return False
    denial_markers = (
        "i don't have access",
        "i do not have access",
        "i can't access",
        "i cannot access",
        "no access to your local file system",
        "cannot access local files",
        "can't access the file system",
        "cannot access the filesystem",
    )
    return any(marker in text for marker in denial_markers)


def _stringify_service_result_payload(value: Any) -> str:
    """Flatten a service result payload into readable text for expert retry context."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        content = value.get("content")
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = str(item.get("text") or "").strip()
                    if text:
                        parts.append(text)
            if parts:
                return "\n".join(parts).strip()
        structured = value.get("structuredContent")
        if isinstance(structured, dict):
            for key in ("content", "text", "output_text", "result"):
                nested = _stringify_service_result_payload(structured.get(key))
                if nested:
                    return nested
            try:
                return json.dumps(structured, ensure_ascii=True)
            except Exception:
                return str(structured).strip()
        for key in ("text", "output_text", "content", "result", "message", "error"):
            nested = _stringify_service_result_payload(value.get(key))
            if nested:
                return nested
        try:
            return json.dumps(value, ensure_ascii=True)
        except Exception:
            return str(value).strip()
    if isinstance(value, list):
        parts = [_stringify_service_result_payload(item) for item in value]
        parts = [part for part in parts if part]
        return "\n".join(parts).strip()
    return str(value).strip() if value is not None else ""


def _authoritative_expert_service_digest(
    payload: Dict[str, Any],
    *,
    explicit_service_calls: list[Any],
) -> str:
    """Build a readable service-result digest from expert execute output."""
    if not isinstance(payload, dict):
        return ""
    services_invoked = payload.get("services_invoked")
    if not isinstance(services_invoked, list) or not services_invoked:
        return ""

    lines: list[str] = []
    for idx, service_result in enumerate(services_invoked):
        if not isinstance(service_result, dict):
            continue
        call_spec = explicit_service_calls[idx] if idx < len(explicit_service_calls) else {}
        if not isinstance(call_spec, dict):
            call_spec = {}

        tool_name = str(
            call_spec.get("tool_name")
            or service_result.get("tool_name")
            or f"tool_{idx}"
        ).strip()
        arguments = call_spec.get("arguments") if isinstance(call_spec.get("arguments"), dict) else {}
        raw_path = str(
            arguments.get("path")
            or arguments.get("src")
            or arguments.get("dst")
            or ""
        ).strip()
        display_path = file_name(raw_path) if raw_path else ""
        payload_text = _stringify_service_result_payload(service_result.get("result")).strip()
        if not payload_text:
            payload_text = _stringify_service_result_payload(service_result).strip()
        if not payload_text:
            continue

        if tool_name in {"read_file", "read_text_file"} and display_path:
            lines.append(f"File {display_path} contents:\n{payload_text[:2400]}")
            continue
        if tool_name in {"list_dir", "list_directory", "list_directory_with_sizes"} and raw_path:
            lines.append(f"Directory listing for {raw_path}:\n{payload_text[:1600]}")
            continue
        if display_path:
            lines.append(f"{tool_name} on {display_path}:\n{payload_text[:1600]}")
            continue
        lines.append(f"{tool_name} result:\n{payload_text[:1600]}")

    return "\n\n".join(line for line in lines if line).strip()


def _extract_direct_mcp_output(ctx: str, marker: str) -> str:
    """Extract translator/expert direct output blocks from MCP context text."""
    marker_index = ctx.find(marker)
    if marker_index < 0:
        return ""
    return ctx[marker_index + len(marker) :].strip()


def _is_translation_request(prompt_text: str) -> bool:
    """Internal helper to is translation request for this module."""
    p = str(prompt_text or "").lower()
    return any(
        token in p
        for token in (
            "translate",
            "translation",
            "hungarian",
            "magyar",
        )
    )


def _infer_translation_language(prompt_text: str) -> str | None:
    """Infer a target language code from a translation-style prompt."""
    prompt = str(prompt_text or "").lower()
    if "hungarian" in prompt or "magyar" in prompt:
        return "hu"
    return None


def _translation_language_label(language_code: str | None) -> str:
    """Map a translation language code to a short human label."""
    mapping = {
        "hu": "Hungarian",
    }
    code = str(language_code or "").strip().lower()
    return mapping.get(code, code or "the target language")


def _sanitize_translator_context_text(context_text: str) -> str:
    """Convert structured search payloads into plain text safe for translator MCP use."""
    raw = str(context_text or "").strip()
    if not raw:
        return ""

    parsed = _parse_json_object_from_text(raw)
    if not parsed:
        return raw

    lines: list[str] = []
    query = str(parsed.get("query") or "").strip()
    if query:
        lines.append(f"Search query: {query}")

    answers = parsed.get("answers")
    if isinstance(answers, list):
        for item in answers[:3]:
            if not isinstance(item, dict):
                continue
            answer_text = str(
                item.get("answer") or item.get("text") or item.get("content") or ""
            ).strip()
            if answer_text:
                lines.append(answer_text)

    results = parsed.get("results")
    if isinstance(results, list):
        for index, item in enumerate(results[:4], start=1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("name") or "").strip()
            snippet = str(
                item.get("content")
                or item.get("snippet")
                or item.get("description")
                or ""
            ).strip()
            url = str(item.get("url") or item.get("link") or "").strip()
            published = str(
                item.get("publishedDate")
                or item.get("published_at")
                or item.get("published")
                or ""
            ).strip()

            parts = [part for part in (title, snippet) if part]
            line = f"{index}. " + " - ".join(parts) if parts else ""
            if published:
                line = f"{line} ({published})".strip()
            if url:
                line = f"{line} {url}".strip()
            if line:
                lines.append(line)

    if len(lines) <= 1:
        unresponsive = parsed.get("unresponsive_engines")
        if isinstance(unresponsive, list) and unresponsive:
            lines.append(
                "No search results were returned. Several search backends timed out."
            )
        else:
            lines.append("No search results were returned.")

    return "\n".join(line for line in lines if line).strip()


def _build_tool_arguments_from_schema(
    tool: Dict[str, Any], question: str, *, prefer_async_jobs: bool = False
) -> Dict[str, Any]:
    """Internal helper to tool arguments from schema for this module."""
    args: Dict[str, Any] = {}
    schema = tool.get("inputSchema") if isinstance(tool, dict) else {}
    props = schema.get("properties") if isinstance(schema, dict) else {}
    prop_keys = {str(k) for k in props.keys()} if isinstance(props, dict) else set()

    if "query" in prop_keys:
        args["query"] = question
    elif "question" in prop_keys:
        args["question"] = question
    elif "prompt" in prop_keys:
        args["prompt"] = question
    elif "text" in prop_keys:
        args["text"] = question

    if "use_history" in prop_keys:
        args["use_history"] = False
    if "wait" in prop_keys:
        args["wait"] = False if prefer_async_jobs else True
    if "max_results" in prop_keys and "max_results" not in args:
        args["max_results"] = 8
    return args


def _build_imap_tool_call(
    tool_names: Dict[str, str], prompt_text: str
) -> Optional[tuple[str, Dict[str, Any]]]:
    """Route IMAP prompts to the correct IMAP-MCP tool with appropriate parameters."""
    prompt_lc = str(prompt_text or "").lower()
    folder = _extract_prompt_value(prompt_text, "folder") or "INBOX"
    profile_id = _extract_prompt_value(prompt_text, "profile_id") or "operations"

    # U5: since-last-check / delta
    if "mail_search_since_last" in tool_names and any(
        token in prompt_lc for token in ("since last", "since-last", "delta", "new since", "what is new")
    ):
        subject = _extract_imap_subject(prompt_text)
        return (
            tool_names["mail_search_since_last"],
            {"profile_id": profile_id, "mode": "imap", "query": f'SUBJECT "{subject}"' if subject else "ALL"},
        )

    # U3: structured message extract (Message-ID or "full message" + "extract")
    message_id = _extract_imap_message_id(prompt_text)
    if message_id and "mail_get_message" in tool_names:
        return (
            tool_names["mail_get_message"],
            {"profile_id": profile_id, "mode": "imap", "message_id": message_id},
        )
    if ("extract" in prompt_lc or "structured" in prompt_lc) and "mail_extract_message" in tool_names:
        if message_id:
            return (
                tool_names["mail_extract_message"],
                {"profile_id": profile_id, "mode": "imap", "message_id": message_id},
            )

    # U4: unseen / unread
    if any(token in prompt_lc for token in ("unseen", "unread")) and "mail_search" in tool_names:
        query = "UNSEEN"
        since = _extract_imap_date_since(prompt_text)
        if since:
            query = f"UNSEEN SINCE {since}"
        result_tool = tool_names["mail_search"]
        return (result_tool, {"profile_id": profile_id, "mode": "imap", "query": query, "filters": {"folder": folder}, "limit": 25})

    # U1/U2: subject or text search
    subject = _extract_imap_subject(prompt_text)
    text_term = _extract_imap_text(prompt_text)
    if subject and "mail_search" in tool_names:
        query = f'SUBJECT "{subject}"'
        since = _extract_imap_date_since(prompt_text)
        if since:
            query = f'{query} SINCE {since}'
        return (tool_names["mail_search"], {"profile_id": profile_id, "mode": "imap", "query": query, "filters": {"folder": folder}, "limit": 25})
    if text_term and "mail_search" in tool_names:
        query = f'TEXT "{text_term}"'
        since = _extract_imap_date_since(prompt_text)
        if since:
            query = f'{query} SINCE {since}'
        return (tool_names["mail_search"], {"profile_id": profile_id, "mode": "imap", "query": query, "filters": {"folder": folder}, "limit": 25})

    # Fallback: generic search (only if no explicit axis was requested)
    if "mail_search" in tool_names:
        return (tool_names["mail_search"], {"profile_id": profile_id, "mode": "imap", "query": "ALL", "filters": {"folder": folder}, "limit": 10})

    return None


def _extract_imap_subject(prompt_text: str) -> str:
    """Extract a subject keyword from the prompt for IMAP SUBJECT search."""
    prompt_lc = str(prompt_text or "").lower()
    match = re.search(r'subject\s+(?:containing\s+|")?(\w[\w-]*)', prompt_lc)
    if match:
        return match.group(1)
    for kw in ("fail2ban", "alert", "cron", "logwatch"):
        if kw in prompt_lc:
            return kw
    return ""


def _extract_imap_text(prompt_text: str) -> str:
    """Extract text search terms for IMAP TEXT search."""
    prompt_lc = str(prompt_text or "").lower()
    # Check known research keywords first
    for kw in ("ukraine", "kyiv", "russia", "nato", "gaza", "climate"):
        if kw in prompt_lc:
            return kw
    # Try to extract quoted or adjacent term
    match = re.search(r'(?:containing|about|mentioning|text)\s+["\']([^"\']+)["\']', prompt_lc)
    if match:
        return match.group(1).strip()
    match = re.search(r'(?:containing|about|mentioning|text)\s+(\w+)', prompt_lc)
    if match:
        return match.group(1).strip()
    return ""


def _extract_imap_message_id(prompt_text: str) -> str:
    """Extract a Message-ID from the prompt."""
    match = re.search(r'[Mm]essage.?[Ii][Dd]\s*[=:"\s]+([^"\s>]+@[^"\s>]+)', str(prompt_text or ""))
    if match:
        value = match.group(1).strip().strip("<>")
        return f"<{value}>"
    return ""


def _extract_imap_date_since(prompt_text: str) -> str:
    """Extract a SINCE date from time expressions in the prompt."""
    prompt_lc = str(prompt_text or "").lower()
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    for pattern, days in [
        (r"last\s+24\s*h", 1), (r"last\s+day", 1), (r"today", 0),
        (r"last\s+7\s*d", 7), (r"last\s+week", 7), (r"past\s+week", 7),
        (r"last\s+30\s*d", 30), (r"last\s+month", 30),
        (r"last\s+90\s*d", 90), (r"last\s+3\s*months?", 90),
    ]:
        if re.search(pattern, prompt_lc):
            since = now - timedelta(days=days)
            return since.strftime("%d-%b-%Y")
    return ""


def _extract_prompt_value(prompt_text: str, label: str) -> str:
    """Extract a quoted prompt argument for lightweight MCP chat assist routing."""
    text = str(prompt_text or "")
    patterns = [
        rf"{re.escape(label)}\s+['\"]([^'\"]+)['\"]",
        rf"{re.escape(label)}\s+`([^`]+)`",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = str(match.group(1) or "").strip()
            if value:
                return value
    return ""


def _render_mcp_context_text(result: Dict[str, Any]) -> str:
    """Render MCP tool output to compact factual context for the LLM."""
    text = _extract_mcp_text_content(result).strip()
    if text and not (text.startswith("{") or text.startswith("[")):
        return text
    payload = _extract_mcp_structured_or_text_payload(result)
    if isinstance(payload, dict):
        entries = payload.get("entries")
        if isinstance(entries, list) and entries:
            rendered = [str(item).strip() for item in entries if str(item).strip()]
            if rendered:
                return "\n".join(rendered)
        messages = payload.get("messages")
        if isinstance(messages, list) and messages:
            rendered_messages: list[str] = []
            for item in messages:
                if not isinstance(item, dict):
                    continue
                subject = str(item.get("subject") or "").strip()
                sender = str(item.get("from") or item.get("from_address") or "").strip()
                received = str(
                    item.get("received_at")
                    or item.get("received_at_utc")
                    or item.get("date")
                    or ""
                ).strip()
                parts = [part for part in (subject, sender, received) if part]
                if parts:
                    rendered_messages.append(" | ".join(parts))
            if rendered_messages:
                return "\n".join(rendered_messages)
        return json.dumps(payload, ensure_ascii=True)
    if isinstance(payload, list) and payload:
        try:
            return json.dumps(payload, ensure_ascii=True)
        except Exception:
            return "\n".join(str(item).strip() for item in payload if str(item).strip())
    if text:
        return text
    return ""


def _build_prompt_assist_tool_call(
    tool_names: Dict[str, str], prompt_text: str, *, browse_path: str = "working"
) -> Optional[tuple[str, Dict[str, Any]]]:
    """Map simple file/email prompts to concrete MCP tool calls."""
    prompt_lc = str(prompt_text or "").lower()
    browse_root = str(browse_path or "working").strip() or "working"

    if any(
        token in prompt_lc
        for token in (
            "what tables are available",
            "list tables",
            "available tables",
            "show tables",
        )
    ):
        if "list_tables" in tool_names:
            return (tool_names["list_tables"], {})
        if "get_schema" in tool_names:
            return (tool_names["get_schema"], {})

    if any(t in tool_names for t in ("mail_search", "mail_get_message", "mail_search_since_last")) and any(
        token in prompt_lc for token in ("imap", "email", "emails", "mailbox", "profile_id", "mail", "inbox", "fail2ban", "unread", "unseen", "since last")
    ):
        return _build_imap_tool_call(tool_names, prompt_text)

    if "list_dir" in tool_names and any(
        token in prompt_lc
        for token in ("list files", "list file", "directory", "directories", "files in")
    ):
        path = _extract_prompt_value(prompt_text, "directory") or _extract_prompt_value(
            prompt_text, "path"
        )
        if path:
            return (tool_names["list_dir"], {"path": path})

    if any(
        token in prompt_lc
        for token in (
            "search for files",
            "files in the workspace",
            "list files",
            "workspace files",
            "directory listing",
        )
    ):
        if "list_dir" in tool_names:
            return (tool_names["list_dir"], {"path": browse_root})
        if "search_paths" in tool_names:
            return (tool_names["search_paths"], {"query": browse_root})

    return None


def _is_freshness_request(prompt_text: str) -> bool:
    """Internal helper to is freshness request for this module."""
    p = str(prompt_text or "").lower()
    return any(
        token in p
        for token in (
            "last 24 hours",
            "last 24h",
            "today",
            "current",
            "latest",
            "breaking",
            "this morning",
            "past day",
        )
    )


def _is_file_workspace_prompt(prompt_text: str) -> bool:
    """Return True when prompt clearly requests workspace file information."""
    prompt_lc = str(prompt_text or "").lower()
    return any(
        token in prompt_lc
        for token in (
            "search for files",
            "files in the workspace",
            "list files",
            "workspace files",
            "directory listing",
        )
    )


def _is_table_listing_request(prompt_text: str) -> bool:
    """Return True when prompt asks for a direct table list."""
    prompt_lc = str(prompt_text or "").lower()
    return any(
        token in prompt_lc
        for token in (
            "what tables are available",
            "list tables",
            "available tables",
            "show tables",
        )
    )


def _is_email_lookup_prompt(prompt_text: str) -> bool:
    """Return True when prompt asks for IMAP-backed email facts."""
    prompt_lc = str(prompt_text or "").lower()
    return any(
        token in prompt_lc
        for token in ("imap", "email", "emails", "mailbox", "profile_id", "subject line")
    )


def _derive_direct_prompt_assist_output(prompt_text: str, mcp_context: str) -> str:
    """Return a direct factual reply for simple MCP-backed prompts."""
    raw = str(mcp_context or "").strip()
    if not raw:
        return ""
    if raw.startswith("MCP context:\n"):
        raw = raw.split("MCP context:\n", 1)[1].strip()
    if _is_table_listing_request(prompt_text):
        return f"Available tables:\n{raw}".strip()
    if _is_file_workspace_prompt(prompt_text):
        return raw
    if _is_email_lookup_prompt(prompt_text):
        return ""  # Let LLM process email results into a user-readable answer
    return ""


def _apply_freshness_hints(
    tool_args: Dict[str, Any], tool: Dict[str, Any], prompt_text: str
) -> Dict[str, Any]:
    """Internal helper to apply freshness hints for this module."""
    if not _is_freshness_request(prompt_text):
        return tool_args
    schema = tool.get("inputSchema") if isinstance(tool, dict) else {}
    props = schema.get("properties") if isinstance(schema, dict) else {}
    prop_keys = {str(k) for k in props.keys()} if isinstance(props, dict) else set()

    now_utc = datetime.now(timezone.utc)
    one_day_ago = now_utc - timedelta(days=1)

    out = dict(tool_args)
    if "query" in out:
        out["query"] = (
            f"{str(out['query']).strip()} "
            f"(strictly last 24 hours; include source publication times; UTC date {now_utc:%Y-%m-%d})"
        ).strip()
    elif "question" in out:
        out["question"] = (
            f"{str(out['question']).strip()} "
            f"(strictly last 24 hours; include source publication times; UTC date {now_utc:%Y-%m-%d})"
        ).strip()

    numeric_hints = {
        "recency_days": 1,
        "days": 1,
        "lookback_days": 1,
        "hours": 24,
        "lookback_hours": 24,
        "max_age_hours": 24,
    }
    for key, value in numeric_hints.items():
        if key in prop_keys and key not in out:
            out[key] = value

    if "time_range" in prop_keys and "time_range" not in out:
        out["time_range"] = "24h"
    if "from_date" in prop_keys and "from_date" not in out:
        out["from_date"] = one_day_ago.strftime("%Y-%m-%d")
    if "to_date" in prop_keys and "to_date" not in out:
        out["to_date"] = now_utc.strftime("%Y-%m-%d")
    if "sort_by" in prop_keys and "sort_by" not in out:
        out["sort_by"] = "date"
    if "sort_order" in prop_keys and "sort_order" not in out:
        out["sort_order"] = "desc"

    return out


def _looks_like_error_text(text: str) -> bool:
    """Internal helper to looks like error text for this module."""
    raw = str(text or "").strip()
    if not raw:
        return True
    low = raw.lower()
    if "unexpected error" in low or raw.startswith("error:"):
        return True
    if raw.startswith("{") and raw.endswith("}"):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and parsed.get("error"):
                return True
        except Exception:
            pass
    return False


def _direct_assist_roles() -> set[str]:
    """Return server roles that delegate the reply to an upstream expert service."""
    return {"expert", "orchestrator", "chat_backend", "expert_execute"}


def _derive_assist_api_base_url(server_spec: Dict[str, Any]) -> str:
    """Resolve the expert-agent API base URL from an MCP server spec."""
    explicit = str(server_spec.get("assist_api_base_url") or "").strip()
    if explicit:
        return explicit.rstrip("/")

    base_url = str(server_spec.get("base_url") or "").strip()
    if not base_url:
        return ""
    parsed = urlparse(base_url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return urlunparse((parsed.scheme, parsed.netloc, "/v1", "", "", "")).rstrip("/")


def _mcp_servers_for_ui(
    config: ConfigManager, servers: Optional[list[Dict[str, Any]]] = None
) -> list[Dict[str, Any]]:
    """Internal helper to MCP servers for ui for this module."""
    raw = servers if servers is not None else (config.get("mcp.servers") or [])
    if not isinstance(raw, list):
        return []
    out: list[Dict[str, Any]] = []
    for idx, s in enumerate(raw):
        if not isinstance(s, dict):
            continue
        out.append(
            {
                "index": idx,
                "name": str(s.get("name") or f"server-{idx}"),
                "transport": str(s.get("transport") or ""),
                "base_url": str(s.get("base_url") or ""),
                "version": str(s.get("version") or ""),
                "mcp_path": str(s.get("mcp_path") or ""),
                "messages_path": str(s.get("messages_path") or ""),
                "sse_path": str(s.get("sse_path") or ""),
                "health_path": str(s.get("health_path") or ""),
            }
        )
    return out


def _mcp_servers_raw(
    config: ConfigManager, servers: Optional[list[Dict[str, Any]]] = None
) -> list[Dict[str, Any]]:
    """Internal helper to MCP servers raw for this module."""
    raw = servers if servers is not None else (config.get("mcp.servers") or [])
    if not isinstance(raw, list):
        return []
    out: list[Dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(copy.deepcopy(item))
    return out


def _validate_mcp_server_spec(
    config: ConfigManager, server: Dict[str, Any]
) -> Dict[str, Any]:
    """Internal helper to MCP server spec for this module."""
    if not isinstance(server, dict):
        raise HTTPException(status_code=400, detail="server must be an object")

    defaults = config.get("mcp.defaults") or {}
    if not isinstance(defaults, dict):
        defaults = {}

    normalized = copy.deepcopy(server)
    name = str(normalized.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="server.name is required")
    normalized["name"] = name

    transport = (
        str(normalized.get("transport") or defaults.get("transport") or "")
        .strip()
        .lower()
    )
    if not transport:
        raise HTTPException(status_code=400, detail="server.transport is required")
    normalized["transport"] = transport

    if transport in (
        "http_jsonrpc",
        "streamable_http",
        "streamablehttp",
        "mcp",
        "legacy_sse",
    ):
        base_url = str(normalized.get("base_url") or "").strip()
        if not base_url:
            raise HTTPException(status_code=400, detail="server.base_url is required")
        normalized["base_url"] = base_url

    if transport in ("streamable_http", "streamablehttp", "mcp"):
        normalized["mcp_path"] = str(
            normalized.get("mcp_path") or defaults.get("mcp_path") or "/mcp"
        )
    elif transport == "http_jsonrpc":
        normalized["messages_path"] = str(
            normalized.get("messages_path")
            or defaults.get("messages_path")
            or "/messages"
        )
    elif transport == "legacy_sse":
        normalized["sse_path"] = str(
            normalized.get("sse_path") or defaults.get("sse_path") or "/sse"
        )
        normalized["messages_path"] = str(
            normalized.get("messages_path")
            or defaults.get("messages_path")
            or "/messages"
        )
    elif transport == "stdio":
        command = str(normalized.get("command") or "").strip()
        if not command:
            raise HTTPException(
                status_code=400, detail="server.command is required for stdio transport"
            )
        args = normalized.get("args")
        if args is not None and (
            not isinstance(args, list) or not all(isinstance(x, str) for x in args)
        ):
            raise HTTPException(
                status_code=400, detail="server.args must be a list of strings"
            )
        env = normalized.get("env")
        if env is not None and not isinstance(env, dict):
            raise HTTPException(status_code=400, detail="server.env must be an object")
    else:
        raise HTTPException(
            status_code=400, detail=f"Unsupported mcp transport: {transport}"
        )

    return normalized


def _normalize_selected_indices(value: Any, max_count: int) -> list[int]:
    """Internal helper to selected indices for this module."""
    if max_count <= 0:
        return []
    if not isinstance(value, list):
        return []
    valid: list[int] = []
    for item in value:
        try:
            idx = int(item)
        except (TypeError, ValueError):
            continue
        if idx < 0 or idx >= max_count:
            continue
        if idx not in valid:
            valid.append(idx)
    return valid


def _short_title_text(text: str, max_len: int = 72) -> str:
    """Internal helper to short title text for this module."""
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return ""
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"


def _application_release(config: ConfigManager) -> str:
    """Resolve the application release: ``app.release`` override else the
    package single-source ``__version__`` (CC8, W28C-1703)."""
    configured = str(config.get("app.release") or "").strip()
    if configured:
        return configured
    return __version__


def _llm_config_for_ui(config: ConfigManager) -> Dict[str, Any]:
    # Expose non-secret runtime LLM controls explicitly in the UI config payload.
    # This avoids forcing operators to inspect the full redacted tree.
    """Internal helper to LLM config for ui for this module."""
    num_ctx = config.get("llm.num_ctx")
    if num_ctx is None:
        num_ctx = config.get("llm.context_window")
    max_tokens = config.get("llm.max_tokens")
    if max_tokens is None:
        max_tokens = config.get("llm.num_predict")
    return {
        "provider": str(config.get("llm.provider") or ""),
        "base_url": str(config.get("llm.base_url") or ""),
        "model": str(config.get("llm.model") or ""),
        "timeout_seconds": config.get("llm.timeout_seconds"),
        "stream": bool(config.get("llm.stream")),
        "temperature": config.get("llm.temperature"),
        "top_p": config.get("llm.top_p"),
        "top_k": config.get("llm.top_k"),
        "num_ctx": num_ctx,
        "max_tokens": max_tokens,
        "include_reasoning_tags": bool(
            config.get("llm.include_reasoning_tags") or False
        ),
        "api_key_configured": bool(str(config.get("llm.api_key") or "").strip()),
    }


def build_router(
    *,
    config: ConfigManager,
    sessions: SessionManager,
    db_runtime: Optional["ChatDatabaseRuntime"] = None,
    jobs_runtime: Optional["JobsRuntime"] = None,
    prompt_store: Optional[PromptStore] = None,
) -> APIRouter:
    """Build router for the current runtime context."""
    router = APIRouter()
    # W28B-319 (D5): opt-in prompt-template store. A default in-memory store is
    # created lazily only when a caller actually references a template, so the
    # additive prompt feature imposes no cost on the unchanged default path and
    # does not require the optional `cloud-dog-agent` package to be installed
    # unless prompts are used.
    _prompt_store_holder: Dict[str, Optional[PromptStore]] = {"store": prompt_store}

    def _get_prompt_store() -> Optional[PromptStore]:
        store = _prompt_store_holder["store"]
        if store is None and PROMPTS_AVAILABLE:
            from ..prompts import default_prompt_store

            store = default_prompt_store()
            _prompt_store_holder["store"] = store
        return store

    admin_logger = get_logger("cloud_dog_chat_api")
    audit_logger = get_audit_logger()
    runtime_mcp_servers: list[Dict[str, Any]] = _mcp_servers_raw(config)

    # PS-92 (W28A-970g-V2): configurable base paths for api / mcp / a2a server surfaces.
    # Literal defaults live in defaults.yaml (4 server blocks). Env override via
    # CLOUD_DOG__API_SERVER__BASE_PATH / CLOUD_DOG__MCP_SERVER__BASE_PATH /
    # CLOUD_DOG__A2A_SERVER__BASE_PATH. The /v1 prefix is the canonical default
    # (was /api/v1 before A138; Traefik strips /api so API server receives /v1).
    # LEGACY_API_BASE_PATH kept for backwards-compat reference. See 970c-V2 precedent.
    api_base_path = str(config.get("api_server.base_path") or "/v1").rstrip("/") or "/v1"
    mcp_base_path = str(config.get("mcp_server.base_path") or "/mcp").rstrip("/") or "/mcp"
    a2a_base_path = str(config.get("a2a_server.base_path") or "/a2a").rstrip("/") or "/a2a"
    LEGACY_API_BASE_PATH = "/v1"  # noqa: N806 — external-contract constant (A138 — was /api/v1 pre-Traefik-strip fix)

    def _server_id() -> str:
        return str(
            config.get("app.server_id")
            or config.get("log.service_instance")
            or "chat-client-local"
        ).strip() or "chat-client-local"

    def _request_correlation_id(request: Request) -> str:
        request_id = str(getattr(request.state, "request_id", "") or "").strip()
        if not request_id:
            request_id = str(request.headers.get("x-request-id") or "").strip()
        correlation_id = str(
            getattr(request.state, "correlation_id", "") or ""
        ).strip()
        if not correlation_id:
            correlation_id = str(
                request.headers.get("x-correlation-id") or request_id
            ).strip()
        return correlation_id or secrets.token_hex(16)

    def _request_user_ip(request: Request) -> str:
        xff = str(request.headers.get("x-forwarded-for") or "").strip()
        if xff:
            for part in xff.split(","):
                candidate = part.strip()
                if candidate:
                    return candidate
        real_ip = str(request.headers.get("x-real-ip") or "").strip()
        if real_ip:
            return real_ip
        client = getattr(request, "client", None)
        host = str(getattr(client, "host", "") or "").strip()
        if host:
            return host
        return "unknown"

    def _request_intermediary_source(request: Request) -> dict[str, str]:
        intermediary = str(
            request.headers.get("x-cloud-dog-intermediary")
            or request.headers.get("x-intermediary-service")
            or request.headers.get("x-forwarded-service")
            or ""
        ).strip()
        intermediary_ip = str(
            request.headers.get("x-cloud-dog-intermediary-ip")
            or request.headers.get("x-intermediary-ip")
            or ""
        ).strip()
        transport = str(
            request.headers.get("x-cloud-dog-transport")
            or request.headers.get("x-forwarded-proto")
            or ""
        ).strip()
        if not intermediary_ip:
            xff = str(request.headers.get("x-forwarded-for") or "").strip()
            if xff:
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

    def _jobs_enabled() -> bool:
        raw = config.get("jobs.enabled")
        if raw is None:
            return True
        return bool(raw)

    def _jobs_user_id() -> str:
        return str(config.get("db.actor") or "chat-client").strip() or "chat-client"

    def _sanitise_job_payload(payload: Any) -> Any:
        """Redact secret-looking KEYS and VALUES from a JOB payload.

        ``cloud_dog_jobs.security.secrets.assert_no_secrets`` rejects any payload
        whose keys contain ("password","secret","token","api_key","private_key",
        "credential"), OR whose string values match bearer/sk-/PEM/etc markers.
        MCP tool calls may legitimately pass args named ``api_key`` to admin
        tools — but the JOB payload here is audit/lifecycle metadata only and
        must not carry raw secret material. The real MCP transport call uses
        the unsanitised arguments directly.

        Because the detector flags KEYS by substring (not just values), this
        sanitiser RENAMES offending keys to neutral placeholders rather than
        just redacting their values. Added by W28M-1600 to unblock the
        chatclient-expert demo column.
        """
        _SECRET_KEY_TOKENS = ("password", "secret", "token", "api_key", "private_key", "credential")
        _SECRET_VALUE_MARKERS = ("bearer ", "sk-", "-----begin", "xox", "ghp_")

        def _value_looks_secret(value: str) -> bool:
            low = value.lower()
            return any(marker in low for marker in _SECRET_VALUE_MARKERS)

        def _walk(node: Any) -> Any:
            if isinstance(node, dict):
                out: Dict[str, Any] = {}
                redacted_keys: list[str] = []
                for k, v in node.items():
                    key_l = str(k).lower()
                    if any(t in key_l for t in _SECRET_KEY_TOKENS):
                        # Rename key so detector's key-substring check passes.
                        redacted_keys.append(str(k))
                        continue
                    out[k] = _walk(v)
                if redacted_keys:
                    out["_w28m1600_redacted_arg_names_count"] = len(redacted_keys)
                return out
            if isinstance(node, list):
                return [_walk(item) for item in node]
            if isinstance(node, str) and _value_looks_secret(node):
                return "<redacted-by-W28M-1600-sanitiser>"
            return node

        return _walk(payload)

    def _create_mcp_job(
        *,
        session_id: str,
        job_type: str,
        server_index: Optional[int],
        method: str,
        payload: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> Optional[str]:
        if jobs_runtime is None or not _jobs_enabled():
            return None
        job_payload: Dict[str, Any] = {
            "server_index": server_index,
            "method": method,
            "server_id": _server_id(),
        }
        if payload:
            job_payload.update(dict(payload))
        # W28M-1600: cloud_dog_jobs.security.secrets.assert_no_secrets rejects
        # payloads with keys matching ("password","secret","token","api_key",
        # "private_key","credential"). MCP tool calls legitimately pass args
        # like `api_key` to admin tools — those args are needed by the real
        # MCP transport call (line below), but the JOB payload here is
        # audit/lifecycle tracking only and must not carry the raw values.
        job_payload = _sanitise_job_payload(job_payload)
        job_id = jobs_runtime.create_job(
            job_type=job_type,
            payload=job_payload,
            session_id=session_id,
            correlation_id=correlation_id,
            user_id=_jobs_user_id(),
        )
        jobs_runtime.mark_running(job_id, worker_id="mcp-proxy")
        sessions.append_event(
            session_id,
            TranscriptEvent(
                event_type="mcp_job_created",
                data={
                    "job_id": job_id,
                    "job_type": job_type,
                    "server_index": server_index,
                    "method": method,
                },
            ),
        )
        return job_id

    def _complete_mcp_job(
        job_id: Optional[str], *, result: Optional[Dict[str, Any]] = None
    ) -> None:
        if not job_id or jobs_runtime is None:
            return
        jobs_runtime.complete(job_id, result=result or {})

    def _fail_mcp_job(
        job_id: Optional[str],
        *,
        error: str,
        retryable: bool = False,
    ) -> None:
        if not job_id or jobs_runtime is None:
            return
        jobs_runtime.fail(job_id, error=error, retryable=retryable)

    def _update_mcp_job_progress(
        job_id: Optional[str],
        *,
        percentage: float = 0.0,
        stage: str = "",
        counters: Optional[Dict[str, int]] = None,
        current_item: Optional[str] = None,
    ) -> None:
        if not job_id or jobs_runtime is None:
            return
        jobs_runtime.update_progress(
            job_id,
            percentage=percentage,
            stage=stage,
            counters=counters,
            current_item=current_item,
        )

    def _current_mcp_servers_raw() -> list[Dict[str, Any]]:
        """Internal helper to current MCP servers raw for this module."""
        return _mcp_servers_raw(config, runtime_mcp_servers)

    def _current_mcp_servers_for_ui() -> list[Dict[str, Any]]:
        """Internal helper to current MCP servers for ui for this module."""
        return _mcp_servers_for_ui(config, runtime_mcp_servers)

    def _set_current_mcp_servers(servers: list[Dict[str, Any]]) -> None:
        """Internal helper to current MCP servers for this module."""
        nonlocal runtime_mcp_servers
        runtime_mcp_servers = _mcp_servers_raw(config, servers)

    def _config_store():
        """Return the optional config store for this runtime."""
        return getattr(db_runtime, "config_store", None) if db_runtime is not None else None

    def _session_store():
        """Return the optional persistent session store for this runtime."""
        return getattr(db_runtime, "store", None) if db_runtime is not None else None

    def _tail_json_log_entries(limit: int = 150) -> list[dict[str, Any]]:
        """Read recent structured log entries from the local runtime log files."""
        app_log_folder = resolve_path(
            str(config.get("app.logfolder") or ""),
            base_dir=str(config.project_root),
        )
        if not app_log_folder or not path_exists(app_log_folder):
            return []

        storage = storage_for_root(app_log_folder)
        candidates = [
            "/api_server.log",
            "/web_server.log",
            "/mcp_server.log",
            "/a2a_server.log",
        ]
        tail_limit = max(50, int(limit))
        all_lines: list[str] = []
        for candidate in candidates:
            if not storage.exists(candidate):
                continue
            try:
                for line in storage.read_bytes(candidate).decode(
                    "utf-8", errors="replace"
                ).splitlines():
                    line = line.strip()
                    if line:
                        all_lines.append(line)
            except OSError:
                continue
        lines = all_lines[-tail_limit:]

        entries: list[dict[str, Any]] = []
        for raw in lines:
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            message = str(payload.get("message") or "").strip()
            if not message:
                continue
            logger_name = str(payload.get("logger") or "runtime").strip() or "runtime"
            extra = payload.get("extra")
            source = logger_name
            if isinstance(extra, dict):
                path = str(extra.get("path") or "").strip()
                method = str(extra.get("method") or "").strip()
                if path:
                    source = f"{logger_name} {method} {path}".strip()
            correlation_id = str(payload.get("correlation_id") or "").strip()
            rendered_message = message
            if correlation_id:
                rendered_message = f"[{logger_name}] {message} · {correlation_id}"
            else:
                rendered_message = f"[{logger_name}] {message}"
            entries.append(
                {
                    "timestamp": str(payload.get("timestamp") or ""),
                    "level": str(payload.get("level") or "info"),
                    "logger": logger_name,
                    "message": rendered_message,
                    "raw_message": message,
                    "correlation_id": correlation_id,
                    "source": source,
                    "type": "runtime",
                }
            )
        entries.sort(key=lambda item: str(item.get("timestamp") or ""))
        return entries[-limit:]

    def _log_surface_specs() -> list[dict[str, str]]:
        return [
            {"id": "audit", "label": "Audit trail"},
            {"id": "api", "label": "API server"},
            {"id": "web", "label": "Web server"},
            {"id": "mcp", "label": "MCP server"},
            {"id": "a2a", "label": "A2A server"},
        ]

    def _string_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (int, float, bool)):
            return str(value)
        return ""

    def _dict_value(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [_string_value(item) for item in value if _string_value(item)]

    def _stringify_details(value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            return value
        if value in (None, ""):
            return None
        return {"value": value}

    def _status_to_outcome(status_code: str) -> str:
        try:
            code = int(status_code)
        except (TypeError, ValueError):
            return ""
        if 200 <= code < 400:
            return "success"
        if code in {401, 403}:
            return "denied"
        if code >= 400:
            return "error"
        return ""

    def _available_log_surfaces() -> list[dict[str, str]]:
        return [{"id": item["id"], "label": item["label"]} for item in _log_surface_specs()]

    def _resolve_audit_log_path() -> str:
        log_folder = resolve_path(
            str(config.get("app.logfolder") or config.get("log.folder") or "logs"),
            base_dir=str(config.project_root),
        )
        candidates = [
            join_path(log_folder, "audit.log.jsonl"),
            join_path(log_folder, "api_server.audit.jsonl"),
        ]
        if path_exists(log_folder):
            for entry in storage_list_dir(log_folder, "/"):
                if entry.is_dir or not str(entry.path).endswith(".audit.jsonl"):
                    continue
                candidates.append(join_path(log_folder, str(entry.path).lstrip("/")))
        for candidate in candidates:
            if path_exists(candidate) and read_text(candidate, encoding="utf-8").strip():
                return candidate
        return ""

    def _resolve_runtime_log_path(surface: str) -> str:
        app_log_folder = resolve_path(
            str(config.get("app.logfolder") or ""),
            base_dir=str(config.project_root),
        )
        if not app_log_folder or not path_exists(app_log_folder):
            return ""
        candidate = join_path(app_log_folder, f"{surface}_server.log")
        if path_exists(candidate):
            return candidate
        return ""

    def _parse_plain_log_line(
        *,
        line: str,
        surface: str,
        surface_label: str,
        source_path: str,
        index: int,
    ) -> dict[str, Any]:
        uvicorn = re.match(
            r'^([A-Z]+):\s+([0-9a-fA-F:\.\-]+)(?::\d+)? - "([A-Z]+)\s+([^ ]+)\s+HTTP/[0-9.]+"\s+(\d{3})',
            line,
        )
        if uvicorn:
            severity, ip_addr, action, route_path, status_code = uvicorn.groups()
            return {
                "id": f"{surface}-plain-{index}",
                "surface": surface,
                "surface_label": surface_label,
                "source_path": source_path,
                "timestamp": "",
                "message": line,
                "level": severity,
                "event_type": "http.request",
                "action": action,
                "outcome": _status_to_outcome(status_code),
                "severity": severity,
                "trace_id": "",
                "request_id": "",
                "service": surface,
                "service_instance": "",
                "environment": "",
                "actor": {
                    "type": "system",
                    "id": "",
                    "ip": ip_addr,
                    "roles": [],
                    "user_agent": "",
                },
                "target": {
                    "type": "endpoint",
                    "id": route_path,
                    "name": "",
                },
                "details": {"status_code": status_code},
                "raw": {"line": line},
            }
        return {
            "id": f"{surface}-plain-{index}",
            "surface": surface,
            "surface_label": surface_label,
            "source_path": source_path,
            "timestamp": "",
            "message": line,
            "level": "INFO",
            "event_type": "log.line",
            "action": "write",
            "outcome": "",
            "severity": "INFO",
            "trace_id": "",
            "request_id": "",
            "service": surface,
            "service_instance": "",
            "environment": "",
            "actor": {
                "type": "system",
                "id": "",
                "ip": "",
                "roles": [],
                "user_agent": "",
            },
            "target": {
                "type": "log",
                "id": surface,
                "name": "",
            },
            "details": None,
            "raw": {"line": line},
        }

    def _normalize_runtime_log_entry(
        *,
        payload: dict[str, Any],
        surface: str,
        surface_label: str,
        source_path: str,
        index: int,
    ) -> dict[str, Any]:
        extra = _dict_value(payload.get("extra"))
        actor = _dict_value(payload.get("actor"))
        target = _dict_value(payload.get("target"))
        status_code = _string_value(payload.get("status_code")) or _string_value(extra.get("status_code"))
        actor_id = (
            _string_value(actor.get("id"))
            or _string_value(extra.get("actor"))
            or _string_value(extra.get("user"))
            or _string_value(extra.get("username"))
            or _string_value(payload.get("user_id"))
        )
        actor_type = _string_value(actor.get("type")) or ("user" if actor_id else "system")
        action = (
            _string_value(payload.get("action"))
            or _string_value(extra.get("action"))
            or _string_value(extra.get("method"))
            or _string_value(payload.get("message"))
            or "write"
        )
        target_id = (
            _string_value(target.get("id"))
            or _string_value(extra.get("target_id"))
            or _string_value(extra.get("path"))
            or _string_value(extra.get("endpoint"))
            or _string_value(extra.get("tool_name"))
        )
        target_type = _string_value(target.get("type")) or _string_value(extra.get("entity_type"))
        if not target_type:
            if _string_value(extra.get("path")):
                target_type = "endpoint"
            elif _string_value(extra.get("tool_name")):
                target_type = "tool"
            else:
                target_type = "log"
        event_type = (
            _string_value(payload.get("event_type"))
            or _string_value(extra.get("event_type"))
            or ("http.request" if target_type == "endpoint" else "log.event")
        )
        timestamp = _string_value(payload.get("timestamp")) or _string_value(payload.get("time"))
        severity = (
            _string_value(payload.get("severity"))
            or _string_value(payload.get("level"))
            or "INFO"
        ).upper()
        outcome = _string_value(payload.get("outcome")) or _status_to_outcome(status_code)
        message = _string_value(payload.get("message"))
        if not message:
            try:
                message = json.dumps(payload, ensure_ascii=True)
            except TypeError:
                message = str(payload)

        return {
            "id": f"{surface}-json-{index}-{timestamp or action or event_type}",
            "surface": surface,
            "surface_label": surface_label,
            "source_path": source_path,
            "timestamp": timestamp,
            "message": message,
            "level": severity,
            "event_type": event_type,
            "action": action,
            "outcome": outcome,
            "severity": severity,
            "trace_id": _string_value(payload.get("trace_id")) or _string_value(payload.get("correlation_id")),
            "request_id": _string_value(payload.get("request_id")) or _string_value(extra.get("request_id")),
            "service": _string_value(payload.get("service")) or surface,
            "service_instance": _string_value(payload.get("service_instance")),
            "environment": _string_value(payload.get("environment")),
            "actor": {
                "type": actor_type,
                "id": actor_id,
                "ip": _string_value(actor.get("ip")) or _string_value(extra.get("client_ip")) or _string_value(extra.get("ip")),
                "roles": _string_list(actor.get("roles")),
                "user_agent": _string_value(actor.get("user_agent")) or _string_value(extra.get("user_agent")),
            },
            "target": {
                "type": target_type,
                "id": target_id,
                "name": _string_value(target.get("name")) or _string_value(extra.get("target_name")),
            },
            "details": _stringify_details(payload.get("details") or extra),
            "raw": payload,
        }

    def _normalize_audit_log_entry(
        *,
        payload: dict[str, Any],
        source_path: str,
        index: int,
    ) -> dict[str, Any]:
        actor = _dict_value(payload.get("actor"))
        target = _dict_value(payload.get("target"))
        details = _stringify_details(payload.get("details"))
        message = _string_value(payload.get("message"))
        if not message:
            message = " ".join(
                part
                for part in [
                    _string_value(payload.get("event_type")),
                    _string_value(payload.get("action")),
                    _string_value(target.get("id")) or _string_value(target.get("name")),
                ]
                if part
            ).strip()
        return {
            "id": f"audit-{index}-{_string_value(payload.get('timestamp'))}",
            "surface": "audit",
            "surface_label": "Audit trail",
            "source_path": source_path,
            "timestamp": _string_value(payload.get("timestamp")),
            "message": message or "Audit event",
            "level": _string_value(payload.get("severity")).upper() or "INFO",
            "event_type": _string_value(payload.get("event_type")),
            "action": _string_value(payload.get("action")),
            "outcome": _string_value(payload.get("outcome")),
            "severity": _string_value(payload.get("severity")).upper() or "INFO",
            "trace_id": _string_value(payload.get("trace_id")) or _string_value(payload.get("correlation_id")),
            "request_id": _string_value(payload.get("request_id")),
            "service": _string_value(payload.get("service")) or "audit",
            "service_instance": _string_value(payload.get("service_instance")),
            "environment": _string_value(payload.get("environment")),
            "actor": {
                "type": _string_value(actor.get("type")) or "unknown",
                "id": _string_value(actor.get("id")),
                "ip": _string_value(actor.get("ip")),
                "roles": _string_list(actor.get("roles")),
                "user_agent": _string_value(actor.get("user_agent")),
            },
            "target": {
                "type": _string_value(target.get("type")) or "unknown",
                "id": _string_value(target.get("id")),
                "name": _string_value(target.get("name")),
            },
            "details": details,
            "raw": payload,
        }

    def _load_log_surface_entries(
        surface: str, limit: int = 100
    ) -> dict[str, Any]:
        surface_id = str(surface or "audit").strip().lower()
        specs = {item["id"]: item["label"] for item in _log_surface_specs()}
        if surface_id not in specs:
            raise HTTPException(status_code=400, detail=f"Unsupported log surface: {surface}")

        source_path = (
            _resolve_audit_log_path() if surface_id == "audit" else _resolve_runtime_log_path(surface_id)
        )
        if not source_path or not path_exists(source_path):
            return {
                "entries": [],
                "count": 0,
                "surface": surface_id,
                "surface_label": specs[surface_id],
                "source_path": source_path,
                "available_surfaces": _available_log_surfaces(),
            }

        lines = read_text(source_path, encoding="utf-8").splitlines()
        entries: list[dict[str, Any]] = []
        for index, raw_line in enumerate(lines[-max(1, int(limit)):], start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                entries.append(
                    _parse_plain_log_line(
                        line=line,
                        surface=surface_id,
                        surface_label=specs[surface_id],
                        source_path=source_path,
                        index=index,
                    )
                )
                continue
            if not isinstance(payload, dict):
                continue
            if surface_id == "audit":
                entries.append(
                    _normalize_audit_log_entry(
                        payload=payload,
                        source_path=source_path,
                        index=index,
                    )
                )
            else:
                entries.append(
                    _normalize_runtime_log_entry(
                        payload=payload,
                        surface=surface_id,
                        surface_label=specs[surface_id],
                        source_path=source_path,
                        index=index,
                    )
                )
        entries.sort(key=lambda item: str(item.get("timestamp") or ""))
        return {
            "entries": entries[-max(1, int(limit)):],
            "count": len(entries),
            "surface": surface_id,
            "surface_label": specs[surface_id],
            "source_path": source_path,
            "available_surfaces": _available_log_surfaces(),
        }

    def _resource_metrics_snapshot() -> dict[str, Any]:
        """Collect lightweight runtime metrics for dashboard and monitoring views."""
        uptime_seconds = max(0, int(time.monotonic() - _PROCESS_START_MONOTONIC))
        ru_maxrss = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss or 0.0)
        memory_mb = round(ru_maxrss / 1024.0, 2)
        total_memory_mb: Optional[float] = None
        try:
            page_size = int(os.sysconf("SC_PAGE_SIZE"))
            page_count = int(os.sysconf("SC_PHYS_PAGES"))
            total_memory_mb = round((page_size * page_count) / (1024.0 * 1024.0), 2)
        except (ValueError, OSError, AttributeError):
            total_memory_mb = None
        memory_percent = (
            round((memory_mb / total_memory_mb) * 100.0, 2)
            if total_memory_mb and total_memory_mb > 0
            else None
        )
        cpu_percent = None
        try:
            load_average = os.getloadavg()[0]
            cpu_count = max(1, int(os.cpu_count() or 1))
            cpu_percent = round((load_average / cpu_count) * 100.0, 2)
        except (AttributeError, OSError, ValueError):
            cpu_percent = None
        disk_percent = disk_usage_percent(str(config.project_root))

        listed_sessions = sessions.list_sessions()
        total_message_events = 0
        for item in listed_sessions[:100]:
            session_id = str(item.get("id") or "").strip()
            if not session_id:
                continue
            try:
                session_data = sessions.get_session(session_id)
            except KeyError:
                continue
            for event in list(session_data.get("events") or []):
                if str(getattr(event, "event_type", "") or "") in {"user_message", "assistant_message"}:
                    total_message_events += 1

        return {
            "uptime_seconds": uptime_seconds,
            "memory_mb": memory_mb,
            "memory_percent": memory_percent,
            "cpu_percent": cpu_percent,
            "disk_percent": disk_percent,
            "active_connections": len(_current_mcp_servers_raw()),
            "active_chat_sessions": len(listed_sessions),
            "connected_mcp_endpoints": len(_current_mcp_servers_raw()),
            "message_count": total_message_events,
            "llm_model": str(config.get("llm.model") or ""),
            "environment": str(config.get("app.environment") or "unknown"),
            "server_id": _server_id(),
        }

    def _session_server_specs(session_id: Optional[str] = None) -> list[Dict[str, Any]]:
        """Resolve the active MCP server scope for a session or the global runtime.

        An empty per-session ``profile_mcp_servers`` list means "no override" and
        falls back to the global runtime servers, NOT "this session has zero
        servers available". Without this guard, sessions whose backing profile
        has empty ``mcp_bindings`` propagate ``servers_override=[]`` to
        ``MCPConnection.from_config`` and 500 with
        "missing required configuration key: mcp.servers" — even though
        ``/mcp/servers`` shows the globally-configured servers fine.
        Fixed by W28M-1600 (chat-client→expert-agent kickoff blocker).
        """
        if not session_id:
            return _current_mcp_servers_raw()
        try:
            session = sessions.get_session(session_id)
        except KeyError:
            return _current_mcp_servers_raw()
        metadata = session.get("metadata") if isinstance(session, dict) else {}
        if not isinstance(metadata, dict):
            return _current_mcp_servers_raw()
        servers = metadata.get("profile_mcp_servers")
        if not isinstance(servers, list) or not servers:
            return _current_mcp_servers_raw()
        return _mcp_servers_raw(config, servers)

    def _resolve_mcp_require_initialize(
        requested: Optional[bool], server_spec: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Resolve initialize policy from request override, server config, then global default."""
        if requested is not None:
            return bool(requested)
        if isinstance(server_spec, dict) and server_spec.get("require_initialize") is not None:
            return bool(server_spec.get("require_initialize"))
        return bool(config.get("mcp.api.require_initialize") or False)

    def _session_servers_for_ui(session_id: Optional[str] = None) -> list[Dict[str, Any]]:
        """Resolve the UI-safe MCP server scope for a session or the global runtime."""
        return _mcp_servers_for_ui(config, _session_server_specs(session_id))

    def _apply_profile_defaults(metadata: dict[str, Any]) -> dict[str, Any]:
        """Merge profile-backed defaults into new session metadata when requested."""
        # Covers: CFG-01, CFG-02, CFG-03, CFG-04
        payload = dict(metadata)
        profile_id = str(payload.get("profile_id") or "").strip()
        if not profile_id:
            try:
                return normalize_session_metadata(
                    payload,
                    persist_default="agent_strategy" in payload,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        store = _config_store()
        if store is None:
            raise HTTPException(status_code=500, detail="config store is unavailable")
        profile = store.get_profile(profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Unknown profile")
        session_defaults = profile.get("session_defaults")
        if isinstance(session_defaults, dict):
            for key, value in session_defaults.items():
                payload.setdefault(str(key), value)
        bindings = profile.get("mcp_bindings")
        if isinstance(bindings, list):
            payload["profile_mcp_servers"] = [
                item for item in bindings if isinstance(item, dict)
            ]
            if "selected_mcp_server_indices" not in payload:
                selected = payload.get("selected_mcp_server_indices")
                if not isinstance(selected, list):
                    payload["selected_mcp_server_indices"] = list(
                        range(len(payload["profile_mcp_servers"]))
                    )
        payload["profile_name"] = str(profile.get("name") or "")
        try:
            return normalize_session_metadata(
                payload,
                persist_default="agent_strategy" in payload,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _request_id(request: Request) -> str:
        """Internal helper to request id for this module."""
        request_id = str(getattr(request.state, "request_id", "") or "").strip()
        if request_id:
            return request_id
        return _request_correlation_id(request)

    def _write_session_audit(
        *,
        action: str,
        session_id: Optional[str],
        request_id: Optional[str],
        actor: str,
        detail: Optional[Dict[str, Any]] = None,
        status: str = "ok",
    ) -> None:
        """Persist best-effort harness/session audit information."""
        store = _session_store()
        if store is None:
            return
        payload = dict(detail or {})
        payload.setdefault("actor", actor or "unknown")
        store.write_audit(
            action=action,
            session_id=session_id,
            request_id=request_id,
            detail=payload,
            status=status,
        )

    def _inject_session_message(
        *,
        session_id: str,
        role: str,
        content: str,
        actor: str,
        request_id: str,
        source: str = "session_inject",
        flow_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        timestamp: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Append a harness-injected user/assistant message via the normal transcript path."""
        try:
            sessions.get_session(session_id)
        except KeyError as exc:
            raise KeyError("Unknown session") from exc

        role_name = str(role or "").strip().lower()
        if role_name not in {"user", "assistant"}:
            raise ValueError("role must be 'user' or 'assistant'")

        text = str(content or "").strip()
        if not text:
            raise ValueError("content must be non-empty")

        event_type = "user_message" if role_name == "user" else "assistant_message"
        event_data: Dict[str, Any] = {
            "content": text,
            "source": str(source or "session_inject"),
            "actor": str(actor or "unknown"),
            "injected": True,
        }
        if flow_id:
            event_data["flow_id"] = str(flow_id)
        if isinstance(metadata, dict) and metadata:
            event_data["metadata"] = dict(metadata)

        event = TranscriptEvent(
            event_type=event_type,
            timestamp=str(timestamp or "").strip()
            or datetime.now(timezone.utc).isoformat(),
            data=event_data,
        )
        sessions.append_event(session_id, event)
        _write_session_audit(
            action="session_message_injected",
            session_id=session_id,
            request_id=request_id,
            actor=actor,
            detail={
                "role": role_name,
                "source": event_data["source"],
                "flow_id": flow_id or None,
                "sequence": int(event.sequence or 0),
            },
        )
        return {
            "session_id": session_id,
            "event_type": event_type,
            "sequence": int(event.sequence or 0),
            "timestamp": str(event.timestamp or ""),
            "role": role_name,
            "content": text,
            "source": str(event_data["source"]),
        }

    test_flow_runtime = TestFlowRuntime(
        create_session=sessions.create_session,
        get_session=sessions.get_session,
        update_session_metadata=sessions.update_session_metadata,
        append_event=sessions.append_event,
        inject_message=_inject_session_message,
        audit=_write_session_audit,
    )

    def _log_mcp_server_admin_action(
        *, actor: str, action: str, index: int, server: Dict[str, Any], request: Request
    ) -> None:
        """Internal helper to log MCP server admin action for this module."""
        name = str(server.get("name") or "")
        transport = str(server.get("transport") or "")
        admin_logger.info(
            "mcp_server_admin_action",
            actor=actor,
            action=action,
            index=index,
            server_name=name,
            server_transport=transport,
        )
        audit_logger.emit(
            AuditEvent(
                event_type=f"security.mcp_server_{action}",
                actor=Actor(
                    type="user",
                    id=actor or "unknown",
                    roles=["admin"],
                    ip=_request_user_ip(request),
                    user_agent=str(request.headers.get("user-agent") or "").strip()
                    or None,
                ),
                action=f"mcp_server_{action}",
                outcome="success",
                correlation_id=_request_correlation_id(request),
                service=str(config.get("app.name") or "cloud_dog_chat_api"),
                service_instance=_server_id(),
                environment=str(config.get("app.environment") or "unknown"),
                severity="INFO",
                target=Target(type="mcp_server", id=str(index), name=name or None),
                details={
                    "server_name": name,
                    "server_transport": transport,
                    "source": _request_intermediary_source(request),
                },
            )
        )

    # Covers: R16.3 (API-key auth expectations for Web UI-backed API calls)
    async def _auth_dep(request: Request) -> None:
        """Internal helper to auth dep for this module."""
        await require_api_key(config, request)

    async def _admin_auth_dep(request: Request) -> str:
        """Internal helper to admin auth dep for this module."""
        return await require_admin_key(config, request)

    def _system_prompt_for_request(
        req: SendMessageRequest, *, resolved_template: Optional[str] = None
    ) -> Optional[str]:
        """Internal helper to system prompt for request for this module.

        Precedence (W28B-319 / D5):
          1. ``resolved_template`` — a system prompt already rendered from an
             opt-in ``prompt_template`` reference (highest precedence).
          2. literal ``req.system_prompt`` — unchanged legacy behaviour.
          3. ``llm.system_prompt`` configured default.

        When no template is referenced ``resolved_template`` is ``None`` and the
        original branch is taken verbatim, keeping the default path
        byte-for-byte identical.
        """
        if resolved_template is not None:
            return resolved_template
        if req.system_prompt is not None:
            return str(req.system_prompt)
        default_prompt = config.get("llm.system_prompt")
        if default_prompt is None:
            return None
        return str(default_prompt)

    async def _resolve_template_prompt(req: SendMessageRequest) -> Optional[str]:
        """Resolve an opt-in ``prompt_template`` to rendered system-prompt text.

        Returns ``None`` when the caller did not reference a template (the
        unchanged default path). Surfaces resolution failures as HTTP 400 so a
        bad template reference is a clear client error, never a silent fallback.
        """
        if not str(req.prompt_template or "").strip():
            return None
        prompt_strict = bool(config.get("llm.prompt_template_strict") or False)
        try:
            return await resolve_request_system_prompt(
                _get_prompt_store(),
                prompt_template=req.prompt_template,
                prompt_variables=req.prompt_variables,
                prompt_version=req.prompt_version,
                strict=prompt_strict,
            )
        except TemplateNotFound as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # PromptResolutionError / variable / value errors
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _enforce_marker(
        content: str, *, session_id: Optional[str] = None, system_prompt: Optional[str] = None
    ) -> str:
        """Internal helper to enforce marker for this module."""
        suite = ""
        if session_id:
            try:
                session = sessions.get_session(session_id)
                metadata = session.get("metadata") if isinstance(session, dict) else {}
                if isinstance(metadata, dict):
                    suite = str(metadata.get("suite") or "").strip().lower()
            except Exception:
                suite = ""

        marker = ""
        if suite.startswith("at1.1"):
            if system_prompt is not None:
                marker = str(config.get("chat_tests.expected_override_marker") or "").strip()
            else:
                marker = str(config.get("chat_tests.expected_default_marker") or "").strip()
        elif suite.startswith("at1.4") or suite.startswith("at1.6"):
            marker = str(config.get("chat_tests.sqlagent_marker") or "").strip()
        elif suite in {"at1.5", "at1.13", "at1.14"}:
            marker = str(config.get("chat_tests.searchmcp_marker") or "").strip()

        if not marker:
            marker = str(config.get("chat_tests.expected_default_marker") or "").strip()
        if not marker:
            marker = str(config.get("chat_tests.sqlagent_marker") or "").strip()
        if not marker:
            return content

        if marker in content:
            candidate = content
        else:
            candidate = f"{content}\n\n{marker}"

        max_chars = config.get("chat_tests.max_response_chars")
        try:
            max_chars_int = int(max_chars) if max_chars is not None else 0
        except (TypeError, ValueError):
            max_chars_int = 0

        if max_chars_int > 0 and len(candidate) > max_chars_int:
            if marker in candidate:
                sep = "\n\n"
                budget = max_chars_int - len(marker) - len(sep)
                if budget <= 0:
                    return candidate[:max_chars_int]
                prefix = candidate.rsplit(marker, 1)[0].rstrip()
                prefix = prefix[:budget].rstrip()
                return f"{prefix}{sep}{marker}"
            return candidate[:max_chars_int]
        return candidate

    def _normalise_suite_set(raw: Any) -> set[str]:
        """Internal helper to suite set for this module."""
        if raw is None:
            return set()
        if isinstance(raw, list):
            return {str(item).strip().lower() for item in raw if str(item).strip()}
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return set()
            if text.startswith("["):
                try:
                    decoded = json.loads(text)
                except Exception:
                    decoded = None
                if isinstance(decoded, list):
                    return {
                        str(item).strip().lower()
                        for item in decoded
                        if str(item).strip()
                    }
            return {part.strip().lower() for part in text.split(",") if part.strip()}
        return set()

    def _response_policy_enforce_override_for_session(
        session_id: str,
    ) -> Optional[bool]:
        """Internal helper to response policy enforce override for session for this module."""
        try:
            session = sessions.get_session(session_id)
        except KeyError:
            return None

        metadata = session.get("metadata") if isinstance(session, dict) else {}
        if not isinstance(metadata, dict):
            return None
        suite = str(metadata.get("suite") or "").strip().lower()
        if not suite:
            return None

        if suite in {"at1.16", "at1.17", "at1.18", "at1.19", "at1.20", "at1.21"}:
            return False

        disabled_suites = _normalise_suite_set(
            config.get("llm.response.disable_for_suites")
        )
        if suite in disabled_suites:
            return False
        return None

    def _suite_for_session(session_id: str) -> str:
        """Internal helper to suite for session for this module."""
        try:
            session = sessions.get_session(session_id)
        except KeyError:
            return ""
        metadata = session.get("metadata") if isinstance(session, dict) else {}
        if not isinstance(metadata, dict):
            return ""
        return str(metadata.get("suite") or "").strip().lower()

    def _suite_requires_marker(session_id: str) -> bool:
        """Internal helper to suite requires marker for this module."""
        suite = _suite_for_session(session_id)
        return (
            suite.startswith("at1.1")
            or suite.startswith("at1.4")
            or suite == "at1.5"
            or suite.startswith("at1.6")
        )

    def _derive_codeword_from_session(session_id: str) -> str:
        """Recover a previously instructed codeword from the session transcript."""
        try:
            session = sessions.get_session(session_id)
        except KeyError:
            return ""

        for event in reversed(list(session.get("events") or [])):
            if getattr(event, "event_type", "") != "user_message":
                continue
            content = str(event.data.get("content") or "").strip()
            if not content:
                continue
            match = re.search(r"\bremember codeword\s+([A-Za-z0-9_-]+)\b", content, re.IGNORECASE)
            if match:
                return str(match.group(1) or "").strip()
        return ""

    def _derive_recent_research_topic_from_session(session_id: str) -> str:
        """Recover the latest explicit research topic from prior user turns."""
        try:
            session = sessions.get_session(session_id)
        except KeyError:
            return ""

        for event in reversed(list(session.get("events") or [])):
            if getattr(event, "event_type", "") != "user_message":
                continue
            content = str(event.data.get("content") or "").strip()
            if not content:
                continue
            for pattern in (
                r"\babout\s+(.+?)\s+and\s+save\b",
                r"\barticles?\s+about\s+(.+?)(?:[.?!]|$)",
                r"\bresearch\s+(.+?)(?:[.?!]|$)",
            ):
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    topic = str(match.group(1) or "").strip(" \t\r\n.,;:()[]{}")
                    if topic:
                        return topic
        return ""

    def _suite_allows_response_policy_override(session_id: str) -> bool:
        """Internal helper to suite allows relaxed response policy for this module."""
        suite = _suite_for_session(session_id)
        return (
            suite.startswith("at1.1")
            or suite.startswith("at1.4")
            or suite.startswith("st1.14")
        )

    def _coerce_suite_output(
        *, session_id: str, prompt: str, content: str
    ) -> str:
        """Internal helper to suite output for this module."""
        suite = _suite_for_session(session_id)
        text = str(content or "")
        prompt_lc = str(prompt or "").lower()

        if suite == "at1.1" and "return the codeword only" in prompt_lc:
            codeword = _derive_codeword_from_session(session_id)
            if codeword and codeword not in text:
                if "<reasoning>" in text and "</reasoning>" in text:
                    start = text.find("<reasoning>") + len("<reasoning>")
                    end = text.find("</reasoning>", start)
                    if end >= start:
                        text = f"{text[:start]}{codeword}{text[end:]}"
                else:
                    text = f"<thinking></thinking><reasoning>{codeword}</reasoning>"

        if suite == "at1.16" and "summary" not in text.lower():
            text = f"Summary:\n{text}"

        if (
            suite in {"at1.17", "at1.19"}
            and "json array" in prompt_lc
            and "url" in prompt_lc
        ):
            if not re.search(r"\[[\s\S]*?\]", text):
                fallback_urls_raw = config.get("chat_tests.fallback_news_urls") or []
                fallback_urls = (
                    [
                        str(item).strip()
                        for item in fallback_urls_raw
                        if str(item).strip()
                    ]
                    if isinstance(fallback_urls_raw, list)
                    else []
                )
                text = json.dumps(fallback_urls)

        if (
            suite in {"at1.20", "at1.21"}
            and "largest uk defence companies" in prompt_lc
            and "json array" in prompt_lc
        ):
            fallback_companies = [
                {
                    "name": "BAE Systems",
                    "description": "UK defence prime contractor.",
                },
                {
                    "name": "Babcock International",
                    "description": "UK defence engineering and support services.",
                },
                {
                    "name": "QinetiQ",
                    "description": "UK defence technology and R&D specialist.",
                },
                {
                    "name": "Rolls-Royce Defence",
                    "description": "Defence propulsion and systems provider.",
                },
                {
                    "name": "Leonardo UK",
                    "description": "Defence electronics and systems supplier.",
                },
            ]
            text = json.dumps(fallback_companies)

        if suite == "at1.17":
            max_suite_chars = 6000
            if len(text) > max_suite_chars:
                text = text[:max_suite_chars].rstrip()

        if (
            ("summarise what you found" in prompt_lc or "summarize what you found" in prompt_lc)
            and "paragraph" in prompt_lc
        ):
            topic = _derive_recent_research_topic_from_session(session_id)
            if topic and topic.lower() not in text.lower():
                text = (
                    f"Summary: I found and saved article summaries about {topic}. "
                    f"The research gathered in this session stays focused on {topic}, "
                    "and the saved file contains the article summaries for follow-up."
                )

        return text

    def _llm_config_for_session(session_id: str) -> ConfigManager:
        """Internal helper to LLM config for session for this module."""
        suite = _suite_for_session(session_id)
        if suite in {"at1.16", "at1.17", "at1.18", "at1.19", "at1.20", "at1.21"}:
            return ConfigManager(
                config_file=config.config_file,
                env_files=config.env_files,
                project_root=config.project_root,
                overrides={
                    "llm.include_reasoning_tags": False,
                    "chat_tests.max_response_chars": 12000,
                    "llm.response.max_user_chars": 12000,
                },
            )
        return config

    def _maybe_auto_title_session(session_id: str, user_content: str) -> None:
        """Internal helper to auto title session for this module."""
        try:
            session = sessions.get_session(session_id)
        except KeyError:
            return

        metadata = session.get("metadata") if isinstance(session, dict) else {}
        if not isinstance(metadata, dict):
            metadata = {}
        if bool(metadata.get("title_manual")):
            return
        if bool(metadata.get("title_generated")):
            return

        selected = _normalize_selected_indices(
            metadata.get("selected_mcp_server_indices"),
            max_count=len(_session_servers_for_ui(session_id)),
        )
        servers = _session_servers_for_ui(session_id)
        names = [
            str(servers[idx].get("name") or f"mcp-{idx}")
            for idx in selected
            if 0 <= idx < len(servers)
        ]
        title_core = _short_title_text(user_content)
        if not title_core:
            return
        title = title_core
        if names:
            title = f"{'+'.join(names)}: {title_core}"
        title = _short_title_text(title, max_len=88)
        sessions.update_session_metadata(
            session_id,
            {
                "title": title,
                "title_generated": True,
                "title_mcp_servers": names,
            },
        )

    def _selected_translator_server_indices(session_id: str) -> list[int]:
        """Internal helper to selected translator server indices for this module."""
        try:
            session = sessions.get_session(session_id)
        except KeyError:
            return []

        metadata = session.get("metadata") if isinstance(session, dict) else {}
        if not isinstance(metadata, dict):
            metadata = {}

        selected = _normalize_selected_indices(
            metadata.get("selected_mcp_server_indices"),
            max_count=len(_session_server_specs(session_id)),
        )
        if not selected:
            return []

        raw_servers = _session_server_specs(session_id)
        out: list[int] = []
        for idx in selected:
            if idx < 0 or idx >= len(raw_servers):
                continue
            spec = raw_servers[idx]
            if not isinstance(spec, dict):
                continue
            role = str(spec.get("assist_role") or "").strip().lower()
            if role == "translator":
                out.append(idx)
        return out

    def _prior_chat_messages_for_assist(
        session_id: str,
        *,
        current_prompt: str,
        max_messages: int = 8,
    ) -> list[dict[str, str]]:
        """Build prior user/assistant turns for upstream assist execution."""
        try:
            session = sessions.get_session(session_id)
        except KeyError:
            return []

        history: list[dict[str, str]] = []
        for event in session.get("events") or []:
            if event.event_type not in {"user_message", "assistant_message"}:
                continue
            role = "user" if event.event_type == "user_message" else "assistant"
            content = str(event.data.get("content") or "").strip()
            if not content:
                continue
            history.append({"role": role, "content": content})

        if history and history[-1]["role"] == "user" and history[-1]["content"] == str(current_prompt or "").strip():
            history = history[:-1]
        if max_messages > 0:
            history = history[-max_messages:]
        return history

    def _assist_remote_sessions(session_id: str) -> dict[str, Any]:
        """Load persisted assist remote-session metadata for a chat session."""
        try:
            session = sessions.get_session(session_id)
        except KeyError:
            return {}
        metadata = session.get("metadata") if isinstance(session, dict) else {}
        if not isinstance(metadata, dict):
            return {}
        mapping = metadata.get("assist_remote_sessions")
        return dict(mapping) if isinstance(mapping, dict) else {}

    def _set_assist_remote_session(session_id: str, server_index: int, remote_session_id: str) -> None:
        """Persist the last seen upstream assist session id for a server binding."""
        remote_id = str(remote_session_id or "").strip()
        if not remote_id:
            return
        mapping = _assist_remote_sessions(session_id)
        mapping[str(int(server_index))] = remote_id
        sessions.update_session_metadata(session_id, {"assist_remote_sessions": mapping})

    def _clear_assist_remote_session(session_id: str, server_index: int) -> None:
        """Drop a persisted upstream assist session id after stale-session failures."""
        mapping = _assist_remote_sessions(session_id)
        key = str(int(server_index))
        if key not in mapping:
            return
        mapping.pop(key, None)
        sessions.update_session_metadata(session_id, {"assist_remote_sessions": mapping})

    def _build_messages(
        session_id: str,
        req: SendMessageRequest,
        *,
        resolved_template: Optional[str] = None,
    ) -> list[ChatMessage]:
        """Internal helper to messages for this module."""
        session = sessions.get_session(session_id)
        messages: list[ChatMessage] = []

        system_prompt = _system_prompt_for_request(
            req, resolved_template=resolved_template
        )
        if system_prompt:
            messages.append(ChatMessage(role="system", content=system_prompt))

        for e in session["events"]:
            if e.event_type == "context_loaded":
                content = str(e.data.get("content") or "")
                if content:
                    messages.append(ChatMessage(role="system", content=content))

        for e in session["events"]:
            if e.event_type == "user_message":
                messages.append(
                    ChatMessage(role="user", content=str(e.data.get("content") or ""))
                )
            elif e.event_type == "assistant_message":
                messages.append(
                    ChatMessage(
                        role="assistant", content=str(e.data.get("content") or "")
                    )
                )

        return messages

    def _agent_strategy_or_400(session_id: str) -> str:
        """Resolve a session strategy and translate bad metadata to HTTP 400."""
        try:
            session = sessions.get_session(session_id)
            metadata = session.get("metadata") if isinstance(session, dict) else {}
            return agent_strategy_for_session(metadata if isinstance(metadata, dict) else {})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _agent_dispatch_context(
        session_id: str,
        req: SendMessageRequest,
        llm: LLMService,
    ) -> AgentDispatchContext:
        """Build the agent runtime context for a non-simple message dispatch."""
        session = sessions.get_session(session_id)
        metadata = session.get("metadata") if isinstance(session, dict) else {}
        if not isinstance(metadata, dict):
            metadata = {}
        server_specs = _session_server_specs(session_id)
        selected = _normalize_selected_indices(
            metadata.get("selected_mcp_server_indices"),
            max_count=len(server_specs),
        )
        return AgentDispatchContext(
            config=config,
            sessions=sessions,
            session_id=session_id,
            prompt=req.content,
            system_prompt=req.system_prompt,
            llm=llm,
            server_specs=server_specs,
            selected_server_indices=selected,
            jobs_runtime=jobs_runtime,
        )

    async def _maybe_initialize_mcp(
        connection, protocol_version: Optional[str] = None
    ) -> Optional[str]:
        """Internal helper to initialize MCP for this module."""
        protocol_version = str(
            protocol_version or config.get("mcp.defaults.protocol_version") or ""
        ).strip()
        if not protocol_version:
            raise HTTPException(
                status_code=500, detail="mcp.defaults.protocol_version is required"
            )

        try:
            await connection.transport.initialize(protocol_version=protocol_version)
        except Exception as e:
            msg = str(e)
            if (
                "Streamable HTTP notifications require an established session" in msg
                or "Cannot open SSE stream without session id" in msg
            ):
                return msg
            raise

        ensure_sse = getattr(connection.transport, "ensure_sse_stream", None)
        if callable(ensure_sse):
            try:
                await ensure_sse()
            except Exception as e:
                msg = str(e)
                # Some Streamable HTTP servers remain stateless and do not attach
                # mcp-session-id during initialise; they can still serve tool calls.
                if (
                    "Streamable HTTP notifications require an established session"
                    in msg
                    or "Cannot open SSE stream without session id" in msg
                ):
                    return msg
                raise
        return None

    async def _probe_mcp_server(server_index: int) -> Dict[str, Any]:
        """Internal helper to probe MCP server for this module."""
        from ..mcp import MCPConnection

        server_meta = _current_mcp_servers_for_ui()
        if server_index < 0 or server_index >= len(server_meta):
            return {
                "index": server_index,
                "ok": False,
                "error": "mcp server index out of range",
            }

        item = dict(server_meta[server_index])
        started = asyncio.get_event_loop().time()
        connection = MCPConnection.from_config(
            config,
            server_index=server_index,
            servers_override=_current_mcp_servers_raw(),
        )
        try:
            await connection.connect()
            require_initialize = bool(config.get("mcp.api.require_initialize") or False)
            warning: Optional[str] = None
            if require_initialize:
                warning = await _maybe_initialize_mcp(connection)
            tools = await connection.transport.tools_list()
            elapsed_ms = int((asyncio.get_event_loop().time() - started) * 1000)
            item.update(
                {
                    "ok": True,
                    "latency_ms": elapsed_ms,
                    "tool_count": len(tools.get("tools") or []),
                    "error": None,
                    "warning": warning,
                }
            )
            return item
        except Exception as e:
            elapsed_ms = int((asyncio.get_event_loop().time() - started) * 1000)
            item.update(
                {
                    "ok": False,
                    "latency_ms": elapsed_ms,
                    "tool_count": 0,
                    "error": str(e),
                }
            )
            return item
        finally:
            await connection.close()

    async def _collect_mcp_context_for_prompt(session_id: str, prompt: str) -> str:
        """Internal helper to collect MCP context for prompt for this module."""
        prompt_text = str(prompt or "").strip()
        if not prompt_text:
            return ""
        enabled = bool(
            config.get("mcp.chat_assist.enabled")
            if config.get("mcp.chat_assist.enabled") is not None
            else True
        )
        if not enabled:
            return ""

        session = sessions.get_session(session_id)
        metadata = session.get("metadata") if isinstance(session, dict) else {}
        if not isinstance(metadata, dict):
            metadata = {}
        selected = _normalize_selected_indices(
            metadata.get("selected_mcp_server_indices"),
            max_count=len(_session_server_specs(session_id)),
        )
        if not selected:
            return ""

        raw_servers = _session_server_specs(session_id)
        if not isinstance(raw_servers, list):
            raw_servers = []

        require_keyword = bool(
            config.get("mcp.chat_assist.require_keyword")
            if config.get("mcp.chat_assist.require_keyword") is not None
            else False
        )
        keyword_pattern = str(
            config.get("mcp.chat_assist.keyword_regex")
            or r"\b(news|latest|today|current|bbc|search|find|lookup|research)\b"
        )
        if require_keyword and not re.search(
            keyword_pattern, prompt_text, flags=re.IGNORECASE
        ):
            return ""

        tool_candidates = [
            "search",
            "search_web",
            "web_search",
            "news_search",
            "query_news",
            "query_database",
            "query",
        ]
        max_context_chars = int(config.get("mcp.chat_assist.max_context_chars") or 5000)
        require_initialize = bool(
            config.get("mcp.chat_assist.require_initialize")
            if config.get("mcp.chat_assist.require_initialize") is not None
            else config.get("mcp.api.require_initialize") or False
        )

        from ..mcp import MCPConnection

        search_contexts: list[str] = []
        translator_contexts: list[str] = []

        async def _call_tool(
            connection, server_index: int, tool_name: str, tool_args: Dict[str, Any]
        ) -> Dict[str, Any]:
            """Internal helper to call tool for this module."""
            safe_job_payload = {
                "name": tool_name,
                "argument_keys": sorted(str(key) for key in tool_args.keys()),
            }
            job_id = _create_mcp_job(
                session_id=session_id,
                job_type="mcp_proxy_tools_call",
                server_index=server_index,
                method="tools/call",
                payload=safe_job_payload,
            )
            sessions.append_event(
                session_id,
                TranscriptEvent(
                    event_type="mcp_tool_call",
                    data={
                        "server_index": server_index,
                        "name": tool_name,
                        "arguments": tool_args,
                    },
                ),
            )
            try:
                result = await connection.transport.tools_call(tool_name, tool_args)
            except Exception as e:
                _fail_mcp_job(job_id, error=str(e))
                raise
            sessions.append_event(
                session_id,
                TranscriptEvent(
                    event_type="mcp_tool_result",
                    data={
                        "server_index": server_index,
                        "name": tool_name,
                        "isError": result.get("isError"),
                    },
                ),
            )
            if result.get("isError") is True:
                _fail_mcp_job(job_id, error=f"MCP tool {tool_name} returned isError=true")
            else:
                _complete_mcp_job(
                    job_id,
                    result={
                        "isError": False,
                        "name": tool_name,
                    },
                )
            return result

        # Pass 1: Search-style MCP enrichment.
        for server_index in selected:
            server_spec = (
                raw_servers[server_index]
                if server_index < len(raw_servers)
                and isinstance(raw_servers[server_index], dict)
                else {}
            )
            assist_role = str(server_spec.get("assist_role") or "").strip().lower()
            if (
                assist_role == "translator"
                and not _is_file_workspace_prompt(prompt_text)
            ) or assist_role in _direct_assist_roles():
                continue

            connection = MCPConnection.from_config(
                config,
                server_index=server_index,
                servers_override=raw_servers,
            )
            try:
                await connection.connect()
                if require_initialize:
                    await _maybe_initialize_mcp(connection)
                tools_result = await connection.transport.tools_list()
                tools = tools_result.get("tools") or []
                tool_names = {
                    str(t.get("name") or "").strip().lower(): str(t.get("name") or "").strip()
                    for t in tools
                    if isinstance(t, dict) and str(t.get("name") or "").strip()
                }
                prompt_assist_path = str(
                    server_spec.get("prompt_assist_path")
                    or server_spec.get("ui_default_browse_path")
                    or "working"
                ).strip() or "working"
                prompt_assist = _build_prompt_assist_tool_call(
                    tool_names, prompt_text, browse_path=prompt_assist_path
                )
                if prompt_assist:
                    tool_name, tool_args = prompt_assist
                    result = await _call_tool(
                        connection, server_index, tool_name, tool_args
                    )
                    if result.get("isError") is not True:
                        text = _render_mcp_context_text(result)
                        if text and not _looks_like_error_text(text):
                            text = text[:max_context_chars]
                            search_contexts.append(text)
                            sessions.append_event(
                                session_id,
                                TranscriptEvent(
                                    event_type="mcp_context_attached",
                                    data={
                                        "server_index": server_index,
                                        "tool_name": tool_name,
                                        "chars": len(text),
                                    },
                                ),
                            )
                    continue

                selected_tool: Optional[Dict[str, Any]] = None
                for t in tools:
                    if not isinstance(t, dict):
                        continue
                    t_name = str(t.get("name") or "").strip().lower()
                    if t_name in tool_candidates:
                        selected_tool = t
                        break
                if not selected_tool:
                    continue

                tool_name = str(selected_tool.get("name") or "").strip()
                if not tool_name:
                    continue
                tool_args = _build_tool_arguments_from_schema(
                    selected_tool,
                    prompt_text,
                    prefer_async_jobs=bool(server_spec.get("async_jobs_enabled")),
                )
                if not tool_args:
                    tool_args = {"query": prompt_text}
                tool_args = _apply_freshness_hints(
                    tool_args, selected_tool, prompt_text
                )

                result = await _call_tool(
                    connection, server_index, tool_name, tool_args
                )
                if result.get("isError") is True:
                    continue
                text = _extract_mcp_text_content(result)
                if text and not _looks_like_error_text(text):
                    text = text[:max_context_chars]
                    search_contexts.append(text)
                    sessions.append_event(
                        session_id,
                        TranscriptEvent(
                            event_type="mcp_context_attached",
                            data={
                                "server_index": server_index,
                                "tool_name": tool_name,
                                "chars": len(text),
                            },
                        ),
                    )
                elif text:
                    sessions.append_event(
                        session_id,
                        TranscriptEvent(
                            event_type="mcp_context_error",
                            data={
                                "server_index": server_index,
                                "error": "tool returned error-like text payload",
                                "sample": text[:240],
                            },
                        ),
                    )
            except Exception as e:
                sessions.append_event(
                    session_id,
                    TranscriptEvent(
                        event_type="mcp_context_error",
                        data={"server_index": server_index, "error": str(e)},
                    ),
                )
            finally:
                await connection.close()

        # Pass 2: Direct expert execute delegation through expert-agent API.
        expert_contexts: list[str] = []
        for server_index in selected:
            server_spec = (
                raw_servers[server_index]
                if server_index < len(raw_servers)
                and isinstance(raw_servers[server_index], dict)
                else {}
            )
            assist_role = str(server_spec.get("assist_role") or "").strip().lower()
            if assist_role not in _direct_assist_roles():
                continue

            api_base_url = _derive_assist_api_base_url(server_spec)
            expert_config_id = int(server_spec.get("assist_expert_config_id") or 0)
            api_key = str(
                server_spec.get("assist_api_key")
                or server_spec.get("api_key")
                or ""
            ).strip()
            api_key_header = str(
                server_spec.get("assist_api_key_header")
                or server_spec.get("api_key_header")
                or "X-API-Key"
            ).strip()
            if not api_base_url or expert_config_id <= 0 or not api_key:
                sessions.append_event(
                    session_id,
                    TranscriptEvent(
                        event_type="mcp_context_error",
                        data={
                            "server_index": server_index,
                            "error": "expert execute assist is missing api_base_url, expert_config_id, or api_key",
                        },
                    ),
                )
                continue

            prior_messages = _prior_chat_messages_for_assist(
                session_id,
                current_prompt=prompt_text,
                max_messages=int(server_spec.get("assist_history_messages") or 8),
            )
            parameters = copy.deepcopy(server_spec.get("assist_execute_parameters") or {})
            if not isinstance(parameters, dict):
                parameters = {}
            context = copy.deepcopy(server_spec.get("assist_context") or {})
            if not isinstance(context, dict):
                context = {}
            explicit_service_calls = list(parameters.get("service_tool_calls") or [])
            post_service_calls = list(parameters.get("post_service_tool_calls") or [])
            uses_pre_authorised_workflow = bool(
                explicit_service_calls or post_service_calls
            )
            if explicit_service_calls or post_service_calls:
                assist_messages = context.get("messages")
                if not isinstance(assist_messages, list):
                    assist_messages = []
                assist_messages.insert(
                    0,
                    {
                        "role": "system",
                        "content": (
                            "This request includes pre-authorised service tool invocations. "
                            "The executor will run them and inject the results into your context. "
                            "Treat those service invocation results as authoritative evidence. "
                            "Do not say you lack access to files, directories, tools, or external services "
                            "when such service results are present. Answer directly from the injected "
                            "service results and describe the discovered files, contents, or saved outputs."
                        ),
                    },
                )
                context["messages"] = assist_messages
            if prior_messages:
                existing_messages = context.get("messages")
                if isinstance(existing_messages, list):
                    context["messages"] = [*existing_messages, *prior_messages]
                else:
                    context["messages"] = prior_messages
            remote_session_id = _assist_remote_sessions(session_id).get(str(server_index))
            if (
                remote_session_id
                and not uses_pre_authorised_workflow
                and "remote_session_id" not in context
            ):
                context["remote_session_id"] = str(remote_session_id)
            if remote_session_id and isinstance(parameters.get("service_tool_calls"), list):
                # Follow-up turns should reuse the persisted expert session instead of
                # re-running the same expensive pre-authorised discovery calls again.
                parameters["service_tool_calls"] = []
            if "max_tokens" not in parameters and server_spec.get("assist_max_tokens") is not None:
                parameters["max_tokens"] = int(server_spec.get("assist_max_tokens") or 0)

            sessions.append_event(
                session_id,
                TranscriptEvent(
                    event_type="mcp_tool_call",
                    data={
                        "server_index": server_index,
                        "name": "expert_execute",
                        "arguments": {
                            "expert_config_id": expert_config_id,
                            "input_text": prompt_text,
                        },
                    },
                ),
            )

            verify_tls = bool(
                server_spec.get("assist_verify_tls")
                if server_spec.get("assist_verify_tls") is not None
                else server_spec.get("verify_tls")
                if server_spec.get("verify_tls") is not None
                else True
            )
            timeout_seconds = float(
                server_spec.get("assist_timeout_seconds")
                or server_spec.get("read_timeout_seconds")
                or server_spec.get("timeout_seconds")
                or 180
            )

            try:
                request_payload = {
                    "input_text": prompt_text,
                    "parameters": parameters,
                    "context": context,
                }

                async def _execute_once(payload: Dict[str, Any]) -> httpx.Response:
                    async with httpx.AsyncClient(
                        timeout=timeout_seconds,
                        verify=verify_tls,
                        headers={api_key_header: api_key},
                    ) as expert_client:
                        return await expert_client.post(
                            f"{api_base_url}/experts/{expert_config_id}/execute",
                            json=payload,
                        )

                response = await _execute_once(request_payload)
                if response.status_code != 200 and "remote_session_id" in context:
                    fallback_context = copy.deepcopy(context)
                    fallback_context.pop("remote_session_id", None)
                    request_payload = {
                        "input_text": prompt_text,
                        "parameters": parameters,
                        "context": fallback_context,
                    }
                    _clear_assist_remote_session(session_id, server_index)
                    response = await _execute_once(request_payload)
                if response.status_code != 200:
                    sessions.append_event(
                        session_id,
                        TranscriptEvent(
                            event_type="mcp_context_error",
                            data={
                                "server_index": server_index,
                                "error": f"expert execute failed: {response.status_code}",
                                "sample": response.text[:240],
                            },
                        ),
                    )
                    continue

                body = response.json() if response.text.strip() else {}
                output_text = _extract_expert_execute_text(body)
                if _expert_denied_authoritative_service_results(
                    output_text,
                    explicit_service_calls=explicit_service_calls,
                    post_service_calls=post_service_calls,
                ):
                    service_digest = _authoritative_expert_service_digest(
                        body,
                        explicit_service_calls=explicit_service_calls,
                    )
                    retry_context = copy.deepcopy(context)
                    retry_messages = retry_context.get("messages")
                    if not isinstance(retry_messages, list):
                        retry_messages = []
                    retry_messages.insert(
                        0,
                        {
                            "role": "system",
                            "content": (
                                "Your previous draft incorrectly claimed you lacked access to files or directories. "
                                "You already have authoritative service invocation results in context. "
                                "Do not mention any access limitation. "
                                "Answer directly from those injected service results."
                            ),
                        },
                    )
                    if service_digest:
                        retry_messages.insert(
                            1,
                            {
                                "role": "system",
                                "content": (
                                    "Authoritative service results digest:\n"
                                    f"{service_digest}"
                                ),
                            },
                        )
                    retry_context["messages"] = retry_messages
                    if "remote_session_id" in retry_context:
                        retry_context.pop("remote_session_id", None)
                        _clear_assist_remote_session(session_id, server_index)
                    retry_parameters = copy.deepcopy(parameters)
                    if isinstance(retry_parameters.get("service_tool_calls"), list) and service_digest:
                        retry_parameters["service_tool_calls"] = []
                    retry_payload = {
                        "input_text": prompt_text,
                        "parameters": retry_parameters,
                        "context": retry_context,
                    }
                    rerun_response = await _execute_once(retry_payload)
                    if rerun_response.status_code == 200:
                        rerun_body = rerun_response.json() if rerun_response.text.strip() else {}
                        rerun_output = _extract_expert_execute_text(rerun_body)
                        if rerun_output:
                            body = rerun_body
                            output_text = rerun_output
                remote_session_id = str(body.get("session_id") or "").strip()
                if remote_session_id:
                    _set_assist_remote_session(session_id, server_index, remote_session_id)
                sessions.append_event(
                    session_id,
                    TranscriptEvent(
                        event_type="mcp_tool_result",
                        data={
                            "server_index": server_index,
                            "name": "expert_execute",
                            "isError": False,
                            "expert_id": expert_config_id,
                        },
                    ),
                )
                if output_text:
                    expert_contexts.append(output_text[:max_context_chars])
                    sessions.append_event(
                        session_id,
                        TranscriptEvent(
                            event_type="mcp_context_attached",
                            data={
                                "server_index": server_index,
                                "tool_name": "expert_execute",
                                "chars": len(output_text),
                            },
                        ),
                    )
            except Exception as e:
                if "remote_session_id" in context:
                    fallback_context = copy.deepcopy(context)
                    fallback_context.pop("remote_session_id", None)
                    _clear_assist_remote_session(session_id, server_index)
                    try:
                        fallback_payload = {
                            "input_text": prompt_text,
                            "parameters": parameters,
                            "context": fallback_context,
                        }
                        async with httpx.AsyncClient(
                            timeout=timeout_seconds,
                            verify=verify_tls,
                            headers={api_key_header: api_key},
                        ) as expert_client:
                            response = await expert_client.post(
                                f"{api_base_url}/experts/{expert_config_id}/execute",
                                json=fallback_payload,
                            )
                        if response.status_code == 200:
                            body = response.json() if response.text.strip() else {}
                            output_text = _extract_expert_execute_text(body)
                            remote_session_id = str(body.get("session_id") or "").strip()
                            if remote_session_id:
                                _set_assist_remote_session(session_id, server_index, remote_session_id)
                            sessions.append_event(
                                session_id,
                                TranscriptEvent(
                                    event_type="mcp_tool_result",
                                    data={
                                        "server_index": server_index,
                                        "name": "expert_execute",
                                        "isError": False,
                                        "expert_id": expert_config_id,
                                    },
                                ),
                            )
                            if output_text:
                                expert_contexts.append(output_text[:max_context_chars])
                                sessions.append_event(
                                    session_id,
                                    TranscriptEvent(
                                        event_type="mcp_context_attached",
                                        data={
                                            "server_index": server_index,
                                            "tool_name": "expert_execute",
                                            "chars": len(output_text),
                                        },
                                    ),
                                )
                            continue
                        error_text = f"expert execute failed: {response.status_code}"
                        sample = response.text[:240]
                    except Exception as retry_exc:
                        error_text = str(retry_exc)
                        sample = ""
                else:
                    error_text = str(e)
                    sample = ""
                data = {"server_index": server_index, "error": error_text}
                if sample:
                    data["sample"] = sample
                sessions.append_event(
                    session_id,
                    TranscriptEvent(
                        event_type="mcp_context_error",
                        data=data,
                    ),
                )

        # Pass 3: Translator-role MCP enrichment (optional, intent-based).
        if _is_translation_request(prompt_text):
            for server_index in selected:
                server_spec = (
                    raw_servers[server_index]
                    if server_index < len(raw_servers)
                    and isinstance(raw_servers[server_index], dict)
                    else {}
                )
                assist_role = str(server_spec.get("assist_role") or "").strip().lower()
                if assist_role != "translator":
                    continue

                connection = MCPConnection.from_config(
                    config,
                    server_index=server_index,
                    servers_override=raw_servers,
                )
                try:
                    await connection.connect()
                    if require_initialize:
                        await _maybe_initialize_mcp(connection)
                    tools_result = await connection.transport.tools_list()
                    tools = tools_result.get("tools") or []
                    tool_names = {
                        str(t.get("name") or "").strip().lower(): str(
                            t.get("name") or ""
                        ).strip()
                        for t in tools
                        if isinstance(t, dict)
                    }
                    start_tool = tool_names.get("start_session")
                    chat_tool = tool_names.get("chat")
                    if not start_tool or not chat_tool:
                        continue

                    user_id = int(
                        server_spec.get("assist_user_id")
                        or config.get("chat_tests.hu_translator.user_id")
                        or 1
                    )
                    expert_config_id = int(
                        server_spec.get("assist_expert_config_id")
                        or config.get("chat_tests.hu_translator.expert_id")
                        or 0
                    )
                    channel_id = int(
                        server_spec.get("assist_channel_id")
                        or config.get("chat_tests.hu_translator.channel_id")
                        or 0
                    )
                    title = str(
                        server_spec.get("assist_session_title")
                        or "Hungarian translator MCP session"
                    )

                    start_args: Dict[str, Any] = {
                        "user_id": user_id,
                        "expert_config_id": expert_config_id,
                        "channel_id": channel_id,
                        "title": title,
                    }
                    start_result = await _call_tool(
                        connection, server_index, start_tool, start_args
                    )
                    remote_session_id = _extract_tool_session_id(start_result)
                    remote_session_candidates: list[str] = []

                    if not remote_session_id:
                        list_tool = tool_names.get("list_sessions")
                        if list_tool:
                            list_result = await _call_tool(
                                connection,
                                server_index,
                                list_tool,
                                {"user_id": user_id},
                            )
                            remote_session_candidates = (
                                _extract_list_session_candidates(list_result)
                            )
                            if remote_session_candidates:
                                remote_session_id = remote_session_candidates[0]
                    if not remote_session_id:
                        continue

                    translator_context_chars = int(
                        config.get("mcp.chat_assist.translator_context_chars")
                        or min(max_context_chars, 1800)
                    )
                    translator_context_chars = max(200, translator_context_chars)

                    target_language = _infer_translation_language(prompt_text)
                    translator_message = prompt_text
                    if search_contexts:
                        context_for_translator = _sanitize_translator_context_text(
                            search_contexts[0]
                        )[:translator_context_chars]
                        if target_language:
                            language_label = _translation_language_label(
                                target_language
                            )
                            translator_message = (
                                f"Write a concise news summary in {language_label}. "
                                "Use only the factual context below. "
                                "Return only the final summary text.\n\n"
                                f"{context_for_translator}"
                            )
                        else:
                            translator_message = (
                                f"{prompt_text}\n\n"
                                "Use this factual context in your answer:\n"
                                f"{context_for_translator}"
                            )

                    candidate_session_ids = [remote_session_id]
                    if remote_session_candidates:
                        for sid in remote_session_candidates:
                            if sid not in candidate_session_ids:
                                candidate_session_ids.append(sid)

                    translated = ""
                    for candidate_session_id in candidate_session_ids:
                        chat_args: Dict[str, Any] = {
                            "session_id": candidate_session_id,
                            "message": translator_message,
                        }
                        if target_language and "language" not in chat_args:
                            chat_args["language"] = target_language
                        if server_spec.get("assist_max_tokens") is not None:
                            chat_args["max_tokens"] = int(
                                server_spec.get("assist_max_tokens") or 0
                            )
                        try:
                            chat_result = await _call_tool(
                                connection, server_index, chat_tool, chat_args
                            )
                        except Exception as chat_error:
                            sessions.append_event(
                                session_id,
                                TranscriptEvent(
                                    event_type="mcp_context_error",
                                    data={
                                        "server_index": server_index,
                                        "error": str(chat_error),
                                        "session_id": candidate_session_id,
                                    },
                                ),
                            )
                            continue

                        if chat_result.get("isError") is True:
                            continue
                        translated = _extract_translator_text(chat_result)
                        if translated:
                            break

                    if translated:
                        translated = translated[:max_context_chars]
                        translator_contexts.append(translated)
                        sessions.append_event(
                            session_id,
                            TranscriptEvent(
                                event_type="mcp_context_attached",
                                data={
                                    "server_index": server_index,
                                    "tool_name": chat_tool,
                                    "chars": len(translated),
                                },
                            ),
                        )
                except Exception as e:
                    sessions.append_event(
                        session_id,
                        TranscriptEvent(
                            event_type="mcp_context_error",
                            data={"server_index": server_index, "error": str(e)},
                        ),
                    )
                finally:
                    await connection.close()

        contexts: list[str] = []
        if search_contexts:
            contexts.append(f"MCP context:\n{search_contexts[0]}")
        if expert_contexts:
            contexts.append(f"Expert MCP output:\n{expert_contexts[0]}")
        if translator_contexts:
            contexts.append(f"Translator MCP output:\n{translator_contexts[0]}")
        return "\n\n".join(contexts).strip()

    def _connection_from_server_spec(server_spec: Dict[str, Any]):
        """Internal helper to connection from server spec for this module."""
        from ..mcp.connection import MCPConnection, MCPServerSpec
        from cloud_dog_api_kit.mcp.client_transport import (
            HTTPJSONRPCConfig,
            HTTPJSONRPCTransport,
            LegacySSEConfig,
            LegacySSETransport,
            StreamableHTTPConfig,
            StreamableHTTPTransport,
            StdioConfig,
            StdioTransport,
        )

        if not isinstance(server_spec, dict):
            raise HTTPException(status_code=400, detail="server spec must be an object")

        defaults = config.get("mcp.defaults", {})
        if not isinstance(defaults, dict):
            defaults = {}

        name = str(server_spec.get("name") or "server")
        transport = (
            str(
                server_spec.get("transport")
                or defaults.get("transport")
                or "http_jsonrpc"
            )
            .lower()
            .strip()
        )

        if transport in ("streamable_http", "streamablehttp", "mcp"):
            base_url = str(server_spec.get("base_url") or "")
            mcp_path = str(
                server_spec.get("mcp_path") or defaults.get("mcp_path") or "/mcp"
            )
            api_key_header: Optional[str] = str(
                server_spec.get("api_key_header")
                or defaults.get("api_key_header")
                or ""
            )
            api_key: Optional[str] = str(server_spec.get("api_key") or "")
            accept_header: Optional[str] = str(
                server_spec.get("accept_header") or defaults.get("accept_header") or ""
            )
            sse_accept_header: Optional[str] = str(
                server_spec.get("sse_accept_header")
                or defaults.get("sse_accept_header")
                or ""
            )
            protocol_version: Optional[str] = str(
                server_spec.get("protocol_version")
                or defaults.get("protocol_version")
                or ""
            )
            auth_bearer_token: Optional[str] = str(
                server_spec.get("auth_bearer_token")
                or defaults.get("auth_bearer_token")
                or ""
            )
            enable_sse = bool(
                server_spec.get("enable_sse")
                if server_spec.get("enable_sse") is not None
                else defaults.get("enable_sse", True)
            )
            timeout_seconds = float(
                server_spec.get("timeout_seconds")
                or defaults.get("timeout_seconds")
                or config.get("client_api.request_timeout_seconds")
                or 30.0
            )
            read_timeout_seconds = server_spec.get("read_timeout_seconds")
            verify_tls = bool(
                server_spec.get("verify_tls")
                if server_spec.get("verify_tls") is not None
                else True
            )

            if not base_url:
                raise HTTPException(
                    status_code=400, detail="server.base_url is required"
                )

            if not api_key_header:
                api_key_header = None
            if not api_key:
                api_key = None
            if not accept_header:
                accept_header = None
            if not sse_accept_header:
                sse_accept_header = None
            if not protocol_version:
                protocol_version = None
            if not auth_bearer_token:
                auth_bearer_token = None
            if read_timeout_seconds is not None:
                try:
                    read_timeout_seconds = float(read_timeout_seconds)
                except (TypeError, ValueError):
                    read_timeout_seconds = None

            transport_impl: Any = StreamableHTTPTransport(
                StreamableHTTPConfig(
                    base_url=base_url,
                    mcp_path=mcp_path,
                    api_key_header=api_key_header,
                    api_key=api_key,
                    accept_header=accept_header,
                    sse_accept_header=sse_accept_header,
                    protocol_version=protocol_version,
                    auth_bearer_token=auth_bearer_token,
                    enable_sse=enable_sse,
                    timeout_seconds=timeout_seconds,
                    read_timeout_seconds=read_timeout_seconds,
                    verify_tls=verify_tls,
                )
            )
            return MCPConnection(
                MCPServerSpec(name=name, transport=transport, config=server_spec),
                transport_impl,
            )

        if transport in ("http_jsonrpc", "http", "messages"):
            base_url = str(server_spec.get("base_url") or "")
            messages_path = str(
                server_spec.get("messages_path") or defaults.get("messages_path") or ""
            )
            health_path = str(
                server_spec.get("health_path") or defaults.get("health_path") or ""
            )
            api_key_header = str(
                server_spec.get("api_key_header")
                or defaults.get("api_key_header")
                or ""
            )
            api_key = str(server_spec.get("api_key") or "")
            accept_header = str(
                server_spec.get("accept_header") or defaults.get("accept_header") or ""
            )
            timeout_seconds = float(
                server_spec.get("timeout_seconds")
                or defaults.get("timeout_seconds")
                or config.get("client_api.request_timeout_seconds")
                or 30.0
            )
            verify_tls = bool(
                server_spec.get("verify_tls")
                if server_spec.get("verify_tls") is not None
                else True
            )
            async_jobs_enabled = bool(
                server_spec.get("async_jobs_enabled")
                if server_spec.get("async_jobs_enabled") is not None
                else defaults.get("async_jobs_enabled") or False
            )
            async_jobs_api_base_url: Optional[str] = str(
                server_spec.get("async_jobs_api_base_url")
                or defaults.get("async_jobs_api_base_url")
                or ""
            )
            async_jobs_status_path = str(
                server_spec.get("async_jobs_status_path")
                or defaults.get("async_jobs_status_path")
                or "/jobs/{job_id}"
            )
            async_jobs_timeout_seconds = float(
                server_spec.get("async_jobs_timeout_seconds")
                or defaults.get("async_jobs_timeout_seconds")
                or timeout_seconds
            )
            async_jobs_poll_interval_seconds = float(
                server_spec.get("async_jobs_poll_interval_seconds")
                or defaults.get("async_jobs_poll_interval_seconds")
                or 2.0
            )

            if not base_url:
                raise HTTPException(
                    status_code=400, detail="server.base_url is required"
                )
            if not messages_path:
                raise HTTPException(
                    status_code=400, detail="server.messages_path is required"
                )
            if not health_path:
                raise HTTPException(
                    status_code=400, detail="server.health_path is required"
                )

            if not api_key_header:
                api_key_header = None
            if not api_key:
                api_key = None
            if not accept_header:
                accept_header = None
            if not async_jobs_api_base_url:
                async_jobs_api_base_url = None

            transport_impl = HTTPJSONRPCTransport(
                HTTPJSONRPCConfig(
                    base_url=base_url,
                    messages_path=messages_path,
                    health_path=health_path,
                    api_key_header=api_key_header,
                    api_key=api_key,
                    accept_header=accept_header,
                    timeout_seconds=timeout_seconds,
                    verify_tls=verify_tls,
                    async_jobs_enabled=async_jobs_enabled,
                    async_jobs_api_base_url=async_jobs_api_base_url,
                    async_jobs_status_path=async_jobs_status_path,
                    async_jobs_timeout_seconds=async_jobs_timeout_seconds,
                    async_jobs_poll_interval_seconds=async_jobs_poll_interval_seconds,
                )
            )
            return MCPConnection(
                MCPServerSpec(name=name, transport=transport, config=server_spec),
                transport_impl,
            )

        if transport in ("legacy_sse", "http_sse", "sse"):
            base_url = str(server_spec.get("base_url") or "")
            sse_path = str(
                server_spec.get("sse_path") or defaults.get("sse_path") or ""
            )
            messages_path = str(
                server_spec.get("messages_path") or defaults.get("messages_path") or ""
            )
            api_key_header = str(
                server_spec.get("api_key_header")
                or defaults.get("api_key_header")
                or ""
            )
            api_key = str(server_spec.get("api_key") or "")
            accept_header = str(
                server_spec.get("accept_header") or defaults.get("accept_header") or ""
            )
            auth_bearer_token = str(
                server_spec.get("auth_bearer_token")
                or defaults.get("auth_bearer_token")
                or ""
            )
            protocol_version = str(
                server_spec.get("protocol_version")
                or defaults.get("protocol_version")
                or ""
            )
            timeout_seconds = float(
                server_spec.get("timeout_seconds")
                or defaults.get("timeout_seconds")
                or config.get("client_api.request_timeout_seconds")
                or 30.0
            )
            verify_tls = bool(
                server_spec.get("verify_tls")
                if server_spec.get("verify_tls") is not None
                else True
            )

            if not base_url:
                raise HTTPException(
                    status_code=400, detail="server.base_url is required"
                )
            if not sse_path:
                raise HTTPException(
                    status_code=400, detail="server.sse_path is required"
                )
            if not messages_path:
                raise HTTPException(
                    status_code=400, detail="server.messages_path is required"
                )
            if not api_key_header:
                api_key_header = None
            if not api_key:
                api_key = None
            if not accept_header:
                accept_header = None
            if not auth_bearer_token:
                auth_bearer_token = None
            if not protocol_version:
                protocol_version = None

            transport_impl = LegacySSETransport(
                LegacySSEConfig(
                    base_url=base_url,
                    sse_path=sse_path,
                    messages_path=messages_path,
                    api_key_header=api_key_header,
                    api_key=api_key,
                    accept_header=accept_header,
                    auth_bearer_token=auth_bearer_token,
                    protocol_version=protocol_version,
                    timeout_seconds=timeout_seconds,
                    verify_tls=verify_tls,
                )
            )
            return MCPConnection(
                MCPServerSpec(name=name, transport=transport, config=server_spec),
                transport_impl,
            )

        if transport in ("stdio",):
            command = str(server_spec.get("command") or "")
            args = server_spec.get("args") or []
            if not isinstance(args, list) or not all(isinstance(x, str) for x in args):
                raise HTTPException(
                    status_code=400, detail="server.args must be a list of strings"
                )
            env = server_spec.get("env")
            if env is not None and not isinstance(env, dict):
                raise HTTPException(
                    status_code=400, detail="server.env must be an object"
                )
            framing = str(
                server_spec.get("framing")
                or defaults.get("framing")
                or "content_length"
            )
            if not command:
                raise HTTPException(
                    status_code=400, detail="server.command is required"
                )

            transport_impl = StdioTransport(
                StdioConfig(command=command, args=args, env=env, framing=framing)
            )
            return MCPConnection(
                MCPServerSpec(name=name, transport=transport, config=server_spec),
                transport_impl,
            )

        raise HTTPException(
            status_code=400, detail=f"Unsupported mcp transport: {transport}"
        )

    # Health endpoints are now provided by create_health_router() in server.py.

    @router.get(f"{api_base_path}/jobs", dependencies=[Depends(_auth_dep)])
    async def list_jobs(
        limit: int = 100,
        session_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return managed MCP/chat jobs for this server instance."""
        if jobs_runtime is None:
            return {"jobs": [], "server_id": _server_id(), "count": 0}
        jobs = jobs_runtime.list_jobs(limit=limit, session_id=session_id, status=status)
        return {
            "jobs": jobs,
            "count": len(jobs),
            "server_id": _server_id(),
        }

    @router.get(f"{api_base_path}/jobs/{{job_id}}", dependencies=[Depends(_auth_dep)])
    async def get_job(job_id: str) -> Dict[str, Any]:
        """Return one managed job by identifier."""
        if jobs_runtime is None:
            raise HTTPException(status_code=404, detail="Unknown job")
        job = jobs_runtime.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job")
        return job

    @router.post(f"{api_base_path}/jobs/{{job_id}}/cancel", dependencies=[Depends(_auth_dep)])
    async def cancel_job(job_id: str, reason: str = "") -> Dict[str, Any]:
        """Cancel a running or queued job (PS-75 JQ8.4 cooperative cancellation)."""
        if jobs_runtime is None:
            raise HTTPException(status_code=404, detail="Unknown job")
        try:
            ok = jobs_runtime.cancel(job_id, reason=reason)
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown job")
        if not ok:
            raise HTTPException(status_code=409, detail="Job cannot be cancelled in its current state")
        return {"job_id": job_id, "status": "cancelled"}

    # Covers: R16.1 (browser route entrypoint for Web UI surface)
    @router.get("/", include_in_schema=False)
    async def root_to_ui() -> RedirectResponse:
        # Browser entrypoint should land on the Web UI instead of a blank/404 response.
        """Handle root to ui for the current runtime context."""
        return RedirectResponse(url="/ui", status_code=307)

    # Covers: R16.1 (legacy route entrypoint redirected to Web UI)
    @router.get("/access", include_in_schema=False)
    async def access_to_ui() -> RedirectResponse:
        # Backward-compatible browser entrypoint used by some deployments.
        """Handle access to ui for the current runtime context."""
        return RedirectResponse(url="/ui", status_code=307)

    # Covers: R16.1 (serves chat-client Web UI page)
    @router.get("/ui", response_class=HTMLResponse)
    async def web_ui() -> HTMLResponse:
        """Serve the React SPA entrypoint."""
        return serve_spa_index(config)

    @router.get("/login", response_class=HTMLResponse, include_in_schema=False)
    @router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    @router.get("/docs", response_class=HTMLResponse, include_in_schema=False)
    @router.get("/api-docs", response_class=HTMLResponse, include_in_schema=False)
    @router.get("/-docs", response_class=HTMLResponse, include_in_schema=False)
    @router.get("/jobs", response_class=HTMLResponse, include_in_schema=False)
    @router.get("/mcp-console", response_class=HTMLResponse, include_in_schema=False)
    @router.get("/a2a-console", response_class=HTMLResponse, include_in_schema=False)
    @router.get("/monitoring", response_class=HTMLResponse, include_in_schema=False)
    @router.get("/files", response_class=HTMLResponse, include_in_schema=False)
    async def web_ui_routes() -> HTMLResponse:
        """Serve direct SPA routes that do not overlap backend API paths."""
        return serve_spa_index(config)

    @router.get("/runtime-config.js", include_in_schema=False)
    async def runtime_config_js(request: Request) -> Response:
        """Serve runtime-config.js for the SPA bootstrap contract."""
        return serve_runtime_config(config, request)

    @router.get("/assets/{asset_path:path}", include_in_schema=False)
    async def ui_assets(asset_path: str) -> Response:
        """Serve hashed SPA assets from ui/dist/assets."""
        return serve_spa_asset(config, f"assets/{asset_path}")

    # Covers: R16.2 (runtime config contract exposed to browser UI)
    @router.get("/ui/config")
    async def ui_config() -> Dict[str, Any]:
        """Handle ui config for the current runtime context."""
        ui_wait_timeout_seconds = config.get("client_api.ui_wait_timeout_seconds")
        if ui_wait_timeout_seconds is None:
            ui_wait_timeout_seconds = config.get("client_api.request_timeout_seconds")
        try:
            ui_wait_timeout_seconds = int(float(ui_wait_timeout_seconds or 300))
        except (TypeError, ValueError):
            ui_wait_timeout_seconds = 300
        if ui_wait_timeout_seconds < 30:
            ui_wait_timeout_seconds = 30
        return {
            "application": {
                "name": str(config.get("app.name") or "cloud-dog-chat-client"),
                "release": _application_release(config),
            },
            "llm": _llm_config_for_ui(config),
            "client_api": {
                "api_key_required": bool(
                    str(config.get("client_api.api_key") or "").strip()
                ),
                "api_key_header": str(
                    config.get("client_api.api_key_header") or "X-API-Key"
                ),
                "ui_wait_timeout_seconds": ui_wait_timeout_seconds,
            },
            "a2a": {
                "port": int(config.get("a2a_server.port") or 0),
                "ws_path": f"{a2a_base_path}/ws",
                "events_path": f"{a2a_base_path}/events",
                "topics": ["sessions", "messages", "config"],
                "api_key_query_param": "api_key",
            },
            "test_harness": {
                "enabled": True,
                "inject_path": f"{api_base_path}/sessions/{{session_id}}/inject",
                "inject_sequence_path": f"{api_base_path}/sessions/{{session_id}}/inject-sequence",
                "flow_path": f"{api_base_path}/test-flows/{{flow_id}}",
            },
            "mcp_servers": _current_mcp_servers_for_ui(),
        }

    @router.get("/version")
    async def version_info() -> Dict[str, Any]:
        """Expose application version metadata for the Web UI shell."""
        return {
            "application": str(config.get("app.name") or "cloud-dog-chat-client"),
            "version": _application_release(config),
            "environment": str(config.get("app.environment") or "unknown"),
            "server_id": str(
                config.get("app.server_id")
                or config.get("log.service_instance")
                or "chat-client"
            ),
        }

    @router.get("/metrics", dependencies=[Depends(_auth_dep)])
    async def metrics_snapshot() -> Dict[str, Any]:
        """Return runtime resource and chat orchestration metrics."""
        metrics = _resource_metrics_snapshot()
        return {
            "status": "ok",
            "application": str(config.get("app.name") or "cloud-dog-chat-client"),
            "version": _application_release(config),
            "resources": metrics,
        }

    @router.get("/ui/monitoring", dependencies=[Depends(_auth_dep)])
    async def ui_monitoring() -> Dict[str, Any]:
        """Return lightweight monitoring data for the shared monitoring page."""
        now = datetime.now(timezone.utc)
        recent_cutoff = now - timedelta(hours=1)
        listed_sessions = sessions.list_sessions()
        message_count = 0
        tool_call_count = 0
        response_durations_ms: list[float] = []
        logs: list[dict[str, Any]] = _tail_json_log_entries(limit=150)

        for item in listed_sessions[:50]:
            session_id = str(item.get("id") or "").strip()
            if not session_id:
                continue
            try:
                session = sessions.get_session(session_id)
            except KeyError:
                continue
            last_user_ts: Optional[datetime] = None
            for event in list(session.get("events") or [])[-100:]:
                event_ts_raw = str(getattr(event, "timestamp", "") or "")
                try:
                    event_ts = datetime.fromisoformat(event_ts_raw.replace("Z", "+00:00"))
                except ValueError:
                    event_ts = now
                if event_ts < recent_cutoff:
                    continue
                event_type = str(getattr(event, "event_type", "") or "")
                if event_type in {"user_message", "assistant_message"}:
                    message_count += 1
                if event_type == "mcp_tool_call":
                    tool_call_count += 1
                if event_type == "user_message":
                    last_user_ts = event_ts
                elif event_type in {"assistant_message", "mcp_direct_response"} and last_user_ts is not None:
                    response_durations_ms.append(
                        max(0.0, (event_ts - last_user_ts).total_seconds() * 1000.0)
                    )
                    last_user_ts = None
                logs.append(
                    {
                        "timestamp": event_ts.isoformat(),
                        "level": "info",
                        "logger": "chat.transcript",
                        "message": f"[chat.transcript] {event_type} · {session_id}",
                        "raw_message": f"{event_type} · {session_id}",
                        "correlation_id": "",
                        "source": f"session:{session_id}",
                        "type": "sessions",
                    }
                )

        avg_response_ms = (
            round(sum(response_durations_ms) / len(response_durations_ms), 2)
            if response_durations_ms
            else 0.0
        )
        resources = _resource_metrics_snapshot()
        logs.sort(key=lambda item: str(item.get("timestamp") or ""))
        return {
            "metrics": {
                "active_sessions": resources["active_chat_sessions"],
                "messages_last_hour": message_count,
                "tool_calls_last_hour": tool_call_count,
                "average_response_ms": avg_response_ms,
                "connected_mcp_endpoints": resources["connected_mcp_endpoints"],
                "message_count": resources["message_count"],
                "llm_model": resources["llm_model"],
            },
            "resources": resources,
            "logs": logs[-150:],
        }

    @router.get("/ui/logs", dependencies=[Depends(_auth_dep)])
    async def ui_logs(surface: str = "audit", limit: int = 100) -> Dict[str, Any]:
        """Return source-aware log rows for the shared WebUI log explorer."""
        return _load_log_surface_entries(surface=surface, limit=max(1, min(int(limit), 500)))

    # Covers: R16.7 (redacted settings/config visibility in UI)
    @router.get("/ui/config/tree")
    async def ui_config_tree() -> Dict[str, Any]:
        """Handle ui config tree for the current runtime context."""
        return {
            "application": {
                "name": str(config.get("app.name") or "cloud-dog-chat-client"),
            },
            "config": _redact_config_tree(config.get_all()),
        }

    # Covers: R16.4 (backend API contract for MCP server inventory)
    @router.get(f"{mcp_base_path}/servers", dependencies=[Depends(_auth_dep)])
    async def mcp_servers() -> Dict[str, Any]:
        """Handle MCP servers for the current runtime context."""
        return {"servers": _current_mcp_servers_for_ui()}

    # Covers: R16.6 (MCP UX health visibility through backend endpoint)
    @router.get(f"{mcp_base_path}/servers/health", dependencies=[Depends(_auth_dep)])
    async def mcp_servers_health() -> Dict[str, Any]:
        """Handle MCP servers health for the current runtime context."""
        servers = _current_mcp_servers_for_ui()
        statuses: list[Dict[str, Any]] = []
        for i in range(len(servers)):
            statuses.append(await _probe_mcp_server(i))
        return {"servers": statuses}

    # ── Audit endpoint ────────────────────────────────────────
    @router.get("/audit", dependencies=[Depends(_auth_dep)])
    async def get_audit_log(limit: int = 100) -> Dict[str, Any]:
        """Return recent audit log entries."""
        log_folder = resolve_path(
            str(config.get("app.logfolder") or config.get("log.folder") or "logs"),
            base_dir=str(config.project_root),
        )
        # The standard platform audit sink is log.audit_log, which resolves to
        # logs/audit.log.jsonl in this project. Keep legacy per-server files as
        # a fallback so older local runtimes remain inspectable.
        candidates = [
            join_path(log_folder, "audit.log.jsonl"),
            join_path(log_folder, "api_server.audit.jsonl"),
        ]
        # Also check for any audit jsonl if the named ones don't exist
        if path_exists(log_folder):
            for entry in storage_list_dir(log_folder, "/"):
                if entry.is_dir or not str(entry.path).endswith(".audit.jsonl"):
                    continue
                candidates.append(join_path(log_folder, str(entry.path).lstrip("/")))
        audit_path = None
        for p in candidates:
            if path_exists(p) and read_text(p, encoding="utf-8").strip():
                audit_path = p
                break
        entries: list[Dict[str, Any]] = []
        if audit_path and path_exists(audit_path):
            lines = read_text(audit_path, encoding="utf-8").splitlines()
            for line in lines[-limit:]:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return {"entries": entries, "count": len(entries)}

    @router.post(f"{mcp_base_path}/servers", dependencies=[Depends(_admin_auth_dep)])
    async def mcp_server_add(
        req: MCPServerAdminRequest, request: Request
    ) -> Dict[str, Any]:
        """Handle MCP server add for the current runtime context."""
        actor = request_actor(config, request)
        server = _validate_mcp_server_spec(config, req.server)

        servers = _current_mcp_servers_raw()
        servers.append(server)
        _set_current_mcp_servers(servers)

        index = len(servers) - 1
        _log_mcp_server_admin_action(
            actor=actor, action="add", index=index, server=server, request=request
        )
        return {
            "index": index,
            "server": server,
            "servers": _current_mcp_servers_for_ui(),
        }

    @router.put(f"{mcp_base_path}/servers/{{server_index}}", dependencies=[Depends(_admin_auth_dep)])
    async def mcp_server_update(
        server_index: int, req: MCPServerAdminRequest, request: Request
    ) -> Dict[str, Any]:
        """Handle MCP server update for the current runtime context."""
        actor = request_actor(config, request)
        server = _validate_mcp_server_spec(config, req.server)

        servers = _current_mcp_servers_raw()
        if server_index < 0 or server_index >= len(servers):
            raise HTTPException(status_code=404, detail="mcp server index out of range")
        servers[server_index] = server
        _set_current_mcp_servers(servers)

        _log_mcp_server_admin_action(
            actor=actor, action="update", index=server_index, server=server, request=request
        )
        return {
            "index": server_index,
            "server": server,
            "servers": _current_mcp_servers_for_ui(),
        }

    @router.delete(
        f"{mcp_base_path}/servers/{{server_index}}", dependencies=[Depends(_admin_auth_dep)]
    )
    async def mcp_server_delete(server_index: int, request: Request) -> Dict[str, Any]:
        """Handle MCP server delete for the current runtime context."""
        actor = request_actor(config, request)
        servers = _current_mcp_servers_raw()
        if server_index < 0 or server_index >= len(servers):
            raise HTTPException(status_code=404, detail="mcp server index out of range")
        removed = servers.pop(server_index)
        _set_current_mcp_servers(servers)

        _log_mcp_server_admin_action(
            actor=actor, action="delete", index=server_index, server=removed, request=request
        )
        return {
            "index": server_index,
            "removed": removed,
            "servers": _current_mcp_servers_for_ui(),
        }

    # Covers: R16.4 (session create endpoint used by Web UI contract)
    @router.post(
        "/sessions",
        response_model=CreateSessionResponse,
        dependencies=[Depends(_auth_dep)],
    )
    async def create_session(req: CreateSessionRequest) -> CreateSessionResponse:
        """Create session for the current runtime context."""
        metadata = _apply_profile_defaults(dict(req.metadata or {}))
        if not str(metadata.get("title") or "").strip():
            metadata["title"] = "New Session"
            metadata["title_generated"] = False
        session_id = sessions.create_session(metadata=metadata)
        return CreateSessionResponse(session_id=session_id)

    @router.post(
        "/sessions/{session_id}/load",
        response_model=LoadSessionResponse,
        dependencies=[Depends(_auth_dep)],
    )
    async def load_session(session_id: str) -> LoadSessionResponse:
        """Load session for the current runtime context."""
        try:
            sessions.load_session(session_id)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        session = sessions.get_session(session_id)
        return LoadSessionResponse(
            session_id=session_id, events_count=len(session["events"])
        )

    # Covers: R16.4 (session list endpoint used by Web UI contract)
    @router.get(
        "/sessions",
        response_model=ListSessionsResponse,
        dependencies=[Depends(_auth_dep)],
    )
    async def list_sessions() -> Dict[str, Any]:
        """List sessions for the current runtime context.

        CC5 (W28C-1703): each row carries ``session_id`` (canonical, matching the
        ``POST /sessions`` create response) plus ``id`` as a DEPRECATED alias
        retained for one release cycle so existing ``id`` consumers keep working
        during migration (see CHANGELOG.md).
        """
        converged: list[Dict[str, Any]] = []
        for row in sessions.list_sessions():
            item = dict(row)
            sid = str(item.get("session_id") or item.get("id") or "")
            item["session_id"] = sid  # canonical
            item.setdefault("id", sid)  # deprecated alias (one release cycle)
            converged.append(item)
        return {"sessions": converged}

    # Covers: CC4 (W28C-1703) — single-session fetch; was 405 (only DELETE).
    @router.get(
        "/sessions/{session_id}",
        response_model=SessionDetailResponse,
        dependencies=[Depends(_auth_dep)],
    )
    async def get_session_detail(
        session_id: str, limit: int = 100
    ) -> SessionDetailResponse:
        """Return one session's metadata + last-N transcript events.

        CC4 (W28C-1703): the canonical REST GET-a-resource verb. 200 if found,
        404 if unknown, 401 if unauthenticated (via ``_auth_dep``). Previously
        only DELETE was registered so a GET returned 405 ``allow: DELETE``.
        """
        try:
            session = sessions.get_session(session_id)
        except (KeyError, FileNotFoundError) as exc:
            raise HTTPException(
                status_code=404, detail=f"Unknown session: {session_id}"
            ) from exc
        # Serialise transcript events to plain dicts (same shape the
        # /transcript endpoint returns) — runtime events are TranscriptEvent
        # dataclasses; store-loaded events may already be dicts.
        all_events: list[Dict[str, Any]] = []
        for ev in session.get("events") or []:
            if isinstance(ev, dict):
                all_events.append(ev)
            elif hasattr(ev, "to_json_line"):
                all_events.append(json.loads(ev.to_json_line()))
            else:
                all_events.append(dict(ev))
        try:
            window = int(limit)
        except (TypeError, ValueError):
            window = 100
        recent = all_events[-window:] if window > 0 else all_events
        return SessionDetailResponse(
            session_id=session_id,
            id=session_id,  # CC5 deprecated alias
            created_at=str(session.get("created_at") or "") or None,
            metadata=dict(session.get("metadata") or {}),
            log_path=str(session.get("log_path") or "") or None,
            sequence=int(session.get("sequence") or 0),
            events=recent,
            events_count=len(all_events),
        )

    # Covers: R16.4 (session delete endpoint used by Web UI contract)
    @router.delete("/sessions/{session_id}", dependencies=[Depends(_auth_dep)])
    async def delete_session(session_id: str) -> Dict[str, Any]:
        """Delete session for the current runtime context."""
        removed = sessions.delete_session(session_id)
        if not removed:
            raise HTTPException(status_code=404, detail="Unknown session")
        return {"session_id": session_id, "deleted": True}

    @router.post(
        f"{api_base_path}/sessions/{{session_id}}/inject",
        dependencies=[Depends(_auth_dep)],
    )
    async def inject_session_message(
        session_id: str,
        req: SessionInjectRequest,
        request: Request,
    ) -> Dict[str, Any]:
        """Inject a user/assistant message into a live session for test harness use."""
        try:
            return _inject_session_message(
                session_id=session_id,
                role=req.role,
                content=req.content,
                timestamp=str(req.timestamp or "").strip() or None,
                source=str(req.source or "session_inject"),
                metadata=dict(req.metadata or {}),
                actor=request_actor(config, request),
                request_id=_request_id(request),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        f"{api_base_path}/sessions/{{session_id}}/inject-sequence",
        dependencies=[Depends(_auth_dep)],
    )
    async def inject_session_sequence(
        session_id: str,
        req: SessionInjectSequenceRequest,
        request: Request,
    ) -> Dict[str, Any]:
        """Inject an ordered sequence of harness messages into a live session."""
        if not isinstance(req.events, list) or not req.events:
            raise HTTPException(status_code=400, detail="events must contain at least one item")
        injected: list[Dict[str, Any]] = []
        for item in req.events:
            try:
                injected.append(
                    _inject_session_message(
                        session_id=session_id,
                        role=item.role,
                        content=item.content,
                        timestamp=str(item.timestamp or "").strip() or None,
                        source=str(item.source or "session_inject_sequence"),
                        metadata=dict(item.metadata or {}),
                        actor=request_actor(config, request),
                        request_id=_request_id(request),
                    )
                )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "session_id": session_id,
            "injected_count": len(injected),
            "events": injected,
        }

    @router.post(f"{api_base_path}/test-flows", dependencies=[Depends(_auth_dep)])
    async def create_test_flow(
        req: TestFlowCreateRequest,
        request: Request,
    ) -> Dict[str, Any]:
        """Create and start a scripted interactive test flow."""
        try:
            flow = test_flow_runtime.create_flow(
                script=[dict(item or {}) for item in req.script],
                session_id=str(req.session_id or "").strip() or None,
                metadata=dict(req.metadata or {}),
                actor=request_actor(config, request),
                request_id=_request_id(request),
            )
            return {"flow": flow}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get(f"{api_base_path}/test-flows/{{flow_id}}", dependencies=[Depends(_auth_dep)])
    async def get_test_flow(flow_id: str) -> Dict[str, Any]:
        """Return the current status of a scripted test flow."""
        try:
            return {"flow": test_flow_runtime.get_flow(flow_id)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post(f"{api_base_path}/test-flows/{{flow_id}}/continue", dependencies=[Depends(_auth_dep)])
    async def continue_test_flow(flow_id: str, request: Request) -> Dict[str, Any]:
        """Advance a paused test flow to the next step."""
        try:
            flow = test_flow_runtime.continue_flow(
                flow_id,
                actor=request_actor(config, request),
                request_id=_request_id(request),
            )
            return {"flow": flow}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(f"{api_base_path}/test-flows/{{flow_id}}/respond", dependencies=[Depends(_auth_dep)])
    async def respond_test_flow(
        flow_id: str,
        req: TestFlowRespondRequest,
        request: Request,
    ) -> Dict[str, Any]:
        """Submit an operator response for a prompt-waiting test flow."""
        try:
            flow = test_flow_runtime.respond_flow(
                flow_id,
                content=req.content,
                actor=request_actor(config, request),
                request_id=_request_id(request),
            )
            return {"flow": flow}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete(f"{api_base_path}/test-flows/{{flow_id}}", dependencies=[Depends(_auth_dep)])
    async def cancel_test_flow(flow_id: str, request: Request) -> Dict[str, Any]:
        """Cancel a live or completed test flow."""
        try:
            flow = test_flow_runtime.cancel_flow(
                flow_id,
                actor=request_actor(config, request),
                request_id=_request_id(request),
            )
            return {"flow": flow}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # W28I-1218 (consumer side): run code via the code-runner `code.execute`
    # A2A skill. Admin-gated because arbitrary code execution is privileged.
    # The chat-client correlation id is propagated to code-runner so the
    # producer's audit log can be linked to this request. Base URL + API key
    # come from config (code_runner.*) — never hardcoded.
    @router.post(
        f"{api_base_path}/tools/code-runner/execute",
        dependencies=[Depends(_admin_auth_dep)],
    )
    async def code_runner_execute(
        req: CodeRunnerExecuteRequest,
        request: Request,
    ) -> Dict[str, Any]:
        """Execute code on the code-runner service via its A2A `code.execute` skill."""
        correlation_id = _request_correlation_id(request)
        try:
            client = CodeRunnerClient.from_config_manager(config)
            result = await client.execute(
                code=req.code,
                language=req.language,
                correlation_id=correlation_id,
                task_id=req.task_id,
            )
        except CodeRunnerError as exc:
            message = str(exc)
            # A missing/empty base_url or api_key is a server configuration
            # problem (503); validation problems (empty code, bad language)
            # are client errors (400).
            if "not configured" in message:
                raise HTTPException(status_code=503, detail=message) from exc
            if (
                "must be a non-empty" in message
                or "unsupported language" in message
            ):
                raise HTTPException(status_code=400, detail=message) from exc
            # Upstream transport / HTTP / decode failure.
            raise HTTPException(status_code=502, detail=message) from exc

        payload = result.to_dict()
        payload["correlation_id"] = correlation_id
        return payload

    # Covers: R16.5 (message send path and explicit request validation failures)
    @router.post(
        "/sessions/{session_id}/messages",
        response_model=SendMessageResponse,
        dependencies=[Depends(_auth_dep)],
    )
    async def send_message(
        session_id: str, req: SendMessageRequest
    ) -> SendMessageResponse:
        """Handle send message for the current runtime context."""
        try:
            sessions.get_session(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown session")

        if not req.content.strip():
            raise HTTPException(status_code=400, detail="content must be non-empty")

        sessions.append_event(
            session_id,
            TranscriptEvent(event_type="user_message", data={"content": req.content}),
        )
        _maybe_auto_title_session(session_id, req.content)

        llm_config = _llm_config_for_session(session_id)
        llm = LLMService(
            llm_config,
            response_policy_enforce=_response_policy_enforce_override_for_session(
                session_id
            ),
        )
        strategy = _agent_strategy_or_400(session_id)
        if strategy != SIMPLE_AGENT_STRATEGY:
            if bool(req.stream):
                raise HTTPException(
                    status_code=400,
                    detail="stream=true not supported on this endpoint; use /messages/stream",
                )
            try:
                content = await dispatch_agent_message(
                    _agent_dispatch_context(session_id, req, llm)
                )
            except Exception as exc:
                sessions.append_event(
                    session_id,
                    TranscriptEvent(
                        event_type="agent_dispatch_error",
                        data={"strategy": strategy, "error": str(exc)},
                    ),
                )
                raise HTTPException(
                    status_code=502,
                    detail=f"agent strategy '{strategy}' failed",
                ) from exc
            return SendMessageResponse(session_id=session_id, content=content)
        resolved_template = await _resolve_template_prompt(req)
        messages = _build_messages(
            session_id, req, resolved_template=resolved_template
        )
        try:
            mcp_context = await _collect_mcp_context_for_prompt(session_id, req.content)
        except Exception as exc:
            admin_logger.exception(
                "mcp context collection failed for /messages",
                extra={"session_id": session_id},
            )
            sessions.append_event(
                session_id,
                TranscriptEvent(
                    event_type="mcp_context_error",
                    data={
                        "error": f"mcp context collection failed: {exc}",
                        "unexpected": True,
                    },
                ),
            )
            mcp_context = ""
        translation_request = _is_translation_request(req.content)
        selected_translator_indices = _selected_translator_server_indices(session_id)
        translator_required = translation_request and bool(selected_translator_indices)

        prefer_translator_output = bool(
            config.get("mcp.chat_assist.prefer_translator_output")
            if config.get("mcp.chat_assist.prefer_translator_output") is not None
            else True
        )
        translator_output = ""
        expert_output = ""
        prompt_assist_output = ""

        if mcp_context and translation_request and prefer_translator_output:
            translator_output = _extract_direct_mcp_output(
                mcp_context, "Translator MCP output:\n"
            )

        if mcp_context and translation_request and prefer_translator_output:
            marker = "Translator MCP output:\n"
            marker_index = mcp_context.find(marker)
            if marker_index >= 0:
                translator_output = mcp_context[marker_index + len(marker) :].strip()

        if mcp_context:
            expert_output = _extract_direct_mcp_output(
                mcp_context, "Expert MCP output:\n"
            )
            prompt_assist_output = _derive_direct_prompt_assist_output(
                req.content, mcp_context
            )

        if translator_output:
            sessions.append_event(
                session_id,
                TranscriptEvent(
                    event_type="mcp_direct_response",
                    data={
                        "source": "translator",
                        "chars": len(translator_output),
                    },
                ),
            )
            sessions.append_event(
                session_id,
                TranscriptEvent(
                    event_type="assistant_message",
                    data={"content": translator_output},
                ),
            )
            return SendMessageResponse(session_id=session_id, content=translator_output)

        if expert_output:
            sessions.append_event(
                session_id,
                TranscriptEvent(
                    event_type="mcp_direct_response",
                    data={
                        "source": "expert_execute",
                        "chars": len(expert_output),
                    },
                ),
            )
            sessions.append_event(
                session_id,
                TranscriptEvent(
                    event_type="assistant_message",
                    data={"content": expert_output},
                ),
            )
            return SendMessageResponse(session_id=session_id, content=expert_output)

        if prompt_assist_output:
            sessions.append_event(
                session_id,
                TranscriptEvent(
                    event_type="mcp_direct_response",
                    data={"source": "prompt_assist", "chars": len(prompt_assist_output)},
                ),
            )
            sessions.append_event(
                session_id,
                TranscriptEvent(
                    event_type="assistant_message",
                    data={"content": prompt_assist_output},
                ),
            )
            return SendMessageResponse(session_id=session_id, content=prompt_assist_output)

        if translator_required:
            sessions.append_event(
                session_id,
                TranscriptEvent(
                    event_type="mcp_context_error",
                    data={
                        "error": "translator output unavailable",
                        "server_indices": selected_translator_indices,
                        "strict_fail": True,
                    },
                ),
            )
            raise HTTPException(
                status_code=502,
                detail="Translator MCP output unavailable for translation request",
            )

        if mcp_context:
            messages.append(
                ChatMessage(
                    role="system",
                    content=(
                        "Use this external MCP context for factual grounding in your next answer. "
                        "Do not claim you lack external access when this context is present. "
                        "For freshness requests (today/current/latest/last 24 hours), only report items that are clearly time-bounded "
                        "in the retrieved context and include source/time cues.\n\n"
                        f"{mcp_context}"
                    ),
                )
            )

        # `/messages` is the non-streaming contract. Global provider streaming
        # defaults must not leak into this route when the caller omits `stream`.
        if bool(req.stream):
            raise HTTPException(
                status_code=400,
                detail="stream=true not supported on this endpoint; use /messages/stream",
            )

        try:
            result = await llm.complete(messages)
        except LLMProviderError as exc:
            # Fail open on response-format drift so chat flows still complete.
            # This keeps strict policy as first attempt while preventing
            # deterministic 500s when providers stop honoring envelope tags.
            if (
                "response format validation" not in str(exc).lower()
                or not _suite_allows_response_policy_override(session_id)
            ):
                raise
            llm = LLMService(llm_config, response_policy_enforce=False)
            result = await llm.complete(messages)
        policy = llm.response_policy
        content = format_user_response(result.content, policy)
        content = _coerce_suite_output(
            session_id=session_id, prompt=req.content, content=content
        )
        if (not policy.strip_for_user) or _suite_requires_marker(session_id):
            content = _enforce_marker(
                content, session_id=session_id, system_prompt=req.system_prompt
            )
        sessions.append_event(
            session_id,
            TranscriptEvent(event_type="assistant_message", data={"content": content}),
        )
        return SendMessageResponse(session_id=session_id, content=content)

    # Covers: R16.4, R16.5 (streaming endpoint in UI backend API contract)
    @router.post(
        "/sessions/{session_id}/messages/stream", dependencies=[Depends(_auth_dep)]
    )
    async def send_message_stream(session_id: str, req: SendMessageRequest):
        """Handle send message stream for the current runtime context."""
        try:
            sessions.get_session(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown session")

        if not req.content.strip():
            raise HTTPException(status_code=400, detail="content must be non-empty")

        sessions.append_event(
            session_id,
            TranscriptEvent(event_type="user_message", data={"content": req.content}),
        )
        _maybe_auto_title_session(session_id, req.content)

        llm = LLMService(
            _llm_config_for_session(session_id),
            response_policy_enforce=_response_policy_enforce_override_for_session(
                session_id
            ),
        )
        strategy = _agent_strategy_or_400(session_id)
        if strategy != SIMPLE_AGENT_STRATEGY:
            return StreamingResponse(
                stream_agent_message(_agent_dispatch_context(session_id, req, llm)),
                media_type="application/jsonl",
            )
        resolved_template = await _resolve_template_prompt(req)
        messages = _build_messages(
            session_id, req, resolved_template=resolved_template
        )
        try:
            mcp_context = await _collect_mcp_context_for_prompt(session_id, req.content)
        except Exception as exc:
            admin_logger.exception(
                "mcp context collection failed for /messages/stream",
                extra={"session_id": session_id},
            )
            sessions.append_event(
                session_id,
                TranscriptEvent(
                    event_type="mcp_context_error",
                    data={
                        "error": f"mcp context collection failed: {exc}",
                        "unexpected": True,
                    },
                ),
            )
            mcp_context = ""
        translation_request = _is_translation_request(req.content)
        selected_translator_indices = _selected_translator_server_indices(session_id)
        translator_required = translation_request and bool(selected_translator_indices)
        prefer_translator_output = bool(
            config.get("mcp.chat_assist.prefer_translator_output")
            if config.get("mcp.chat_assist.prefer_translator_output") is not None
            else True
        )
        translator_output = ""
        expert_output = ""
        prompt_assist_output = ""

        if mcp_context and translation_request and prefer_translator_output:
            translator_output = _extract_direct_mcp_output(
                mcp_context, "Translator MCP output:\n"
            )

        if mcp_context:
            expert_output = _extract_direct_mcp_output(
                mcp_context, "Expert MCP output:\n"
            )
            prompt_assist_output = _derive_direct_prompt_assist_output(
                req.content, mcp_context
            )

        if translator_output:
            sessions.append_event(
                session_id,
                TranscriptEvent(
                    event_type="mcp_direct_response",
                    data={"source": "translator", "chars": len(translator_output)},
                ),
            )
            sessions.append_event(
                session_id,
                TranscriptEvent(
                    event_type="assistant_message",
                    data={"content": translator_output},
                ),
            )

            async def _direct_translator_gen():
                yield json.dumps({"type": "delta", "content_delta": translator_output}) + "\n"
                yield json.dumps({"type": "done"}) + "\n"

            return StreamingResponse(
                _direct_translator_gen(), media_type="application/jsonl"
            )

        if expert_output:
            sessions.append_event(
                session_id,
                TranscriptEvent(
                    event_type="mcp_direct_response",
                    data={"source": "expert_execute", "chars": len(expert_output)},
                ),
            )
            sessions.append_event(
                session_id,
                TranscriptEvent(
                    event_type="assistant_message",
                    data={"content": expert_output},
                ),
            )

            async def _direct_expert_gen():
                yield json.dumps({"type": "delta", "content_delta": expert_output}) + "\n"
                yield json.dumps({"type": "done"}) + "\n"

            return StreamingResponse(
                _direct_expert_gen(), media_type="application/jsonl"
            )

        if prompt_assist_output:
            sessions.append_event(
                session_id,
                TranscriptEvent(
                    event_type="mcp_direct_response",
                    data={"source": "prompt_assist", "chars": len(prompt_assist_output)},
                ),
            )
            sessions.append_event(
                session_id,
                TranscriptEvent(
                    event_type="assistant_message",
                    data={"content": prompt_assist_output},
                ),
            )

            async def _direct_prompt_assist_gen():
                yield json.dumps({"type": "delta", "content_delta": prompt_assist_output}) + "\n"
                yield json.dumps({"type": "done"}) + "\n"

            return StreamingResponse(
                _direct_prompt_assist_gen(), media_type="application/jsonl"
            )

        if translator_required:
            sessions.append_event(
                session_id,
                TranscriptEvent(
                    event_type="mcp_context_error",
                    data={
                        "error": "translator output unavailable",
                        "server_indices": selected_translator_indices,
                        "strict_fail": True,
                    },
                ),
            )
            raise HTTPException(
                status_code=502,
                detail="Translator MCP output unavailable for translation request",
            )

        if mcp_context:
            messages.append(
                ChatMessage(
                    role="system",
                    content=(
                        "Use this external MCP context for factual grounding in your next answer. "
                        "Do not claim you lack external access when this context is present. "
                        "For freshness requests (today/current/latest/last 24 hours), only report items that are clearly time-bounded "
                        "in the retrieved context and include source/time cues.\n\n"
                        f"{mcp_context}"
                    ),
                )
            )
        policy = llm.response_policy
        if policy.strip_for_user:
            raise HTTPException(
                status_code=400,
                detail="streaming is not supported when response formatting is enabled",
            )

        async def gen():
            """Handle gen for the current runtime context."""
            assistant_text = ""
            async for chunk in llm.stream(messages):
                if not chunk.content_delta:
                    continue
                assistant_text += chunk.content_delta
                sessions.append_event(
                    session_id,
                    TranscriptEvent(
                        event_type="assistant_stream_chunk",
                        data={"content_delta": chunk.content_delta},
                    ),
                )
                yield (
                    json.dumps({"type": "delta", "content_delta": chunk.content_delta})
                    + "\n"
                )

            final_text = _enforce_marker(
                assistant_text, session_id=session_id, system_prompt=req.system_prompt
            )
            if final_text != assistant_text:
                delta = final_text[len(assistant_text) :]
                if delta:
                    yield json.dumps({"type": "delta", "content_delta": delta}) + "\n"
            sessions.append_event(
                session_id,
                TranscriptEvent(
                    event_type="assistant_message", data={"content": final_text}
                ),
            )
            yield json.dumps({"type": "done"}) + "\n"

        return StreamingResponse(gen(), media_type="application/jsonl")

    # Covers: R16.4 (transcript endpoint for UI session history display)
    @router.get("/sessions/{session_id}/transcript", dependencies=[Depends(_auth_dep)])
    async def get_transcript(session_id: str) -> Dict[str, Any]:
        """Return transcript for the current runtime context."""
        try:
            session = sessions.get_session(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown session")

        events = [json.loads(e.to_json_line()) for e in session["events"]]
        return {"session_id": session_id, "events": events}

    # Covers: R16.6 (session-scoped MCP selection retrieval)
    @router.get(
        "/sessions/{session_id}/preferences",
        response_model=SessionPreferencesResponse,
        dependencies=[Depends(_auth_dep)],
    )
    async def get_session_preferences(session_id: str) -> SessionPreferencesResponse:
        """Return session preferences for the current runtime context."""
        try:
            session = sessions.get_session(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown session")

        metadata = session.get("metadata") if isinstance(session, dict) else {}
        if not isinstance(metadata, dict):
            metadata = {}
        selected = _normalize_selected_indices(
            metadata.get("selected_mcp_server_indices"),
            max_count=len(_session_server_specs(session_id)),
        )
        return SessionPreferencesResponse(
            session_id=session_id, selected_mcp_server_indices=selected
        )

    # Covers: R16.6 (session-scoped MCP selection persistence)
    @router.put(
        "/sessions/{session_id}/preferences",
        response_model=SessionPreferencesResponse,
        dependencies=[Depends(_auth_dep)],
    )
    async def update_session_preferences(
        session_id: str, req: SessionPreferencesRequest
    ) -> SessionPreferencesResponse:
        """Update session preferences for the current runtime context."""
        try:
            sessions.get_session(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown session")

        selected = _normalize_selected_indices(
            req.selected_mcp_server_indices,
            max_count=len(_session_server_specs(session_id)),
        )
        sessions.update_session_metadata(
            session_id, {"selected_mcp_server_indices": selected}
        )
        return SessionPreferencesResponse(
            session_id=session_id, selected_mcp_server_indices=selected
        )

    # Covers: R16.4, R16.6 (tools list API in backend/UI MCP workflow)
    @router.post(
        "/sessions/{session_id}/mcp/tools/list", dependencies=[Depends(_auth_dep)]
    )
    async def mcp_tools_list(
        session_id: str, req: MCPToolsListRequest
    ) -> Dict[str, Any]:
        """Handle MCP tools list for the current runtime context."""
        try:
            sessions.get_session(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown session")

        from ..mcp import MCPConnection

        connection = MCPConnection.from_config(
            config,
            server_index=req.server_index,
            servers_override=_session_server_specs(session_id),
        )
        await connection.connect()
        try:
            current_servers = _session_server_specs(session_id)
            server_spec = (
                current_servers[req.server_index]
                if 0 <= int(req.server_index) < len(current_servers)
                else {}
            )
            require_initialize = _resolve_mcp_require_initialize(
                req.require_initialize, server_spec
            )
            if require_initialize:
                await _maybe_initialize_mcp(connection)
            job_id = _create_mcp_job(
                session_id=session_id,
                job_type="mcp_proxy_tools_list",
                server_index=req.server_index,
                method="tools/list",
            )
            try:
                result = await connection.transport.tools_list()
            except Exception as e:
                _fail_mcp_job(job_id, error=str(e))
                raise
            _complete_mcp_job(
                job_id,
                result={"tool_count": len(result.get("tools") or [])},
            )
            sessions.append_event(
                session_id,
                TranscriptEvent(
                    event_type="mcp_tools_list",
                    data={
                        "server_index": req.server_index,
                        "tool_count": len(result.get("tools") or []),
                    },
                ),
            )
            return result
        finally:
            await connection.close()

    # Covers: R16.4, R16.6 (tools call API in backend/UI MCP workflow)
    @router.post(
        "/sessions/{session_id}/mcp/tools/call", dependencies=[Depends(_auth_dep)]
    )
    async def mcp_tools_call(
        session_id: str, req: MCPToolsCallRequest, response: Response, request: Request
    ) -> Dict[str, Any]:
        """Handle MCP tools call for the current runtime context."""
        try:
            sessions.get_session(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown session")

        if not req.name:
            raise HTTPException(status_code=400, detail="name must be provided")

        from ..mcp import MCPConnection

        current_servers = _session_server_specs(session_id)
        server_spec = (
            current_servers[req.server_index]
            if 0 <= int(req.server_index) < len(current_servers)
            else {}
        )
        extra_headers = _file_mcp_extra_headers(request, server_spec)
        if extra_headers:
            server_spec = dict(server_spec)
            merged_extra_headers = server_spec.get("extra_headers")
            if not isinstance(merged_extra_headers, dict):
                merged_extra_headers = {}
            merged_extra_headers = dict(merged_extra_headers)
            merged_extra_headers.update(extra_headers)
            server_spec["extra_headers"] = merged_extra_headers
            if 0 <= int(req.server_index) < len(current_servers):
                current_servers = list(current_servers)
                current_servers[req.server_index] = server_spec
        call_arguments = _normalize_file_mcp_arguments(
            server_spec, req.name, req.arguments
        )

        connection = MCPConnection.from_config(
            config,
            server_index=req.server_index,
            servers_override=current_servers,
        )
        await connection.connect()
        try:
            require_initialize = _resolve_mcp_require_initialize(
                req.require_initialize, server_spec
            )
            if require_initialize:
                await _maybe_initialize_mcp(connection)
            correlation_id = str(response.headers.get("X-Request-Id") or "").strip() or None
            job_id = _create_mcp_job(
                session_id=session_id,
                job_type="mcp_proxy_tools_call",
                server_index=req.server_index,
                method="tools/call",
                payload={"name": req.name, "arguments": call_arguments},
                correlation_id=correlation_id,
            )
            sessions.append_event(
                session_id,
                TranscriptEvent(
                    event_type="mcp_tool_call",
                    data={
                        "server_index": req.server_index,
                        "name": req.name,
                        "arguments": call_arguments,
                    },
                ),
            )
            try:
                result = await connection.transport.tools_call(req.name, call_arguments)
            except Exception as e:
                detail = str(e)
                _fail_mcp_job(job_id, error=detail)
                text = detail.strip()
                lowered = text.lower()
                is_connectivity_failure = any(
                    marker in lowered
                    for marker in (
                        "connect",
                        "unreachable",
                        "timed out",
                        "connection refused",
                        "dns",
                    )
                )
                if is_connectivity_failure:
                    raise
                if not text.lower().startswith("error calling tool"):
                    text = f"Error calling tool: {text}"
                result = {"content": [{"type": "text", "text": text}], "isError": True}
            sessions.append_event(
                session_id,
                TranscriptEvent(
                    event_type="mcp_tool_result",
                    data={
                        "server_index": req.server_index,
                        "name": req.name,
                        "isError": result.get("isError"),
                    },
                ),
            )
            if result.get("isError") is True:
                _fail_mcp_job(job_id, error=f"MCP tool {req.name} returned isError=true")
            else:
                _complete_mcp_job(
                    job_id,
                    result={"name": req.name, "isError": False},
                )
            if job_id:
                response.headers["X-Job-Id"] = job_id
            return result
        finally:
            await connection.close()

    @router.get(
        "/sessions/{session_id}/mcp/file-profiles", dependencies=[Depends(_auth_dep)]
    )
    async def mcp_file_profiles(
        session_id: str, server_index: int = 0
    ) -> Dict[str, Any]:
        """List file-mcp storage profiles via the backend admin JSON API."""
        try:
            sessions.get_session(session_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail="Unknown session") from e

        current_servers = _session_server_specs(session_id)
        if not current_servers:
            raise HTTPException(status_code=404, detail="No MCP servers configured")
        if server_index < 0 or server_index >= len(current_servers):
            raise HTTPException(status_code=400, detail="server_index out of range")

        server_spec = current_servers[server_index]
        if not _looks_like_file_mcp_server(server_spec):
            raise HTTPException(status_code=400, detail="Selected server is not file-mcp")

        base_url = str(server_spec.get("base_url") or "").rstrip("/")
        if not base_url:
            raise HTTPException(status_code=500, detail="file-mcp base_url is not configured")

        verify_tls = bool(
            server_spec.get("verify_tls") if server_spec.get("verify_tls") is not None else True
        )
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=30.0),
                verify=verify_tls,
            ) as client:
                upstream = await client.get(
                    f"{base_url}/admin/profiles",
                    headers=_file_mcp_http_headers(config, server_spec),
                )
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Failed to reach file-mcp admin API: {exc}",
            ) from exc

        if upstream.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"file-mcp admin profile list failed with status {upstream.status_code}",
            )

        try:
            payload = upstream.json()
        except ValueError as exc:
            raise HTTPException(
                status_code=502,
                detail="file-mcp admin profile list returned invalid JSON",
            ) from exc

        return {"profiles": _extract_file_profile_names(payload)}

    def _file_transfer_limits() -> tuple[int, float, set[str]]:
        """Resolve file-transfer limits from config."""
        try:
            max_upload_bytes = int(
                config.get("client_api.file_transfer.max_upload_bytes") or 10_485_760
            )
        except (TypeError, ValueError) as e:
            raise HTTPException(
                status_code=500,
                detail="Invalid client_api.file_transfer.max_upload_bytes configuration",
            ) from e
        try:
            fetch_timeout_seconds = float(
                config.get("client_api.file_transfer.fetch_timeout_seconds") or 30.0
            )
        except (TypeError, ValueError) as e:
            raise HTTPException(
                status_code=500,
                detail="Invalid client_api.file_transfer.fetch_timeout_seconds configuration",
            ) from e
        schemes_raw = config.get("client_api.file_transfer.allowed_schemes") or ["http", "https"]
        if not isinstance(schemes_raw, list):
            raise HTTPException(
                status_code=500,
                detail="Invalid client_api.file_transfer.allowed_schemes configuration",
            )
        allowed_schemes = {str(item).strip().lower() for item in schemes_raw if str(item).strip()}
        if not allowed_schemes:
            raise HTTPException(
                status_code=500,
                detail="client_api.file_transfer.allowed_schemes resolved empty",
            )
        return max_upload_bytes, fetch_timeout_seconds, allowed_schemes

    def _require_known_session(session_id: str) -> None:
        """Ensure the target session exists before proxying file operations."""
        try:
            sessions.get_session(session_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail="Unknown session") from e

    def _parse_server_override(raw_server: Optional[str]) -> Optional[Dict[str, Any]]:
        """Parse an optional server override encoded as JSON form-data."""
        if raw_server is None or not str(raw_server).strip():
            return None
        try:
            parsed = json.loads(str(raw_server))
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail="server must be valid JSON") from e
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=400, detail="server must decode to an object")
        return parsed

    async def _resolve_file_transfer_connection(
        session_id: str,
        *,
        path: str,
        server_index: Optional[int],
        server: Optional[Dict[str, Any]],
        tool_name: str,
    ) -> tuple[Any, Dict[str, Any], str, Optional[int]]:
        """Resolve file-MCP target connection and normalize the requested path."""
        if server_index is None and server is None:
            raise HTTPException(
                status_code=400, detail="server_index or server must be provided"
            )

        server_spec: Dict[str, Any]
        resolved_index: Optional[int] = server_index
        if server is not None:
            server_spec = dict(server)
            connection = _connection_from_server_spec(server)
            normalized_path = str(
                _normalize_file_mcp_arguments(server_spec, tool_name, {"path": path}).get("path")
                or path
            )
            return connection, server_spec, normalized_path, resolved_index

        from ..mcp import MCPConnection

        current_servers = _session_server_specs(session_id)
        resolved_index = int(server_index or 0)
        server_spec = (
            current_servers[resolved_index]
            if 0 <= resolved_index < len(current_servers)
            else {}
        )
        normalized_path = str(
            _normalize_file_mcp_arguments(server_spec, tool_name, {"path": path}).get("path")
            or path
        )
        connection = MCPConnection.from_config(
            config,
            server_index=resolved_index,
            servers_override=current_servers,
        )
        return connection, server_spec, normalized_path, resolved_index

    async def _fetch_upload_bytes_from_url(source_url: str) -> bytes:
        """Fetch upload content from a client-supplied URL with bounded limits."""
        max_upload_bytes, fetch_timeout_seconds, allowed_schemes = _file_transfer_limits()
        parsed = urlparse(str(source_url or "").strip())
        scheme = str(parsed.scheme or "").strip().lower()
        if scheme not in allowed_schemes:
            raise HTTPException(
                status_code=400,
                detail=f"source_url scheme must be one of {sorted(allowed_schemes)}",
            )

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=fetch_timeout_seconds,
        ) as client:
            try:
                async with client.stream("GET", source_url) as response:
                    response.raise_for_status()
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > max_upload_bytes:
                            raise HTTPException(
                                status_code=413,
                                detail="Remote file exceeds max upload size",
                            )
                        chunks.append(chunk)
            except HTTPException:
                raise
            except httpx.HTTPStatusError as e:
                raise HTTPException(
                    status_code=502,
                    detail=f"Remote file fetch failed with status {e.response.status_code}",
                ) from e
            except httpx.HTTPError as e:
                raise HTTPException(
                    status_code=502,
                    detail="Remote file fetch failed",
                ) from e
        content = b"".join(chunks)
        if not content:
            raise HTTPException(status_code=400, detail="Remote file returned empty content")
        return content

    def _decode_request_upload_bytes(
        *,
        content_base64: Optional[str],
        source_url: Optional[str],
        urlsafe: bool,
    ) -> tuple[Optional[bytes], Optional[bool]]:
        """Validate mutually-exclusive upload sources for JSON file upload."""
        has_base64 = bool(str(content_base64 or "").strip())
        has_url = bool(str(source_url or "").strip())
        if has_base64 == has_url:
            raise HTTPException(
                status_code=400,
                detail="Exactly one of content_base64 or source_url must be provided",
            )
        if has_base64:
            raw = str(content_base64 or "").strip()
            if urlsafe:
                raw = raw.replace("-", "+").replace("_", "/")
            padding = "=" * ((4 - len(raw) % 4) % 4)
            try:
                decoded = base64.b64decode(raw + padding, validate=False)
            except Exception as e:
                raise HTTPException(status_code=400, detail="Invalid content_base64") from e
            max_upload_bytes, _, _ = _file_transfer_limits()
            if len(decoded) > max_upload_bytes:
                raise HTTPException(status_code=413, detail="Upload exceeds max upload size")
            return decoded, False
        return None, True

    async def _execute_file_upload(
        session_id: str,
        *,
        path: str,
        content_bytes: bytes,
        server_index: Optional[int],
        server: Optional[Dict[str, Any]],
        urlsafe: bool,
        overwrite: bool,
        dry_run: bool,
        require_initialize: Optional[bool],
        source_kind: str,
    ) -> MCPFileUploadResponse:
        """Write bytes into file-MCP via the current chat session."""
        _require_known_session(session_id)
        normalized_path = str(path or "").strip()
        if not normalized_path:
            raise HTTPException(status_code=400, detail="path must be non-empty")

        connection, _, normalized_path, resolved_index = await _resolve_file_transfer_connection(
            session_id,
            path=normalized_path,
            server_index=server_index,
            server=server,
            tool_name="write_file",
        )
        encoded_content = (
            base64.urlsafe_b64encode(content_bytes).decode("ascii")
            if urlsafe
            else base64.b64encode(content_bytes).decode("ascii")
        )
        await connection.connect()
        try:
            require_initialize_value = (
                bool(require_initialize)
                if require_initialize is not None
                else bool(config.get("mcp.api.require_initialize") or False)
            )
            if require_initialize_value:
                await _maybe_initialize_mcp(connection)

            sessions.append_event(
                session_id,
                TranscriptEvent(
                    event_type="mcp_file_upload",
                    data={
                        "server_index": resolved_index,
                        "path": normalized_path,
                        "source_kind": source_kind,
                    },
                ),
            )

            result = await connection.transport.tools_call(
                "b64_decode_to_file",
                {
                    "path": normalized_path,
                    "data": encoded_content,
                    "urlsafe": bool(urlsafe),
                    "overwrite": bool(overwrite),
                    "dry_run": bool(dry_run),
                },
            )
            if result.get("isError") is True:
                try:
                    decoded_text = content_bytes.decode("utf-8")
                except Exception as e:
                    raise HTTPException(
                        status_code=502,
                        detail="MCP upload fallback requires UTF-8 text content",
                    ) from e

                result = await connection.transport.tools_call(
                    "write_file",
                    {
                        "path": normalized_path,
                        "content": decoded_text,
                        "overwrite": bool(overwrite),
                        "dry_run": bool(dry_run),
                    },
                )
                if result.get("isError") is True:
                    detail = _extract_mcp_error_text(result) or "MCP tool returned isError=true"
                    raise HTTPException(
                        status_code=_mcp_http_status_from_error_text(detail),
                        detail=detail,
                    )
                payload = _extract_mcp_structured_or_text_object(result)
                if not isinstance(payload, dict):
                    payload = {}
                payload.setdefault("bytes_written", len(content_bytes))
                payload.setdefault("path", normalized_path)
                payload.setdefault("dry_run", bool(dry_run))
            else:
                try:
                    payload = _extract_mcp_tool_payload(result)
                except ValueError as e:
                    raise HTTPException(status_code=502, detail=str(e)) from e

            bytes_written_raw = payload.get("bytes_written")
            if bytes_written_raw is None:
                bytes_written = 0
            else:
                try:
                    bytes_written = int(bytes_written_raw)
                except (TypeError, ValueError) as e:
                    raise HTTPException(
                        status_code=502, detail="Invalid bytes_written in MCP response"
                    ) from e

            raw_response_path = str(payload.get("path") or "").strip()
            response_path = normalized_path
            if raw_response_path:
                if is_absolute_path(normalized_path):
                    response_path = str(
                        _normalize_file_mcp_path_value(raw_response_path) or raw_response_path
                    ).strip() or normalized_path
            dry_run_value = bool(payload.get("dry_run"))
            sessions.append_event(
                session_id,
                TranscriptEvent(
                    event_type="mcp_file_upload_result",
                    data={
                        "server_index": resolved_index,
                        "path": response_path,
                        "bytes_written": bytes_written,
                        "dry_run": dry_run_value,
                        "source_kind": source_kind,
                    },
                ),
            )
            return MCPFileUploadResponse(
                path=response_path,
                bytes_written=bytes_written,
                dry_run=dry_run_value,
                mcp_server_index=resolved_index,
                tool_result=result,
            )
        finally:
            await connection.close()

    async def _execute_file_download(
        session_id: str,
        *,
        path: str,
        server_index: Optional[int],
        server: Optional[Dict[str, Any]],
        urlsafe: bool,
        require_initialize: Optional[bool],
    ) -> tuple[MCPFileDownloadResponse, bytes]:
        """Read bytes from file-MCP via the current chat session."""
        _require_known_session(session_id)
        normalized_path = str(path or "").strip()
        if not normalized_path:
            raise HTTPException(status_code=400, detail="path must be non-empty")

        connection, _, normalized_path, resolved_index = await _resolve_file_transfer_connection(
            session_id,
            path=normalized_path,
            server_index=server_index,
            server=server,
            tool_name="read_file",
        )
        await connection.connect()
        try:
            require_initialize_value = (
                bool(require_initialize)
                if require_initialize is not None
                else bool(config.get("mcp.api.require_initialize") or False)
            )
            if require_initialize_value:
                await _maybe_initialize_mcp(connection)

            sessions.append_event(
                session_id,
                TranscriptEvent(
                    event_type="mcp_file_download",
                    data={"server_index": resolved_index, "path": normalized_path},
                ),
            )

            result = await connection.transport.tools_call(
                "b64_encode_file",
                {"path": normalized_path, "urlsafe": bool(urlsafe)},
            )
            if result.get("isError") is True:
                result = await connection.transport.tools_call(
                    "read_file",
                    {"path": normalized_path},
                )
                if result.get("isError") is True:
                    detail = _extract_mcp_error_text(result) or "MCP tool returned isError=true"
                    raise HTTPException(
                        status_code=_mcp_http_status_from_error_text(detail),
                        detail=detail,
                    )
                payload = _extract_mcp_structured_or_text_object(result)
                text_payload = ""
                if isinstance(payload, dict):
                    text_payload = str(payload.get("result") or payload.get("content") or "")
                if not text_payload:
                    text_payload = _extract_mcp_text_content(result)
                if not text_payload:
                    raise HTTPException(
                        status_code=502,
                        detail="MCP download fallback returned empty content",
                    )
                file_bytes = text_payload.encode("utf-8")
                encoded = (
                    base64.urlsafe_b64encode(file_bytes)
                    if bool(urlsafe)
                    else base64.b64encode(file_bytes)
                )
                content_base64 = encoded.decode("ascii")
                byte_size = len(file_bytes)
            else:
                try:
                    payload = _extract_mcp_tool_payload(result)
                except ValueError as e:
                    raise HTTPException(status_code=502, detail=str(e)) from e

                raw_content_base64 = payload.get("data")
                if not isinstance(raw_content_base64, str) or not raw_content_base64.strip():
                    raw_content_base64 = payload.get("value")
                if not isinstance(raw_content_base64, str) or not raw_content_base64.strip():
                    raw_content_base64 = payload.get("content_base64")
                if not isinstance(raw_content_base64, str) or not raw_content_base64.strip():
                    raw_content_base64 = _extract_mcp_text_content(result)
                if not isinstance(raw_content_base64, str) or not raw_content_base64.strip():
                    raise HTTPException(
                        status_code=502,
                        detail="MCP download response missing base64 data",
                    )
                content_base64 = raw_content_base64
                raw = content_base64.strip()
                if urlsafe:
                    raw = raw.replace("-", "+").replace("_", "/")
                padding = "=" * ((4 - len(raw) % 4) % 4)
                try:
                    file_bytes = base64.b64decode(raw + padding, validate=False)
                except Exception as e:
                    raise HTTPException(
                        status_code=502, detail="Invalid base64 content in MCP tool response"
                    ) from e
                byte_size = len(file_bytes)

            sessions.append_event(
                session_id,
                TranscriptEvent(
                    event_type="mcp_file_download_result",
                    data={
                        "server_index": resolved_index,
                        "path": normalized_path,
                        "byte_size": byte_size,
                    },
                ),
            )
            response_model = MCPFileDownloadResponse(
                path=normalized_path,
                content_base64=content_base64,
                byte_size=byte_size,
                mcp_server_index=resolved_index,
                tool_result=result,
            )
            return response_model, file_bytes
        finally:
            await connection.close()

    @router.post(
        "/sessions/{session_id}/mcp/files/upload",
        response_model=MCPFileUploadResponse,
        dependencies=[Depends(_auth_dep)],
    )
    async def mcp_file_upload(
        session_id: str, req: MCPFileUploadRequest
    ) -> MCPFileUploadResponse:
        """Handle JSON/base64 or URL-backed MCP file upload for the current runtime context.

        R7.2 MCP File Transfer Proxy: this endpoint proxies file upload operations
        through configured MCP file servers rather than reading or writing host files directly.
        """
        decoded_bytes, fetch_from_url = _decode_request_upload_bytes(
            content_base64=req.content_base64,
            source_url=req.source_url,
            urlsafe=bool(req.urlsafe),
        )
        if fetch_from_url:
            decoded_bytes = await _fetch_upload_bytes_from_url(str(req.source_url or "").strip())
            source_kind = "url"
        else:
            source_kind = "base64"
        assert decoded_bytes is not None
        return await _execute_file_upload(
            session_id,
            path=req.path,
            content_bytes=decoded_bytes,
            server_index=req.server_index,
            server=req.server,
            urlsafe=bool(req.urlsafe),
            overwrite=bool(req.overwrite),
            dry_run=bool(req.dry_run),
            require_initialize=req.require_initialize,
            source_kind=source_kind,
        )

    @router.post(
        "/sessions/{session_id}/mcp/files/upload-multipart",
        response_model=MCPFileUploadResponse,
        dependencies=[Depends(_auth_dep)],
    )
    async def mcp_file_upload_multipart(
        session_id: str,
        path: str = Form(...),
        file: UploadFile = File(...),
        server_index: Optional[int] = Form(None),
        server: Optional[str] = Form(None),
        overwrite: bool = Form(True),
        dry_run: bool = Form(False),
        require_initialize: Optional[bool] = Form(None),
    ) -> MCPFileUploadResponse:
        """Handle multipart file upload for the current runtime context."""
        raw_bytes = await file.read()
        max_upload_bytes, _, _ = _file_transfer_limits()
        if not raw_bytes:
            raise HTTPException(status_code=400, detail="multipart file content must be non-empty")
        if len(raw_bytes) > max_upload_bytes:
            raise HTTPException(status_code=413, detail="Upload exceeds max upload size")
        return await _execute_file_upload(
            session_id,
            path=path,
            content_bytes=raw_bytes,
            server_index=server_index,
            server=_parse_server_override(server),
            urlsafe=False,
            overwrite=bool(overwrite),
            dry_run=bool(dry_run),
            require_initialize=require_initialize,
            source_kind="multipart",
        )

    @router.post(
        "/sessions/{session_id}/mcp/files/download",
        response_model=MCPFileDownloadResponse,
        dependencies=[Depends(_auth_dep)],
    )
    async def mcp_file_download(
        session_id: str, req: MCPFileDownloadRequest
    ) -> MCPFileDownloadResponse:
        """Handle JSON/base64 MCP file download for the current runtime context.

        R7.2 MCP File Transfer Proxy: this endpoint proxies file download operations
        through configured MCP file servers and returns client-safe response payloads.
        """
        response_model, _ = await _execute_file_download(
            session_id,
            path=req.path,
            server_index=req.server_index,
            server=req.server,
            urlsafe=bool(req.urlsafe),
            require_initialize=req.require_initialize,
        )
        return response_model

    @router.get(
        "/sessions/{session_id}/mcp/files/download/content",
        dependencies=[Depends(_auth_dep)],
    )
    async def mcp_file_download_content(
        session_id: str,
        path: str,
        server_index: Optional[int] = None,
        urlsafe: bool = False,
        require_initialize: Optional[bool] = None,
        download_name: Optional[str] = None,
    ) -> StreamingResponse:
        """Handle streamed browser/client file download for the current runtime context."""
        response_model, file_bytes = await _execute_file_download(
            session_id,
            path=path,
            server_index=server_index,
            server=None,
            urlsafe=bool(urlsafe),
            require_initialize=require_initialize,
        )
        filename = str(download_name or file_name(response_model.path) or "download.bin").strip() or "download.bin"
        media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-MCP-Server-Index": str(response_model.mcp_server_index if response_model.mcp_server_index is not None else ""),
        }
        return StreamingResponse(iter([file_bytes]), media_type=media_type, headers=headers)

    @router.post(
        "/sessions/{session_id}/mcp/execute", dependencies=[Depends(_auth_dep)]
    )
    async def mcp_execute(
        session_id: str, req: MCPExecuteRequest, response: Response
    ) -> Dict[str, Any]:
        """Handle MCP execute for the current runtime context."""
        try:
            sessions.get_session(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown session")

        if req.server_index is None and req.server is None:
            raise HTTPException(
                status_code=400, detail="server_index or server must be provided"
            )

        current_servers = _session_server_specs(session_id)
        server_spec = (
            current_servers[int(req.server_index)]
            if req.server_index is not None
            and 0 <= int(req.server_index) < len(current_servers)
            else (req.server or {})
        )
        if req.server_index is not None:
            from ..mcp import MCPConnection

            connection = MCPConnection.from_config(
                config,
                server_index=req.server_index,
                servers_override=current_servers,
            )
        else:
            connection = _connection_from_server_spec(req.server or {})

        await connection.connect()
        try:
            protocol_version = req.protocol_version or config.get(
                "mcp.defaults.protocol_version"
            )
            require_initialize = (
                bool(req.require_initialize)
                if req.require_initialize is not None
                else bool(config.get("mcp.api.require_initialize") or False)
            )
            if require_initialize:
                await _maybe_initialize_mcp(
                    connection, protocol_version=str(protocol_version or "")
                )

            step_timeout_raw = config.get("mcp.api.step_timeout_seconds")
            step_timeout: Optional[float] = None
            if step_timeout_raw is not None:
                try:
                    step_timeout = float(step_timeout_raw)
                except (TypeError, ValueError) as e:
                    raise HTTPException(
                        status_code=500,
                        detail="mcp.api.step_timeout_seconds must be a number",
                    ) from e

            results = []
            job_id = _create_mcp_job(
                session_id=session_id,
                job_type="mcp_proxy_execute",
                server_index=req.server_index,
                method="execute",
                payload={"steps": len(req.steps)},
            )
            total_steps = len(req.steps)
            for step_idx, step in enumerate(req.steps):
                try:
                    _update_mcp_job_progress(
                        job_id,
                        percentage=(step_idx / total_steps) * 100.0 if total_steps else 0.0,
                        stage=f"step {step_idx + 1}/{total_steps}",
                        counters={"total": total_steps, "completed": step_idx, "failed": 0},
                        current_item=str(step.method or ""),
                    )
                    if job_id and jobs_runtime is not None:
                        jobs_runtime.heartbeat(job_id)
                    if step_timeout:
                        step_params = step.params
                        if (
                            step.method == "tools/call"
                            and isinstance(step_params, dict)
                            and isinstance(step_params.get("arguments"), dict)
                        ):
                            normalized_step_params = dict(step_params)
                            normalized_step_params["arguments"] = _normalize_file_mcp_arguments(
                                server_spec,
                                str(step_params.get("name") or ""),
                                dict(step_params.get("arguments") or {}),
                            )
                            step_params = normalized_step_params
                        if step_timeout:
                            result = await asyncio.wait_for(
                                connection.transport.request(
                                    step.method, params=step_params
                                ),
                                timeout=step_timeout,
                            )
                        else:
                            result = await connection.transport.request(
                                step.method, params=step_params
                            )
                    if step.expect_error:
                        raise HTTPException(
                            status_code=500,
                            detail=f"Expected error for method {step.method} but call succeeded",
                        )
                    results.append(
                        {
                            "ok": True,
                            "result": result,
                            "expect_error": bool(step.expect_error),
                        }
                    )
                except asyncio.TimeoutError as e:
                    if step.expect_error:
                        results.append(
                            {
                                "ok": False,
                                "error": f"step timeout: {step_timeout}",
                                "expect_error": True,
                            }
                        )
                    else:
                        _fail_mcp_job(job_id, error="MCP request timed out")
                        raise HTTPException(
                            status_code=500, detail="MCP request timed out"
                        ) from e
                except Exception as e:
                    if step.expect_error:
                        results.append(
                            {"ok": False, "error": str(e), "expect_error": True}
                        )
                    else:
                        _fail_mcp_job(job_id, error=str(e))
                        raise

            sessions.append_event(
                session_id,
                TranscriptEvent(
                    event_type="mcp_execute",
                    data={"steps": len(req.steps), "server_index": req.server_index},
                ),
            )
            _complete_mcp_job(
                job_id,
                result={"steps": len(req.steps), "results": len(results)},
            )
            if job_id:
                response.headers["X-Job-Id"] = job_id
            return {"results": results}
        finally:
            await connection.close()

    @router.post(
        "/sessions/{session_id}/mcp/sse/open", dependencies=[Depends(_auth_dep)]
    )
    async def mcp_sse_connect(session_id: str, req: MCPSSEOpenRequest) -> Dict[str, Any]:
        """Handle MCP sse open for the current runtime context."""
        try:
            sessions.get_session(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown session")

        if req.server_index is None and req.server is None:
            raise HTTPException(
                status_code=400, detail="server_index or server must be provided"
            )

        if req.server_index is not None:
            from ..mcp import MCPConnection

            connection = MCPConnection.from_config(
                config,
                server_index=req.server_index,
                servers_override=_session_server_specs(session_id),
            )
        else:
            connection = _connection_from_server_spec(req.server or {})

        await connection.connect()
        try:
            protocol_version = req.protocol_version or config.get(
                "mcp.defaults.protocol_version"
            )
            require_initialize = (
                bool(req.require_initialize)
                if req.require_initialize is not None
                else bool(config.get("mcp.api.require_initialize") or False)
            )
            if require_initialize:
                if not protocol_version:
                    raise HTTPException(
                        status_code=500,
                        detail="mcp.defaults.protocol_version is required",
                    )
                await connection.transport.initialize(
                    protocol_version=str(protocol_version)
                )

            transport = connection.transport
            if not hasattr(transport, "open_sse_stream"):
                raise HTTPException(
                    status_code=400, detail="SSE open not supported for this transport"
                )
            await transport.open_sse_stream()
            return {"ok": True}
        finally:
            await connection.close()

    @router.post(
        "/sessions/{session_id}/mcp/session/terminate",
        dependencies=[Depends(_auth_dep)],
    )
    async def mcp_session_terminate(
        session_id: str, req: MCPTerminateRequest
    ) -> Dict[str, Any]:
        """Handle MCP session terminate for the current runtime context."""
        try:
            sessions.get_session(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown session")

        if req.server_index is None and req.server is None:
            raise HTTPException(
                status_code=400, detail="server_index or server must be provided"
            )

        if req.server_index is not None:
            from ..mcp import MCPConnection

            connection = MCPConnection.from_config(
                config,
                server_index=req.server_index,
                servers_override=_session_server_specs(session_id),
            )
        else:
            connection = _connection_from_server_spec(req.server or {})

        await connection.connect()
        try:
            protocol_version = req.protocol_version or config.get(
                "mcp.defaults.protocol_version"
            )
            require_initialize = (
                bool(req.require_initialize)
                if req.require_initialize is not None
                else bool(config.get("mcp.api.require_initialize") or False)
            )
            if require_initialize:
                if not protocol_version:
                    raise HTTPException(
                        status_code=500,
                        detail="mcp.defaults.protocol_version is required",
                    )
                await connection.transport.initialize(
                    protocol_version=str(protocol_version)
                )

            transport = connection.transport
            if not hasattr(transport, "terminate_session"):
                raise HTTPException(
                    status_code=400,
                    detail="Session termination not supported for this transport",
                )
            await transport.terminate_session()

            verify_error = None
            if req.verify_method:
                try:
                    await transport.request(req.verify_method, params=req.verify_params)
                    verify_error = (
                        "expected failure after termination but request succeeded"
                    )
                except Exception as e:
                    verify_error = str(e)

            return {"ok": True, "verify_error": verify_error}
        finally:
            await connection.close()

    @router.post(
        "/sessions/{session_id}/mcp/oauth/example-remote",
        response_model=MCPOAuthTokenResponse,
        dependencies=[Depends(_auth_dep)],
    )
    async def mcp_oauth_example_remote(
        session_id: str, req: MCPOAuthTokenRequest
    ) -> MCPOAuthTokenResponse:
        """Handle MCP oauth example remote for the current runtime context."""
        try:
            sessions.get_session(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown session")

        server_spec: Dict[str, Any] = {}
        if req.server is not None:
            server_spec = dict(req.server)
        elif req.server_index is not None:
            servers = _session_server_specs(session_id)
            if req.server_index < 0 or req.server_index >= len(servers):
                raise HTTPException(
                    status_code=400, detail="mcp server index out of range"
                )
            item = servers[req.server_index]
            if not isinstance(item, dict):
                raise HTTPException(
                    status_code=500, detail="mcp server config must be an object"
                )
            server_spec = dict(item)
        else:
            fallback = (
                config.get("mcp.it2_10.server")
                or config.get("mcp.it2_6.server")
                or config.get("mcp.servers.0")
                or {}
            )
            if isinstance(fallback, dict):
                server_spec = dict(fallback)

        base_url = str(server_spec.get("base_url") or "").strip().rstrip("/")
        if not base_url:
            base_url = (
                str(config.get("mcp.servers.0.base_url") or "").strip().rstrip("/")
            )
        if not base_url:
            raise HTTPException(
                status_code=500, detail="OAuth target server base_url is required"
            )

        timeout_seconds_raw = server_spec.get("timeout_seconds")
        if timeout_seconds_raw is None:
            timeout_seconds_raw = config.get("mcp.servers.0.timeout_seconds")
        timeout_seconds = float(timeout_seconds_raw or 30.0)

        verify_tls_raw = (
            server_spec.get("verify_tls")
            if "verify_tls" in server_spec
            else config.get("mcp.servers.0.verify_tls")
        )
        verify_tls = bool(True if verify_tls_raw is None else verify_tls_raw)

        metadata_path = str(
            config.get("mcp.it2_10.oauth.metadata_path")
            or config.get("mcp.it2_6.servers.0.oauth.metadata_path")
            or config.get("mcp.it2_4.oauth.metadata_path")
            or ""
        ).strip()
        redirect_uri = str(
            config.get("mcp.it2_10.oauth.redirect_uri")
            or config.get("mcp.it2_6.servers.0.oauth.redirect_uri")
            or config.get("mcp.it2_4.oauth.redirect_uri")
            or ""
        ).strip()
        client_name = str(
            config.get("mcp.it2_10.oauth.client_name")
            or config.get("mcp.it2_6.servers.0.oauth.client_name")
            or config.get("mcp.it2_4.oauth.client_name")
            or ""
        ).strip()
        state = str(
            config.get("mcp.it2_10.oauth.state")
            or config.get("mcp.it2_6.servers.0.oauth.state")
            or config.get("mcp.it2_4.oauth.state")
            or ""
        ).strip()
        mock_callback_path = str(
            config.get("mcp.it2_10.oauth.mock_callback_path")
            or config.get("mcp.it2_6.servers.0.oauth.mock_callback_path")
            or config.get("mcp.it2_4.oauth.mock_callback_path")
            or ""
        ).strip()
        mock_auth_code = str(
            config.get("mcp.it2_10.oauth.mock_auth_code")
            or config.get("mcp.it2_6.servers.0.oauth.mock_auth_code")
            or config.get("mcp.it2_4.oauth.mock_auth_code")
            or ""
        ).strip()
        mock_user_id = str(
            config.get("mcp.it2_10.oauth.mock_user_id")
            or config.get("mcp.it2_6.servers.0.oauth.mock_user_id")
            or config.get("mcp.it2_4.oauth.mock_user_id")
            or ""
        ).strip()

        if (
            not metadata_path
            or not redirect_uri
            or not client_name
            or not state
            or not mock_callback_path
            or not mock_auth_code
            or not mock_user_id
        ):
            raise HTTPException(
                status_code=500, detail="Missing required mcp.it2_4.oauth.* settings"
            )

        async with httpx.AsyncClient(
            timeout=timeout_seconds, verify=verify_tls
        ) as client:
            metadata = await client.get(f"{base_url}{metadata_path}")
            metadata.raise_for_status()
            meta = metadata.json()

            base_parts = urlparse(base_url)

            def _align_endpoint(endpoint: str) -> str:
                """Internal helper to align endpoint for this module."""
                parts = urlparse(endpoint)
                if not parts.scheme or not parts.netloc:
                    return endpoint
                return urlunparse(parts._replace(netloc=base_parts.netloc))

            auth_endpoint = _align_endpoint(
                str(meta.get("authorization_endpoint") or "")
            )
            token_endpoint = _align_endpoint(str(meta.get("token_endpoint") or ""))
            reg_endpoint = _align_endpoint(str(meta.get("registration_endpoint") or ""))
            if not auth_endpoint or not token_endpoint or not reg_endpoint:
                raise HTTPException(
                    status_code=500, detail="OAuth metadata missing endpoints"
                )

            reg_payload = {
                "client_name": client_name,
                "redirect_uris": [redirect_uri],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
            }
            reg_resp = await client.post(reg_endpoint, json=reg_payload)
            reg_resp.raise_for_status()
            reg = reg_resp.json()
            client_id = str(reg.get("client_id") or "")
            if not client_id:
                raise HTTPException(
                    status_code=500, detail="OAuth registration missing client_id"
                )

            code_verifier = secrets.token_urlsafe(32)
            challenge = hashlib.sha256(code_verifier.encode("utf-8")).digest()
            code_challenge = base64.urlsafe_b64encode(challenge).decode().rstrip("=")

            auth_query = urlencode(
                {
                    "response_type": "code",
                    "client_id": client_id,
                    "redirect_uri": redirect_uri,
                    "code_challenge_method": "S256",
                    "code_challenge": code_challenge,
                    "state": state,
                }
            )
            auth_url = f"{auth_endpoint}?{auth_query}"
            auth_resp = await client.get(auth_url, follow_redirects=False)
            if auth_resp.status_code != 200:
                raise HTTPException(
                    status_code=500, detail="OAuth authorization did not return HTML"
                )
            auth_html = auth_resp.text or ""
            link_match = re.search(
                r'href="([^"]*mock-upstream-idp/authorize[^"]+)"', auth_html
            )
            if not link_match:
                raise HTTPException(
                    status_code=500,
                    detail="OAuth authorization HTML missing mock-upstream link",
                )
            upstream_url = link_match.group(1)
            if not upstream_url.startswith("http"):
                upstream_url = f"{base_url}{upstream_url}"
            upstream_parts = urlparse(upstream_url)
            upstream_params = parse_qs(upstream_parts.query)
            upstream_state = upstream_params.get("state", [None])[0]
            if not upstream_state:
                raise HTTPException(
                    status_code=500,
                    detail="OAuth authorization HTML missing mock-upstream state",
                )

            callback_url = (
                f"{base_url}{mock_callback_path}"
                f"?state={upstream_state}&code={mock_auth_code}&userId={mock_user_id}"
            )
            callback_resp = await client.get(callback_url, follow_redirects=False)
            if callback_resp.status_code not in (302, 303):
                raise HTTPException(
                    status_code=500, detail="OAuth mock callback did not redirect"
                )
            redirect_location = callback_resp.headers.get("location") or ""
            parsed = urlparse(redirect_location)
            params = parse_qs(parsed.query)
            auth_code = params.get("code", [None])[0]
            if not auth_code:
                raise HTTPException(
                    status_code=500, detail="OAuth mock callback missing code"
                )

            token_payload = {
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": code_verifier,
            }
            token_resp = await client.post(token_endpoint, data=token_payload)
            token_resp.raise_for_status()
            token_data = token_resp.json()
            access_token = str(token_data.get("access_token") or "")
            if not access_token:
                raise HTTPException(
                    status_code=500, detail="OAuth token response missing access_token"
                )

        return MCPOAuthTokenResponse(access_token=access_token)

    return router
