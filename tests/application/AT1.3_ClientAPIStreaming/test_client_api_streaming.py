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
from typing import Dict, List

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
    cfg = ConfigManager(env_file=env_file)
    curl_ollama_tags(cfg)
    start_api(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        yield None
    finally:
        stop_api(cfg, env_file=env_file)
@pytest.mark.AT
@pytest.mark.api
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
async def test_at1_3_client_api_auth_and_streaming(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds) as client:
        good_headers = api_headers(cfg)

        resp = await client.get("/health", headers=good_headers)
        assert resp.status_code == 200

        resp = await client.post("/sessions", json={"metadata": {"suite": "at1.3"}}, headers=good_headers)
        assert resp.status_code == 200
        session_id = resp.json().get("session_id")
        assert session_id

        prompt = str(_require_cfg(cfg, "chat_tests.single_turn_prompt"))
        expected_marker = str(_require_cfg(cfg, "chat_tests.expected_default_marker"))
        expected_thinking = str(_require_cfg(cfg, "chat_tests.expected_thinking_tag"))
        expected_reasoning = str(_require_cfg(cfg, "chat_tests.expected_reasoning_tag"))

        deltas: List[str] = []
        async with client.stream(
            "POST",
            f"/sessions/{session_id}/messages/stream",
            json={"content": prompt, "stream": True},
            headers=good_headers,
        ) as resp:
            assert resp.status_code == 200
            async for line in resp.aiter_lines():
                if not line:
                    continue
                item = json.loads(line)
                if item.get("type") == "delta":
                    delta = str(item.get("content_delta") or "")
                    if delta:
                        deltas.append(delta)
                elif item.get("type") == "done":
                    break

        content = "".join(deltas)
        assert expected_marker in content
        if expected_thinking not in content:
            raise RuntimeError("CRITICAL ERROR: response missing <thinking> tag")
        if expected_reasoning not in content:
            raise RuntimeError("CRITICAL ERROR: response missing <reasoning> tag")

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.application, pytest.mark.llm, pytest.mark.heavy]

