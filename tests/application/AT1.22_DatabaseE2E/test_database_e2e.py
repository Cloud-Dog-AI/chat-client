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

from pathlib import Path

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_server import api_base_url, api_headers, start_api, stop_api, wait_for_api
from tests.helpers.ollama_preflight import curl_ollama_tags


def _require_cfg(cfg: ConfigManager, key: str):
    value = cfg.get(key)
    if value is None:
        raise RuntimeError(f"CRITICAL ERROR: missing required configuration key: {key}")
    return value
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
async def test_at_db_01_database_backed_session_and_preferences_flow(
    env_file, monkeypatch, tmp_path: Path
):
    cfg = ConfigManager(env_file=env_file)
    if not (
        cfg.get("db.dialect")
        or cfg.get("db.url")
        or cfg.get("cloud_dog.db.dialect")
        or cfg.get("cloud_dog.db.url")
    ):
        db_path = tmp_path / "at_db.sqlite3"
        monkeypatch.setenv("CLOUD_DOG_DB__DIALECT", "sqlite")
        monkeypatch.setenv("CLOUD_DOG_DB__DATABASE", str(db_path))
        cfg = ConfigManager(env_file=env_file)

    curl_ollama_tags(cfg)
    start_api(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        base_url = api_base_url(cfg)
        headers = api_headers(cfg)
        timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
        async with httpx.AsyncClient(
            base_url=base_url, headers=headers, timeout=timeout_seconds
        ) as client:
            health = await client.get("/health")
            assert health.status_code == 200
            assert str(
                ((health.json().get("checks") or {}).get("db") or {}).get("status") or ""
            ) == "ok"

            created = await client.post(
                "/sessions", json={"metadata": {"suite": "at-db-01"}}
            )
            assert created.status_code == 200
            session_id = str(created.json().get("session_id") or "")
            assert session_id

            updated = await client.put(
                f"/sessions/{session_id}/preferences",
                json={"selected_mcp_server_indices": [0, 1]},
            )
            assert updated.status_code == 200
            assert updated.json().get("selected_mcp_server_indices") == [0, 1]

            loaded = await client.get(f"/sessions/{session_id}/preferences")
            assert loaded.status_code == 200
            assert loaded.json().get("selected_mcp_server_indices") == [0, 1]

            listed = await client.get("/sessions")
            assert listed.status_code == 200
            session_ids = {
                str(item.get("id") or "")
                for item in (listed.json().get("sessions") or [])
                if isinstance(item, dict)
            }
            assert session_id in session_ids

            transcript = await client.get(f"/sessions/{session_id}/transcript")
            assert transcript.status_code == 200
            assert str(transcript.json().get("session_id") or "") == session_id

            deleted = await client.delete(f"/sessions/{session_id}")
            assert deleted.status_code == 200
            assert bool(deleted.json().get("deleted")) is True
    finally:
        stop_api(cfg, env_file=env_file)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.application, pytest.mark.llm, pytest.mark.db, pytest.mark.mcp, pytest.mark.heavy]

