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


def _parse_args(value, key: str) -> dict:
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
    raise RuntimeError(f"CRITICAL ERROR: {key} must be an object or JSON string")


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
async def test_it2_11_sqlagent_mcp(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    protocol_version = str(_require_cfg(cfg, "mcp.defaults.protocol_version")).strip()
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))

    tool_name = str(_require_cfg(cfg, "mcp.it2_11.tools_call.name")).strip()
    tool_args = _parse_args(_require_cfg(cfg, "mcp.it2_11.tools_call.arguments"), "mcp.it2_11.tools_call.arguments")
    invalid_tool_name = str(_require_cfg(cfg, "mcp.it2_11.invalid_tools_call.name")).strip()
    invalid_tool_args = _parse_args(
        _require_cfg(cfg, "mcp.it2_11.invalid_tools_call.arguments"),
        "mcp.it2_11.invalid_tools_call.arguments",
    )
    resource_uri = str(_require_cfg(cfg, "mcp.it2_11.resource_uri")).strip()

    require_initialize = bool(cfg.get("mcp.api.require_initialize") or False)
    server_cfg = cfg.get("mcp.it2_11.server")
    if server_cfg is not None and not isinstance(server_cfg, dict):
        raise RuntimeError("CRITICAL ERROR: mcp.it2_11.server must be an object")
    server_index_raw = cfg.get("mcp.it2_11.server_index")
    try:
        server_index = int(server_index_raw) if server_index_raw is not None else 0
    except (TypeError, ValueError) as e:
        raise RuntimeError("CRITICAL ERROR: mcp.it2_11.server_index must be an integer") from e

    async with httpx.AsyncClient(base_url=base_url, headers=api_headers(cfg), timeout=timeout_seconds) as client:
        session_id = await create_session(client, metadata={"suite": "it2.11"})

        steps = [
            {"method": "tools/list"},
            {"method": "tools/call", "params": {"name": tool_name, "arguments": tool_args}},
            {"method": "tools/call", "params": {"name": invalid_tool_name, "arguments": invalid_tool_args}, "expect_error": True},
            {"method": "resources/list"},
            {"method": "resources/read", "params": {"uri": resource_uri}},
        ]

        results = await mcp_execute(
            client,
            session_id=session_id,
            server_index=None if isinstance(server_cfg, dict) else server_index,
            server=server_cfg if isinstance(server_cfg, dict) else None,
            protocol_version=protocol_version,
            require_initialize=require_initialize,
            steps=steps,
        )

        items = results.get("results") or []
        if not items or not items[0].get("ok"):
            raise RuntimeError("CRITICAL ERROR: tools/list failed via API")
        if not items[1].get("ok"):
            raise RuntimeError(f"CRITICAL ERROR: tool call failed via API: {items[1].get('error')}")

        if items[2].get("ok") and not (items[2].get("result") or {}).get("isError"):
            raise RuntimeError("CRITICAL ERROR: invalid tool call unexpectedly succeeded")

        if not items[3].get("ok"):
            raise RuntimeError("CRITICAL ERROR: resources/list failed via API")
        if not items[4].get("ok"):
            raise RuntimeError("CRITICAL ERROR: resources/read failed via API")

        contents = (items[4].get("result") or {}).get("contents")
        if not isinstance(contents, list) or not contents:
            raise RuntimeError("CRITICAL ERROR: resources/read returned empty contents")

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.integration, pytest.mark.mcp, pytest.mark.heavy]

