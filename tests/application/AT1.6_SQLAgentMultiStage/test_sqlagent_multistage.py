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
import json
from typing import Any, Dict, List, Optional

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from cloud_dog_chat_client.llm.response_policy import load_response_policy, validate_response
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


def _assert_tags(content: str, cfg: ConfigManager) -> None:
    thinking_tag = str(_require_cfg(cfg, "chat_tests.expected_thinking_tag"))
    reasoning_tag = str(_require_cfg(cfg, "chat_tests.expected_reasoning_tag"))
    if thinking_tag not in content:
        raise RuntimeError("CRITICAL ERROR: response missing <thinking> tag")
    if reasoning_tag not in content:
        raise RuntimeError("CRITICAL ERROR: response missing <reasoning> tag")


def _assert_length(content: str, cfg: ConfigManager) -> None:
    max_chars = int(_require_cfg(cfg, "chat_tests.max_response_chars"))
    if len(content) > max_chars:
        raise RuntimeError(
            f"CRITICAL ERROR: response length {len(content)} exceeds max {max_chars}"
        )


def _assert_tokens(content: str, required: List[str], optional_any: Optional[List[str]] = None) -> None:
    for token in required:
        if token and token not in content:
            raise RuntimeError(f"CRITICAL ERROR: expected token missing: {token}")
    if optional_any:
        if not any(token in content for token in optional_any if token):
            raise RuntimeError("CRITICAL ERROR: missing any optional expected token")


def _check_tool_output(
    content: str, required_any: List[str], empty_tokens: List[str], label: str
) -> bool:
    if required_any and any(token in content for token in required_any if token):
        return False
    if empty_tokens and any(token in content for token in empty_tokens if token):
        return True
    raise RuntimeError(f"CRITICAL ERROR: {label} output missing expected tokens")


def _extract_tool_text(result: Dict[str, Any]) -> str:
    text = ""
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            text += str(item.get("text") or "")
    return text


async def _post_with_retries(
    client: httpx.AsyncClient,
    url: str,
    payload: Dict[str, Any],
    *,
    retry_attempts: int,
    retry_backoff_seconds: float,
) -> httpx.Response:
    last_error: Optional[Exception] = None
    for attempt in range(retry_attempts + 1):
        try:
            return await client.post(url, json=payload)
        except httpx.TransportError as exc:
            last_error = exc
            if attempt >= retry_attempts:
                break
            await asyncio.sleep(retry_backoff_seconds)
    raise RuntimeError(f"CRITICAL ERROR: API request failed after retries: {last_error}") from last_error


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
    *,
    retry_attempts: int,
    retry_backoff_seconds: float,
) -> List[Dict[str, Any]]:
    list_resp: Optional[httpx.Response] = None
    for attempt in range(retry_attempts + 1):
        list_resp = await _post_with_retries(
            client,
            f"/sessions/{session_id}/mcp/tools/list",
            {"server_index": server_index, "require_initialize": require_initialize},
            retry_attempts=0,
            retry_backoff_seconds=retry_backoff_seconds,
        )
        if list_resp.status_code == 200:
            break
        if list_resp.status_code >= 500 and attempt < retry_attempts:
            await asyncio.sleep(retry_backoff_seconds)
            continue
        break
    assert list_resp is not None
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
    *,
    retry_attempts: int,
    retry_backoff_seconds: float,
) -> Dict[str, Any]:
    call_resp: Optional[httpx.Response] = None
    for attempt in range(retry_attempts + 1):
        call_resp = await _post_with_retries(
            client,
            f"/sessions/{session_id}/mcp/tools/call",
            {
                "server_index": server_index,
                "name": tool_name,
                "arguments": tool_args,
                "require_initialize": require_initialize,
            },
            retry_attempts=0,
            retry_backoff_seconds=retry_backoff_seconds,
        )
        if call_resp.status_code == 200:
            break
        if call_resp.status_code >= 500 and attempt < retry_attempts:
            await asyncio.sleep(retry_backoff_seconds)
            continue
        break
    assert call_resp is not None
    assert call_resp.status_code == 200
    result = call_resp.json() or {}
    if result.get("isError") is True:
        raise RuntimeError(f"CRITICAL ERROR: tool '{tool_name}' returned isError=true")
    return result


async def _send_message(
    client: httpx.AsyncClient,
    session_id: str,
    content: str,
    cfg: ConfigManager,
    response_policy,
    *,
    retry_attempts: int,
    retry_backoff_seconds: float,
) -> str:
    resp: Optional[httpx.Response] = None
    for attempt in range(retry_attempts + 1):
        resp = await _post_with_retries(
            client,
            f"/sessions/{session_id}/messages",
            {"content": content, "stream": False},
            retry_attempts=0,
            retry_backoff_seconds=retry_backoff_seconds,
        )
        if resp.status_code == 200:
            break
        if resp.status_code >= 500 and attempt < retry_attempts:
            await asyncio.sleep(retry_backoff_seconds)
            continue
        break
    assert resp is not None
    assert resp.status_code == 200, f"CRITICAL ERROR: /messages failed: {resp.status_code} {resp.text}"
    reply = str(resp.json().get("content") or "")
    _assert_tags(reply, cfg)
    _assert_length(reply, cfg)
    if response_policy.enforce:
        ok, error = validate_response(reply, response_policy)
        if not ok:
            raise RuntimeError(f"CRITICAL ERROR: response format invalid: {error}")
    return reply
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
async def test_at1_6_sqlagent_multi_step_with_searchmcp(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    marker = str(_require_cfg(cfg, "chat_tests.sqlagent_marker")).strip()
    response_policy = load_response_policy(cfg)

    sql_tool_name = str(_require_cfg(cfg, "mcp.at1_6.sql_tool_name")).strip()
    search_tool_name = str(_require_cfg(cfg, "mcp.at1_6.search_tool_name")).strip()
    corruption_args = _parse_json_obj(
        _require_cfg(cfg, "mcp.at1_6.corruption_sql_args"), "mcp.at1_6.corruption_sql_args"
    )
    happiness_args = _parse_json_obj(
        _require_cfg(cfg, "mcp.at1_6.happiness_sql_args"), "mcp.at1_6.happiness_sql_args"
    )
    news_args = _parse_json_obj(
        _require_cfg(cfg, "mcp.at1_6.news_search_args"), "mcp.at1_6.news_search_args"
    )

    step1_prompt = str(_require_cfg(cfg, "chat_tests.at1_6.step1_prompt"))
    step2_prompt = str(_require_cfg(cfg, "chat_tests.at1_6.step2_prompt"))
    step3_prompt = str(_require_cfg(cfg, "chat_tests.at1_6.step3_prompt"))
    step4_prompt = str(_require_cfg(cfg, "chat_tests.at1_6.step4_prompt"))
    step5_prompt = str(_require_cfg(cfg, "chat_tests.at1_6.step5_prompt"))

    step1_tokens = _parse_json_list(
        _require_cfg(cfg, "chat_tests.at1_6.step1_expected_tokens"),
        "chat_tests.at1_6.step1_expected_tokens",
    )
    corruption_tokens = _parse_json_list(
        _require_cfg(cfg, "chat_tests.at1_6.corruption_sql_tokens"),
        "chat_tests.at1_6.corruption_sql_tokens",
    )
    happiness_tokens = _parse_json_list(
        _require_cfg(cfg, "chat_tests.at1_6.happiness_sql_tokens"),
        "chat_tests.at1_6.happiness_sql_tokens",
    )
    corruption_empty_tokens = _parse_json_list(
        _require_cfg(cfg, "chat_tests.at1_6.corruption_empty_tokens"),
        "chat_tests.at1_6.corruption_empty_tokens",
    )
    happiness_empty_tokens = _parse_json_list(
        _require_cfg(cfg, "chat_tests.at1_6.happiness_empty_tokens"),
        "chat_tests.at1_6.happiness_empty_tokens",
    )
    corruption_missing_response_tokens = _parse_json_list(
        _require_cfg(cfg, "chat_tests.at1_6.corruption_missing_response_tokens"),
        "chat_tests.at1_6.corruption_missing_response_tokens",
    )
    corruption_missing_response_optional = _parse_json_list(
        _require_cfg(cfg, "chat_tests.at1_6.corruption_missing_response_optional_tokens"),
        "chat_tests.at1_6.corruption_missing_response_optional_tokens",
    )
    happiness_missing_response_tokens = _parse_json_list(
        _require_cfg(cfg, "chat_tests.at1_6.happiness_missing_response_tokens"),
        "chat_tests.at1_6.happiness_missing_response_tokens",
    )
    happiness_missing_response_optional = _parse_json_list(
        _require_cfg(cfg, "chat_tests.at1_6.happiness_missing_response_optional_tokens"),
        "chat_tests.at1_6.happiness_missing_response_optional_tokens",
    )
    news_tokens = _parse_json_list(
        _require_cfg(cfg, "chat_tests.at1_6.news_search_tokens"),
        "chat_tests.at1_6.news_search_tokens",
    )
    news_empty_tokens = _parse_json_list(
        _require_cfg(cfg, "chat_tests.at1_6.news_empty_tokens"),
        "chat_tests.at1_6.news_empty_tokens",
    )
    news_missing_response_tokens = _parse_json_list(
        _require_cfg(cfg, "chat_tests.at1_6.news_missing_response_tokens"),
        "chat_tests.at1_6.news_missing_response_tokens",
    )
    news_missing_response_optional = _parse_json_list(
        _require_cfg(cfg, "chat_tests.at1_6.news_missing_response_optional_tokens"),
        "chat_tests.at1_6.news_missing_response_optional_tokens",
    )
    compare_tokens = _parse_json_list(
        _require_cfg(cfg, "chat_tests.at1_6.compare_expected_tokens"),
        "chat_tests.at1_6.compare_expected_tokens",
    )
    retry_attempts = int(_require_cfg(cfg, "chat_tests.at1_6.api_retry_attempts"))
    retry_backoff_seconds = float(_require_cfg(cfg, "chat_tests.at1_6.api_retry_backoff_seconds"))

    require_initialize_sql = bool(cfg.get("mcp.at1_6.require_initialize_sqlagent") or False)
    require_initialize_search = bool(cfg.get("mcp.at1_6.require_initialize_search") or False)
    sql_server_index_raw = cfg.get("mcp.at1_6.sql_server_index")
    search_server_index_raw = cfg.get("mcp.at1_6.search_server_index")
    try:
        sql_server_index = int(sql_server_index_raw) if sql_server_index_raw is not None else 0
    except (TypeError, ValueError) as e:
        raise RuntimeError("CRITICAL ERROR: mcp.at1_6.sql_server_index must be an integer") from e
    try:
        search_server_index = int(search_server_index_raw) if search_server_index_raw is not None else 1
    except (TypeError, ValueError) as e:
        raise RuntimeError("CRITICAL ERROR: mcp.at1_6.search_server_index must be an integer") from e

    _start_api(cfg, env_file)
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds) as client:
            headers = api_headers(cfg)
            client.headers.update(headers)

            resp = await client.post("/sessions", json={"metadata": {"suite": "at1.6"}}, headers=headers)
            assert resp.status_code == 200
            session_id = resp.json().get("session_id")
            assert session_id

            sql_tools = await _mcp_tools_list(
                client,
                session_id,
                sql_server_index,
                require_initialize_sql,
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            if sql_tool_name not in [t.get("name") for t in sql_tools]:
                raise RuntimeError(f"CRITICAL ERROR: SQLAgent tool missing: {sql_tool_name}")

            search_tools = await _mcp_tools_list(
                client,
                session_id,
                search_server_index,
                require_initialize_search,
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            if search_tool_name not in [t.get("name") for t in search_tools]:
                raise RuntimeError(f"CRITICAL ERROR: Search MCP tool missing: {search_tool_name}")

            step1 = await _send_message(
                client,
                session_id,
                step1_prompt,
                cfg,
                response_policy,
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            if marker not in step1:
                raise RuntimeError("CRITICAL ERROR: response missing SQLAGENT marker")
            _assert_tokens(step1, step1_tokens)

            corruption_result = await _mcp_tools_call(
                client,
                session_id,
                sql_server_index,
                sql_tool_name,
                corruption_args,
                require_initialize_sql,
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            corruption_text = format_tool_output(_extract_tool_text(corruption_result), cfg, sql_server_index)
            if not corruption_text:
                raise RuntimeError("CRITICAL ERROR: SQLAgent corruption query returned empty text")
            corruption_missing = _check_tool_output(
                corruption_text, corruption_tokens, corruption_empty_tokens, "corruption"
            )

            step2 = await _send_message(
                client,
                session_id,
                f"{step2_prompt}\n\nOfficial data (from SQLAgent results):\n{corruption_text}",
                cfg,
                response_policy,
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            if marker not in step2:
                raise RuntimeError("CRITICAL ERROR: response missing SQLAGENT marker")
            if corruption_missing:
                _assert_tokens(step2, corruption_missing_response_tokens, corruption_missing_response_optional)

            happiness_result = await _mcp_tools_call(
                client,
                session_id,
                sql_server_index,
                sql_tool_name,
                happiness_args,
                require_initialize_sql,
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            happiness_text = format_tool_output(_extract_tool_text(happiness_result), cfg, sql_server_index)
            if not happiness_text:
                raise RuntimeError("CRITICAL ERROR: SQLAgent happiness query returned empty text")
            happiness_missing = _check_tool_output(
                happiness_text, happiness_tokens, happiness_empty_tokens, "happiness"
            )

            step3 = await _send_message(
                client,
                session_id,
                f"{step3_prompt}\n\nOfficial data (from SQLAgent results):\n{happiness_text}",
                cfg,
                response_policy,
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            if marker not in step3:
                raise RuntimeError("CRITICAL ERROR: response missing SQLAGENT marker")
            if happiness_missing:
                _assert_tokens(step3, happiness_missing_response_tokens, happiness_missing_response_optional)

            news_result = await _mcp_tools_call(
                client,
                session_id,
                search_server_index,
                search_tool_name,
                news_args,
                require_initialize_search,
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            news_text = format_tool_output(_extract_tool_text(news_result), cfg, search_server_index)
            if not news_text:
                raise RuntimeError("CRITICAL ERROR: Search MCP news query returned empty text")
            news_missing = _check_tool_output(news_text, news_tokens, news_empty_tokens, "news")

            step4 = await _send_message(
                client,
                session_id,
                f"{step4_prompt}\n\nOfficial data (from SearchMCP results):\n{news_text}",
                cfg,
                response_policy,
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            if marker not in step4:
                raise RuntimeError("CRITICAL ERROR: response missing SQLAGENT marker")
            if news_missing:
                _assert_tokens(step4, news_missing_response_tokens, news_missing_response_optional)

            step5_prompt_full = (
                f"{step5_prompt}\n\nOfficial data (corruption):\n{corruption_text}\n\n"
                f"Official data (happiness/health):\n{happiness_text}\n\n"
                f"Official data (news):\n{news_text}"
            )
            step5 = await _send_message(
                client,
                session_id,
                step5_prompt_full,
                cfg,
                response_policy,
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            if marker not in step5:
                raise RuntimeError("CRITICAL ERROR: response missing SQLAGENT marker")
            _assert_tokens(step5, compare_tokens)
    finally:
        _stop_api(cfg, env_file)

    _start_api(cfg, env_file)
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds) as client:
            headers = api_headers(cfg)
            client.headers.update(headers)

            resp = await client.post("/sessions", json={"metadata": {"suite": "at1.6-resume"}}, headers=headers)
            assert resp.status_code == 200
            session_id = resp.json().get("session_id")
            assert session_id

            sql_tools = await _mcp_tools_list(
                client,
                session_id,
                sql_server_index,
                require_initialize_sql,
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            if sql_tool_name not in [t.get("name") for t in sql_tools]:
                raise RuntimeError(f"CRITICAL ERROR: SQLAgent tool missing: {sql_tool_name}")

            search_tools = await _mcp_tools_list(
                client,
                session_id,
                search_server_index,
                require_initialize_search,
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            if search_tool_name not in [t.get("name") for t in search_tools]:
                raise RuntimeError(f"CRITICAL ERROR: Search MCP tool missing: {search_tool_name}")

            step1 = await _send_message(
                client,
                session_id,
                step1_prompt,
                cfg,
                response_policy,
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            if marker not in step1:
                raise RuntimeError("CRITICAL ERROR: response missing SQLAGENT marker")

            corruption_result = await _mcp_tools_call(
                client,
                session_id,
                sql_server_index,
                sql_tool_name,
                corruption_args,
                require_initialize_sql,
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            corruption_text = format_tool_output(_extract_tool_text(corruption_result), cfg, sql_server_index)
            if not corruption_text:
                raise RuntimeError("CRITICAL ERROR: SQLAgent corruption query returned empty text")
            corruption_missing = _check_tool_output(
                corruption_text, corruption_tokens, corruption_empty_tokens, "corruption"
            )

            step2 = await _send_message(
                client,
                session_id,
                f"{step2_prompt}\n\nOfficial data (from SQLAgent results):\n{corruption_text}",
                cfg,
                response_policy,
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            if marker not in step2:
                raise RuntimeError("CRITICAL ERROR: response missing SQLAGENT marker")
            if corruption_missing:
                _assert_tokens(step2, corruption_missing_response_tokens, corruption_missing_response_optional)

        _stop_api(cfg, env_file)

        _start_api(cfg, env_file)
        async with httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds) as client:
            headers = api_headers(cfg)
            client.headers.update(headers)

            load_resp = await client.post(f"/sessions/{session_id}/load", headers=headers)
            assert load_resp.status_code == 200

            happiness_result = await _mcp_tools_call(
                client,
                session_id,
                sql_server_index,
                sql_tool_name,
                happiness_args,
                require_initialize_sql,
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            happiness_text = format_tool_output(_extract_tool_text(happiness_result), cfg, sql_server_index)
            if not happiness_text:
                raise RuntimeError("CRITICAL ERROR: SQLAgent happiness query returned empty text")
            happiness_missing = _check_tool_output(
                happiness_text, happiness_tokens, happiness_empty_tokens, "happiness"
            )

            step3 = await _send_message(
                client,
                session_id,
                f"{step3_prompt}\n\nOfficial data (from SQLAgent results):\n{happiness_text}",
                cfg,
                response_policy,
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            if marker not in step3:
                raise RuntimeError("CRITICAL ERROR: response missing SQLAGENT marker")
            if happiness_missing:
                _assert_tokens(step3, happiness_missing_response_tokens, happiness_missing_response_optional)

            news_result = await _mcp_tools_call(
                client,
                session_id,
                search_server_index,
                search_tool_name,
                news_args,
                require_initialize_search,
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            news_text = format_tool_output(_extract_tool_text(news_result), cfg, search_server_index)
            if not news_text:
                raise RuntimeError("CRITICAL ERROR: Search MCP news query returned empty text")
            news_missing = _check_tool_output(news_text, news_tokens, news_empty_tokens, "news")

            step4 = await _send_message(
                client,
                session_id,
                f"{step4_prompt}\n\nOfficial data (from SearchMCP results):\n{news_text}",
                cfg,
                response_policy,
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            if marker not in step4:
                raise RuntimeError("CRITICAL ERROR: response missing SQLAGENT marker")
            if news_missing:
                _assert_tokens(step4, news_missing_response_tokens, news_missing_response_optional)

            transcript = await client.get(f"/sessions/{session_id}/transcript", headers=headers)
            assert transcript.status_code == 200
            events = transcript.json().get("events") or []
            resumed = [e for e in events if e.get("event_type") == "session_resumed"]
            if not resumed:
                raise RuntimeError("CRITICAL ERROR: session resume event missing after reload")

            step5_prompt_full = (
                f"{step5_prompt}\n\nOfficial data (corruption):\n{corruption_text}\n\n"
                f"Official data (happiness/health):\n{happiness_text}\n\n"
                f"Official data (news):\n{news_text}"
            )
            step5 = await _send_message(
                client,
                session_id,
                step5_prompt_full,
                cfg,
                response_policy,
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            if marker not in step5:
                raise RuntimeError("CRITICAL ERROR: response missing SQLAGENT marker")
            _assert_tokens(step5, compare_tokens)
    finally:
        _stop_api(cfg, env_file)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.application, pytest.mark.llm, pytest.mark.mcp, pytest.mark.heavy]

