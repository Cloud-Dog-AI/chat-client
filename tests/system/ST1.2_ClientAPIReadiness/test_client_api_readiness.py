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

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_server import api_base_url, api_headers, start_api, stop_api, wait_for_api


def _require_cfg(cfg: ConfigManager, key: str):
    value = cfg.get(key)
    if value is None:
        raise RuntimeError(f"CRITICAL ERROR: missing required configuration key: {key}")
    return value


@pytest.fixture(scope="module", autouse=True)
def _api_server(env_file):
    cfg = ConfigManager(env_file=env_file)
    start_api(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        yield None
    finally:
        stop_api(cfg, env_file=env_file)
@pytest.mark.ST
@pytest.mark.api
@pytest.mark.req("FR-009")


@pytest.mark.asyncio
async def test_st1_2_client_api_health(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds) as client:
        resp = await client.get("/health", headers=api_headers(cfg))
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ok"
@pytest.mark.ST
@pytest.mark.api
@pytest.mark.req("FR-009")


@pytest.mark.asyncio
async def test_st1_2_client_api_session_create_and_list(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    headers = api_headers(cfg)
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds) as client:
        resp = await client.post("/sessions", json={"metadata": {"suite": "st1.2"}}, headers=headers)
        assert resp.status_code == 200
        session_id = resp.json().get("session_id")
        assert session_id

        resp2 = await client.get("/sessions", headers=headers)
        assert resp2.status_code == 200
        payload = resp2.json()
        assert isinstance(payload.get("sessions"), list)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.system, pytest.mark.pure, pytest.mark.slow]

