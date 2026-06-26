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

import json
from typing import Any, Dict

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_mcp import create_session, mcp_execute
from tests.helpers.api_server import api_base_url, api_headers, start_api, stop_api, wait_for_api


def _require_cfg(cfg: ConfigManager, key: str):
    value = cfg.get(key)
    if value is None:
        raise RuntimeError(f"CRITICAL ERROR: missing required configuration key: {key}")
    return value


def _extract_text(result: Dict[str, Any]) -> str:
    result = _unwrap_rpc_result(result)
    content = result.get("content")
    if not isinstance(content, list):
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            response = str(structured.get("response") or "").strip()
            if response:
                return response
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and str(item.get("type") or "") == "text":
            text = str(item.get("text") or "").strip()
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def _unwrap_rpc_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise tool payloads that may be wrapped in a JSON-RPC envelope."""
    inner = result.get("result")
    if isinstance(inner, dict):
        return inner
    return result


def _extract_session_id_from_start_session(result: Dict[str, Any]) -> str:
    result = _unwrap_rpc_result(result)
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        sid = str(structured.get("session_id") or structured.get("id") or "").strip()
        if sid:
            return sid

    text = _extract_text(result)
    if text:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                sid = str(parsed.get("session_id") or parsed.get("id") or "").strip()
                if sid:
                    return sid
        except Exception:
            pass
    return ""


def _extract_session_ids_from_list_sessions(result: Dict[str, Any]) -> list[str]:
    result = _unwrap_rpc_result(result)
    candidate_ids: list[str] = []

    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        sessions = structured.get("sessions")
        if isinstance(sessions, list) and sessions:
            for item in sessions:
                if isinstance(item, dict):
                    sid = str(item.get("id") or item.get("session_id") or "").strip()
                    if sid and sid not in candidate_ids:
                        candidate_ids.append(sid)

    if not candidate_ids:
        text = _extract_text(result)
        if text:
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = {}
            if isinstance(parsed, dict):
                sessions = parsed.get("sessions")
                if isinstance(sessions, list) and sessions:
                    for item in sessions:
                        if isinstance(item, dict):
                            sid = str(item.get("id") or item.get("session_id") or "").strip()
                            if sid and sid not in candidate_ids:
                                candidate_ids.append(sid)

    def _sort_key(value: str):
        if value.isdigit():
            return (1, int(value))
        return (0, value)

    return sorted(candidate_ids, key=_sort_key, reverse=True)


@pytest.fixture(scope="module", autouse=True)
def _api_server(env_file):
    cfg = ConfigManager(env_file=env_file)
    start_api(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        yield None
    finally:
        stop_api(cfg, env_file=env_file)
@pytest.mark.IT
@pytest.mark.mcp
@pytest.mark.req("FR-011")


@pytest.mark.asyncio
async def test_it2_18_expertagent_hungarian_mcp(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds") or 360)
    protocol_version = str(_require_cfg(cfg, "mcp.defaults.protocol_version")).strip()
    require_initialize = bool(cfg.get("mcp.api.require_initialize") or False)
    server_cfg = cfg.get("mcp.it2_18.server")
    if server_cfg is not None and not isinstance(server_cfg, dict):
        raise RuntimeError("CRITICAL ERROR: mcp.it2_18.server must be an object")
    server_index_raw = cfg.get("mcp.it2_18.server_index")
    try:
        server_index = int(server_index_raw) if server_index_raw is not None else 0
    except (TypeError, ValueError) as e:
        raise RuntimeError("CRITICAL ERROR: mcp.it2_18.server_index must be an integer") from e

    start_name = str(_require_cfg(cfg, "mcp.it2_18.tools.start_session.name")).strip()
    start_args = dict(_require_cfg(cfg, "mcp.it2_18.tools.start_session.arguments") or {})
    chat_name = str(_require_cfg(cfg, "mcp.it2_18.tools.chat.name")).strip()
    chat_args = dict(_require_cfg(cfg, "mcp.it2_18.tools.chat.arguments") or {})
    prompt = str(_require_cfg(cfg, "chat_tests.hu_translator.prompt")).strip()

    async with httpx.AsyncClient(base_url=base_url, headers=api_headers(cfg), timeout=timeout_seconds) as client:
        session_id = await create_session(client, metadata={"suite": "it2.18"})

        start_result = await mcp_execute(
            client,
            session_id=session_id,
            server_index=None if isinstance(server_cfg, dict) else server_index,
            server=server_cfg if isinstance(server_cfg, dict) else None,
            protocol_version=protocol_version,
            require_initialize=require_initialize,
            steps=[
                {"method": "tools/list"},
                {"method": "tools/call", "params": {"name": start_name, "arguments": start_args}},
            ],
        )
        start_items = start_result.get("results") or []
        if not start_items or not start_items[0].get("ok"):
            raise RuntimeError("CRITICAL ERROR: tools/list failed for IT2.18")
        if not start_items[1].get("ok"):
            raise RuntimeError(f"CRITICAL ERROR: start_session failed: {start_items[1].get('error')}")
        start_payload = start_items[1].get("result") or {}
        if start_payload.get("isError") is True:
            raise RuntimeError("CRITICAL ERROR: start_session returned isError=true")

        remote_session_id = _extract_session_id_from_start_session(start_payload)
        candidate_session_ids: list[str] = []
        if remote_session_id:
            candidate_session_ids.append(remote_session_id)
        if not remote_session_id:
            # Known server behavior: when session quota is full, start_session can return a queued response.
            list_result = await mcp_execute(
                client,
                session_id=session_id,
                server_index=None if isinstance(server_cfg, dict) else server_index,
                server=server_cfg if isinstance(server_cfg, dict) else None,
                protocol_version=protocol_version,
                require_initialize=require_initialize,
                steps=[
                    {
                        "method": "tools/call",
                        "params": {"name": "list_sessions", "arguments": {"user_id": int(start_args.get("user_id", 1))}},
                    }
                ],
            )
            list_items = list_result.get("results") or []
            if not list_items or not list_items[0].get("ok"):
                raise RuntimeError("CRITICAL ERROR: queued start_session and list_sessions fallback failed")
            candidate_session_ids.extend(
                sid
                for sid in _extract_session_ids_from_list_sessions(list_items[0].get("result") or {})
                if sid not in candidate_session_ids
            )
        if not candidate_session_ids:
            raise RuntimeError("CRITICAL ERROR: no usable session_id from start_session/list_sessions")

        last_error = "chat did not return a usable response"
        for candidate_session_id in candidate_session_ids:
            chat_call_args = dict(chat_args)
            chat_call_args["session_id"] = candidate_session_id
            chat_call_args["message"] = prompt

            try:
                chat_result = await mcp_execute(
                    client,
                    session_id=session_id,
                    server_index=None if isinstance(server_cfg, dict) else server_index,
                    server=server_cfg if isinstance(server_cfg, dict) else None,
                    protocol_version=protocol_version,
                    require_initialize=require_initialize,
                    steps=[
                        {"method": "tools/call", "params": {"name": chat_name, "arguments": chat_call_args}},
                    ],
                )
            except Exception as e:
                last_error = str(e)
                continue

            chat_items = chat_result.get("results") or []
            if not chat_items or not chat_items[0].get("ok"):
                last_error = f"chat tool failed: {(chat_items[0] if chat_items else {}).get('error')}"
                continue

            payload = chat_items[0].get("result") or {}
            if payload.get("isError") is True:
                last_error = "chat tool returned isError=true"
                continue
            text = _extract_text(payload)
            if text:
                break
            last_error = "chat tool returned empty text"
        else:
            raise RuntimeError(f"CRITICAL ERROR: {last_error}")

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.integration, pytest.mark.mcp, pytest.mark.heavy]
