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


def _parse_json_list(value: Any, key: str) -> List[Dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception as e:
            raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON list") from e
    if not isinstance(value, list):
        raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON list")
    out: List[Dict[str, Any]] = []
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            raise RuntimeError(f"CRITICAL ERROR: {key}.{i} must be an object")
        out.append(item)
    return out


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
async def test_it2_15_file_mcp_config_variants(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    protocol_version = str(_require_cfg(cfg, "mcp.defaults.protocol_version"))
    steps = _parse_json_list(_require_cfg(cfg, "mcp.it2_15.steps"), "mcp.it2_15.steps")
    variants = _parse_json_list(_require_cfg(cfg, "mcp.it2_15.variants"), "mcp.it2_15.variants")

    async with httpx.AsyncClient(base_url=base_url, headers=api_headers(cfg), timeout=timeout_seconds) as client:
        session_resp = await client.post("/sessions", json={"metadata": {"suite": "it2.15"}})
        assert session_resp.status_code == 200
        session_id = str(session_resp.json().get("session_id") or "")
        assert session_id

        for idx, variant in enumerate(variants):
            server = _parse_json_obj(variant.get("server") or {}, f"mcp.it2_15.variants.{idx}.server")
            require_initialize = bool(variant.get("require_initialize"))

            exec_resp = await client.post(
                f"/sessions/{session_id}/mcp/execute",
                json={
                    "server": server,
                    "require_initialize": require_initialize,
                    "protocol_version": protocol_version,
                    "steps": steps,
                },
            )
            assert exec_resp.status_code == 200
            results = exec_resp.json().get("results") or []
            if not results:
                raise RuntimeError(f"CRITICAL ERROR: variant {idx} returned empty results")
            for step_i, item in enumerate(results):
                if not item.get("ok"):
                    raise RuntimeError(
                        f"CRITICAL ERROR: variant {idx} failed at step {step_i}: {item.get('error')}"
                    )

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.integration, pytest.mark.mcp, pytest.mark.heavy]

