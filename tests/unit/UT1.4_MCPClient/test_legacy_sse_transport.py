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

import json

import httpx
import pytest

from cloud_dog_api_kit.mcp.client_transport import (
    LegacySSEConfig,
    LegacySSETransport,
)


def _transport() -> LegacySSETransport:
    return LegacySSETransport(
        LegacySSEConfig(
            base_url="http://mcp.example",
            sse_path="/sse",
            messages_path="/message",
            timeout_seconds=5.0,
            verify_tls=True,
        )
    )
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


def test_ut1_4_legacy_sse_endpoint_event_accepts_messages_path_and_session_id(env_file):
    transport = _transport()
    transport._handle_event(
        "endpoint",
        json.dumps(
            {
                "messages_path": "/message",
                "session_id": "sess-legacy-1",
            }
        ),
    )

    assert transport._message_endpoint == "/message"
    assert transport._session_id == "sess-legacy-1"
    assert transport._endpoint_with_session("/message") == "/message?session_id=sess-legacy-1"
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


def test_ut1_4_legacy_sse_endpoint_event_accepts_endpoint_and_sessionid(env_file):
    transport = _transport()
    transport._handle_event(
        "endpoint",
        json.dumps({"endpoint": "/message?sessionId=sess-modern-1"}),
    )

    assert transport._message_endpoint == "/message?sessionId=sess-modern-1"
    assert transport._session_id == "sess-modern-1"
    assert (
        transport._endpoint_with_session("/message?sessionId=sess-modern-1")
        == "/message?sessionId=sess-modern-1"
    )
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_ut1_4_legacy_sse_request_adds_session_to_message_post(env_file):
    transport = _transport()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/message"
        assert request.url.params.get("session_id") == "sess-compat-1"
        assert request.headers.get("mcp-session-id") == "sess-compat-1"
        payload = json.loads(request.content.decode("utf-8"))
        transport._handle_event(
            "message",
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "result": {"ok": True},
                }
            ),
        )
        return httpx.Response(200, json={"accepted": True})

    transport._client = httpx.AsyncClient(
        base_url="http://mcp.example",
        transport=httpx.MockTransport(handler),
    )
    transport._message_endpoint = "/message"
    transport._session_id = "sess-compat-1"
    transport._endpoint_ready.set()

    try:
        result = await transport.request("tools/list")
    finally:
        await transport.close()

    assert result.get("jsonrpc") == "2.0"
    assert result.get("result", {}).get("ok") is True

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.unit, pytest.mark.mcp, pytest.mark.fast]
