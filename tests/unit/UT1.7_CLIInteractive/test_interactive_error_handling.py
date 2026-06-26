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
from cloud_dog_chat_client.session import SessionManager


class _Cfg:
    def __init__(self, *, stream: bool = False):
        self._stream = stream

    def get(self, path):
        if path == "llm.system_prompt":
            return None
        if path == "llm.stream":
            return self._stream
        if path == "mcp.servers":
            return []
        return None


class _FailingLLM:
    def __init__(self, _cfg):
        pass

    async def complete(self, _messages):
        raise RuntimeError("connect failed")
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_ut1_7_cli_loop_handles_llm_exception_without_crashing(monkeypatch, tmp_path, env_file):
    monkeypatch.setattr(interactive, "LLMService", _FailingLLM)

    session_manager = SessionManager(str(tmp_path / "logs"))
    session_id = session_manager.create_session(metadata={})

    inputs = iter(["Hello", "/exit"])
    out: list[str] = []

    def _read_in():
        return next(inputs)

    def _write(s: str):
        out.append(s)

    await interactive.run_chat_loop(
        config=_Cfg(stream=False),
        session_manager=session_manager,
        session_id=session_id,
        stream_override=None,
        write_out=_write,
        write_out_flush=lambda: None,
        read_in=_read_in,
    )

    text = "".join(out)
    assert "Press ? for help" in text
    assert "[error] LLM request failed:" in text
    session = session_manager.get_session(session_id)
    assert any(e.event_type == "assistant_error" for e in session["events"])

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.unit, pytest.mark.mcp, pytest.mark.fast]

