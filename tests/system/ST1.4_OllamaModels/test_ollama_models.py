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

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from cloud_dog_chat_client.llm.protocols import ChatMessage
from cloud_dog_chat_client.llm.service import LLMService


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


async def _ollama_has_model(base_url: str, model: str) -> bool:
    async with httpx.AsyncClient(timeout=10.0, base_url=base_url.rstrip("/")) as client:
        resp = await client.get("/api/tags")
        if resp.status_code != 200:
            return False
        data = resp.json()
        models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(models, list):
            return False
        return any(isinstance(m, dict) and m.get("name") == model for m in models)
@pytest.mark.ST
@pytest.mark.cli
@pytest.mark.req("FR-009")


@pytest.mark.asyncio
async def test_st1_4_ollama_model_non_streaming(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = str(cfg.get("llm.base_url") or "")
    model = str(cfg.get("llm.model") or "")
    _curl_ollama_tags(base_url)
    if not await _ollama_has_model(base_url, model):
        raise RuntimeError(f"CRITICAL ERROR: Ollama model not available: {model}")

    llm = LLMService(cfg)
    result = await llm.complete([ChatMessage(role="user", content="Return the word OK only.")])
    assert isinstance(result.content, str)
    assert result.content.strip() != ""
@pytest.mark.ST
@pytest.mark.cli
@pytest.mark.req("FR-009")


@pytest.mark.asyncio
async def test_st1_4_ollama_model_streaming(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = str(cfg.get("llm.base_url") or "")
    model = str(cfg.get("llm.model") or "")
    _curl_ollama_tags(base_url)
    if not await _ollama_has_model(base_url, model):
        raise RuntimeError(f"CRITICAL ERROR: Ollama model not available: {model}")

    llm = LLMService(cfg)
    content = ""
    received_delta = False
    async for chunk in llm.stream([ChatMessage(role="user", content="Return the word OK only.")]):
        content += chunk.content_delta
        if chunk.content_delta:
            received_delta = True
    assert isinstance(content, str)
    assert content.strip() != ""
    assert received_delta

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.system, pytest.mark.llm, pytest.mark.slow]

