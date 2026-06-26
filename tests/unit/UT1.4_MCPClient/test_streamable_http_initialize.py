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

from __future__ import annotations

import pytest

from cloud_dog_api_kit.mcp.client_transport import (
    MCPTransportError,
    StreamableHTTPConfig,
    StreamableHTTPTransport,
)
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_ut1_4_streamable_initialize_allows_missing_session_notify(monkeypatch):
    t = StreamableHTTPTransport(
        StreamableHTTPConfig(
            base_url="http://localhost:3000",
            mcp_path="/mcp",
            timeout_seconds=1.0,
        )
    )

    async def _fake_request(method, params=None):
        assert method == "initialize"
        return {}

    async def _fake_notify(method, params=None):
        raise MCPTransportError("Streamable HTTP notifications require an established session")

    monkeypatch.setattr(t, "request", _fake_request)
    monkeypatch.setattr(t, "notify", _fake_notify)

    await t.initialize(protocol_version="2024-11-05")
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_ut1_4_streamable_initialize_raises_other_notify_error(monkeypatch):
    t = StreamableHTTPTransport(
        StreamableHTTPConfig(
            base_url="http://localhost:3000",
            mcp_path="/mcp",
            timeout_seconds=1.0,
        )
    )

    async def _fake_request(method, params=None):
        assert method == "initialize"
        return {}

    async def _fake_notify(method, params=None):
        raise MCPTransportError("notify failed for unrelated reason")

    monkeypatch.setattr(t, "request", _fake_request)
    monkeypatch.setattr(t, "notify", _fake_notify)

    with pytest.raises(MCPTransportError, match="unrelated"):
        await t.initialize(protocol_version="2024-11-05")

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.unit, pytest.mark.mcp, pytest.mark.fast]
