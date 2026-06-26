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

"""Protocol adapters between Chat-Client runtime and cloud_dog_agent.

Related requirements: W28B-317 rows 7, 8, 11, 14, 15, 17.
Related tests: UT_AGENT_LLM_ADAPTER, UT_AGENT_TOOL_EXECUTOR,
UT_AGENT_MEMORY_SCOPE, UT_AGENT_TRANSCRIPT_COMPAT.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from cloud_dog_cache.memory import MemoryScope, MemoryStore

from ..config import ConfigManager
from ..llm.protocols import ChatMessage
from ..llm.service import LLMService
from ..mcp import MCPConnection
from ..session.session_manager import SessionManager
from ..session.transcript import TranscriptEvent

if TYPE_CHECKING:
    from ..jobs import JobsRuntime

_REDACTED_VALUE = "***REDACTED***"
_SECRET_KEY_FRAGMENTS = ("secret", "token", "password", "api_key", "apikey", "bearer")
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*(?P<body>.*?)\s*```$", re.IGNORECASE | re.DOTALL)


def _safe_json(value: Any) -> Any:
    """Return a JSON-compatible value for transcript metadata."""
    try:
        json.dumps(value, ensure_ascii=True)
        return value
    except TypeError:
        return str(value)


def redact_secret_values(value: Any) -> Any:
    """Redact values for keys that are likely to contain credentials."""
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            key_lower = key_text.lower()
            if any(fragment in key_lower for fragment in _SECRET_KEY_FRAGMENTS):
                redacted[key_text] = _REDACTED_VALUE
            else:
                redacted[key_text] = redact_secret_values(item)
        return redacted
    if isinstance(value, list):
        return [redact_secret_values(item) for item in value]
    return value


class ChatClientLLMCaller:
    """cloud_dog_agent LLMCaller adapter backed by Chat-Client LLMService."""

    def __init__(self, llm: LLMService) -> None:
        """Store the Chat-Client LLM service used for all model calls."""
        self._llm = llm

    async def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Call Chat-Client LLMService and return the agent package dict shape."""
        chat_messages: list[ChatMessage] = []
        if tools:
            chat_messages.append(
                ChatMessage(
                    role="system",
                    content=(
                        "Available MCP tools are provided as JSON descriptors. "
                        "Return structured JSON when selecting a tool.\n"
                        f"{json.dumps(tools, ensure_ascii=True, sort_keys=True)}"
                    ),
                )
            )
        for item in messages:
            role = str(item.get("role") or "user").strip().lower()
            if role not in {"system", "user", "assistant", "tool"}:
                role = "user"
            chat_messages.append(
                ChatMessage(role=role, content=str(item.get("content") or ""))
            )
        result = await self._llm.complete(chat_messages)
        return self._parse_agent_response(result.content, raw=result.raw)

    def _parse_agent_response(self, content: str, *, raw: Any) -> dict[str, Any]:
        """Parse JSON agent responses while preserving plain text compatibility."""
        text = str(content or "").strip()
        parsed: Any = None
        if text:
            candidate = text
            fence_match = _JSON_FENCE_RE.match(text)
            if fence_match:
                candidate = fence_match.group("body").strip()
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                parsed = None
        if isinstance(parsed, dict):
            parsed = self._normalise_agent_response_shape(parsed)
            parsed.setdefault("content", text)
            parsed.setdefault("raw", raw)
            if (
                "final_answer" not in parsed
                and "tool_call" not in parsed
                and "code" not in parsed
                and "subtasks" not in parsed
            ):
                parsed["final_answer"] = parsed.get("content", text)
            return parsed
        return {"content": text, "final_answer": text, "raw": raw}

    def _normalise_agent_response_shape(self, parsed: dict[str, Any]) -> dict[str, Any]:
        """Normalise common model tool-call aliases into cloud_dog_agent shape."""
        out = dict(parsed)
        if "tool_call" not in out and out.get("tool"):
            arguments = out.get("arguments")
            if arguments is None:
                arguments = out.get("parameters")
            if arguments is None:
                arguments = out.get("args")
            if arguments is None:
                arguments = {}
            out["tool_call"] = {
                "name": str(out.get("tool") or "").strip(),
                "arguments": arguments if isinstance(arguments, dict) else {},
            }
            out.pop("final_answer", None)
        if "tool_call" not in out and out.get("name"):
            arguments = out.get("arguments")
            if arguments is None:
                arguments = out.get("parameters")
            if arguments is None:
                arguments = out.get("args")
            if arguments is None:
                arguments = out.get("input")
            if arguments is None:
                arguments = {}
            out["tool_call"] = {
                "name": str(out.get("name") or "").strip(),
                "arguments": arguments if isinstance(arguments, dict) else {},
            }
            out.pop("final_answer", None)
        tool_call = out.get("tool_call")
        if isinstance(tool_call, dict) and not str(out.get("reasoning") or "").strip():
            name = str(tool_call.get("name") or "tool").strip() or "tool"
            out["reasoning"] = f"Calling tool {name}."
        return out


class TranscriptProgressCallback:
    """Persist cloud_dog_agent progress notifications as additive transcript events."""

    def __init__(
        self,
        *,
        sessions: SessionManager,
        session_id: str,
        strategy: str,
    ) -> None:
        """Bind progress notifications to one chat session."""
        self._sessions = sessions
        self._session_id = session_id
        self._strategy = strategy

    async def emit(self, iteration: int, data: dict[str, Any]) -> None:
        """Append an additive agent progress event to the session transcript."""
        self._sessions.append_event(
            self._session_id,
            TranscriptEvent(
                event_type="agent_progress",
                data={
                    "strategy": self._strategy,
                    "iteration": int(iteration),
                    "data": _safe_json(redact_secret_values(data)),
                },
            ),
        )


class SessionMCPToolExecutor:
    """cloud_dog_agent ToolExecutor backed by selected session MCP servers."""

    def __init__(
        self,
        *,
        config: ConfigManager,
        sessions: SessionManager,
        session_id: str,
        server_specs: list[dict[str, Any]],
        selected_server_indices: list[int],
    ) -> None:
        """Bind tool execution to the session's selected MCP server scope."""
        self._config = config
        self._sessions = sessions
        self._session_id = session_id
        self._server_specs = list(server_specs)
        self._selected = list(selected_server_indices)

    async def available_tools(self) -> list[dict[str, Any]]:
        """List tools from selected MCP servers using the existing MCP transport."""
        tools: list[dict[str, Any]] = []
        for server_index in self._selected:
            connection = MCPConnection.from_config(
                self._config,
                server_index=server_index,
                servers_override=self._server_specs,
            )
            await connection.connect()
            try:
                await self._maybe_initialize(connection, server_index)
                result = await connection.transport.tools_list()
                for tool in result.get("tools") or []:
                    if isinstance(tool, dict):
                        item = dict(tool)
                        item["server_index"] = server_index
                        item["server_name"] = connection.spec.name
                        tools.append(item)
            finally:
                await connection.close()
        return tools

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Execute a tool via MCP; no local tool execution is permitted."""
        name = str(tool_name or "").strip()
        if not name:
            raise RuntimeError("tool_name is required")
        server_index, resolved_name = await self._resolve_tool(name)
        safe_arguments = redact_secret_values(arguments)
        self._sessions.append_event(
            self._session_id,
            TranscriptEvent(
                event_type="agent_mcp_tool_call",
                data={
                    "server_index": server_index,
                    "name": resolved_name,
                    "arguments": _safe_json(safe_arguments),
                },
            ),
        )
        connection = MCPConnection.from_config(
            self._config,
            server_index=server_index,
            servers_override=self._server_specs,
        )
        await connection.connect()
        try:
            await self._maybe_initialize(connection, server_index)
            result = await connection.transport.tools_call(resolved_name, dict(arguments or {}))
        finally:
            await connection.close()
        self._sessions.append_event(
            self._session_id,
            TranscriptEvent(
                event_type="agent_mcp_tool_result",
                data={
                    "server_index": server_index,
                    "name": resolved_name,
                    "isError": bool(isinstance(result, dict) and result.get("isError")),
                },
            ),
        )
        return result

    async def _resolve_tool(self, name: str) -> tuple[int, str]:
        """Resolve a tool name to a selected server and canonical MCP tool name."""
        if ":" in name:
            raw_index, raw_name = name.split(":", 1)
            try:
                server_index = int(raw_index)
            except ValueError:
                server_index = -1
            if server_index in self._selected and raw_name.strip():
                return server_index, raw_name.strip()
        for tool in await self.available_tools():
            candidate = str(tool.get("name") or "").strip()
            server_index = int(tool.get("server_index") or -1)
            if candidate == name and server_index in self._selected:
                return server_index, candidate
        raise RuntimeError(f"MCP tool '{name}' is not available on selected session servers")

    async def _maybe_initialize(self, connection: MCPConnection, server_index: int) -> None:
        """Run MCP initialize only when the selected server requires it."""
        server_spec = (
            self._server_specs[server_index]
            if 0 <= server_index < len(self._server_specs)
            and isinstance(self._server_specs[server_index], dict)
            else {}
        )
        require_initialize = server_spec.get("require_initialize")
        if require_initialize is None:
            require_initialize = self._config.get("mcp.api.require_initialize") or False
        if not bool(require_initialize):
            return
        protocol_version = str(
            server_spec.get("protocol_version")
            or self._config.get("mcp.defaults.protocol_version")
            or ""
        ).strip()
        if protocol_version and hasattr(connection.transport, "initialize"):
            await connection.transport.initialize(protocol_version=protocol_version)


class JobsSubAgentYieldHook:
    """cloud_dog_agent JobYieldHook backed by Chat-Client JobsRuntime."""

    def __init__(
        self,
        *,
        jobs_runtime: "JobsRuntime | None",
        session_id: str,
        user_id: str,
    ) -> None:
        """Bind child-agent yield bookkeeping to the configured jobs runtime."""
        self._jobs_runtime = jobs_runtime
        self._session_id = session_id
        self._user_id = user_id

    async def yield_for(self, child_job_id: str) -> Any:
        """Submit and complete a durable yield marker for a child-agent wait."""
        if self._jobs_runtime is None:
            raise RuntimeError("jobs runtime is required for subagent yield")
        job_id = self._jobs_runtime.create_job(
            job_type="agent_subagent_yield",
            payload={"child_job_id": str(child_job_id)},
            session_id=self._session_id,
            user_id=self._user_id,
        )
        self._jobs_runtime.mark_running(job_id, worker_id="agent-subagent")
        result = {"job_id": job_id, "child_job_id": str(child_job_id), "status": "yielded"}
        self._jobs_runtime.complete(job_id, result=result)
        return result


class ChatClientCheckpointHook:
    """cloud_dog_agent CheckpointHook backed by session metadata."""

    def __init__(self, *, sessions: SessionManager, session_id: str) -> None:
        """Bind workflow checkpoint state to one session."""
        self._sessions = sessions
        self._session_id = session_id

    async def save(self, workflow_id: str, step_id: str, state: dict[str, Any]) -> None:
        """Save a workflow checkpoint in session metadata."""
        session = self._sessions.get_session(self._session_id)
        metadata = session.get("metadata") if isinstance(session, dict) else {}
        if not isinstance(metadata, dict):
            metadata = {}
        checkpoints = metadata.get("agent_workflow_checkpoints")
        if not isinstance(checkpoints, dict):
            checkpoints = {}
        checkpoints[str(workflow_id)] = {
            "step_id": str(step_id),
            "state": dict(state or {}),
        }
        self._sessions.update_session_metadata(
            self._session_id,
            {"agent_workflow_checkpoints": checkpoints},
        )

    async def load(self, workflow_id: str) -> dict[str, Any] | None:
        """Load the latest workflow checkpoint from session metadata."""
        session = self._sessions.get_session(self._session_id)
        metadata = session.get("metadata") if isinstance(session, dict) else {}
        if not isinstance(metadata, dict):
            return None
        checkpoints = metadata.get("agent_workflow_checkpoints")
        if not isinstance(checkpoints, dict):
            return None
        checkpoint = checkpoints.get(str(workflow_id))
        if not isinstance(checkpoint, dict):
            return None
        state = checkpoint.get("state")
        return dict(state) if isinstance(state, dict) else None


class ChatClientMemoryStore:
    """Scoped cloud_dog_cache MemoryStore adapter for agent strategies."""

    def __init__(
        self,
        *,
        enabled: bool,
        tenant_id: str,
        namespace: str,
        store: MemoryStore | None = None,
    ) -> None:
        """Create an adapter that is inert until memory is explicitly enabled."""
        self._enabled = bool(enabled)
        self._namespace = str(namespace or "chat-client")
        self._store = store or MemoryStore(tenant_id=str(tenant_id or "default"))

    @property
    def enabled(self) -> bool:
        """Return whether memory operations are enabled for this session."""
        return self._enabled

    async def get(self, scope: str, key: str) -> Any | None:
        """Get a memory value if enabled, otherwise return None."""
        if not self._enabled:
            return None
        return await self._store.get(str(key), self._scope(scope), self._namespace)

    async def set(self, scope: str, key: str, value: Any) -> None:
        """Set a memory value only when memory is enabled."""
        if not self._enabled:
            return
        await self._store.set(str(key), value, self._scope(scope), self._namespace)

    async def clear_scope(self, scope: str) -> int:
        """Clear the configured memory namespace for one scope when enabled."""
        if not self._enabled:
            return 0
        return await self._store.clear_scope(self._scope(scope), self._namespace)

    def _scope(self, value: str) -> MemoryScope:
        """Map Chat-Client metadata scope names to cloud_dog_cache scopes."""
        candidate = str(value or "").strip().lower()
        if candidate in {"request"}:
            return MemoryScope.REQUEST
        if candidate in {"user", "profile", "user_profile"}:
            return MemoryScope.USER_PROFILE
        if candidate in {"global"}:
            return MemoryScope.GLOBAL
        return MemoryScope.SESSION
