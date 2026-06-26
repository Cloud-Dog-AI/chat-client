# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited
#
# Licensed under the Apache License, Version 2.0.

"""W28M-1600: regression test for session-tools-list 500 on empty profile_mcp_servers.

The /sessions/{session_id}/mcp/tools/list handler previously 500'd with
"missing required configuration key: mcp.servers" when a session's profile had
empty mcp_bindings (passed through as profile_mcp_servers=[] in session
metadata). The list passed the isinstance(servers, list) check and was forwarded
to MCPConnection.from_config as servers_override=[], which then raised because
the empty list is treated as "no servers configured".

Fix: in routes.py _session_server_specs(), treat an EMPTY profile_mcp_servers
list the same as "no override" — fall back to the global runtime servers.
"""

from __future__ import annotations

import pytest

from cloud_dog_chat_client.api.routes import MCPToolsListRequest, build_router
from cloud_dog_chat_client.config import ConfigManager
from cloud_dog_chat_client.session import SessionManager


def _route_endpoint(router, path: str, method: str):
    for route in router.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise RuntimeError(f"endpoint not found: {method} {path}")
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_session_tools_list_falls_back_to_global_when_profile_servers_empty(
    env_file, monkeypatch
):
    """Empty profile_mcp_servers must not 500 — must use the globally-configured MCP servers."""
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__0__NAME", "search-mcp")
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__0__TRANSPORT", "streamable_http")
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__0__BASE_URL", "https://search.example")
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__0__MCP_PATH", "/mcp")

    cfg = ConfigManager(env_file=env_file)
    sessions = SessionManager("./logs")
    router = build_router(config=cfg, sessions=sessions)

    # Create a session whose backing profile has empty mcp_bindings.
    session_id = sessions.create_session(
        metadata={
            "profile_id": "empty-bindings-profile",
            "profile_mcp_servers": [],  # <-- the bug trigger
            "selected_mcp_server_indices": [0],
        }
    )

    # Stub MCPConnection so we don't hit a network — capture what servers_override it receives.
    captured: dict[str, object] = {}

    class _FakeTransport:
        async def tools_list(self):
            return {"tools": [{"name": "search"}]}

    class _FakeConnection:
        def __init__(self, transport):
            self.transport = transport

        @classmethod
        def from_config(cls, cfg_arg, server_index=0, servers_override=None):
            captured["server_index"] = server_index
            captured["servers_override"] = servers_override
            # Must NOT be an empty list — the fix passes the GLOBAL servers here.
            if isinstance(servers_override, list) and not servers_override:
                raise RuntimeError(
                    "REGRESSION: _session_server_specs returned empty list for empty profile_mcp_servers"
                )
            return cls(_FakeTransport())

        async def connect(self):
            return None

        async def close(self):
            return None

    monkeypatch.setattr(
        "cloud_dog_chat_client.mcp.MCPConnection",
        _FakeConnection,
    )

    endpoint = _route_endpoint(router, "/sessions/{session_id}/mcp/tools/list", "POST")
    result = await endpoint(session_id, MCPToolsListRequest(server_index=0))

    assert result == {"tools": [{"name": "search"}]}
    # Fix-defining assertion: servers_override is non-empty (globally-configured servers).
    assert isinstance(captured["servers_override"], list)
    assert len(captured["servers_override"]) >= 1
    assert captured["servers_override"][0].get("name") == "search-mcp"
