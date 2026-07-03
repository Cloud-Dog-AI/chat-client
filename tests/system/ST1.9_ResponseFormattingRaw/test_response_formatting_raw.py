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
        "CLOUD_DOG__CLIENT_API__API_KEY": "<api-key>",
        "CLOUD_DOG__CLIENT_API__API_KEY_HEADER": "X-API-Key",
        "CLOUD_DOG__LLM__SYSTEM_PROMPT": "You must respond with <thinking> and <reasoning> tags and comply with the required response envelope when instructed.",
        "CLOUD_DOG__LLM__INCLUDE_REASONING_TAGS": "true",
        "CLOUD_DOG__LLM__RESPONSE__ENFORCE": "true",
        "CLOUD_DOG__LLM__RESPONSE__ENVELOPE_TAG": "RESPONSE",
        "CLOUD_DOG__LLM__RESPONSE__FORMAT": "markdown",
        "CLOUD_DOG__LLM__RESPONSE__MARKER_KEY": "MARKER",
        "CLOUD_DOG__LLM__RESPONSE__MARKER_VALUE": "RAW_OK",
        "CLOUD_DOG__LLM__RESPONSE__ANSWER_KEY": "ANSWER",
        "CLOUD_DOG__LLM__RESPONSE__ALLOW_HEADER_ONLY": "false",
        "CLOUD_DOG__LLM__RESPONSE__STRIP_FOR_USER": "false",
        "CLOUD_DOG__LLM__RESPONSE__SHOW_THINKING": "false",
        "CLOUD_DOG__LLM__RESPONSE__DISPLAY_ANSWER_TAG": "answer",
        "CLOUD_DOG__CHAT_TESTS__EXPECTED_THINKING_TAG": "<thinking>",
        "CLOUD_DOG__CHAT_TESTS__EXPECTED_REASONING_TAG": "<reasoning>",
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
@pytest.mark.cli
@pytest.mark.req("FR-009")


@pytest.mark.asyncio
async def test_st1_9_response_formatting_raw(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    headers = api_headers(cfg)

    thinking_tag = str(_require_cfg(cfg, "chat_tests.expected_thinking_tag"))
    reasoning_tag = str(_require_cfg(cfg, "chat_tests.expected_reasoning_tag"))
    marker_key = str(_require_cfg(cfg, "llm.response.marker_key")).strip()
    marker_value = str(_require_cfg(cfg, "llm.response.marker_value")).strip()
    answer_key = str(_require_cfg(cfg, "llm.response.answer_key")).strip()
    envelope_tag = str(_require_cfg(cfg, "llm.response.envelope_tag")).strip()

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout_seconds) as client:
        resp = await client.post("/sessions", json={"metadata": {"suite": "st1.9"}})
        assert resp.status_code == 200
        session_id = resp.json().get("session_id")
        assert session_id

        message = "Confirm the scope in one sentence."
        send = await client.post(
            f"/sessions/{session_id}/messages",
            json={"content": message, "stream": False},
        )
        assert send.status_code == 200
        content = str(send.json().get("content") or "")

    if thinking_tag not in content:
        raise RuntimeError("CRITICAL ERROR: expected thinking tag missing from response")
    if reasoning_tag not in content:
        raise RuntimeError("CRITICAL ERROR: expected reasoning tag missing from response")

    if envelope_tag:
        if f"<{envelope_tag}" not in content or f"</{envelope_tag}>" not in content:
            raise RuntimeError("CRITICAL ERROR: expected response envelope missing from raw response")

    if marker_key and marker_value:
        expected_marker = f"{marker_key}: {marker_value}"
        if expected_marker not in content:
            raise RuntimeError("CRITICAL ERROR: expected marker missing from raw response")

    if answer_key:
        expected_answer = f"{answer_key}:"
        if expected_answer not in content:
            raise RuntimeError("CRITICAL ERROR: expected answer key missing from raw response")

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.system, pytest.mark.llm, pytest.mark.slow]

