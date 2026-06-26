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
import os

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


@pytest.fixture(scope="module", autouse=True)
def _api_server(env_file):
    overrides = {
        "CLOUD_DOG__CLIENT_API__API_KEY": "dev-key",
        "CLOUD_DOG__CLIENT_API__API_KEY_HEADER": "X-API-Key",
        # ST1.3 validates streaming endpoint behaviour; keep user-stripping disabled.
        "CLOUD_DOG__LLM__RESPONSE__STRIP_FOR_USER": "false",
    }
    previous = {k: os.environ.get(k) for k in overrides}
    os.environ.update(overrides)

    cfg = ConfigManager(env_file=env_file)
    curl_ollama_tags(cfg)
    start_api(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        yield None
    finally:
        stop_api(cfg, env_file=env_file)
        for key, old in previous.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
@pytest.mark.ST
@pytest.mark.api
@pytest.mark.req("FR-009")


@pytest.mark.asyncio
async def test_st1_3_client_api_message_non_streaming(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    headers = api_headers(cfg)
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds) as client:
        resp = await client.post("/sessions", json={"metadata": {"suite": "st1.3"}}, headers=headers)
        assert resp.status_code == 200
        session_id = resp.json().get("session_id")
        assert session_id

        resp2 = await client.post(
            f"/sessions/{session_id}/messages",
            json={"content": "Return the word OK only", "stream": False},
            headers=headers,
        )
        assert resp2.status_code == 200
        data = resp2.json()
        assert data.get("session_id") == session_id
        assert isinstance(data.get("content"), str)
        assert data["content"].strip() != ""
@pytest.mark.ST
@pytest.mark.api
@pytest.mark.req("FR-009")


@pytest.mark.asyncio
async def test_st1_3_client_api_message_streaming(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    headers = api_headers(cfg)
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds) as client:
        resp = await client.post("/sessions", json={"metadata": {"suite": "st1.3"}}, headers=headers)
        assert resp.status_code == 200
        session_id = resp.json().get("session_id")
        assert session_id

        resp2 = await client.post(
            f"/sessions/{session_id}/messages/stream",
            json={"content": "Return the word OK only", "stream": True},
            headers=headers,
        )
        assert resp2.status_code == 200

        received_delta = False
        async for line in resp2.aiter_lines():
            if not line:
                continue
            obj = json.loads(line)
            assert obj.get("type") in ("delta", "done")
            if obj.get("type") == "delta":
                assert isinstance(obj.get("content_delta"), str)
                received_delta = True
            if obj.get("type") == "done":
                break

        assert received_delta

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.system, pytest.mark.llm, pytest.mark.slow]

