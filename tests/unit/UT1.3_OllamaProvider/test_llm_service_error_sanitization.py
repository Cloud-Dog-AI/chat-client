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

import pytest

from cloud_dog_llm.domain.errors import LLMError

from cloud_dog_chat_client.config import ConfigManager
from cloud_dog_chat_client.llm.protocols import ChatMessage
from cloud_dog_chat_client.llm.providers import LLMProviderError
from cloud_dog_chat_client.llm.service import LLMService
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_ut1_3_llm_service_sanitises_auth_error(env_file, monkeypatch):
    cfg = ConfigManager(env_file=env_file)
    llm = LLMService(cfg)

    async def _fail(*_args, **_kwargs):
        raise LLMError("401 upstream body includes sk-or-v1-secret", retryable=False)

    monkeypatch.setattr(llm._runtime_client, "chat", _fail)

    with pytest.raises(LLMProviderError) as exc_info:
        await llm.complete([ChatMessage(role="user", content="hello")])

    message = str(exc_info.value)
    assert message == "LLM provider authentication failed"
    assert "sk-or-v1" not in message
    assert "upstream" not in message.lower()
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_ut1_3_llm_service_sanitises_dns_error(env_file, monkeypatch):
    cfg = ConfigManager(env_file=env_file)
    llm = LLMService(cfg)

    async def _fail(*_args, **_kwargs):
        raise LLMError("[Errno -2] Name or service not known", retryable=True)

    monkeypatch.setattr(llm._runtime_client, "chat", _fail)

    with pytest.raises(LLMProviderError) as exc_info:
        await llm.complete([ChatMessage(role="user", content="hello")])

    assert str(exc_info.value) == "LLM provider host resolution failed"

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.unit, pytest.mark.llm, pytest.mark.fast]

