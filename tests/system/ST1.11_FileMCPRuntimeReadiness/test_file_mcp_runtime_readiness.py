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

from pathlib import Path

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.file_mcp_runtime import maybe_start_file_mcp, maybe_stop_file_mcp


def _require_cfg(cfg: ConfigManager, key: str):
    value = cfg.get(key)
    if value is None:
        raise RuntimeError(f"CRITICAL ERROR: missing required configuration key: {key}")
    return value
@pytest.mark.ST
@pytest.mark.mcp
@pytest.mark.req("FR-009")


@pytest.mark.asyncio
async def test_st1_11_file_mcp_runtime_readiness(env_file):
    cfg = ConfigManager(env_file=env_file)
    started = maybe_start_file_mcp(cfg)
    try:
        health_url = str(_require_cfg(cfg, "chat_tests.file_mcp.health_url"))
        timeout_seconds = float(_require_cfg(cfg, "chat_tests.file_mcp.health_request_timeout_seconds"))
        expected_name = str(_require_cfg(cfg, "chat_tests.file_mcp.expected_application_name"))
        expected_env_path = Path(str(_require_cfg(cfg, "chat_tests.file_mcp.env_path"))).resolve()

        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.get(health_url)
            assert resp.status_code == 200
            payload = resp.json() or {}

        app_name = str(((payload.get("application") or {}).get("name")) or "")
        if app_name != expected_name:
            raise RuntimeError(f"CRITICAL ERROR: unexpected application.name: {app_name}")

        runtime_env = str(((payload.get("runtime") or {}).get("env_file")) or "")
        if not runtime_env:
            raise RuntimeError("CRITICAL ERROR: runtime.env_file missing in /health")
        if Path(runtime_env).resolve() != expected_env_path:
            raise RuntimeError(
                f"CRITICAL ERROR: runtime.env_file mismatch: {runtime_env} != {expected_env_path}"
            )
    finally:
        if started:
            maybe_stop_file_mcp(cfg)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.system, pytest.mark.pure, pytest.mark.slow]

