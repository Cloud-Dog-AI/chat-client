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

import asyncio
import time
import json
from typing import Any, Dict, List

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from cloud_dog_chat_client.mcp.output_reducer import format_tool_output
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


def _parse_json_list(value: Any, key: str) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception as e:
            raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON list") from e
        if not isinstance(parsed, list):
            raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON list")
        return [str(item) for item in parsed]
    raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON list")


def _assert_call_duration(elapsed_seconds: float, *, min_seconds: float, max_seconds: float, label: str) -> None:
    if elapsed_seconds < min_seconds:
        raise RuntimeError(
            f"CRITICAL ERROR: {label} completed too quickly ({elapsed_seconds:.2f}s < {min_seconds:.2f}s)"
        )
    if elapsed_seconds > max_seconds:
        raise RuntimeError(
            f"CRITICAL ERROR: {label} exceeded max duration ({elapsed_seconds:.2f}s > {max_seconds:.2f}s)"
        )


def _assert_user_response(content: str, cfg: ConfigManager) -> None:
    thinking_tag = str(_require_cfg(cfg, "chat_tests.expected_thinking_tag"))
    if thinking_tag not in content:
        raise RuntimeError("CRITICAL ERROR: response missing <thinking> tag")

    answer_tag = str(_require_cfg(cfg, "llm.response.display_answer_tag")).strip()
    require_answer_tag = bool(cfg.get("chat_tests.at1_7.require_answer_tag") or False)
    if answer_tag and require_answer_tag:
        if f"<{answer_tag}>" not in content or f"</{answer_tag}>" not in content:
            raise RuntimeError("CRITICAL ERROR: response missing answer tag")

    marker_value = str(_require_cfg(cfg, "llm.response.marker_value")).strip()
    if marker_value and marker_value in content:
        raise RuntimeError("CRITICAL ERROR: marker leaked into user response")

    envelope_tag = str(_require_cfg(cfg, "llm.response.envelope_tag")).strip()
    if envelope_tag and f"<{envelope_tag}" in content:
        raise RuntimeError("CRITICAL ERROR: response envelope leaked into user response")

    max_chars = int(_require_cfg(cfg, "chat_tests.max_response_chars"))
    if len(content) > max_chars:
        raise RuntimeError(
            f"CRITICAL ERROR: response length {len(content)} exceeds max {max_chars}"
        )


def _extract_tool_text(result: Dict[str, Any]) -> str:
    text = ""
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            text += str(item.get("text") or "")
    return text


def _start_api(cfg: ConfigManager, env_file: str) -> None:
    curl_ollama_tags(cfg)
    start_api(cfg, env_file=env_file)
    wait_for_api(cfg)


def _stop_api(cfg: ConfigManager, env_file: str) -> None:
    stop_api(cfg, env_file=env_file)


async def _mcp_tools_list(
    client: httpx.AsyncClient,
    session_id: str,
    server_index: int,
    require_initialize: bool,
) -> List[Dict[str, Any]]:
    list_resp = await client.post(
        f"/sessions/{session_id}/mcp/tools/list",
        json={"server_index": server_index, "require_initialize": require_initialize},
    )
    assert list_resp.status_code == 200
    tools = list_resp.json().get("tools") or []
    if not isinstance(tools, list):
        raise RuntimeError("CRITICAL ERROR: tools/list returned non-list")
    return [t for t in tools if isinstance(t, dict)]


async def _mcp_tools_call(
    client: httpx.AsyncClient,
    session_id: str,
    server_index: int,
    tool_name: str,
    tool_args: Dict[str, Any],
    require_initialize: bool,
    retry_attempts: int = 3,
    retry_backoff_seconds: float = 2.0,
) -> Dict[str, Any]:
    for attempt in range(max(1, retry_attempts)):
        call_resp = await client.post(
            f"/sessions/{session_id}/mcp/tools/call",
            json={
                "server_index": server_index,
                "name": tool_name,
                "arguments": tool_args,
                "require_initialize": require_initialize,
            },
        )
        if call_resp.status_code == 200:
            result = call_resp.json() or {}
            if result.get("isError") is not True:
                return result
            if attempt >= retry_attempts - 1:
                raise RuntimeError(f"CRITICAL ERROR: tool '{tool_name}' returned isError=true")
        elif call_resp.status_code not in {502, 503, 504} or attempt >= retry_attempts - 1:
            assert call_resp.status_code == 200
        await asyncio.sleep(max(0.1, retry_backoff_seconds))
    raise RuntimeError(f"CRITICAL ERROR: tool '{tool_name}' call exhausted retries")
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
async def test_at1_7_sqlagent_thinking_display(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(cfg.get("chat_tests.at1_7.api_timeout_seconds") or _require_cfg(cfg, "client_api.request_timeout_seconds"))
    headers = api_headers(cfg)

    tool_name = str(_require_cfg(cfg, "mcp.at1_7.tool_name")).strip()
    tool_args = _parse_json_obj(_require_cfg(cfg, "mcp.at1_7.query_args"), "mcp.at1_7.query_args")
    tool_tokens = _parse_json_list(
        _require_cfg(cfg, "chat_tests.at1_7.tool_expected_tokens"),
        "chat_tests.at1_7.tool_expected_tokens",
    )

    prompt_template = str(_require_cfg(cfg, "chat_tests.at1_7.prompt"))
    if "{tool_output}" not in prompt_template:
        raise RuntimeError("CRITICAL ERROR: chat_tests.at1_7.prompt must include {tool_output}")

    require_initialize_sql = bool(cfg.get("mcp.at1_7.require_initialize_sqlagent") or False)
    sql_server_index_raw = cfg.get("mcp.at1_7.sql_server_index")
    api_retry_attempts = int(cfg.get("chat_tests.at1_7.api_retry_attempts") or 3)
    api_retry_backoff_seconds = float(cfg.get("chat_tests.at1_7.api_retry_backoff_seconds") or 2.0)
    sql_call_min_seconds = float(cfg.get("chat_tests.at1_7.sql_call_min_seconds") or 120)
    sql_call_max_seconds = float(cfg.get("chat_tests.at1_7.sql_call_max_seconds") or 420)
    try:
        sql_server_index = int(sql_server_index_raw) if sql_server_index_raw is not None else 0
    except (TypeError, ValueError) as e:
        raise RuntimeError("CRITICAL ERROR: mcp.at1_7.sql_server_index must be an integer") from e

    _start_api(cfg, env_file)
    try:
        async with httpx.AsyncClient(
            base_url=base_url, timeout=timeout_seconds, headers=headers
        ) as client:
            resp = await client.post("/sessions", json={"metadata": {"suite": "at1.7"}})
            assert resp.status_code == 200
            session_id = resp.json().get("session_id")
            assert session_id

            tools = await _mcp_tools_list(client, session_id, sql_server_index, require_initialize_sql)
            tool_names = {str(tool.get("name")) for tool in tools if tool.get("name")}
            if tool_name not in tool_names:
                raise RuntimeError("CRITICAL ERROR: expected SQLAgent tool missing from tools list")

            call_started = time.monotonic()
            result = await _mcp_tools_call(
                client,
                session_id,
                sql_server_index,
                tool_name,
                tool_args,
                require_initialize_sql,
                retry_attempts=api_retry_attempts,
                retry_backoff_seconds=api_retry_backoff_seconds,
            )
            _assert_call_duration(
                time.monotonic() - call_started,
                min_seconds=sql_call_min_seconds,
                max_seconds=sql_call_max_seconds,
                label="AT1.7 SQLAgent corruption query",
            )
            raw_text = _extract_tool_text(result)
            tool_text = format_tool_output(raw_text, cfg, sql_server_index)
            if not tool_text:
                raise RuntimeError("CRITICAL ERROR: SQLAgent tool output empty")
            for token in tool_tokens:
                if token and token not in tool_text:
                    raise RuntimeError(f"CRITICAL ERROR: expected SQLAgent token missing: {token}")

            prompt = prompt_template.format(tool_output=tool_text)
            send = await client.post(
                f"/sessions/{session_id}/messages",
                json={"content": prompt, "stream": False},
            )
            assert send.status_code == 200
            content = str(send.json().get("content") or "")

    finally:
        _stop_api(cfg, env_file)

    _assert_user_response(content, cfg)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.application, pytest.mark.llm, pytest.mark.mcp, pytest.mark.heavy]

