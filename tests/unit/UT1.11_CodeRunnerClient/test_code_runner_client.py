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

"""UT1.11 — chat-client consumer-side ``code.execute`` A2A client (W28I-1218).

These tests mock the outbound HTTP call (``httpx.MockTransport``) so no live
network is required. They assert the request shape sent to code-runner
(``skill_id == code.execute``, ``code``/``language`` in ``input``), that the
chat-client correlation id is propagated, and that a ``completed`` response is
surfaced with its stdout/exit_code.
"""

import json

import httpx
import pytest

from cloud_dog_chat_client.clients.code_runner import (
    SKILL_ID,
    CodeRunnerClient,
    CodeRunnerConfig,
    CodeRunnerError,
)


def _config() -> CodeRunnerConfig:
    return CodeRunnerConfig(
        base_url="https://codemcpserver.example.com",
        api_key="test-code-runner-key",
        api_key_header="X-API-Key",
    )
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_ut1_11_execute_request_shape_and_completed_response_no_network():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        # Endpoint is the producer A2A tasks path.
        assert request.url.path == "/a2a/tasks"
        # API key + correlation id are propagated on headers.
        assert request.headers.get("X-API-Key") == "test-code-runner-key"
        assert request.headers.get("X-Correlation-Id") == "corr-abc-123"
        body = json.loads(request.content.decode("utf-8"))
        captured.update(body)
        # Request shape: skill_id=code.execute, code+language in input.
        assert body["skill_id"] == SKILL_ID
        assert body["input"]["code"] == "print('hi')"
        assert body["input"]["language"] == "python"
        assert isinstance(body["task_id"], str) and body["task_id"]
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "task_id": body["task_id"],
                "result": {
                    "stdout": "hi\n",
                    "stderr": "",
                    "exit_code": 0,
                    "duration_ms": 12,
                },
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        client = CodeRunnerClient(_config(), client=mock_client)
        result = await client.execute(
            code="print('hi')",
            language="python",
            correlation_id="corr-abc-123",
        )

    assert captured["skill_id"] == SKILL_ID
    assert result.completed is True
    assert result.status == "completed"
    assert result.stdout == "hi\n"
    assert result.exit_code == 0
    assert result.duration_ms == 12
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_ut1_11_node_language_and_failed_status_is_surfaced_not_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        assert body["input"]["language"] == "node"
        return httpx.Response(
            200,
            json={
                "status": "failed",
                "task_id": body["task_id"],
                "result": {
                    "stdout": "",
                    "stderr": "ReferenceError",
                    "exit_code": 1,
                },
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        client = CodeRunnerClient(_config(), client=mock_client)
        result = await client.execute(code="boom()", language="node")

    # A non-zero task is surfaced as a populated result, not an exception.
    assert result.completed is False
    assert result.status == "failed"
    assert result.stderr == "ReferenceError"
    assert result.exit_code == 1
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_ut1_11_non_2xx_raises_code_runner_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        client = CodeRunnerClient(_config(), client=mock_client)
        with pytest.raises(CodeRunnerError) as exc_info:
            await client.execute(code="print(1)")
    assert "401" in str(exc_info.value)
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_ut1_11_unsupported_language_rejected_before_network():
    client = CodeRunnerClient(_config(), client=None)
    with pytest.raises(CodeRunnerError):
        await client.execute(code="print(1)", language="ruby")
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_ut1_11_empty_code_rejected_before_network():
    client = CodeRunnerClient(_config(), client=None)
    with pytest.raises(CodeRunnerError):
        await client.execute(code="   ", language="python")
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_ut1_11_build_payload_and_headers_contract():
    client = CodeRunnerClient(_config())
    payload = client.build_payload(code="x = 1", language="python", task_id="t-1")
    assert payload == {
        "task_id": "t-1",
        "skill_id": "code.execute",
        "input": {"code": "x = 1", "language": "python"},
    }
    headers = client.build_headers(correlation_id="corr-1")
    assert headers["X-API-Key"] == "test-code-runner-key"
    assert headers["content-type"] == "application/json"
    assert headers["X-Correlation-Id"] == "corr-1"
    # No correlation id -> header omitted.
    assert "X-Correlation-Id" not in client.build_headers(correlation_id="")


# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [
    pytest.mark.unit,
    pytest.mark.pure,
    pytest.mark.fast,
]
