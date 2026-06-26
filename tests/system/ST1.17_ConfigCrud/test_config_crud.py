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

from pathlib import Path

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_server import api_base_url, start_api, stop_api, wait_for_api


# Covers: CFG-01, CFG-02, CFG-06, CFG-07, CFG-08, CFG-09, CFG-10, CFG-11, CFG-13
@pytest.mark.ST
@pytest.mark.cli
@pytest.mark.req("FR-009")
@pytest.mark.asyncio
async def test_st1_17_config_crud_live_server(env_file, monkeypatch, tmp_path: Path):
    db_path = tmp_path / "st_config_crud.sqlite3"
    monkeypatch.setenv("CLOUD_DOG_DB__DIALECT", "sqlite")
    monkeypatch.setenv("CLOUD_DOG_DB__DATABASE", str(db_path))
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY_HEADER", "X-API-Key")
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY", "viewer-key")
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__ADMIN_API_KEY", "admin-key")

    cfg = ConfigManager(env_file=env_file)
    start_api(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        base_url = api_base_url(cfg)
        timeout_seconds = float(cfg.get("client_api.request_timeout_seconds") or 30)

        async with httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds) as client:
            login = await client.get("/login")
            assert login.status_code == 200
            assert "<div id=\"root\"></div>" in login.text
            assert "/runtime-config.js" in login.text

            profile_resp = await client.post(
                "/v1/profiles",
                headers={"X-API-Key": "admin-key"},
                json={
                    "profile_id": "ops-profile",
                    "name": "Operations",
                    "description": "Operations profile",
                    "mcp_bindings": [
                        {"name": "search-mcp", "transport": "streamable_http", "base_url": "https://searchmcp.example.com"},
                        {"name": "sqlagent-mcp", "transport": "http_jsonrpc", "base_url": "https://sqlagent.example.com", "messages_path": "/messages", "health_path": "/health"},
                    ],
                    "session_defaults": {"selected_mcp_server_indices": [0, 1], "ui_theme": "ops"},
                    "access_control": {"roles": ["admin", "viewer"]},
                },
            )
            assert profile_resp.status_code == 200

            user_resp = await client.post(
                "/v1/users",
                headers={"X-API-Key": "admin-key"},
                json={
                    "user_id": "ops-admin",
                    "display_name": "Operations Admin",
                    "email": "ops-admin@example.com",
                    "role": "admin",
                    "status": "active",
                    "group_ids": [],
                    "metadata": {},
                },
            )
            assert user_resp.status_code == 200

            key_resp = await client.post(
                "/v1/api-keys",
                headers={"X-API-Key": "admin-key"},
                json={
                    "name": "Ops Admin Key",
                    "user_id": "ops-admin",
                    "scopes": ["config:write"],
                    "metadata": {},
                },
            )
            assert key_resp.status_code == 200
            raw_key = key_resp.json()["api_key"]["api_key"]

            profile_list = await client.get("/v1/profiles", headers={"X-API-Key": raw_key})
            assert profile_list.status_code == 200
            assert profile_list.json()["profiles"][0]["profile_id"] == "ops-profile"

            created_session = await client.post(
                "/sessions",
                headers={"X-API-Key": raw_key},
                json={"metadata": {"profile_id": "ops-profile", "suite": "st1.17"}},
            )
            assert created_session.status_code == 200
            session_id = created_session.json()["session_id"]
            assert session_id

            sessions_list = await client.get("/sessions", headers={"X-API-Key": raw_key})
            assert sessions_list.status_code == 200
            metadata = next(item["metadata"] for item in sessions_list.json()["sessions"] if item["id"] == session_id)
            assert metadata["profile_id"] == "ops-profile"
            assert metadata["profile_name"] == "Operations"
            assert len(metadata["profile_mcp_servers"]) == 2

            prefs = await client.get(f"/sessions/{session_id}/preferences", headers={"X-API-Key": raw_key})
            assert prefs.status_code == 200
            assert prefs.json()["selected_mcp_server_indices"] == [0, 1]

            events = await client.get("/a2a/events", headers={"X-API-Key": raw_key})
            assert events.status_code == 200
            assert any(item.get("event_type") == "profile.created" for item in events.json()["events"])
    finally:
        stop_api(cfg, env_file=env_file)
