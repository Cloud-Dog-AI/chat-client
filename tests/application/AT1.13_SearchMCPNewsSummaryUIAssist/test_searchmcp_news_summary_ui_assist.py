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
from typing import Any, Dict

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_server import api_base_url, api_headers, start_api, stop_api, wait_for_api
from tests.helpers.ollama_preflight import curl_ollama_tags


def _require_cfg(cfg: ConfigManager, key: str):
    value = cfg.get(key)
    if value is None:
        raise RuntimeError(f"CRITICAL ERROR: missing required configuration key: {key}")
    return value


def _parse_json_obj(value: Any, key: str) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception as e:
            raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON object") from e
        if not isinstance(parsed, dict):
            raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON object")
        return parsed
    raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON object")


def _extract_tool_text(result: Dict[str, Any]) -> str:
    parts = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            text = str(item.get("text") or "")
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


@pytest.fixture(scope="module", autouse=True)
def _api_server(env_file):
    cfg = ConfigManager(env_file=env_file)
    curl_ollama_tags(cfg)
    start_api(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        yield None
    finally:
        stop_api(cfg, env_file=env_file)
@pytest.mark.AT
@pytest.mark.mcp
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
async def test_at1_13_bbc_world_news_summary_uses_search_context(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))

    search_server_index = int(cfg.get("mcp.at1_5.search_server_index") or 0)
    search_tool_name = str(_require_cfg(cfg, "mcp.at1_5.search_tool_name")).strip()
    news_search_args = _parse_json_obj(_require_cfg(cfg, "mcp.at1_5.news_search_args"), "mcp.at1_5.news_search_args")
    require_initialize = bool(cfg.get("mcp.at1_5.require_initialize_search") or False)

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds, headers=api_headers(cfg)) as client:
        created = await client.post("/sessions", json={"metadata": {"suite": "at1.13"}})
        assert created.status_code == 200
        session_id = str(created.json().get("session_id") or "")
        assert session_id

        prefs = await client.put(
            f"/sessions/{session_id}/preferences",
            json={"selected_mcp_server_indices": [search_server_index]},
        )
        assert prefs.status_code == 200

        tools = await client.post(
            f"/sessions/{session_id}/mcp/tools/list",
            json={"server_index": search_server_index, "require_initialize": require_initialize},
        )
        assert tools.status_code == 200
        tool_names = {str(t.get("name") or "") for t in (tools.json().get("tools") or []) if isinstance(t, dict)}
        if search_tool_name not in tool_names:
            raise RuntimeError(f"CRITICAL ERROR: required search tool not listed: {search_tool_name}")

        search_call = await client.post(
            f"/sessions/{session_id}/mcp/tools/call",
            json={
                "server_index": search_server_index,
                "name": search_tool_name,
                "arguments": news_search_args,
                "require_initialize": require_initialize,
            },
        )
        assert search_call.status_code == 200
        search_result = search_call.json() or {}
        if search_result.get("isError") is True:
            raise RuntimeError("CRITICAL ERROR: search tool returned isError=true")

        search_text = _extract_tool_text(search_result)
        if not search_text:
            raise RuntimeError("CRITICAL ERROR: search tool returned empty text output")

        prompt = (
            "Summarise world news from BBC using only the context below. "
            "If context is present, do not claim you lack outside access.\n\n"
            f"Search context:\n{search_text}"
        )

        reply = await client.post(
            f"/sessions/{session_id}/messages",
            json={"content": prompt, "stream": False},
        )
        assert reply.status_code == 200
        content = str(reply.json().get("content") or "")
        if not content.strip():
            raise RuntimeError("CRITICAL ERROR: assistant response is empty")

        lowered = content.lower()
        blocked_markers = [
            "no current access to the outside world",
            "no access to the outside world",
            "i don't have access to the outside world",
            "i do not have access to the outside world",
        ]
        if any(marker in lowered for marker in blocked_markers):
            raise RuntimeError("CRITICAL ERROR: assistant still reported no outside access despite MCP context")

        transcript = await client.get(f"/sessions/{session_id}/transcript")
        assert transcript.status_code == 200
        events = transcript.json().get("events") or []
        assert any(e.get("event_type") == "mcp_tool_call" for e in events)
        assert any(e.get("event_type") == "mcp_tool_result" for e in events)
        assert any(e.get("event_type") == "assistant_message" for e in events)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.application, pytest.mark.llm, pytest.mark.mcp, pytest.mark.heavy]

