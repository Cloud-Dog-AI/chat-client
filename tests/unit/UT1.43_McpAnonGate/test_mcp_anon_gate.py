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

"""
UT1.43 — chat-client MCP anonymous-access gate (negative-auth regression guard).

W28C-1703 / CC1 / 1601-B (S-SECURITY P0). The api-kit ``register_mcp_contract``
transport mount was registered BEFORE the bespoke ``mcp_endpoint``, so FastAPI
first-wins routing served ``POST /mcp`` from the api-kit transport with NO auth,
making the local ``_authorised`` + ``_TOOL_PERMISSIONS`` gate dead code (the anon
``tools/call list_sessions`` 575-session leak). The redundant api-kit mount is
dropped and the bespoke endpoint now enforces default-deny on EVERY JSON-RPC
call (HTTP 401), with unknown tools refused.

This test fails if the gate regresses to anon access.

Related Tasks: W28C-1703 (CC1)

Recent Changes:
- 2026-06-10: W28C-1703 — initial MCP anon-gate regression guard.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.fast]

_KEY = "ut143-key"


def _jsonrpc(method: str, **params):
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}


@pytest.fixture()
def mcp_client(monkeypatch) -> TestClient:
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY", _KEY)
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY_HEADER", "X-API-Key")
    from cloud_dog_chat_client.servers import mcp_server

    return TestClient(mcp_server.create_app(), raise_server_exceptions=False)


@pytest.mark.parametrize(
    "method,params",
    [
        ("initialize", {}),
        ("tools/list", {}),
        ("tools/call", {"name": "list_sessions", "arguments": {}}),
    ],
)
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")
def test_anonymous_jsonrpc_is_denied_401(mcp_client, method, params) -> None:
    with mcp_client as client:
        resp = client.post("/mcp", json=_jsonrpc(method, **params))
        assert resp.status_code == 401, (
            f"anon {method} must be HTTP 401: {resp.status_code} {resp.text[:200]}"
        )
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


def test_anonymous_messages_alias_denied_401(mcp_client) -> None:
    with mcp_client as client:
        resp = client.post("/messages", json=_jsonrpc("tools/call", name="list_sessions", arguments={}))
        assert resp.status_code == 401, resp.text[:200]
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


def test_authenticated_handshake_passes_gate(mcp_client) -> None:
    headers = {"X-API-Key": _KEY}
    with mcp_client as client:
        assert client.post("/mcp", json=_jsonrpc("initialize"), headers=headers).status_code == 200
        listed = client.post("/mcp", json=_jsonrpc("tools/list"), headers=headers)
        assert listed.status_code == 200
        assert (listed.json().get("result") or {}).get("tools"), listed.text[:200]
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


def test_authenticated_unknown_tool_default_denied_401(mcp_client) -> None:
    headers = {"X-API-Key": _KEY}
    with mcp_client as client:
        resp = client.post(
            "/mcp",
            json=_jsonrpc("tools/call", name="evil_unknown_tool", arguments={}),
            headers=headers,
        )
        assert resp.status_code == 401, (
            f"a tool not in _TOOL_PERMISSIONS must be default-denied 401: "
            f"{resp.status_code} {resp.text[:200]}"
        )
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


def test_apikit_anon_rest_surface_removed(mcp_client) -> None:
    """The dropped api-kit contract mount no longer exposes anon REST tool paths."""
    with mcp_client as client:
        assert client.get("/mcp/tools").status_code == 404
        assert client.post("/mcp/tools/list_sessions", json={}).status_code in (404, 405)
