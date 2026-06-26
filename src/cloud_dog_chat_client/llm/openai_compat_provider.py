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

from typing import Any, AsyncIterator, Dict, List, Optional

from cloud_dog_llm.config.models import ProviderConfig  # type: ignore[import-untyped]
from cloud_dog_llm.domain.enums import EventType  # type: ignore[import-untyped]
from cloud_dog_llm.domain.errors import LLMError  # type: ignore[import-untyped]
from cloud_dog_llm.domain.models import LLMRequest, Message  # type: ignore[import-untyped]
from cloud_dog_llm.providers.openai_compat import (  # type: ignore[import-untyped]
    OpenAICompatAdapter,
)

from .protocols import ChatCompletionResult, ChatMessage, ChatStreamChunk
from .providers import BaseLLMProvider, LLMProviderError


class OpenAICompatProvider(BaseLLMProvider):
    def _apply_stop(self, content: str) -> str:
        """Internal helper to apply stop for this module."""
        if not content or not self.stop:
            return content
        first_idx = None
        for token in self.stop:
            if not token:
                continue
            idx = content.find(token)
            if idx >= 0 and (first_idx is None or idx < first_idx):
                first_idx = idx
        if first_idx is None:
            return content
        return content[:first_idx]

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        timeout_seconds: float,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stop: Optional[list[str]] = None,
        include_reasoning_tags: bool = False,
        client: Any = None,
    ):
        """Initialise OpenAICompatProvider state and dependencies."""
        self.base_url = str(base_url).rstrip("/")
        self.model = str(model)
        self.api_key = str(api_key)
        self.timeout_seconds = float(timeout_seconds)
        self.temperature = float(temperature) if temperature is not None else None
        self.top_p = float(top_p) if top_p is not None else None
        self.max_tokens = int(max_tokens) if max_tokens is not None else None
        self.stop = stop if isinstance(stop, list) else None
        self.include_reasoning_tags = bool(include_reasoning_tags)

        provider_base_url = self.base_url
        if not provider_base_url.rstrip("/").endswith("/v1"):
            provider_base_url = f"{provider_base_url.rstrip('/')}/v1"

        provider_cfg = ProviderConfig(
            provider_id="openai_compat",
            base_url=provider_base_url,
            model=self.model,
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
        )
        self._adapter = OpenAICompatAdapter(provider_cfg, client=client)

    def _params(self) -> Dict[str, Any]:
        """Internal helper to params for this module."""
        params: Dict[str, Any] = {}
        if self.top_p is not None:
            params["top_p"] = self.top_p
        if self.stop:
            params["stop"] = list(self.stop)
        return params

    @staticmethod
    def _to_runtime_messages(messages: List[ChatMessage]) -> List[Message]:
        """Internal helper to to runtime messages for this module."""
        return [Message(role=m.role, content=m.content) for m in messages]

    @staticmethod
    def _reasoning(raw: Any) -> str:
        """Internal helper to reasoning for this module."""
        if not isinstance(raw, dict):
            return ""
        choices = raw.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict):
                    return str(message.get("reasoning") or "")
        return ""

    async def complete(self, messages: List[ChatMessage]) -> ChatCompletionResult:
        """Handle complete for the current runtime context."""
        request = LLMRequest(
            provider_id="openai_compat",
            model=self.model,
            messages=self._to_runtime_messages(messages),
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            params=self._params(),
            stream=False,
        )
        try:
            result = await self._adapter.invoke(request)
        except LLMError as e:
            raise LLMProviderError("LLM provider request failed") from e

        content = str(result.content or "")
        if self.include_reasoning_tags:
            reasoning = self._reasoning(result.raw_provider_response)
            content = (
                f"<thinking>{reasoning}</thinking><reasoning>{content}</reasoning>"
            )
        content = self._apply_stop(content)
        return ChatCompletionResult(content=content, raw=result.raw_provider_response)

    async def stream(
        self, messages: List[ChatMessage]
    ) -> AsyncIterator[ChatStreamChunk]:
        """Handle stream for the current runtime context."""
        request = LLMRequest(
            provider_id="openai_compat",
            model=self.model,
            messages=self._to_runtime_messages(messages),
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            params=self._params(),
            stream=True,
        )
        try:
            async for event in self._adapter.invoke_stream(request):
                if event.type == EventType.DELTA_TEXT and event.text:
                    yield ChatStreamChunk(
                        content_delta=str(event.text), raw={"event": event.type.value}
                    )
        except LLMError as e:
            raise LLMProviderError("LLM provider request failed") from e
