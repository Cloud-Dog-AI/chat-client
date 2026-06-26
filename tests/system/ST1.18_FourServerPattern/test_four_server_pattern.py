# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited

from __future__ import annotations

import asyncio
import json
import os

import httpx
import pytest
import websockets

from cloud_dog_chat_client.config import ConfigManager
from cloud_dog_chat_client.database.runtime import ChatDatabaseRuntime
from tests.helpers.api_server import (
    a2a_base_url,
    api_base_url,
    api_headers,
    mcp_base_url,
    start_all,
    stop_all,
    wait_for_api,
    wait_for_base_url,
    web_base_url,
)


@pytest.fixture(scope="module", autouse=True)
def _runtime(env_file):
    os.environ["CLOUD_DOG__CLIENT_API__API_KEY_HEADER"] = "X-API-Key"
    os.environ["CLOUD_DOG__CLIENT_API__API_KEY"] = "dev-key"
    os.environ["CLOUD_DOG__CLIENT_API__ADMIN_API_KEY_HEADER"] = "X-API-Key"
    os.environ["CLOUD_DOG__CLIENT_API__ADMIN_API_KEY"] = "dev-key"
    cfg = ConfigManager(env_file=env_file)
    start_all(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        wait_for_base_url(cfg, web_base_url(cfg))
        wait_for_base_url(cfg, mcp_base_url(cfg))
        wait_for_base_url(cfg, a2a_base_url(cfg))
        yield cfg
    finally:
        stop_all(cfg, env_file=env_file)
@pytest.mark.ST
@pytest.mark.cli
@pytest.mark.req("FR-009")


@pytest.mark.asyncio
async def test_st1_18_four_server_health_and_proxy(_runtime):
    cfg = _runtime
    timeout = float(cfg.get("client_api.request_timeout_seconds") or 60)
    headers = api_headers(cfg)

    async with httpx.AsyncClient(timeout=timeout) as client:
        health_targets = [
            (api_base_url(cfg), "/health", "api"),
            (api_base_url(cfg), "/api/health", "api"),
            (web_base_url(cfg), "/health", "web"),
            (mcp_base_url(cfg), "/health", "mcp"),
            (mcp_base_url(cfg), "/mcp/health", "mcp"),
            (a2a_base_url(cfg), "/health", "a2a"),
            (a2a_base_url(cfg), "/a2a/health", "a2a"),
        ]
        for base, path, expected_server in health_targets:
            resp = await client.get(f"{base}{path}")
            assert resp.status_code == 200
            payload = resp.json()
            assert payload.get("status") == "ok"
            assert payload.get("server") == expected_server
            assert isinstance(payload.get("server_id"), str)
            assert payload.get("server_id", "").strip()

        create = await client.post(
            f"{web_base_url(cfg)}/sessions",
            json={"metadata": {"suite": "st1.18"}},
            headers=headers,
        )
        assert create.status_code == 200
        session_id = create.json()["session_id"]

        login = await client.get(f"{web_base_url(cfg)}/login")
        assert login.status_code == 200
        assert "<div id=\"root\"></div>" in login.text
        assert "/runtime-config.js" in login.text

        ui_cfg = await client.get(f"{web_base_url(cfg)}/ui/config", headers=headers)
        assert ui_cfg.status_code == 200
        assert ui_cfg.json()["application"]["name"]

        init_resp = await client.post(
            f"{mcp_base_url(cfg)}/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            headers=headers,
        )
        assert init_resp.status_code == 200
        assert init_resp.json()["result"]["serverInfo"]["name"] == "cloud-dog-chat-client-mcp"

        tools_resp = await client.post(
            f"{mcp_base_url(cfg)}/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers=headers,
        )
        assert tools_resp.status_code == 200
        names = [item["name"] for item in tools_resp.json()["result"]["tools"]]
        assert {"create_session", "send_message", "list_sessions", "get_history"} <= set(names)

        call_resp = await client.post(
            f"{mcp_base_url(cfg)}/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "list_sessions", "arguments": {}},
            },
        )
        assert call_resp.status_code == 200
        result = call_resp.json()["result"]
        assert result["isError"] is False
        payload = result["structuredContent"]
        assert any(item.get("id") == session_id for item in payload.get("sessions", []))
@pytest.mark.ST
@pytest.mark.cli
@pytest.mark.req("FR-009")


@pytest.mark.asyncio
async def test_st1_18_a2a_websocket_broadcasts_session_and_config_events(_runtime):
    cfg = _runtime
    timeout = float(cfg.get("client_api.request_timeout_seconds") or 60)
    headers = api_headers(cfg)
    runtime = ChatDatabaseRuntime(cfg)
    try:
        after_session_id = max(
            (int(item.get("id") or 0) for item in runtime.store.list_events(limit=10000)),
            default=0,
        )
        after_config_id = max(
            (
                int(item.get("id") or 0)
                for item in runtime.config_store.list_events(limit=10000)
            ),
            default=0,
        )
    finally:
        runtime.dispose()

    ws_url = (
        a2a_base_url(cfg).replace("http://", "ws://", 1)
        + (
            f"/a2a/ws?topics=sessions,config"
            f"&after_session_id={after_session_id}"
            f"&after_config_id={after_config_id}"
        )
    )

    async with websockets.connect(ws_url, additional_headers=headers) as ws:
        async with httpx.AsyncClient(timeout=timeout) as client:
            create = await client.post(
                f"{api_base_url(cfg)}/sessions",
                json={"metadata": {"suite": "st1.18-a2a"}},
                headers=headers,
            )
            assert create.status_code == 200
            profile = await client.post(
                f"{api_base_url(cfg)}/v1/profiles",
                json={"name": "ST1.18 Profile", "description": "four server test"},
                headers=headers,
            )
            assert profile.status_code == 200

        topics = set()
        for _ in range(6):
            event = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
            topics.add(event.get("topic"))
            if {"sessions", "config"} <= topics:
                break
        assert "sessions" in topics
        assert "config" in topics
