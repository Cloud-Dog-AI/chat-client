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

from cloud_dog_chat_client.api.server import create_app
from cloud_dog_chat_client.config import ConfigManager


# Covers: CFG-01, CFG-02, CFG-03, CFG-04, CFG-05, CFG-06, CFG-08, CFG-09, CFG-10, CFG-11, CFG-13
@pytest.mark.UT
@pytest.mark.api
@pytest.mark.req("FR-006")
@pytest.mark.asyncio
async def test_ut1_6_config_crud_routes_and_login(env_file, monkeypatch, tmp_path: Path):
    db_path = tmp_path / "ut_config_crud.sqlite3"
    monkeypatch.setenv("CLOUD_DOG_DB__DIALECT", "sqlite")
    monkeypatch.setenv("CLOUD_DOG_DB__DATABASE", str(db_path))
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY_HEADER", "X-API-Key")
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY", "viewer-key")
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__ADMIN_API_KEY", "admin-key")

    cfg = ConfigManager(env_file=env_file)
    app = create_app(cfg)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        login = await client.get("/login")
        assert login.status_code == 200
        assert "<div id=\"root\"></div>" in login.text
        assert "/runtime-config.js" in login.text
        assert "/assets/index-" in login.text

        created_profile = await client.post(
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
                "session_defaults": {"ui_theme": "operations"},
                "access_control": {"roles": ["admin", "viewer"]},
            },
        )
        assert created_profile.status_code == 200
        assert created_profile.json()["profile"]["profile_id"] == "ops-profile"

        list_profiles = await client.get("/v1/profiles", headers={"X-API-Key": "viewer-key"})
        assert list_profiles.status_code == 200
        assert len(list_profiles.json()["profiles"]) == 1

        deleted_profile = await client.delete(
            "/v1/profiles/ops-profile",
            headers={"X-API-Key": "admin-key"},
        )
        assert deleted_profile.status_code == 200

        recreated_profile = await client.post(
            "/v1/profiles",
            headers={"X-API-Key": "admin-key"},
            json={
                "profile_id": "ops-profile-recreated",
                "name": "Operations",
                "description": "Operations profile recreated after soft delete",
                "mcp_bindings": [
                    {"name": "search-mcp", "transport": "streamable_http", "base_url": "https://searchmcp.example.com"},
                ],
                "session_defaults": {"ui_theme": "operations"},
                "access_control": {"roles": ["admin", "viewer"]},
            },
        )
        assert recreated_profile.status_code == 200
        assert recreated_profile.json()["profile"]["profile_id"] == "ops-profile-recreated"

        created_user = await client.post(
            "/v1/users",
            headers={"X-API-Key": "admin-key"},
            json={
                "user_id": "ops-admin",
                "display_name": "Operations Admin",
                "email": "ops-admin@example.com",
                "role": "admin",
                "status": "active",
                "group_ids": [],
                "metadata": {"team": "ops"},
            },
        )
        assert created_user.status_code == 200
        assert created_user.json()["user"]["user_id"] == "ops-admin"

        audit_logs = await client.get(
            "/ui/logs?source=audit&limit=200",
            headers={"X-API-Key": "admin-key"},
        )
        assert audit_logs.status_code == 200
        assert any(
            entry.get("target", {}).get("id") == "ops-admin"
            and entry.get("action") == "create"
            for entry in audit_logs.json()["entries"]
        )

        api_logs = await client.get(
            "/ui/logs?source=api&limit=200",
            headers={"X-API-Key": "admin-key"},
        )
        assert api_logs.status_code == 200
        assert any(
            entry.get("target", {}).get("id") == "ops-admin"
            and entry.get("action") == "create"
            for entry in api_logs.json()["entries"]
        )

        created_group = await client.post(
            "/v1/groups",
            headers={"X-API-Key": "admin-key"},
            json={
                "group_id": "ops",
                "name": "Operations",
                "description": "Operations RBAC group",
                "roles": ["admin"],
                "member_user_ids": ["ops-admin"],
                "metadata": {},
            },
        )
        assert created_group.status_code == 200
        assert created_group.json()["group"]["group_id"] == "ops"

        created_key = await client.post(
            "/v1/api-keys",
            headers={"X-API-Key": "admin-key"},
            json={
                "name": "Ops Admin Key",
                "user_id": "ops-admin",
                "scopes": ["config:write"],
                "metadata": {"purpose": "ut"},
            },
        )
        assert created_key.status_code == 200
        created_key_body = created_key.json()["api_key"]
        assert created_key_body["key_id"]
        assert created_key_body["api_key"].startswith("chatcfg_")

        db_key_list = await client.get(
            "/v1/profiles",
            headers={"X-API-Key": created_key_body["api_key"]},
        )
        assert db_key_list.status_code == 200
        assert db_key_list.json()["profiles"][0]["profile_id"] == "ops-profile-recreated"

        tool_list = await client.get("/mcp/admin/tools", headers={"X-API-Key": "viewer-key"})
        assert tool_list.status_code == 200
        assert any(item.get("name") == "profile_create" for item in tool_list.json()["tools"])

        tool_call = await client.post(
            "/mcp/admin/tools/call",
            headers={"X-API-Key": created_key_body["api_key"]},
            json={"name": "profile_list", "arguments": {}},
        )
        assert tool_call.status_code == 200
        assert tool_call.json()["result"][0]["profile_id"] == "ops-profile-recreated"

        events = await client.get("/a2a/events", headers={"X-API-Key": "viewer-key"})
        assert events.status_code == 200
        event_types = [item.get("event_type") for item in events.json()["events"]]
        assert "profile.created" in event_types
        assert "user.created" in event_types
        assert "group.created" in event_types
        assert "api_key.created" in event_types

        revoke_key = await client.delete(
            f"/v1/api-keys/{created_key_body['key_id']}",
            headers={"X-API-Key": "admin-key"},
        )
        assert revoke_key.status_code == 200
        assert revoke_key.json()["api_key"]["is_revoked"] is True
