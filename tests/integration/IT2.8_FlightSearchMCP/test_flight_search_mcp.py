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
from typing import Any, Dict, List

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


def _parse_tool_cases(cfg: ConfigManager) -> List[Dict[str, Any]]:
    raw = _require_cfg(cfg, "mcp.it2_8.tools")
    if not isinstance(raw, list):
        raise RuntimeError("CRITICAL ERROR: mcp.it2_8.tools must be a list")

    cases: List[Dict[str, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise RuntimeError(f"CRITICAL ERROR: mcp.it2_8.tools.{i} must be an object")
        name = str(item.get("name") or "").strip()
        if not name:
            raise RuntimeError(f"CRITICAL ERROR: mcp.it2_8.tools.{i}.name is required")
        args = item.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception as e:
                raise RuntimeError(
                    f"CRITICAL ERROR: mcp.it2_8.tools.{i}.arguments must be an object"
                ) from e
        if not isinstance(args, dict):
            raise RuntimeError(f"CRITICAL ERROR: mcp.it2_8.tools.{i}.arguments must be an object")
        cases.append({"name": name, "arguments": args})
    return cases


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
@pytest.mark.IT
@pytest.mark.mcp
@pytest.mark.req("FR-011")


@pytest.mark.asyncio
async def test_it2_8_flight_search_mcp(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    server_cfg = cfg.get("mcp.it2_8.server")
    if server_cfg is not None and not isinstance(server_cfg, dict):
        raise RuntimeError("CRITICAL ERROR: mcp.it2_8.server must be an object")
    server_index_raw = cfg.get("mcp.it2_8.server_index")
    try:
        server_index = int(server_index_raw) if server_index_raw is not None else 0
    except (TypeError, ValueError) as e:
        raise RuntimeError("CRITICAL ERROR: mcp.it2_8.server_index must be an integer") from e

    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    async with httpx.AsyncClient(base_url=base_url, headers=api_headers(cfg), timeout=timeout_seconds) as client:
        resp = await client.post("/sessions", json={"metadata": {"suite": "it2.8"}})
        assert resp.status_code == 200
        session_id = resp.json().get("session_id")
        assert session_id

        steps = [{"method": "tools/list"}]
        for case in _parse_tool_cases(cfg):
            steps.append({"method": "tools/call", "params": {"name": case["name"], "arguments": case["arguments"]}})

        payload = {
            "require_initialize": True,
            "protocol_version": _require_cfg(cfg, "mcp.defaults.protocol_version"),
            "steps": steps,
        }
        if isinstance(server_cfg, dict):
            payload["server"] = server_cfg
        else:
            payload["server_index"] = server_index

        exec_resp = await client.post(
            f"/sessions/{session_id}/mcp/execute",
            json=payload,
        )
        assert exec_resp.status_code == 200
        results = exec_resp.json().get("results") or []
        if not results or not results[0].get("ok"):
            raise RuntimeError("CRITICAL ERROR: tools/list failed via API")

        for idx, case in enumerate(_parse_tool_cases(cfg), start=1):
            item = results[idx]
            if not item.get("ok"):
                raise RuntimeError(f"CRITICAL ERROR: tool '{case['name']}' failed via API: {item.get('error')}")
            result = item.get("result") or {}
            if result.get("isError") is True:
                raise RuntimeError(f"CRITICAL ERROR: tool '{case['name']}' returned isError=true")
            content = result.get("content")
            if not isinstance(content, list) or not content:
                raise RuntimeError(f"CRITICAL ERROR: tool '{case['name']}' returned empty content")

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.integration, pytest.mark.llm, pytest.mark.mcp, pytest.mark.heavy]

