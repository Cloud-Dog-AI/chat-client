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

from __future__ import annotations

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_server import api_base_url, api_headers, start_api, stop_api, wait_for_api


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
async def test_st1_16_openapi_spec_contract(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    request_timeout = float(cfg.get("client_api.request_timeout_seconds") or 20)

    async with httpx.AsyncClient(base_url=base_url, timeout=request_timeout) as client:
        resp = await client.get("/openapi.json", headers=api_headers(cfg))
        assert resp.status_code == 200

    payload = resp.json()
    assert isinstance(payload, dict)
    assert str(payload.get("openapi") or "").startswith("3.")
    assert isinstance(payload.get("info"), dict)
    assert isinstance(payload.get("paths"), dict)

    paths = payload.get("paths") or {}
    assert "/sessions" in paths
    assert "/health" in paths

    # Chat operation is session-scoped in this API contract.
    assert (
        "/chat" in paths or "/sessions/{session_id}/messages" in paths
    ), "Expected chat operation path in OpenAPI schema"

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.system, pytest.mark.pure, pytest.mark.slow]

