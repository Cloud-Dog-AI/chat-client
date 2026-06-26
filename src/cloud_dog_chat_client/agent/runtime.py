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

"""Chat-Client runtime dispatch for accepted cloud_dog_agent strategies.

Related requirements: W28B-317 rows 6, 9-17.
Related tests: UT_AGENT_RUNTIME_STRATEGIES, UT_AGENT_PROMPTS,
UT_AGENT_LONGWORKFLOW_CHECKPOINT, AT_AGENT_*.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator

from cloud_dog_agent import (
    AgentStrategy,
    CodeActConfig,
    CodeActLoop,
    LongWorkflow,
    RLMConfig,
    RLMRunner,
    ReActConfig,
    ReActLoop,
    ReflexionConfig,
    ReflexionWrapper,
    SubAgentInvoker,
    SubAgentRequest,
    WorkflowStep,
)
from cloud_dog_llm.prompts.agentic import (
    render_code_emission_prompt,
    render_reflection_prompt,
)

from ..config import ConfigManager
from ..llm.service import LLMService
from ..session.session_manager import SessionManager
from ..session.transcript import TranscriptEvent
from .adapters import (
    ChatClientCheckpointHook,
    ChatClientLLMCaller,
    ChatClientMemoryStore,
    JobsSubAgentYieldHook,
    SessionMCPToolExecutor,
    TranscriptProgressCallback,
)
from .strategy import LONGWORKFLOW_AGENT_STRATEGY, agent_strategy_for_session

if TYPE_CHECKING:
    from ..jobs import JobsRuntime


@dataclass(slots=True)
class AgentDispatchContext:
    """All Chat-Client dependencies needed for one agent dispatch."""

    config: ConfigManager
    sessions: SessionManager
    session_id: str
    prompt: str
    system_prompt: str | None
    llm: LLMService
    server_specs: list[dict[str, Any]]
    selected_server_indices: list[int]
    jobs_runtime: "JobsRuntime | None" = None

    @property
    def metadata(self) -> dict[str, Any]:
        """Return the current session metadata dictionary."""
        session = self.sessions.get_session(self.session_id)
        metadata = session.get("metadata") if isinstance(session, dict) else {}
        return dict(metadata) if isinstance(metadata, dict) else {}


def _int_metadata(metadata: dict[str, Any], key: str, default: int) -> int:
    """Read a positive integer metadata value with a safe default."""
    try:
        value = int(metadata.get(key) or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _bool_metadata(metadata: dict[str, Any], key: str, default: bool = False) -> bool:
    """Read a boolean metadata value supporting string form values."""
    raw = metadata.get(key)
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _agent_memory_enabled(metadata: dict[str, Any]) -> bool:
    """Require an agent-specific memory opt-in before using package memory."""
    if _bool_metadata(metadata, "agent_memory_enabled", False):
        return True
    scope = str(metadata.get("agent_memory_scope") or "").strip().lower()
    return scope not in {"", "none"} and _bool_metadata(metadata, "memory_enabled", False)


def _result_text(value: Any) -> str:
    """Return a user-facing final result string from package trace objects."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _append_agent_event(
    context: AgentDispatchContext,
    event_type: str,
    data: dict[str, Any],
) -> None:
    """Append an additive agent transcript event."""
    context.sessions.append_event(
        context.session_id,
        TranscriptEvent(event_type=event_type, data=data),
    )


def render_codeact_prompt(context: AgentDispatchContext, tools: list[dict[str, Any]]) -> str:
    """Render the package CodeAct prompt for this Chat-Client dispatch."""
    metadata = context.metadata
    runtime = str(metadata.get("codeact_runtime") or "python").strip() or "python"
    rendered = render_code_emission_prompt(
        task=context.prompt,
        runtime=runtime,
        available_tools=tools,
        constraints=[
            "Execution must be delegated to the configured Code Runner MCP tool.",
            "Do not execute code in the chat-client process.",
        ],
        observations=[],
    )
    _append_agent_event(
        context,
        "agent_prompt_rendered",
        {"strategy": "codeact", "prompt_sha256": rendered.sha256},
    )
    return rendered.text


def render_reflexion_prompt(context: AgentDispatchContext, attempt: str = "", result: str = "") -> str:
    """Render the package Reflexion prompt for this Chat-Client dispatch."""
    rendered = render_reflection_prompt(
        task=context.prompt,
        attempt=attempt or "initial",
        result=result or "pending",
        criteria=["Correctness", "Completeness", "Safety"],
        max_findings=3,
    )
    _append_agent_event(
        context,
        "agent_prompt_rendered",
        {"strategy": "reflexion", "prompt_sha256": rendered.sha256},
    )
    return rendered.text


async def _run_react_strategy(context: AgentDispatchContext) -> tuple[str, dict[str, Any]]:
    """Run cloud_dog_agent ReActLoop for one message."""
    metadata = context.metadata
    llm = ChatClientLLMCaller(context.llm)
    executor = SessionMCPToolExecutor(
        config=context.config,
        sessions=context.sessions,
        session_id=context.session_id,
        server_specs=context.server_specs,
        selected_server_indices=context.selected_server_indices,
    )
    tools = await executor.available_tools()
    loop = ReActLoop(
        ReActConfig(
            max_iterations=_int_metadata(metadata, "agent_max_iterations", 5),
            max_wall_time_seconds=_int_metadata(metadata, "agent_max_wall_time_seconds", 600),
            tools_available=tools,
        ),
        llm=llm,
        executor=executor,
        progress=TranscriptProgressCallback(
            sessions=context.sessions,
            session_id=context.session_id,
            strategy="react",
        ),
    )
    trace = await loop.run(context.prompt)
    return _result_text(trace.final_answer), {
        "iterations_used": trace.iterations_used,
        "terminated_by": trace.terminated_by,
        "tool_observations": len(trace.observations),
    }


async def _run_codeact_strategy(context: AgentDispatchContext) -> tuple[str, dict[str, Any]]:
    """Run cloud_dog_agent CodeActLoop using the selected Code Runner MCP tool."""
    metadata = context.metadata
    llm = ChatClientLLMCaller(context.llm)
    executor = SessionMCPToolExecutor(
        config=context.config,
        sessions=context.sessions,
        session_id=context.session_id,
        server_specs=context.server_specs,
        selected_server_indices=context.selected_server_indices,
    )
    tools = await executor.available_tools()
    prompt = render_codeact_prompt(context, tools)
    loop = CodeActLoop(
        CodeActConfig(
            max_iterations=_int_metadata(metadata, "agent_max_iterations", 3),
            max_wall_time_seconds=_int_metadata(metadata, "agent_max_wall_time_seconds", 600),
            runtime=str(metadata.get("codeact_runtime") or "python").strip() or "python",
        ),
        llm=llm,
        executor=executor,
        progress=TranscriptProgressCallback(
            sessions=context.sessions,
            session_id=context.session_id,
            strategy="codeact",
        ),
    )
    trace = await loop.run(prompt)
    return _result_text(trace.final_answer), {
        "iterations_used": trace.iterations_used,
        "terminated_by": trace.terminated_by,
        "code_requests": len(trace.code_requests),
        "observations": len(trace.observations),
    }


async def _run_subagent_strategy(context: AgentDispatchContext) -> tuple[str, dict[str, Any]]:
    """Run cloud_dog_agent SubAgentInvoker through Chat-Client jobs/yield semantics."""
    metadata = context.metadata
    max_depth = _int_metadata(metadata, "subagent_max_depth", 3)
    invoker = SubAgentInvoker(
        max_depth=max_depth,
        yield_hook=JobsSubAgentYieldHook(
            jobs_runtime=context.jobs_runtime,
            session_id=context.session_id,
            user_id=str(context.config.get("db.actor") or "chat-client"),
        ),
    )
    request = SubAgentRequest(
        agent_url=str(metadata.get("subagent_url") or "chat-client://local"),
        strategy=AgentStrategy.SIMPLE,
        prompt=context.prompt,
        memory_scope=str(metadata.get("agent_memory_scope") or "none"),
        metadata={"parent_session_id": context.session_id},
    )
    result = await invoker.invoke(request, current_depth=0)
    if not result.success:
        raise RuntimeError(result.error or "subagent invocation failed")
    return _result_text(result.result), {"depth": result.depth, "job_id": result.job_id}


async def _run_rlm_strategy(context: AgentDispatchContext) -> tuple[str, dict[str, Any]]:
    """Run cloud_dog_agent RLMRunner for one message."""
    metadata = context.metadata
    runner = RLMRunner(
        RLMConfig(
            max_depth=_int_metadata(metadata, "rlm_max_depth", 2),
            max_subtasks=_int_metadata(metadata, "rlm_max_subtasks", 3),
        ),
        llm=ChatClientLLMCaller(context.llm),
    )
    trace = await runner.run(context.prompt)
    return _result_text(trace.final_result), {
        "subtasks": len(trace.subtasks),
        "max_depth_reached": trace.max_depth_reached,
    }


async def _run_reflexion_strategy(context: AgentDispatchContext) -> tuple[str, dict[str, Any]]:
    """Run cloud_dog_agent ReflexionWrapper with the platform prompt template."""
    metadata = context.metadata
    llm = ChatClientLLMCaller(context.llm)
    prompt = render_reflexion_prompt(context)

    async def _inner(inner_prompt: str, **_: Any) -> Any:
        response = await llm.call([{"role": "user", "content": inner_prompt}])
        return response.get("final_answer", response.get("content", ""))

    wrapper = ReflexionWrapper(
        ReflexionConfig(max_reflections=_int_metadata(metadata, "max_reflections", 2)),
        llm=llm,
        inner=_inner,
    )
    trace = await wrapper.run(prompt)
    return _result_text(trace.final_result), {
        "reflections_used": trace.reflections_used,
        "accepted": trace.accepted,
    }


async def _run_longworkflow_strategy(context: AgentDispatchContext) -> tuple[str, dict[str, Any]]:
    """Run a 100-page cloud_dog_agent LongWorkflow with session checkpoints."""
    page_count = _int_metadata(context.metadata, "longworkflow_page_count", 100)
    page_count = max(1, min(page_count, 100))

    async def _page_handler(prompt: str) -> dict[str, Any]:
        return {"page": prompt, "status": "processed"}

    steps: list[WorkflowStep] = []
    for index in range(1, page_count + 1):
        step_id = f"page-{index:03d}"
        depends_on = [f"page-{index - 1:03d}"] if index > 1 else []
        steps.append(
            WorkflowStep(
                step_id=step_id,
                strategy=AgentStrategy.SIMPLE,
                prompt=f"{context.prompt} :: page {index}",
                depends_on=depends_on,
                handler=_page_handler,
            )
        )
    workflow = LongWorkflow(
        workflow_id=f"{context.session_id}-longworkflow",
        steps=steps,
        checkpoint=ChatClientCheckpointHook(
            sessions=context.sessions,
            session_id=context.session_id,
        ),
    )
    trace = await workflow.run()
    content = (
        f"LongWorkflow completed {trace.completed_steps}/{page_count} pages "
        f"with {trace.failed_steps} failed and {trace.skipped_steps} skipped."
    )
    return content, {
        "workflow_id": trace.workflow_id,
        "completed_steps": trace.completed_steps,
        "failed_steps": trace.failed_steps,
        "skipped_steps": trace.skipped_steps,
        "resumed_from": trace.resumed_from,
    }


async def dispatch_agent_message(context: AgentDispatchContext) -> str:
    """Dispatch one non-simple agent strategy and persist the final response."""
    strategy = agent_strategy_for_session(context.metadata)
    _append_agent_event(
        context,
        "agent_dispatch_started",
        {"strategy": strategy},
    )
    memory = ChatClientMemoryStore(
        enabled=_agent_memory_enabled(context.metadata),
        tenant_id=str(context.config.get("db.tenant_id") or "default"),
        namespace=f"{context.session_id}:{strategy}",
    )
    if memory.enabled:
        await memory.set("session", "last_prompt", context.prompt)
    if strategy == AgentStrategy.REACT.value:
        content, metadata = await _run_react_strategy(context)
    elif strategy == AgentStrategy.CODEACT.value:
        content, metadata = await _run_codeact_strategy(context)
    elif strategy == AgentStrategy.SUBAGENT_ROUTER.value:
        content, metadata = await _run_subagent_strategy(context)
    elif strategy == AgentStrategy.RLM.value:
        content, metadata = await _run_rlm_strategy(context)
    elif strategy == AgentStrategy.REFLEXION.value:
        content, metadata = await _run_reflexion_strategy(context)
    elif strategy == LONGWORKFLOW_AGENT_STRATEGY:
        content, metadata = await _run_longworkflow_strategy(context)
    else:
        content, metadata = context.prompt, {"strategy": "simple"}
    if memory.enabled:
        await memory.set("session", "last_response", content)
    _append_agent_event(
        context,
        "agent_dispatch_completed",
        {"strategy": strategy, "metadata": metadata},
    )
    context.sessions.append_event(
        context.session_id,
        TranscriptEvent(
            event_type="assistant_message",
            data={"content": content, "agent": {"strategy": strategy, **metadata}},
        ),
    )
    return content


async def stream_agent_message(context: AgentDispatchContext) -> AsyncIterator[str]:
    """Stream a non-simple agent response using Chat-Client's JSONL envelope."""
    content = await dispatch_agent_message(context)
    if content:
        yield json.dumps({"type": "delta", "content_delta": content}) + "\n"
    yield json.dumps({"type": "done"}) + "\n"
