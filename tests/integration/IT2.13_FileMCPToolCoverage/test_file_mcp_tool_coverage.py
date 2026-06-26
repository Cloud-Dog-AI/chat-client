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
from typing import Any, Dict, List, Optional

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_server import api_base_url, api_headers, start_api, stop_api, wait_for_api
from tests.helpers.file_mcp_runtime import maybe_start_file_mcp, maybe_stop_file_mcp


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


def _extract_tool_text(result: Dict[str, Any]) -> str:
    text = ""
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            text += str(item.get("text") or "")
    return text


def _resolve_file_server(cfg: ConfigManager) -> tuple[Optional[int], Optional[Dict[str, Any]]]:
    server = cfg.get("mcp.it2_13.file_server")
    if server is not None and not isinstance(server, dict):
        raise RuntimeError("CRITICAL ERROR: mcp.it2_13.file_server must be an object")
    if isinstance(server, dict):
        return None, dict(server)
    return int(_require_cfg(cfg, "mcp.it2_13.file_server_index")), None


@pytest.fixture(scope="module", autouse=True)
def _api_server(env_file):
    cfg = ConfigManager(env_file=env_file)
    started_file_mcp = maybe_start_file_mcp(cfg)
    start_api(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        yield None
    finally:
        stop_api(cfg, env_file=env_file)
        if started_file_mcp:
            maybe_stop_file_mcp(cfg)


async def _mcp_tools_call(
    client: httpx.AsyncClient,
    session_id: str,
    server_index: Optional[int],
    server: Optional[Dict[str, Any]],
    name: str,
    arguments: Dict[str, Any],
    require_initialize: bool,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "name": name,
        "arguments": arguments,
        "require_initialize": require_initialize,
    }
    if server is not None:
        payload["server"] = server
    else:
        payload["server_index"] = server_index

    resp = await client.post(f"/sessions/{session_id}/mcp/tools/call", json=payload)
    assert resp.status_code == 200
    response_payload = resp.json() or {}
    if response_payload.get("isError") is True:
        raise RuntimeError(f"CRITICAL ERROR: tool '{name}' returned isError=true")
    return response_payload
@pytest.mark.IT
@pytest.mark.mcp
@pytest.mark.req("FR-011")


@pytest.mark.asyncio
async def test_it2_13_file_mcp_tool_coverage(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    file_server_index, file_server = _resolve_file_server(cfg)
    require_initialize = bool(cfg.get("mcp.api.require_initialize") or False)

    expected_tools = _parse_json_list(
        _require_cfg(cfg, "mcp.it2_13.expected_tools"),
        "mcp.it2_13.expected_tools",
    )
    write_args = _parse_json_obj(_require_cfg(cfg, "mcp.it2_13.write_args"), "mcp.it2_13.write_args")
    read_args = _parse_json_obj(_require_cfg(cfg, "mcp.it2_13.read_args"), "mcp.it2_13.read_args")
    search_args = _parse_json_obj(_require_cfg(cfg, "mcp.it2_13.search_args"), "mcp.it2_13.search_args")
    sed_args = _parse_json_obj(_require_cfg(cfg, "mcp.it2_13.sed_args"), "mcp.it2_13.sed_args")
    validate_text_args = _parse_json_obj(
        _require_cfg(cfg, "mcp.it2_13.validate_text_args"), "mcp.it2_13.validate_text_args"
    )
    copy_args = _parse_json_obj(_require_cfg(cfg, "mcp.it2_13.copy_args"), "mcp.it2_13.copy_args")
    diff_args = _parse_json_obj(_require_cfg(cfg, "mcp.it2_13.diff_args"), "mcp.it2_13.diff_args")
    b64_encode_args = _parse_json_obj(
        _require_cfg(cfg, "mcp.it2_13.b64_encode_args"), "mcp.it2_13.b64_encode_args"
    )
    b64_decode_args = _parse_json_obj(
        _require_cfg(cfg, "mcp.it2_13.b64_decode_args"), "mcp.it2_13.b64_decode_args"
    )
    audit_search_args = _parse_json_obj(
        _require_cfg(cfg, "mcp.it2_13.audit_search_args"), "mcp.it2_13.audit_search_args"
    )

    async with httpx.AsyncClient(base_url=base_url, headers=api_headers(cfg), timeout=timeout_seconds) as client:
        create_resp = await client.post("/sessions", json={"metadata": {"suite": "it2.13"}})
        assert create_resp.status_code == 200
        session_id = str(create_resp.json().get("session_id") or "")
        assert session_id

        list_payload: Dict[str, Any] = {"require_initialize": require_initialize}
        if file_server is not None:
            list_payload["server"] = file_server
        else:
            list_payload["server_index"] = file_server_index
        list_resp = await client.post(f"/sessions/{session_id}/mcp/tools/list", json=list_payload)
        assert list_resp.status_code == 200
        tools = list_resp.json().get("tools") or []
        names = [str(item.get("name") or "") for item in tools if isinstance(item, dict)]
        for expected in expected_tools:
            if expected not in names:
                raise RuntimeError(f"CRITICAL ERROR: expected tool missing: {expected}")

        await _mcp_tools_call(
            client, session_id, file_server_index, file_server, "write_file", write_args, require_initialize
        )

        read_result = await _mcp_tools_call(
            client, session_id, file_server_index, file_server, "read_file", read_args, require_initialize
        )
        read_text = _extract_tool_text(read_result)
        expected_phrase = str(_require_cfg(cfg, "mcp.it2_13.expected_read_contains"))
        if expected_phrase not in read_text:
            raise RuntimeError("CRITICAL ERROR: read_file output missing expected phrase")

        search_result = await _mcp_tools_call(
            client, session_id, file_server_index, file_server, "search_content", search_args, require_initialize
        )
        if expected_phrase not in _extract_tool_text(search_result):
            raise RuntimeError("CRITICAL ERROR: search_content output missing expected phrase")

        await _mcp_tools_call(
            client, session_id, file_server_index, file_server, "sed_edit_file", sed_args, require_initialize
        )

        validate_result = await _mcp_tools_call(
            client, session_id, file_server_index, file_server, "validate_text", validate_text_args, require_initialize
        )
        validate_text = _extract_tool_text(validate_result)
        if "valid" not in validate_text.lower():
            raise RuntimeError("CRITICAL ERROR: validate_file output missing validity marker")

        await _mcp_tools_call(
            client, session_id, file_server_index, file_server, "copy_file", copy_args, require_initialize
        )

        diff_result = await _mcp_tools_call(
            client, session_id, file_server_index, file_server, "diff_files", diff_args, require_initialize
        )
        if not _extract_tool_text(diff_result).strip():
            raise RuntimeError("CRITICAL ERROR: diff_files output was empty")

        b64_result = await _mcp_tools_call(
            client, session_id, file_server_index, file_server, "b64_encode_file", b64_encode_args, require_initialize
        )
        encoded_text = _extract_tool_text(b64_result)
        if not encoded_text.strip():
            raise RuntimeError("CRITICAL ERROR: b64_encode_file output was empty")

        await _mcp_tools_call(
            client,
            session_id,
            file_server_index,
            file_server,
            "b64_decode_to_file",
            b64_decode_args,
            require_initialize,
        )

        audit_result = await _mcp_tools_call(
            client, session_id, file_server_index, file_server, "search_content", audit_search_args, require_initialize
        )
        if "write_file" not in _extract_tool_text(audit_result):
            raise RuntimeError("CRITICAL ERROR: audit log does not include write_file event")


# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.integration, pytest.mark.mcp, pytest.mark.heavy]
