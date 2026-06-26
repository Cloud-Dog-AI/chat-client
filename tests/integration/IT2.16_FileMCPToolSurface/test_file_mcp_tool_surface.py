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
from typing import Any, Dict, Optional

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


def _resolve_file_server(cfg: ConfigManager) -> tuple[Optional[int], Optional[Dict[str, Any]]]:
    server = cfg.get("mcp.it2_16.file_server")
    if server is not None and not isinstance(server, dict):
        raise RuntimeError("CRITICAL ERROR: mcp.it2_16.file_server must be an object")
    if isinstance(server, dict):
        return None, dict(server)
    return int(_require_cfg(cfg, "mcp.it2_16.file_server_index")), None


@pytest.fixture(scope="module", autouse=True)
def _servers(env_file):
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
@pytest.mark.IT
@pytest.mark.mcp
@pytest.mark.req("FR-011")


@pytest.mark.asyncio
async def test_it2_16_file_mcp_tool_surface_invocation(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    server_index, server = _resolve_file_server(cfg)
    require_initialize = bool(cfg.get("mcp.api.require_initialize") or False)
    args_map = _parse_json_obj(_require_cfg(cfg, "mcp.it2_16.args_map"), "mcp.it2_16.args_map")
    must_succeed = _require_cfg(cfg, "mcp.it2_16.must_succeed_tools")
    if isinstance(must_succeed, str):
        must_succeed = json.loads(must_succeed)
    if not isinstance(must_succeed, list):
        raise RuntimeError("CRITICAL ERROR: mcp.it2_16.must_succeed_tools must be a JSON list")
    must_succeed_ordered = [str(item) for item in must_succeed]
    must_succeed_names = set(must_succeed_ordered)

    async with httpx.AsyncClient(base_url=base_url, headers=api_headers(cfg), timeout=timeout_seconds) as client:
        session_resp = await client.post("/sessions", json={"metadata": {"suite": "it2.16"}})
        assert session_resp.status_code == 200
        session_id = str(session_resp.json().get("session_id") or "")
        assert session_id

        list_payload: Dict[str, Any] = {"require_initialize": require_initialize}
        if server is not None:
            list_payload["server"] = server
        else:
            list_payload["server_index"] = server_index
        list_resp = await client.post(f"/sessions/{session_id}/mcp/tools/list", json=list_payload)
        assert list_resp.status_code == 200
        tools = list_resp.json().get("tools") or []
        tool_names = [str(item.get("name") or "") for item in tools if isinstance(item, dict)]
        if not tool_names:
            raise RuntimeError("CRITICAL ERROR: tools/list returned no tools")

        # Seed deterministic file state so read/copy/encode must-succeed tools can execute.
        if "write_file" in tool_names:
            seed_args = args_map.get("write_file") or {}
            if not isinstance(seed_args, dict):
                raise RuntimeError("CRITICAL ERROR: mcp.it2_16.args_map.write_file must be an object")
            seed_payload: Dict[str, Any] = {
                "name": "write_file",
                "arguments": seed_args,
                "require_initialize": require_initialize,
            }
            if server is not None:
                seed_payload["server"] = server
            else:
                seed_payload["server_index"] = server_index
            seed_resp = await client.post(f"/sessions/{session_id}/mcp/tools/call", json=seed_payload)
            assert seed_resp.status_code == 200
            if (seed_resp.json() or {}).get("isError") is True:
                raise RuntimeError("CRITICAL ERROR: seed write_file call failed")

        for name in must_succeed_ordered:
            if name not in tool_names:
                raise RuntimeError(
                    f"CRITICAL ERROR: required tool missing from tools/list: {name}; got={tool_names}"
                )
            args = args_map.get(name) or {}
            if not isinstance(args, dict):
                raise RuntimeError(f"CRITICAL ERROR: mcp.it2_16.args_map.{name} must be an object")
            call_payload: Dict[str, Any] = {
                "name": name,
                "arguments": args,
                "require_initialize": require_initialize,
            }
            if server is not None:
                call_payload["server"] = server
            else:
                call_payload["server_index"] = server_index
            call_resp = await client.post(f"/sessions/{session_id}/mcp/tools/call", json=call_payload)
            assert call_resp.status_code == 200
            payload = call_resp.json() or {}
            if payload.get("isError") is True:
                raise RuntimeError(f"CRITICAL ERROR: required success tool failed: {name}")

        for name in tool_names:
            if name in must_succeed_names:
                continue
            args = args_map.get(name) or {}
            if not isinstance(args, dict):
                raise RuntimeError(f"CRITICAL ERROR: mcp.it2_16.args_map.{name} must be an object")
            call_payload = {
                "name": name,
                "arguments": args,
                "require_initialize": require_initialize,
            }
            if server is not None:
                call_payload["server"] = server
            else:
                call_payload["server_index"] = server_index
            call_resp = await client.post(f"/sessions/{session_id}/mcp/tools/call", json=call_payload)
            assert call_resp.status_code == 200

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.integration, pytest.mark.mcp, pytest.mark.heavy]
