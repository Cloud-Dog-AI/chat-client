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

"""AT_AGENT_* — application-level profile strategy execution coverage."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

import cloud_dog_chat_client.agent.runtime as runtime_module
import cloud_dog_chat_client.api.routes as routes_module
import cloud_dog_chat_client.api.server as server_module
from cloud_dog_chat_client.api.server import create_app
from cloud_dog_chat_client.config import ConfigManager
from cloud_dog_chat_client.llm.protocols import ChatCompletionResult
from cloud_dog_chat_client.llm.response_policy import ResponsePolicy


@dataclass
class _StrategyCase:
    name: str
    responses: list[str]
    expected_content: str
    defaults: dict[str, Any]


class _SequenceLLMService:
    responses: list[str] = []

    def __init__(self, _cfg, **_kwargs: Any) -> None:
        self._responses = list(type(self).responses)
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
        content = self._responses.pop(0) if self._responses else '{"final_answer":"ok"}'
        return ChatCompletionResult(content=content, raw={"application_test": True})


class _FakeExecutor:
    calls: list[tuple[str, dict[str, Any]]] = []

    def __init__(self, **_kwargs: Any) -> None:
        pass

    async def available_tools(self):
        return [{"name": "code_execute", "inputSchema": {}}]

    async def execute(self, tool_name: str, arguments: dict[str, Any]):
        self.calls.append((tool_name, arguments))
        return {"stdout": "1\n"}


class _FakeJobsRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    @classmethod
    def from_config(cls, _config):
        return cls()

    def health(self) -> bool:
        return True

    def create_job(self, *, job_type, payload, session_id=None, correlation_id=None, user_id=None):
        self.calls.append(("create", job_type))
        return "job-application-test"

    def mark_running(self, job_id, *, worker_id):
        self.calls.append(("running", job_id))

    def complete(self, job_id, *, result=None):
        self.calls.append(("complete", job_id))

    def list_jobs(self, **_kwargs):
        return []


AGENT_CASES = [
    _StrategyCase("react", ['{"final_answer":"react application ok"}'], "react application ok", {}),
    _StrategyCase(
        "codeact",
        ['{"code":"print(1)"}', '{"final_answer":"codeact application ok"}'],
        "codeact application ok",
        {},
    ),
    _StrategyCase("subagent_router", [], "child_job_id", {}),
    _StrategyCase(
        "rlm",
        ['{"subtasks":["part"]}', '{"final_answer":"leaf"}', '{"final_answer":"rlm application ok"}'],
        "rlm application ok",
        {"rlm_max_depth": 1, "rlm_max_subtasks": 1},
    ),
    _StrategyCase(
        "reflexion",
        ['{"final_answer":"reflexion application ok"}', '{"should_retry":false,"critique":"ok"}'],
        "reflexion application ok",
        {"max_reflections": 1},
    ),
    _StrategyCase(
        "longworkflow",
        [],
        "LongWorkflow completed 100/100 pages",
        {"longworkflow_page_count": 100},
    ),
]


def _set_isolated_app_env(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> None:
    monkeypatch.setenv("CLOUD_DOG_DB__DIALECT", "sqlite")
    monkeypatch.setenv("CLOUD_DOG_DB__DATABASE", str(db_path))
    monkeypatch.setenv("CLOUD_DOG__DB__DIALECT", "sqlite")
    monkeypatch.setenv("CLOUD_DOG__DB__DATABASE", str(db_path))
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY_HEADER", "X-API-Key")
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY", "viewer-key")
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__ADMIN_API_KEY", "admin-key")
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
async def test_at_agent_simple_default_compat(env_file, monkeypatch, tmp_path: Path):
    """AT_AGENT_SIMPLE_DEFAULT_COMPAT keeps the default route on the simple LLM path."""
    _set_isolated_app_env(monkeypatch, tmp_path / "at_agent_simple.sqlite3")
    _SequenceLLMService.responses = ["simple application response"]
    monkeypatch.setattr(routes_module, "LLMService", _SequenceLLMService)

    async def _boom(*_args, **_kwargs):
        raise AssertionError("simple default must not enter agent dispatch")

    monkeypatch.setattr(routes_module, "dispatch_agent_message", _boom)
    app = create_app(ConfigManager(env_file=env_file))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        session = await client.post("/sessions", headers={"X-API-Key": "viewer-key"}, json={"metadata": {}})
        assert session.status_code == 200
        session_id = session.json()["session_id"]

        message = await client.post(
            f"/sessions/{session_id}/messages",
            headers={"X-API-Key": "viewer-key"},
            json={"content": "hello", "stream": False},
        )
        assert message.status_code == 200
        assert message.json()["content"] == "simple application response"

        transcript = await client.get(f"/sessions/{session_id}/transcript", headers={"X-API-Key": "viewer-key"})
        assert transcript.status_code == 200
        assert not any(event["event_type"].startswith("agent_dispatch") for event in transcript.json()["events"])
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
@pytest.mark.parametrize("case", AGENT_CASES, ids=[case.name for case in AGENT_CASES])
async def test_at_agent_profile_strategy_executes(
    env_file,
    monkeypatch,
    tmp_path: Path,
    case: _StrategyCase,
):
    """AT_AGENT_* profile strategies round-trip through the application API."""
    _set_isolated_app_env(monkeypatch, tmp_path / f"at_agent_{case.name}.sqlite3")
    _SequenceLLMService.responses = case.responses
    _FakeExecutor.calls = []
    monkeypatch.setattr(routes_module, "LLMService", _SequenceLLMService)
    monkeypatch.setattr(runtime_module, "SessionMCPToolExecutor", _FakeExecutor)
    monkeypatch.setattr(server_module, "JobsRuntime", _FakeJobsRuntime)
    app = create_app(ConfigManager(env_file=env_file))

    profile_id = f"at-{case.name}"
    defaults = {"agent_strategy": case.name, "memory_enabled": False, **case.defaults}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        created = await client.post(
            "/v1/profiles",
            headers={"X-API-Key": "admin-key"},
            json={
                "profile_id": profile_id,
                "name": f"AT {case.name}",
                "description": "AT agent strategy profile",
                "mcp_bindings": [],
                "session_defaults": defaults,
                "access_control": {},
            },
        )
        assert created.status_code == 200
        assert created.json()["profile"]["session_defaults"]["agent_strategy"] == case.name

        session = await client.post(
            "/sessions",
            headers={"X-API-Key": "viewer-key"},
            json={"metadata": {"profile_id": profile_id}},
        )
        assert session.status_code == 200
        session_id = session.json()["session_id"]

        message = await client.post(
            f"/sessions/{session_id}/messages",
            headers={"X-API-Key": "viewer-key"},
            json={"content": "execute application strategy", "stream": False},
        )
        assert message.status_code == 200
        assert case.expected_content in message.json()["content"]

        transcript = await client.get(f"/sessions/{session_id}/transcript", headers={"X-API-Key": "viewer-key"})
        assert transcript.status_code == 200
        events = transcript.json()["events"]
        completed = [event for event in events if event["event_type"] == "agent_dispatch_completed"]
        assert len(completed) == 1
        assert completed[0]["data"]["strategy"] == case.name

    if case.name == "codeact":
        assert _FakeExecutor.calls and _FakeExecutor.calls[0][0] == "code_execute"
