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

import cloud_dog_chat_client.cli.interactive as interactive
from cloud_dog_chat_client.llm.protocols import ChatCompletionResult
from cloud_dog_chat_client.session import SessionManager


class _Cfg:
    def get(self, path):
        if path == "llm.system_prompt":
            return None
        if path == "llm.stream":
            return False
        if path == "mcp.servers":
            return [
                {"name": "sqlagent-mcp", "transport": "http_jsonrpc", "base_url": "http://sql:8081"},
                {"name": "search-mcp", "transport": "streamable_http", "base_url": "https://search/mcp"},
            ]
        return None


class _StaticLLM:
    def __init__(self, _cfg):
        pass

    async def complete(self, _messages):
        return ChatCompletionResult(content="OK_FROM_LLM", raw={})
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_ut1_7_cli_help_sessions_mcp_and_logs_commands(monkeypatch, tmp_path, env_file):
    monkeypatch.setattr(interactive, "LLMService", _StaticLLM)

    session_manager = SessionManager(str(tmp_path / "logs"))
    session_id = session_manager.create_session(metadata={})

    inputs = iter(["?", "/mcp", "/sessions", "/logs", "Hello", "/exit"])
    out: list[str] = []

    await interactive.run_chat_loop(
        config=_Cfg(),
        session_manager=session_manager,
        session_id=session_id,
        stream_override=None,
        write_out=lambda s: out.append(s),
        write_out_flush=lambda: None,
        read_in=lambda: next(inputs),
    )

    text = "".join(out)
    assert "Press ? for help" in text
    assert "Cloud-Dog Chat Client" in text
    assert "/mcp use 0,1" in text
    assert "[mcp] configured servers" in text
    assert "[sessions]" in text
    assert "[logs]" in text
    assert "OK_FROM_LLM" in text

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.unit, pytest.mark.mcp, pytest.mark.fast]

