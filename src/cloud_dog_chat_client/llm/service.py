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

import asyncio
import json
import uuid
from typing import Any, AsyncIterator, List

from cloud_dog_llm.config.models import ProviderConfig  # type: ignore[import-untyped]
from cloud_dog_llm.domain.enums import EventType  # type: ignore[import-untyped]
from cloud_dog_llm.domain.errors import LLMError  # type: ignore[import-untyped]
from cloud_dog_llm.domain.models import (  # type: ignore[import-untyped]
    LLMRequest,
    Message,
    SessionContext,
)
from cloud_dog_llm.providers.factory import create_provider  # type: ignore[import-untyped]
from cloud_dog_llm.providers.registry import ProviderRegistry  # type: ignore[import-untyped]
from cloud_dog_llm.runtime.client import LLMClient  # type: ignore[import-untyped]

from ..config import ConfigManager
from .protocols import ChatCompletionResult, ChatMessage, ChatStreamChunk
from .providers import LLMProviderError
from .response_policy import (
    ResponsePolicy,
    build_response_instruction,
    build_retry_instruction,
    load_response_policy,
    validate_response,
)


class LLMService:
    def __init__(
        self, config: ConfigManager, *, response_policy_enforce: bool | None = None
    ):
        """Initialise LLMService state and dependencies."""
        self._config = config
        provider = str(config.get("llm.provider") or "").lower().strip()
        base_url = str(config.get("llm.base_url") or "").strip()
        model = str(config.get("llm.model") or "").strip()
        timeout_seconds = float(config.get("llm.timeout_seconds"))

        self._stream_enabled = bool(config.get("llm.stream"))
        self._temperature = config.get("llm.temperature")
        self._top_p = config.get("llm.top_p")
        self._top_k = config.get("llm.top_k")

        context_window = config.get("llm.context_window")
        if context_window is None:
            context_window = config.get("llm.num_ctx")
        self._context_window = (
            int(context_window) if context_window is not None else None
        )

        max_tokens = config.get("llm.max_tokens")
        if max_tokens is None:
            max_tokens = config.get("llm.num_predict")
        self._max_tokens = int(max_tokens) if max_tokens is not None else None

        stop = config.get("llm.stop")
        self._include_reasoning_tags = bool(
            config.get("llm.reasoning.output")
            or config.get("llm.include_reasoning_tags")
            or False
        )
        self._think = bool(config.get("llm.reasoning.enabled") or False)
        reasoning_budget = config.get("llm.reasoning.budget")
        self._think_budget = int(reasoning_budget) if reasoning_budget is not None else None
        # Only the native reasoning-output mode requires reasoning to be enabled.
        # include_reasoning_tags is prompt-induced tag wrapping and works without
        # native reasoning (W28R-3023: restore pre-W28C-1720 behaviour; the over-broad
        # check forced native thinking mode, which breaks the <RESPONSE> envelope format
        # for qwen3 and 500s the formatting tests).
        if bool(config.get("llm.reasoning.output")) and not self._think:
            raise RuntimeError("llm.reasoning.output requires llm.reasoning.enabled=true")
        if isinstance(stop, str):
            try:
                parsed = json.loads(stop)
                if isinstance(parsed, list):
                    stop = parsed
            except Exception:
                stop = None
        self._stop_tokens = stop if isinstance(stop, list) else None

        max_user_chars = config.get("llm.response.max_user_chars")
        if max_user_chars is None:
            max_user_chars = config.get("chat_tests.max_response_chars")
        self._max_response_chars = (
            int(max_user_chars) if max_user_chars is not None else None
        )

        if not provider:
            raise RuntimeError(
                "CRITICAL ERROR: missing required configuration key: llm.provider"
            )
        if not base_url:
            raise RuntimeError(
                "CRITICAL ERROR: missing required configuration key: llm.base_url"
            )
        if not model:
            raise RuntimeError(
                "CRITICAL ERROR: missing required configuration key: llm.model"
            )

        self._response_policy: ResponsePolicy = load_response_policy(config)
        if response_policy_enforce is False:
            self._response_policy = self._disabled_response_policy()
        self._response_instruction = (
            build_response_instruction(self._response_policy)
            if self._response_policy.enforce
            else ""
        )

        self._base_url = base_url

        aliases = {"openai-compatible": "openai_compat", "vllm": "openai_compat"}
        self._provider_id = aliases.get(provider, provider)

        registry = ProviderRegistry()
        provider_base_url = base_url
        if self._provider_id in {"openai", "openai_compat"} and not provider_base_url.rstrip("/").endswith("/v1"):
            provider_base_url = f"{provider_base_url.rstrip('/')}/v1"

        extra_headers = None
        if self._provider_id == "openrouter":
            extra_headers = {
                "HTTP-Referer": str(config.get("llm.openrouter.http_referer") or ""),
                "X-Title": str(config.get("llm.openrouter.x_title") or ""),
            }
            extra_headers = {key: value for key, value in extra_headers.items() if value}

        provider_cfg = ProviderConfig(
            provider_id=self._provider_id,
            base_url=provider_base_url,
            model=model,
            api_key=str(config.get("llm.api_key") or ""),
            timeout_seconds=timeout_seconds,
            extra_headers=extra_headers,
        )
        if self._provider_id not in {"ollama", "openrouter", "openai", "openai_compat"}:
            raise RuntimeError(f"Unsupported llm.provider: {provider}")
        registry.register(self._provider_id, create_provider(provider_cfg))

        self._runtime_client = LLMClient(
            provider_registry=registry,
            default_provider_id=self._provider_id,
        )
        self._session_context = SessionContext(
            session_id=uuid.uuid4().hex,
            correlation_id=uuid.uuid4().hex,
        )
        self._model = model

    @staticmethod
    def _disabled_response_policy() -> ResponsePolicy:
        """Internal helper to disabled response policy for this module."""
        return ResponsePolicy(
            enforce=False,
            envelope_tag="",
            format="",
            marker_key="",
            marker_value="",
            answer_key="",
            strip_for_user=False,
            show_thinking=False,
            display_answer_tag="",
            allow_header_only=False,
            retry_attempts=0,
            retry_backoff_seconds=0.0,
        )

    @property
    def response_policy(self) -> ResponsePolicy:
        """Handle response policy for the current runtime context."""
        return self._response_policy

    @staticmethod
    def _apply_stop(content: str, stop_tokens: list[str] | None) -> str:
        """Internal helper to apply stop for this module."""
        if not content or not stop_tokens:
            return content
        first_idx: int | None = None
        for token in stop_tokens:
            if not token:
                continue
            idx = content.find(token)
            if idx >= 0 and (first_idx is None or idx < first_idx):
                first_idx = idx
        if first_idx is None:
            return content
        return content[:first_idx]

    @staticmethod
    def _sanitise_error_message(error: Exception) -> str:
        """Internal helper to sanitise error message for this module."""
        text = str(error or "").strip().lower()
        if "401" in text or "auth" in text:
            return "LLM provider authentication failed"
        if "429" in text or "rate" in text:
            return "LLM provider rate limit exceeded"
        if "timeout" in text or "timed out" in text:
            return "LLM provider request timed out"
        if (
            "name or service not known" in text
            or "temporary failure in name resolution" in text
        ):
            return "LLM provider host resolution failed"
        return "LLM provider request failed"

    def _request_params(self) -> dict[str, Any]:
        """Internal helper to request params for this module."""
        params: dict[str, Any] = {}
        if self._provider_id == "ollama":
            if self._temperature is not None:
                params["temperature"] = float(self._temperature)
            if self._top_p is not None:
                params["top_p"] = float(self._top_p)
            if self._top_k is not None:
                params["top_k"] = int(self._top_k)
            if self._context_window is not None:
                params["num_ctx"] = int(self._context_window)
            if self._stop_tokens:
                params["stop"] = list(self._stop_tokens)
            return params

        if self._top_p is not None:
            params["top_p"] = float(self._top_p)
        if self._stop_tokens:
            params["stop"] = list(self._stop_tokens)
        return params

    def _to_runtime_request(
        self, messages: List[ChatMessage], *, stream: bool
    ) -> LLMRequest:
        """Internal helper to to runtime request for this module."""
        runtime_messages = [
            Message(role=str(m.role), content=str(m.content)) for m in messages
        ]
        return LLMRequest(
            provider_id=self._provider_id,
            model=self._model,
            messages=runtime_messages,
            temperature=(
                float(self._temperature)
                if self._temperature is not None and self._provider_id != "ollama"
                else None
            ),
            max_tokens=self._max_tokens,
            stream=stream,
            think=self._think,
            think_budget=self._think_budget,
            include_reasoning=self._include_reasoning_tags,
            params=self._request_params(),
        )

    def _normalise_content(self, *, content: str, reasoning: str | None) -> str:
        """Internal helper to content for this module."""
        out = str(content or "")
        if self._include_reasoning_tags:
            out = f"<thinking>{str(reasoning or '')[:512]}</thinking><reasoning>{out}</reasoning>"
        out = self._apply_stop(out, self._stop_tokens)
        # Never truncate strict-format responses before validation; clipping can
        # remove closing envelope tags and create false contract failures.
        if (
            not self._response_policy.enforce
            and self._max_response_chars is not None
            and self._max_response_chars > 0
        ):
            out = out[: self._max_response_chars]
        return out

    async def _complete_once(self, messages: List[ChatMessage]) -> ChatCompletionResult:
        """Internal helper to complete once for this module."""
        request = self._to_runtime_request(messages, stream=False)
        try:
            response = await self._runtime_client.chat(request, self._session_context)
        except LLMError as e:
            raise LLMProviderError(self._sanitise_error_message(e)) from e

        raw_payload = response.raw_provider_response
        content = self._normalise_content(content=response.content, reasoning=response.reasoning)
        return ChatCompletionResult(content=content, raw=raw_payload)

    async def complete(self, messages: List[ChatMessage]) -> ChatCompletionResult:
        """Handle complete for the current runtime context."""
        if not self._response_policy.enforce:
            return await self._complete_once(messages)

        last_error = "unknown"
        attempts = max(1, int(self._response_policy.retry_attempts) + 1)

        for attempt in range(attempts):
            augmented = list(messages)
            insert_idx = 1 if augmented and augmented[0].role == "system" else 0
            augmented.insert(
                insert_idx,
                ChatMessage(role="system", content=self._response_instruction),
            )
            if attempt > 0:
                augmented.insert(
                    insert_idx + 1,
                    ChatMessage(
                        role="system",
                        content=build_retry_instruction(
                            self._response_policy, last_error
                        ),
                    ),
                )

            result = await self._complete_once(augmented)
            ok, error = validate_response(result.content, self._response_policy)
            if ok:
                return result
            last_error = error or "response format invalid"
            if (
                attempt < attempts - 1
                and self._response_policy.retry_backoff_seconds > 0
            ):
                await asyncio.sleep(self._response_policy.retry_backoff_seconds)

        raise LLMProviderError(
            f"LLM response failed response format validation: {last_error}"
        )

    async def _stream_ollama(
        self, messages: List[ChatMessage]
    ) -> AsyncIterator[ChatStreamChunk]:
        """Stream Ollama through the shared platform adapter."""
        request = self._to_runtime_request(messages, stream=True)
        wrap = self._include_reasoning_tags
        reasoning_open = False
        text_open = False

        def _tag(text: str) -> ChatStreamChunk:
            return ChatStreamChunk(content_delta=text, raw={"event": "reasoning_tag"})

        try:
            # Mirror _normalise_content for the stream so reasoning tags are present
            # in streamed output too (W28R-3023): <thinking>{reasoning}</thinking><reasoning>{text}</reasoning>.
            if wrap:
                yield _tag("<thinking>")
                reasoning_open = True
            async for event in self._runtime_client.chat_stream(request, self._session_context):
                base_raw = {
                    "event": event.type.value,
                    "request_id": event.request_id,
                    "provider_id": event.provider_id,
                    "model_id": event.model_id,
                }
                if wrap and event.type == EventType.DELTA_REASONING and getattr(event, "text", None):
                    yield ChatStreamChunk(content_delta=str(event.text), raw=base_raw)
                elif event.type == EventType.DELTA_TEXT and event.text:
                    if wrap and not text_open:
                        yield _tag("</thinking><reasoning>")
                        reasoning_open = False
                        text_open = True
                    yield ChatStreamChunk(content_delta=str(event.text), raw=base_raw)
            if wrap:
                if reasoning_open:
                    yield _tag("</thinking><reasoning>")
                yield _tag("</reasoning>")
        except LLMError as exc:
            raise LLMProviderError(self._sanitise_error_message(exc)) from exc
    def stream(self, messages: List[ChatMessage]) -> AsyncIterator[ChatStreamChunk]:
        """Handle stream for the current runtime context."""
        if self._provider_id == "ollama":
            return self._stream_ollama(messages)

        async def _stream() -> AsyncIterator[ChatStreamChunk]:
            """Internal helper to stream for this module."""
            request = self._to_runtime_request(messages, stream=self._stream_enabled)
            try:
                async for event in self._runtime_client.chat_stream(
                    request, self._session_context
                ):
                    if event.type == EventType.DELTA_TEXT and event.text:
                        yield ChatStreamChunk(
                            content_delta=str(event.text),
                            raw={
                                "event": event.type.value,
                                "request_id": event.request_id,
                                "provider_id": event.provider_id,
                                "model_id": event.model_id,
                            },
                        )
            except LLMError as e:
                raise LLMProviderError(self._sanitise_error_message(e)) from e

        return _stream()
