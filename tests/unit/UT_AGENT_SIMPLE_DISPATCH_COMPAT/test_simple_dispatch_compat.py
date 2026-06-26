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

"""UT_AGENT_SIMPLE_DISPATCH_COMPAT - default/simple chat remains unchanged."""

from __future__ import annotations

from pathlib import Path

import pytest

import cloud_dog_chat_client.api.routes as routes_module
from cloud_dog_chat_client.api.routes import SendMessageRequest, build_router
from cloud_dog_chat_client.config import ConfigManager
from cloud_dog_chat_client.llm.protocols import ChatCompletionResult
from cloud_dog_chat_client.llm.response_policy import ResponsePolicy
from cloud_dog_chat_client.session import SessionManager


def _route_endpoint(router, path: str, method: str):
    for route in router.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise RuntimeError(f"endpoint not found: {method} {path}")


class _FakeChunk:
    def __init__(self, content_delta: str) -> None:
        self.content_delta = content_delta


class _FakeLLMService:
    def __init__(self, _cfg, **_kwargs):
        self.response_policy = ResponsePolicy(
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

    async def complete(self, _messages):
        return ChatCompletionResult(content="simple complete response", raw={})

    async def stream(self, _messages):
        yield _FakeChunk("simple stream response")
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", [{}, {"agent_strategy": "simple"}])
async def test_default_and_explicit_simple_non_stream_use_llm_path(
    env_file,
    monkeypatch,
    tmp_path: Path,
    metadata: dict[str, str],
):
    """Default and explicit-simple sessions must not enter agent dispatch."""
    monkeypatch.setattr(routes_module, "LLMService", _FakeLLMService)

    async def _boom(*_args, **_kwargs):
        raise AssertionError("agent dispatch should not run for simple sessions")

    monkeypatch.setattr(routes_module, "dispatch_agent_message", _boom)

    cfg = ConfigManager(env_file=env_file)
    sessions = SessionManager(str(tmp_path / "sessions"))
    router = build_router(config=cfg, sessions=sessions)
    send_message = _route_endpoint(router, "/sessions/{session_id}/messages", "POST")

    session_id = sessions.create_session(metadata=metadata)
    response = await send_message(
        session_id,
        SendMessageRequest(content="hello simple route", stream=False),
    )

    assert response.session_id == session_id
    assert response.content.startswith("simple complete response")
    events = sessions.get_session(session_id)["events"]
    assert not any(event.event_type.startswith("agent_dispatch") for event in events)
    assert events[-1].event_type == "assistant_message"
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_simple_stream_uses_existing_stream_route(env_file, monkeypatch, tmp_path: Path):
    """Simple streaming still uses LLMService.stream rather than agent JSONL dispatch."""
    monkeypatch.setattr(routes_module, "LLMService", _FakeLLMService)

    def _boom(*_args, **_kwargs):
        raise AssertionError("agent stream should not run for simple sessions")

    monkeypatch.setattr(routes_module, "stream_agent_message", _boom)

    cfg = ConfigManager(env_file=env_file)
    sessions = SessionManager(str(tmp_path / "sessions"))
    router = build_router(config=cfg, sessions=sessions)
    send_message_stream = _route_endpoint(
        router,
        "/sessions/{session_id}/messages/stream",
        "POST",
    )

    session_id = sessions.create_session(metadata={"agent_strategy": "simple"})
    response = await send_message_stream(
        session_id,
        SendMessageRequest(content="hello simple stream", stream=True),
    )

    chunks: list[str] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))

    body = "".join(chunks)
    assert '"type": "delta"' in body
    assert "simple stream response" in body
    events = sessions.get_session(session_id)["events"]
    assert not any(event.event_type.startswith("agent_dispatch") for event in events)
