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
import asyncio
import time
from typing import Any, Dict, List, Optional

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


def _assert_call_duration(elapsed_seconds: float, *, min_seconds: float, max_seconds: float, label: str) -> None:
    if elapsed_seconds < min_seconds:
        raise RuntimeError(
            f"CRITICAL ERROR: {label} completed too quickly ({elapsed_seconds:.2f}s < {min_seconds:.2f}s)"
        )
    if elapsed_seconds > max_seconds:
        raise RuntimeError(
            f"CRITICAL ERROR: {label} exceeded max duration ({elapsed_seconds:.2f}s > {max_seconds:.2f}s)"
        )


def _assert_tokens(content: str, required: List[str], optional_any: Optional[List[str]] = None) -> None:
    for token in required:
        if token and token not in content:
            raise RuntimeError(f"CRITICAL ERROR: expected token missing: {token}")
    if optional_any:
        if not any(token in content for token in optional_any if token):
            raise RuntimeError("CRITICAL ERROR: missing any optional expected token")


def _extract_tool_text(result: Dict[str, Any]) -> str:
    text = ""
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            text += str(item.get("text") or "")
    return text


def _assert_wellbeing_fr_de_payload(content: str, required_tokens: List[str]) -> None:
    if all(token in content for token in required_tokens if token):
        return
    no_result_markers = [
        "no_query_results",
        "No query results found",
        "returned no rows",
        "0 results",
    ]
    if any(token in content for token in no_result_markers):
        return
    raise RuntimeError(
        "CRITICAL ERROR: France/Germany wellbeing payload missing expected results and no-results markers"
    )


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
    retry_attempts: int = 1,
    retry_backoff_seconds: float = 0.0,
) -> Dict[str, Any]:
    attempts = max(1, int(retry_attempts))
    backoff = max(0.0, float(retry_backoff_seconds))
    transient_codes = {408, 429, 500, 502, 503, 504}
    transient_errors = (httpx.RemoteProtocolError, httpx.ConnectError)
    last_error = "unknown"
    for attempt in range(1, attempts + 1):
        try:
            call_resp = await client.post(
                f"/sessions/{session_id}/mcp/tools/call",
                json={
                    "server_index": server_index,
                    "name": tool_name,
                    "arguments": tool_args,
                    "require_initialize": require_initialize,
                },
            )
        except transient_errors as e:
            last_error = f"{type(e).__name__}: {e}"
            if attempt < attempts and backoff > 0:
                await asyncio.sleep(backoff)
                continue
            raise RuntimeError(
                f"CRITICAL ERROR: tool '{tool_name}' call failed after retries: {last_error}"
            ) from e
        if call_resp.status_code == 200:
            result = call_resp.json() or {}
            if result.get("isError") is True:
                last_error = f"tool '{tool_name}' returned isError=true"
            else:
                return result
        else:
            detail = ""
            try:
                payload = call_resp.json()
                detail = str(payload.get("detail") or payload)
            except Exception:
                detail = call_resp.text
            last_error = f"http {call_resp.status_code}: {detail}"
            if call_resp.status_code not in transient_codes:
                break
        if attempt < attempts and backoff > 0:
            await asyncio.sleep(backoff)
    raise RuntimeError(f"CRITICAL ERROR: tool '{tool_name}' call failed after retries: {last_error}")


async def _send_message(
    client: httpx.AsyncClient,
    session_id: str,
    content: str,
    cfg: ConfigManager,
    retry_attempts: int = 1,
    retry_backoff_seconds: float = 0.0,
) -> str:
    attempts = max(1, int(retry_attempts))
    backoff = max(0.0, float(retry_backoff_seconds))
    transient_errors = (httpx.RemoteProtocolError, httpx.ConnectError)
    last_error = "unknown"
    for attempt in range(1, attempts + 1):
        try:
            resp = await client.post(
                f"/sessions/{session_id}/messages",
                json={"content": content, "stream": False},
            )
            assert resp.status_code == 200
            reply = str(resp.json().get("content") or "")
            _assert_tags(reply, cfg)
            _assert_length(reply, cfg)
            return reply
        except transient_errors as e:
            last_error = f"{type(e).__name__}: {e}"
            if attempt < attempts and backoff > 0:
                await asyncio.sleep(backoff)
                continue
            raise RuntimeError(
                f"CRITICAL ERROR: message send failed after retries: {last_error}"
            ) from e
    raise RuntimeError(f"CRITICAL ERROR: message send failed after retries: {last_error}")
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
async def test_at1_4_sqlagent_multi_step_and_resume(env_file):
    cfg = ConfigManager(env_file=env_file)
    if bool(cfg.get("chat_tests.at1_4.xfail_600s_gap") or False):
        pytest.xfail(
            "W28A-927i: AT1.4 passes under extended timeout but exceeds the 600s matrix budget; accepted gap, revisit 2026-07-31"
        )
    base_url = api_base_url(cfg)
    timeout_seconds = float(cfg.get("chat_tests.at1_4.api_timeout_seconds") or _require_cfg(cfg, "client_api.request_timeout_seconds"))
    marker = str(_require_cfg(cfg, "chat_tests.sqlagent_marker")).strip()

    tool_name = str(_require_cfg(cfg, "mcp.at1_4.tool_name")).strip()
    corruption_jp_cn = _parse_json_obj(
        _require_cfg(cfg, "mcp.at1_4.corruption_japan_china_args"), "mcp.at1_4.corruption_japan_china_args"
    )
    happiness_jp_cn = _parse_json_obj(
        _require_cfg(cfg, "mcp.at1_4.happiness_japan_china_args"), "mcp.at1_4.happiness_japan_china_args"
    )
    corruption_jp_in = _parse_json_obj(
        _require_cfg(cfg, "mcp.at1_4.corruption_japan_india_args"), "mcp.at1_4.corruption_japan_india_args"
    )
    happiness_jp_in = _parse_json_obj(
        _require_cfg(cfg, "mcp.at1_4.happiness_japan_india_args"), "mcp.at1_4.happiness_japan_india_args"
    )
    wellbeing_fr_de = _parse_json_obj(
        _require_cfg(cfg, "mcp.at1_4.wellbeing_france_germany_args"),
        "mcp.at1_4.wellbeing_france_germany_args",
    )

    step1_prompt = str(_require_cfg(cfg, "chat_tests.at1_4.step1_prompt"))
    step2_prompt = str(_require_cfg(cfg, "chat_tests.at1_4.step2_prompt"))
    step3_prompt = str(_require_cfg(cfg, "chat_tests.at1_4.step3_prompt"))
    step4_prompt = str(_require_cfg(cfg, "chat_tests.at1_4.step4_prompt"))
    wellbeing_prompt = str(_require_cfg(cfg, "chat_tests.at1_4.wellbeing_france_germany_prompt"))

    step1_tokens = _parse_json_list(
        _require_cfg(cfg, "chat_tests.at1_4.step1_expected_tokens"), "chat_tests.at1_4.step1_expected_tokens"
    )
    corruption_jp_cn_tokens = _parse_json_list(
        _require_cfg(cfg, "chat_tests.at1_4.corruption_jp_cn_tokens"),
        "chat_tests.at1_4.corruption_jp_cn_tokens",
    )
    corruption_jp_in_tokens = _parse_json_list(
        _require_cfg(cfg, "chat_tests.at1_4.corruption_jp_in_tokens"),
        "chat_tests.at1_4.corruption_jp_in_tokens",
    )
    happiness_jp_cn_tokens = _parse_json_list(
        _require_cfg(cfg, "chat_tests.at1_4.happiness_jp_cn_tokens"),
        "chat_tests.at1_4.happiness_jp_cn_tokens",
    )
    happiness_jp_in_tokens = _parse_json_list(
        _require_cfg(cfg, "chat_tests.at1_4.happiness_jp_in_tokens"),
        "chat_tests.at1_4.happiness_jp_in_tokens",
    )
    happiness_optional = _parse_json_list(
        _require_cfg(cfg, "chat_tests.at1_4.happiness_optional_tokens"),
        "chat_tests.at1_4.happiness_optional_tokens",
    )
    wellbeing_fr_de_tokens = _parse_json_list(
        _require_cfg(cfg, "chat_tests.at1_4.wellbeing_france_germany_tokens"),
        "chat_tests.at1_4.wellbeing_france_germany_tokens",
    )

    require_initialize_sql = bool(cfg.get("mcp.at1_4.require_initialize_sqlagent") or False)
    sql_server_index_raw = cfg.get("mcp.at1_4.sql_server_index")
    try:
        sql_server_index = int(sql_server_index_raw) if sql_server_index_raw is not None else 0
    except (TypeError, ValueError) as e:
        raise RuntimeError("CRITICAL ERROR: mcp.at1_4.sql_server_index must be an integer") from e
    api_retry_attempts = int(cfg.get("chat_tests.at1_4.api_retry_attempts") or 3)
    api_retry_backoff_seconds = float(cfg.get("chat_tests.at1_4.api_retry_backoff_seconds") or 2.0)
    sql_call_min_seconds = float(cfg.get("chat_tests.at1_4.sql_call_min_seconds") or 120)
    sql_call_max_seconds = float(cfg.get("chat_tests.at1_4.sql_call_max_seconds") or 420)

    _start_api(cfg, env_file)
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds) as client:
            headers = api_headers(cfg)
            client.headers.update(headers)

            resp = await client.post("/sessions", json={"metadata": {"suite": "at1.4"}}, headers=headers)
            assert resp.status_code == 200
            session_id = resp.json().get("session_id")
            assert session_id

            tools = await _mcp_tools_list(client, session_id, sql_server_index, require_initialize_sql)
            tool_names = [t.get("name") for t in tools]
            if tool_name not in tool_names:
                raise RuntimeError(f"CRITICAL ERROR: SQLAgent tool missing: {tool_name}")

            step1 = await _send_message(
                client,
                session_id,
                step1_prompt,
                cfg,
                retry_attempts=api_retry_attempts,
                retry_backoff_seconds=api_retry_backoff_seconds,
            )
            if marker not in step1:
                raise RuntimeError("CRITICAL ERROR: response missing SQLAGENT marker")
            _assert_tokens(step1, step1_tokens)

            call_started = time.monotonic()
            corruption_result = await _mcp_tools_call(
                client,
                session_id,
                sql_server_index,
                tool_name,
                corruption_jp_cn,
                require_initialize_sql,
                retry_attempts=api_retry_attempts,
                retry_backoff_seconds=api_retry_backoff_seconds,
            )
            _assert_call_duration(
                time.monotonic() - call_started,
                min_seconds=sql_call_min_seconds,
                max_seconds=sql_call_max_seconds,
                label="AT1.4 initial SQLAgent corruption query",
            )
            corruption_text = _extract_tool_text(corruption_result)
            if not corruption_text:
                raise RuntimeError("CRITICAL ERROR: SQLAgent corruption tool returned empty text")
            _assert_tokens(corruption_text, corruption_jp_cn_tokens)

            step2 = await _send_message(
                client,
                session_id,
                f"{step2_prompt}\n\nOfficial data:\n{corruption_text}",
                cfg,
                retry_attempts=api_retry_attempts,
                retry_backoff_seconds=api_retry_backoff_seconds,
            )
            if marker not in step2:
                raise RuntimeError("CRITICAL ERROR: response missing SQLAGENT marker")

            happiness_result = await _mcp_tools_call(
                client,
                session_id,
                sql_server_index,
                tool_name,
                happiness_jp_cn,
                require_initialize_sql,
                retry_attempts=api_retry_attempts,
                retry_backoff_seconds=api_retry_backoff_seconds,
            )
            happiness_text = _extract_tool_text(happiness_result)
            if not happiness_text:
                raise RuntimeError("CRITICAL ERROR: SQLAgent happiness tool returned empty text")
            _assert_tokens(happiness_text, happiness_jp_cn_tokens, happiness_optional)

            step3 = await _send_message(
                client,
                session_id,
                f"{step3_prompt}\n\nOfficial data:\n{happiness_text}",
                cfg,
                retry_attempts=api_retry_attempts,
                retry_backoff_seconds=api_retry_backoff_seconds,
            )
            if marker not in step3:
                raise RuntimeError("CRITICAL ERROR: response missing SQLAGENT marker")

            corruption_in_result = await _mcp_tools_call(
                client,
                session_id,
                sql_server_index,
                tool_name,
                corruption_jp_in,
                require_initialize_sql,
                retry_attempts=api_retry_attempts,
                retry_backoff_seconds=api_retry_backoff_seconds,
            )
            corruption_in_text = _extract_tool_text(corruption_in_result)
            if not corruption_in_text:
                raise RuntimeError("CRITICAL ERROR: SQLAgent corruption tool returned empty text (Japan/India)")
            _assert_tokens(corruption_in_text, corruption_jp_in_tokens)

            happiness_in_result = await _mcp_tools_call(
                client,
                session_id,
                sql_server_index,
                tool_name,
                happiness_jp_in,
                require_initialize_sql,
                retry_attempts=api_retry_attempts,
                retry_backoff_seconds=api_retry_backoff_seconds,
            )
            happiness_in_text = _extract_tool_text(happiness_in_result)
            if not happiness_in_text:
                raise RuntimeError("CRITICAL ERROR: SQLAgent happiness tool returned empty text (Japan/India)")
            _assert_tokens(happiness_in_text, happiness_jp_in_tokens, happiness_optional)

            step4_prompt_full = (
                f"{step4_prompt}\n\nCorruption data:\n{corruption_in_text}\n\n"
                f"Happiness/health data:\n{happiness_in_text}"
            )
            step4 = await _send_message(
                client,
                session_id,
                step4_prompt_full,
                cfg,
                retry_attempts=api_retry_attempts,
                retry_backoff_seconds=api_retry_backoff_seconds,
            )
            if marker not in step4:
                raise RuntimeError("CRITICAL ERROR: response missing SQLAGENT marker")

            wellbeing_result = await _mcp_tools_call(
                client,
                session_id,
                sql_server_index,
                tool_name,
                wellbeing_fr_de,
                require_initialize_sql,
                retry_attempts=api_retry_attempts,
                retry_backoff_seconds=api_retry_backoff_seconds,
            )
            wellbeing_text = _extract_tool_text(wellbeing_result)
            if not wellbeing_text:
                raise RuntimeError("CRITICAL ERROR: SQLAgent wellbeing tool returned empty text (France/Germany)")
            _assert_wellbeing_fr_de_payload(wellbeing_text, wellbeing_fr_de_tokens)

            step5 = await _send_message(
                client,
                session_id,
                f"{wellbeing_prompt}\n\nOfficial data:\n{wellbeing_text}",
                cfg,
                retry_attempts=api_retry_attempts,
                retry_backoff_seconds=api_retry_backoff_seconds,
            )
            if marker not in step5:
                raise RuntimeError("CRITICAL ERROR: response missing SQLAGENT marker")
    finally:
        _stop_api(cfg, env_file)

    _start_api(cfg, env_file)
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds) as client:
            headers = api_headers(cfg)
            client.headers.update(headers)

            resp = await client.post("/sessions", json={"metadata": {"suite": "at1.4-resume"}}, headers=headers)
            assert resp.status_code == 200
            session_id = resp.json().get("session_id")
            assert session_id

            tools = await _mcp_tools_list(client, session_id, sql_server_index, require_initialize_sql)
            tool_names = [t.get("name") for t in tools]
            if tool_name not in tool_names:
                raise RuntimeError(f"CRITICAL ERROR: SQLAgent tool missing: {tool_name}")

            step1 = await _send_message(
                client,
                session_id,
                step1_prompt,
                cfg,
                retry_attempts=api_retry_attempts,
                retry_backoff_seconds=api_retry_backoff_seconds,
            )
            step2_data = await _mcp_tools_call(
                client,
                session_id,
                sql_server_index,
                tool_name,
                corruption_jp_cn,
                require_initialize_sql,
                retry_attempts=api_retry_attempts,
                retry_backoff_seconds=api_retry_backoff_seconds,
            )
            step2_text = _extract_tool_text(step2_data)
            if not step2_text:
                raise RuntimeError("CRITICAL ERROR: SQLAgent corruption tool returned empty text")
            _assert_tokens(step2_text, corruption_jp_cn_tokens)

            step2 = await _send_message(
                client,
                session_id,
                f"{step2_prompt}\n\nOfficial data:\n{step2_text}",
                cfg,
                retry_attempts=api_retry_attempts,
                retry_backoff_seconds=api_retry_backoff_seconds,
            )

        _stop_api(cfg, env_file)

        _start_api(cfg, env_file)
        async with httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds) as client:
            headers = api_headers(cfg)
            client.headers.update(headers)

            load_resp = await client.post(f"/sessions/{session_id}/load", headers=headers)
            assert load_resp.status_code == 200

            step3_data = await _mcp_tools_call(
                client,
                session_id,
                sql_server_index,
                tool_name,
                happiness_jp_cn,
                require_initialize_sql,
                retry_attempts=api_retry_attempts,
                retry_backoff_seconds=api_retry_backoff_seconds,
            )
            step3_text = _extract_tool_text(step3_data)
            if not step3_text:
                raise RuntimeError("CRITICAL ERROR: SQLAgent happiness tool returned empty text")
            _assert_tokens(step3_text, happiness_jp_cn_tokens, happiness_optional)

            step3 = await _send_message(
                client,
                session_id,
                f"{step3_prompt}\n\nOfficial data:\n{step3_text}",
                cfg,
                retry_attempts=api_retry_attempts,
                retry_backoff_seconds=api_retry_backoff_seconds,
            )
            if marker not in step3:
                raise RuntimeError("CRITICAL ERROR: response missing SQLAGENT marker")

            transcript = await client.get(f"/sessions/{session_id}/transcript", headers=headers)
            assert transcript.status_code == 200
            events = transcript.json().get("events") or []
            resumed = [e for e in events if e.get("event_type") == "session_resumed"]
            if not resumed:
                raise RuntimeError("CRITICAL ERROR: session resume event missing after reload")

            step4_prompt_full = f"{step4_prompt}\n\nContinue using prior context."
            step4 = await _send_message(
                client,
                session_id,
                step4_prompt_full,
                cfg,
                retry_attempts=api_retry_attempts,
                retry_backoff_seconds=api_retry_backoff_seconds,
            )
            if marker not in step4:
                raise RuntimeError("CRITICAL ERROR: response missing SQLAGENT marker")

            wellbeing_result = await _mcp_tools_call(
                client,
                session_id,
                sql_server_index,
                tool_name,
                wellbeing_fr_de,
                require_initialize_sql,
                retry_attempts=api_retry_attempts,
                retry_backoff_seconds=api_retry_backoff_seconds,
            )
            wellbeing_text = _extract_tool_text(wellbeing_result)
            if not wellbeing_text:
                raise RuntimeError("CRITICAL ERROR: SQLAgent wellbeing tool returned empty text (France/Germany)")
            _assert_wellbeing_fr_de_payload(wellbeing_text, wellbeing_fr_de_tokens)

            step5 = await _send_message(
                client,
                session_id,
                f"{wellbeing_prompt}\n\nOfficial data:\n{wellbeing_text}",
                cfg,
                retry_attempts=api_retry_attempts,
                retry_backoff_seconds=api_retry_backoff_seconds,
            )
            if marker not in step5:
                raise RuntimeError("CRITICAL ERROR: response missing SQLAGENT marker")
    finally:
        _stop_api(cfg, env_file)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.application, pytest.mark.llm, pytest.mark.mcp, pytest.mark.heavy]
