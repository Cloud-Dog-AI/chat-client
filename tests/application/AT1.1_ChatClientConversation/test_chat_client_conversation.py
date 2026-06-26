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


def _assert_tags(content: str, cfg: ConfigManager) -> None:
    thinking_tag = str(_require_cfg(cfg, "chat_tests.expected_thinking_tag"))
    reasoning_tag = str(_require_cfg(cfg, "chat_tests.expected_reasoning_tag"))
    if thinking_tag not in content:
        raise RuntimeError("CRITICAL ERROR: response missing <thinking> tag")
    if reasoning_tag not in content:
        raise RuntimeError("CRITICAL ERROR: response missing <reasoning> tag")


def _assert_length(content: str, cfg: ConfigManager) -> None:
    max_chars = int(_require_cfg(cfg, "chat_tests.max_response_chars"))
    if len(content) > max_chars:
        raise RuntimeError(
            f"CRITICAL ERROR: response length {len(content)} exceeds max {max_chars}"
        )
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
async def test_at1_1_chat_client_default_prompt(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds) as client:
        headers = api_headers(cfg)
        resp = await client.post("/sessions", json={"metadata": {"suite": "at1.1"}}, headers=headers)
        assert resp.status_code == 200
        session_id = resp.json().get("session_id")
        assert session_id

        prompt = str(_require_cfg(cfg, "chat_tests.single_turn_prompt"))
        marker = str(_require_cfg(cfg, "chat_tests.expected_default_marker"))

        resp2 = await client.post(
            f"/sessions/{session_id}/messages",
            json={"content": prompt, "stream": False},
            headers=headers,
        )
        assert resp2.status_code == 200
        content = str(resp2.json().get("content") or "")
        assert marker in content
        _assert_tags(content, cfg)
        _assert_length(content, cfg)
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
async def test_at1_1_chat_client_override_prompt(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds) as client:
        headers = api_headers(cfg)
        resp = await client.post("/sessions", json={"metadata": {"suite": "at1.1"}}, headers=headers)
        assert resp.status_code == 200
        session_id = resp.json().get("session_id")
        assert session_id

        override_prompt = str(_require_cfg(cfg, "chat_tests.system_prompt_override"))
        marker = str(_require_cfg(cfg, "chat_tests.expected_override_marker"))

        resp2 = await client.post(
            f"/sessions/{session_id}/messages",
            json={"content": "ping", "stream": False, "system_prompt": override_prompt},
            headers=headers,
        )
        assert resp2.status_code == 200
        content = str(resp2.json().get("content") or "")
        assert marker in content
        _assert_tags(content, cfg)
        _assert_length(content, cfg)
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
async def test_at1_1_chat_client_multi_step_history(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds) as client:
        headers = api_headers(cfg)
        resp = await client.post("/sessions", json={"metadata": {"suite": "at1.1"}}, headers=headers)
        assert resp.status_code == 200
        session_id = resp.json().get("session_id")
        assert session_id

        codeword = str(_require_cfg(cfg, "chat_tests.multi_step_codeword"))
        step1 = str(_require_cfg(cfg, "chat_tests.multi_step_step1_prompt"))
        step2 = str(_require_cfg(cfg, "chat_tests.multi_step_step2_prompt"))
        step3 = str(_require_cfg(cfg, "chat_tests.multi_step_step3_prompt"))

        resp1 = await client.post(
            f"/sessions/{session_id}/messages",
            json={"content": step1, "stream": False},
            headers=headers,
        )
        assert resp1.status_code == 200

        resp2 = await client.post(
            f"/sessions/{session_id}/messages",
            json={"content": step2, "stream": False},
            headers=headers,
        )
        assert resp2.status_code == 200
        content2 = str(resp2.json().get("content") or "")
        assert codeword in content2
        _assert_tags(content2, cfg)

        resp3 = await client.post(
            f"/sessions/{session_id}/messages",
            json={"content": step3, "stream": False},
            headers=headers,
        )
        assert resp3.status_code == 200
        content3 = str(resp3.json().get("content") or "")
        _assert_tags(content3, cfg)

        transcript = await client.get(f"/sessions/{session_id}/transcript", headers=headers)
        assert transcript.status_code == 200
        events = transcript.json().get("events") or []
        assert isinstance(events, list)
        user_messages = [e for e in events if e.get("event_type") == "user_message"]
        assert len(user_messages) >= 3
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
async def test_at1_1_chat_client_stop_token(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds) as client:
        headers = api_headers(cfg)
        resp = await client.post("/sessions", json={"metadata": {"suite": "at1.1"}}, headers=headers)
        assert resp.status_code == 200
        session_id = resp.json().get("session_id")
        assert session_id

        stop_prompt = str(_require_cfg(cfg, "chat_tests.stop_prompt"))
        stop_disallowed = str(_require_cfg(cfg, "chat_tests.stop_disallowed_text"))

        resp2 = await client.post(
            f"/sessions/{session_id}/messages",
            json={"content": stop_prompt, "stream": False},
            headers=headers,
        )
        assert resp2.status_code == 200
        content = str(resp2.json().get("content") or "")
        if stop_disallowed in content:
            raise RuntimeError("CRITICAL ERROR: stop token not honoured")

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.application, pytest.mark.llm, pytest.mark.heavy]

