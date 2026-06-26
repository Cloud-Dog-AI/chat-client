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


def _parse_cases(value: Any, key: str) -> List[Dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception as e:
            raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON list") from e
    if not isinstance(value, list):
        raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON list")
    cases: List[Dict[str, Any]] = []
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            raise RuntimeError(f"CRITICAL ERROR: {key}.{i} must be an object")
        name = str(item.get("name") or "").strip()
        if not name:
            raise RuntimeError(f"CRITICAL ERROR: {key}.{i}.name is required")
        arguments = _parse_json_obj(item.get("arguments") or {}, f"{key}.{i}.arguments")
        expect_error = bool(item.get("expect_error"))
        contains = str(item.get("error_contains") or "").strip()
        cases.append(
            {"name": name, "arguments": arguments, "expect_error": expect_error, "error_contains": contains}
        )
    return cases


def _resolve_file_server(cfg: ConfigManager) -> tuple[Optional[int], Optional[Dict[str, Any]]]:
    server = cfg.get("mcp.it2_14.file_server")
    if server is not None and not isinstance(server, dict):
        raise RuntimeError("CRITICAL ERROR: mcp.it2_14.file_server must be an object")
    if isinstance(server, dict):
        return None, dict(server)
    return int(_require_cfg(cfg, "mcp.it2_14.file_server_index")), None


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
async def test_it2_14_file_mcp_limits_and_errors(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    server_index, server = _resolve_file_server(cfg)
    require_initialize = bool(cfg.get("mcp.api.require_initialize") or False)
    success_case = _parse_json_obj(_require_cfg(cfg, "mcp.it2_14.success_case"), "mcp.it2_14.success_case")
    error_cases = _parse_cases(_require_cfg(cfg, "mcp.it2_14.error_cases"), "mcp.it2_14.error_cases")

    async with httpx.AsyncClient(base_url=base_url, headers=api_headers(cfg), timeout=timeout_seconds) as client:
        session_resp = await client.post("/sessions", json={"metadata": {"suite": "it2.14"}})
        assert session_resp.status_code == 200
        session_id = str(session_resp.json().get("session_id") or "")
        assert session_id

        success_payload: Dict[str, Any] = {
            "name": str(success_case.get("name") or ""),
            "arguments": _parse_json_obj(success_case.get("arguments") or {}, "mcp.it2_14.success_case.arguments"),
            "require_initialize": require_initialize,
        }
        if server is not None:
            success_payload["server"] = server
        else:
            success_payload["server_index"] = server_index
        success_resp = await client.post(f"/sessions/{session_id}/mcp/tools/call", json=success_payload)
        assert success_resp.status_code == 200
        if (success_resp.json() or {}).get("isError") is True:
            raise RuntimeError("CRITICAL ERROR: baseline success case returned isError=true")

        for case in error_cases:
            payload: Dict[str, Any] = {
                "name": case["name"],
                "arguments": case["arguments"],
                "require_initialize": require_initialize,
            }
            if server is not None:
                payload["server"] = server
            else:
                payload["server_index"] = server_index
            resp = await client.post(f"/sessions/{session_id}/mcp/tools/call", json=payload)
            assert resp.status_code == 200
            payload = resp.json() or {}
            if case["expect_error"]:
                if payload.get("isError") is not True:
                    raise RuntimeError(f"CRITICAL ERROR: expected error for tool {case['name']} but call succeeded")
                if case["error_contains"]:
                    text = json.dumps(payload)
                    if case["error_contains"] not in text:
                        raise RuntimeError(
                            f"CRITICAL ERROR: expected error marker missing for {case['name']}: {case['error_contains']}"
                        )

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.integration, pytest.mark.mcp, pytest.mark.heavy]
