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

"""UT_AGENT_ADAPTERS — LLM, MCP, memory, and checkpoint adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cloud_dog_chat_client.agent import adapters
from cloud_dog_chat_client.agent.adapters import (
    ChatClientCheckpointHook,
    ChatClientLLMCaller,
    ChatClientMemoryStore,
    SessionMCPToolExecutor,
)
from cloud_dog_chat_client.llm.protocols import ChatCompletionResult
from cloud_dog_chat_client.session import SessionManager


@dataclass
class _FakeConfig:
    values: dict[str, Any]

    def get(self, key: str, default: Any = None) -> Any:
        """Return a fake config value."""
        return self.values.get(key, default)


class _FakeLLMService:
    def __init__(self, content: str) -> None:
        """Store the fake completion content."""
        self.content = content
        self.messages: list[Any] = []

    async def complete(self, messages):
        """Return a fake ChatCompletionResult."""
        self.messages.append(messages)
        return ChatCompletionResult(content=self.content, raw={"fake": True})
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_llm_adapter_uses_llm_service_complete_and_preserves_tools():
    """LLM adapter delegates to LLMService.complete and passes tool descriptors."""
    service = _FakeLLMService('{"final_answer":"done"}')
    caller = ChatClientLLMCaller(service)  # type: ignore[arg-type]
    response = await caller.call(
        [{"role": "user", "content": "hello"}],
        tools=[{"name": "lookup", "inputSchema": {"type": "object"}}],
    )
    assert response["final_answer"] == "done"
    assert len(service.messages) == 1
    assert "lookup" in service.messages[0][0].content


@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")
@pytest.mark.asyncio
async def test_llm_adapter_parses_fenced_tool_parameters_as_tool_call():
    """LLM adapter accepts common fenced JSON tool-call aliases."""
    service = _FakeLLMService(
        """```json
{
  "tool": "read_file",
  "parameters": {
    "path": "/CloudDog-Demos/researcher-ukraine-war/source/data-fingerprint.json",
    "profile": "google_drive"
  }
}
```"""
    )
    caller = ChatClientLLMCaller(service)  # type: ignore[arg-type]
    response = await caller.call([{"role": "user", "content": "read drive source"}])
    assert response["tool_call"] == {
        "name": "read_file",
        "arguments": {
            "path": "/CloudDog-Demos/researcher-ukraine-war/source/data-fingerprint.json",
            "profile": "google_drive",
        },
    }
    assert response["reasoning"] == "Calling tool read_file."
    assert "final_answer" not in response


@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")
@pytest.mark.asyncio
async def test_llm_adapter_preserves_canonical_tool_call_shape():
    """Canonical cloud_dog_agent tool_call payloads are passed through."""
    service = _FakeLLMService(
        '{"tool_call":{"name":"read_file","arguments":{"path":"/x","profile":"google_drive"}}}'
    )
    caller = ChatClientLLMCaller(service)  # type: ignore[arg-type]
    response = await caller.call([{"role": "user", "content": "read drive source"}])
    assert response["tool_call"]["name"] == "read_file"
    assert response["tool_call"]["arguments"]["profile"] == "google_drive"
    assert response["reasoning"] == "Calling tool read_file."
    assert "final_answer" not in response


@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")
@pytest.mark.asyncio
async def test_llm_adapter_parses_name_input_tool_payload_as_tool_call():
    """LLM adapter accepts name/input tool JSON emitted by some hosted models."""
    service = _FakeLLMService(
        """{
  "name": "read_file",
  "server_name": "file-mcp",
  "input": {
    "path": "/CloudDog-Demos/researcher-ukraine-war/source/data-fingerprint.json",
    "profile": "google_drive"
  }
}"""
    )
    caller = ChatClientLLMCaller(service)  # type: ignore[arg-type]
    response = await caller.call([{"role": "user", "content": "read drive source"}])
    assert response["tool_call"] == {
        "name": "read_file",
        "arguments": {
            "path": "/CloudDog-Demos/researcher-ukraine-war/source/data-fingerprint.json",
            "profile": "google_drive",
        },
    }
    assert response["reasoning"] == "Calling tool read_file."
    assert "final_answer" not in response


@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_mcp_executor_uses_selected_server_and_redacts_secret_arguments(monkeypatch, tmp_path: Path):
    """MCP executor routes through MCPConnection and redacts secret-like arguments."""
    calls: list[tuple[str, dict[str, Any]]] = []

    class _FakeTransport:
        async def tools_list(self):
            return {"tools": [{"name": "code_execute", "inputSchema": {}}]}

        async def tools_call(self, name, arguments):
            calls.append((name, arguments))
            return {"content": [{"type": "text", "text": "ok"}]}

    class _FakeConnection:
        def __init__(self, server_index: int) -> None:
            self.spec = type("Spec", (), {"name": f"server-{server_index}"})()
            self.transport = _FakeTransport()

        @classmethod
        def from_config(cls, _config, server_index=0, servers_override=None):
            assert server_index == 1
            assert servers_override == [{"name": "zero"}, {"name": "one"}]
            return cls(server_index)

        async def connect(self):
            return None

        async def close(self):
            return None

    monkeypatch.setattr(adapters, "MCPConnection", _FakeConnection)
    sessions = SessionManager(str(tmp_path))
    session_id = sessions.create_session(metadata={})
    executor = SessionMCPToolExecutor(
        config=_FakeConfig({"mcp.api.require_initialize": False}),  # type: ignore[arg-type]
        sessions=sessions,
        session_id=session_id,
        server_specs=[{"name": "zero"}, {"name": "one"}],
        selected_server_indices=[1],
    )
    result = await executor.execute("code_execute", {"api_key": "secret", "query": "safe"})
    assert result["content"][0]["text"] == "ok"
    assert calls == [("code_execute", {"api_key": "secret", "query": "safe"})]
    events = sessions.get_session(session_id)["events"]
    tool_event = next(event for event in events if event.event_type == "agent_mcp_tool_call")
    assert tool_event.data["arguments"]["api_key"] == "***REDACTED***"
    assert tool_event.data["arguments"]["query"] == "safe"
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_memory_disabled_no_write():
    """Memory adapter is inert unless memory is explicitly enabled."""
    calls: list[str] = []

    class _FakeStore:
        async def get(self, key, scope, namespace):
            calls.append(f"get:{key}")
            return "value"

        async def set(self, key, value, scope, namespace):
            calls.append(f"set:{key}")

        async def clear_scope(self, scope, namespace):
            calls.append("clear")
            return 1

    memory = ChatClientMemoryStore(
        enabled=False,
        tenant_id="tenant",
        namespace="session",
        store=_FakeStore(),  # type: ignore[arg-type]
    )
    await memory.set("session", "key", "value")
    assert await memory.get("session", "key") is None
    assert await memory.clear_scope("session") == 0
    assert calls == []
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_checkpoint_save_load_resume(tmp_path: Path):
    """LongWorkflow checkpoints persist in session metadata and can be loaded."""
    sessions = SessionManager(str(tmp_path))
    session_id = sessions.create_session(metadata={})
    checkpoint = ChatClientCheckpointHook(sessions=sessions, session_id=session_id)
    await checkpoint.save("wf", "step-1", {"completed": ["step-1"], "last_step": "step-1"})
    assert await checkpoint.load("wf") == {"completed": ["step-1"], "last_step": "step-1"}
