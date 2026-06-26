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

"""UT_AGENT_RUNTIME_STRATEGIES — package strategy dispatch branches."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cloud_dog_chat_client.agent import runtime
from cloud_dog_chat_client.agent.runtime import AgentDispatchContext, dispatch_agent_message
from cloud_dog_chat_client.llm.protocols import ChatCompletionResult
from cloud_dog_chat_client.session import SessionManager


@dataclass
class _FakeConfig:
    values: dict[str, Any]

    def get(self, key: str, default: Any = None) -> Any:
        """Return a fake config value."""
        return self.values.get(key, default)


class _SequenceLLM:
    def __init__(self, responses: list[str]) -> None:
        """Store the fake response sequence."""
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def complete(self, messages):
        """Return the next fake completion."""
        self.prompts.append(messages[-1].content if messages else "")
        content = self.responses.pop(0) if self.responses else '{"final_answer":"default"}'
        return ChatCompletionResult(content=content, raw={"fake": True})


class _FakeExecutor:
    calls: list[tuple[str, dict[str, Any]]] = []

    def __init__(self, **_kwargs: Any) -> None:
        """Accept the runtime constructor shape."""

    async def available_tools(self):
        """Expose the Code Runner MCP tool."""
        return [{"name": "code_execute", "inputSchema": {}}]

    async def execute(self, tool_name: str, arguments: dict[str, Any]):
        """Record tool execution."""
        self.calls.append((tool_name, arguments))
        return {"stdout": "1\n"}


class _FakeJobsRuntime:
    def __init__(self) -> None:
        """Record job lifecycle calls."""
        self.calls: list[tuple[str, str]] = []

    def create_job(self, *, job_type, payload, session_id=None, correlation_id=None, user_id=None):
        """Create a fake job id."""
        self.calls.append(("create", job_type))
        return "job-1"

    def mark_running(self, job_id, *, worker_id):
        """Record fake running transition."""
        self.calls.append(("running", job_id))

    def complete(self, job_id, *, result=None):
        """Record fake completion."""
        self.calls.append(("complete", job_id))


def _context(
    tmp_path: Path,
    *,
    strategy: str,
    llm: Any | None = None,
    jobs_runtime: Any | None = None,
    metadata: dict[str, Any] | None = None,
) -> AgentDispatchContext:
    sessions = SessionManager(str(tmp_path))
    session_metadata = {"agent_strategy": strategy, **dict(metadata or {})}
    session_id = sessions.create_session(metadata=session_metadata)
    return AgentDispatchContext(
        config=_FakeConfig({"db.tenant_id": "tenant", "db.actor": "agent-test"}),  # type: ignore[arg-type]
        sessions=sessions,
        session_id=session_id,
        prompt="answer the task",
        system_prompt=None,
        llm=llm or _SequenceLLM(['{"final_answer":"ok"}']),  # type: ignore[arg-type]
        server_specs=[],
        selected_server_indices=[],
        jobs_runtime=jobs_runtime,
    )
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_react_strategy_dispatches_with_progress(tmp_path: Path):
    """ReAct uses the package loop and stores additive agent metadata."""
    context = _context(
        tmp_path,
        strategy="react",
        llm=_SequenceLLM(['{"final_answer":"react ok"}']),
        metadata={"memory_enabled": True},
    )
    content = await dispatch_agent_message(context)
    assert content == "react ok"
    events = context.sessions.get_session(context.session_id)["events"]
    assert any(event.event_type == "agent_dispatch_completed" for event in events)
    assert events[-1].data["agent"]["strategy"] == "react"
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_codeact_strategy_calls_only_code_execute_tool(monkeypatch, tmp_path: Path):
    """CodeAct delegates code execution to the accepted Code Runner MCP tool name."""
    _FakeExecutor.calls = []
    monkeypatch.setattr(runtime, "SessionMCPToolExecutor", _FakeExecutor)
    context = _context(
        tmp_path,
        strategy="codeact",
        llm=_SequenceLLM(['{"code":"print(1)"}', '{"final_answer":"code ok"}']),
    )
    content = await dispatch_agent_message(context)
    assert content == "code ok"
    assert _FakeExecutor.calls[0][0] == "code_execute"
    events = context.sessions.get_session(context.session_id)["events"]
    assert any(event.event_type == "agent_prompt_rendered" and event.data["strategy"] == "codeact" for event in events)
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_subagent_strategy_uses_jobs_yield(tmp_path: Path):
    """SubAgent dispatch requires and uses the jobs/yield hook."""
    jobs = _FakeJobsRuntime()
    context = _context(tmp_path, strategy="subagent_router", jobs_runtime=jobs)
    content = await dispatch_agent_message(context)
    assert "child_job_id" in content
    assert ("create", "agent_subagent_yield") in jobs.calls
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_rlm_strategy_depth_and_subtask_bounds(tmp_path: Path):
    """RLM dispatch uses bounded package recursion and aggregates a final answer."""
    llm = _SequenceLLM(
        [
            '{"subtasks":["part"]}',
            '{"final_answer":"leaf"}',
            '{"final_answer":"rlm ok"}',
        ]
    )
    context = _context(
        tmp_path,
        strategy="rlm",
        llm=llm,
        metadata={"rlm_max_depth": 1, "rlm_max_subtasks": 1},
    )
    assert await dispatch_agent_message(context) == "rlm ok"
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_reflexion_strategy_uses_platform_prompt(tmp_path: Path):
    """Reflexion dispatch renders the package prompt and enforces max reflections."""
    llm = _SequenceLLM(['{"final_answer":"reflexion ok"}', '{"should_retry":false,"critique":"ok"}'])
    context = _context(tmp_path, strategy="reflexion", llm=llm, metadata={"max_reflections": 1})
    assert await dispatch_agent_message(context) == "reflexion ok"
    events = context.sessions.get_session(context.session_id)["events"]
    assert any(event.event_type == "agent_prompt_rendered" and event.data["strategy"] == "reflexion" for event in events)
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_longworkflow_completes_100_page_checkpoint(tmp_path: Path):
    """LongWorkflow dispatch completes the required 100-page package workflow."""
    context = _context(tmp_path, strategy="longworkflow", metadata={"longworkflow_page_count": 100})
    content = await dispatch_agent_message(context)
    assert "LongWorkflow completed 100/100 pages" in content
    metadata = context.sessions.get_session(context.session_id)["metadata"]
    checkpoint = metadata["agent_workflow_checkpoints"][f"{context.session_id}-longworkflow"]
    assert checkpoint["state"]["last_step"] == "page-100"
