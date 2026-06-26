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

"""UT_AGENT_PROFILE_API — profile API persistence and session inheritance."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from cloud_dog_chat_client.api.server import create_app
from cloud_dog_chat_client.config import ConfigManager
@pytest.mark.UT
@pytest.mark.api
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_profile_strategy_round_trips_and_merges_to_session(env_file, monkeypatch, tmp_path: Path):
    """Profile session_defaults.agent_strategy persists and is inherited by sessions."""
    db_path = tmp_path / "ut_agent_profile.sqlite3"
    monkeypatch.setenv("CLOUD_DOG_DB__DIALECT", "sqlite")
    monkeypatch.setenv("CLOUD_DOG_DB__DATABASE", str(db_path))
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY_HEADER", "X-API-Key")
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY", "viewer-key")
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__ADMIN_API_KEY", "admin-key")

    app = create_app(ConfigManager(env_file=env_file))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        created = await client.post(
            "/v1/profiles",
            headers={"X-API-Key": "admin-key"},
            json={
                "profile_id": "react-profile",
                "name": "React profile",
                "description": "Agentic profile",
                "mcp_bindings": [],
                "session_defaults": {"agent_strategy": "react", "memory_enabled": False},
                "access_control": {},
            },
        )
        assert created.status_code == 200
        assert created.json()["profile"]["session_defaults"]["agent_strategy"] == "react"

        listed = await client.get("/v1/profiles", headers={"X-API-Key": "viewer-key"})
        assert listed.status_code == 200
        assert listed.json()["profiles"][0]["session_defaults"]["agent_strategy"] == "react"

        updated = await client.put(
            "/v1/profiles/react-profile",
            headers={"X-API-Key": "admin-key"},
            json={
                "profile_id": "react-profile",
                "name": "React profile",
                "description": "Agentic profile",
                "mcp_bindings": [],
                "session_defaults": {"agent_strategy": "reflexion", "memory_enabled": False},
                "access_control": {},
            },
        )
        assert updated.status_code == 200
        assert updated.json()["profile"]["session_defaults"]["agent_strategy"] == "reflexion"

        session = await client.post(
            "/sessions",
            headers={"X-API-Key": "viewer-key"},
            json={"metadata": {"profile_id": "react-profile"}},
        )
        assert session.status_code == 200
        sessions = await client.get("/sessions", headers={"X-API-Key": "viewer-key"})
        assert sessions.status_code == 200
        row = next(item for item in sessions.json()["sessions"] if item["id"] == session.json()["session_id"])
        assert row["metadata"]["agent_strategy"] == "reflexion"
        assert row["metadata"]["profile_name"] == "React profile"
@pytest.mark.UT
@pytest.mark.api
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_invalid_profile_strategy_returns_400(env_file, monkeypatch, tmp_path: Path):
    """Invalid explicit profile strategies are rejected before persistence."""
    db_path = tmp_path / "ut_agent_profile_invalid.sqlite3"
    monkeypatch.setenv("CLOUD_DOG_DB__DIALECT", "sqlite")
    monkeypatch.setenv("CLOUD_DOG_DB__DATABASE", str(db_path))
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY_HEADER", "X-API-Key")
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY", "viewer-key")
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__ADMIN_API_KEY", "admin-key")

    app = create_app(ConfigManager(env_file=env_file))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/profiles",
            headers={"X-API-Key": "admin-key"},
            json={
                "profile_id": "bad-profile",
                "name": "Bad profile",
                "description": "Invalid strategy profile",
                "mcp_bindings": [],
                "session_defaults": {"agent_strategy": "bespoke"},
                "access_control": {},
            },
        )
    assert response.status_code == 400
    assert "Unsupported agent_strategy" in response.text

