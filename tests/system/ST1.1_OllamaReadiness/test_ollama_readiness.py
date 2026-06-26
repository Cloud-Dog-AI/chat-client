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

import subprocess

import pytest

from cloud_dog_chat_client.config import ConfigManager
from cloud_dog_chat_client.llm.protocols import ChatMessage
from cloud_dog_chat_client.llm.service import LLMService
from cloud_dog_chat_client.llm.providers import LLMProviderError


def _curl_ollama_tags(base_url: str) -> None:
    url = f"{base_url.rstrip('/')}/api/tags"
    result = subprocess.run(
        ["curl", "-fsS", url],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"CRITICAL ERROR: curl failed for {url}: {result.stderr.strip()}")
@pytest.mark.ST
@pytest.mark.cli
@pytest.mark.req("FR-009")


@pytest.mark.asyncio
async def test_st1_1_ollama_non_streaming(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = str(cfg.get("llm.base_url") or "")
    _curl_ollama_tags(base_url)
    llm = LLMService(cfg)

    try:
        result = await llm.complete([ChatMessage(role="user", content="Return the word OK only")])
    except LLMProviderError as e:
        base_url = cfg.get("llm.base_url")
        model = cfg.get("llm.model")
        pytest.fail(f"ST1.1 failed to reach Ollama (base_url={base_url!r}, model={model!r}): {e}")

    assert isinstance(result.content, str)
    assert result.content.strip() != ""
@pytest.mark.ST
@pytest.mark.cli
@pytest.mark.req("FR-009")


@pytest.mark.asyncio
async def test_st1_1_ollama_streaming(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = str(cfg.get("llm.base_url") or "")
    _curl_ollama_tags(base_url)
    llm = LLMService(cfg)

    content = ""
    received_delta = False
    try:
        async for chunk in llm.stream([ChatMessage(role="user", content="Return the word OK only")]):
            content += chunk.content_delta
            if chunk.content_delta:
                received_delta = True
    except LLMProviderError as e:
        base_url = cfg.get("llm.base_url")
        model = cfg.get("llm.model")
        pytest.fail(f"ST1.1 failed to reach Ollama (base_url={base_url!r}, model={model!r}): {e}")

    assert isinstance(content, str)
    assert content.strip() != ""
    assert received_delta

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.system, pytest.mark.llm, pytest.mark.slow]

