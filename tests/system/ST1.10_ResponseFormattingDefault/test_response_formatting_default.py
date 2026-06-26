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


_ST1_10_POLICY_OVERRIDES = {
    "CLOUD_DOG__LLM__STREAM": "false",
    "CLOUD_DOG__LLM__RESPONSE__ENFORCE": "true",
    "CLOUD_DOG__LLM__RESPONSE__ENVELOPE_TAG": "RESPONSE",
    "CLOUD_DOG__LLM__RESPONSE__FORMAT": "markdown",
    "CLOUD_DOG__LLM__RESPONSE__MARKER_KEY": "MARKER",
    "CLOUD_DOG__LLM__RESPONSE__MARKER_VALUE": "DEFAULT_OK",
    "CLOUD_DOG__LLM__RESPONSE__ANSWER_KEY": "ANSWER",
    "CLOUD_DOG__LLM__RESPONSE__ALLOW_HEADER_ONLY": "true",
    "CLOUD_DOG__LLM__RESPONSE__STRIP_FOR_USER": "true",
    "CLOUD_DOG__LLM__RESPONSE__SHOW_THINKING": "false",
    "CLOUD_DOG__LLM__RESPONSE__DISPLAY_ANSWER_TAG": "answer",
    "CLOUD_DOG__LLM__RESPONSE__RETRY_ATTEMPTS": "2",
    "CLOUD_DOG__LLM__RESPONSE__RETRY_BACKOFF_SECONDS": "1.0",
}


@pytest.fixture(scope="module", autouse=True)
def _api_server(env_file):
    original = {k: os.environ.get(k) for k in _ST1_10_POLICY_OVERRIDES}
    os.environ.update(_ST1_10_POLICY_OVERRIDES)
    cfg = ConfigManager(env_file=env_file)
    curl_ollama_tags(cfg)
    start_api(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        yield None
    finally:
        try:
            stop_api(cfg, env_file=env_file)
        finally:
            for key, value in original.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
@pytest.mark.ST
@pytest.mark.cli
@pytest.mark.req("FR-009")


@pytest.mark.asyncio
async def test_st1_10_response_formatting_default(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    headers = api_headers(cfg)

    thinking_tag = str(_require_cfg(cfg, "chat_tests.expected_thinking_tag"))
    reasoning_tag = str(_require_cfg(cfg, "chat_tests.expected_reasoning_tag"))
    answer_tag = str(_require_cfg(cfg, "llm.response.display_answer_tag")).strip()
    marker_value = str(_require_cfg(cfg, "llm.response.marker_value")).strip()
    envelope_tag = str(_require_cfg(cfg, "llm.response.envelope_tag")).strip()

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout_seconds) as client:
        resp = await client.post("/sessions", json={"metadata": {"suite": "st1.10"}})
        assert resp.status_code == 200
        session_id = resp.json().get("session_id")
        assert session_id

        message = "Provide a one-sentence acknowledgement for the current scope."
        send = await client.post(
            f"/sessions/{session_id}/messages",
            json={"content": message, "stream": False},
        )
        assert send.status_code == 200
        content = str(send.json().get("content") or "")

    if thinking_tag and thinking_tag in content:
        raise RuntimeError("CRITICAL ERROR: thinking tag leaked into user response")
    if reasoning_tag and reasoning_tag in content:
        raise RuntimeError("CRITICAL ERROR: reasoning tag leaked into user response")

    if answer_tag:
        if f"<{answer_tag}>" not in content or f"</{answer_tag}>" not in content:
            raise RuntimeError("CRITICAL ERROR: expected answer tag missing from response")

    if marker_value and marker_value in content:
        raise RuntimeError("CRITICAL ERROR: marker leaked into user response")
    if envelope_tag and f"<{envelope_tag}" in content:
        raise RuntimeError("CRITICAL ERROR: response envelope leaked into user response")
@pytest.mark.ST
@pytest.mark.cli
@pytest.mark.req("FR-009")


@pytest.mark.asyncio
async def test_st1_10_streaming_blocked_when_stripping(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    headers = api_headers(cfg)
    expected_error = str(_require_cfg(cfg, "chat_tests.expected_stream_error"))

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout_seconds) as client:
        resp = await client.post("/sessions", json={"metadata": {"suite": "st1.10.stream"}})
        assert resp.status_code == 200
        session_id = resp.json().get("session_id")
        assert session_id

        send = await client.post(
            f"/sessions/{session_id}/messages/stream",
            json={"content": "Streaming should be blocked.", "stream": True},
        )
        assert send.status_code == 400
        payload = send.json()
        detail = str(payload.get("detail") or "")
        if not detail:
            errors = payload.get("errors")
            if isinstance(errors, list) and errors:
                first = errors[0]
                if isinstance(first, dict):
                    detail = str(first.get("message") or "")
        if expected_error not in detail:
            raise RuntimeError("CRITICAL ERROR: unexpected streaming error detail")

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.system, pytest.mark.llm, pytest.mark.slow]

