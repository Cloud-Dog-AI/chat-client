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

import pytest
from sqlalchemy import inspect

from cloud_dog_chat_client.config import ConfigManager
from cloud_dog_chat_client.database.runtime import ChatDatabaseRuntime
from cloud_dog_chat_client.session.transcript import TranscriptEvent


@pytest.fixture()
def _db_runtime(env_file, monkeypatch, tmp_path: Path):
    db_path = tmp_path / "st_db.sqlite3"
    monkeypatch.setenv("CLOUD_DOG_DB__DIALECT", "sqlite")
    monkeypatch.setenv("CLOUD_DOG_DB__DATABASE", str(db_path))
    cfg = ConfigManager(env_file=env_file)
    runtime = ChatDatabaseRuntime(cfg)
    try:
        yield runtime
    finally:
        runtime.dispose()
@pytest.mark.ST
@pytest.mark.cli
@pytest.mark.req("FR-009")


def test_st_db_01_migration_runner_upgrades_fresh_sqlite_db(_db_runtime):
    engine = _db_runtime.engine
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    assert "chat_alembic_version" in table_names
    assert "chat_sessions" in table_names
    assert "chat_session_events" in table_names
    assert "chat_session_preferences" in table_names
    assert "chat_audit_logs" in table_names
@pytest.mark.ST
@pytest.mark.cli
@pytest.mark.req("FR-009")


def test_st_db_02_chat_session_store_crud(_db_runtime):
    store = _db_runtime.store
    session_id = "st-db-session-001"

    store.create_session(
        session_id=session_id,
        created_at="2026-03-05T00:00:00+00:00",
        metadata={"suite": "st-db", "selected_mcp_server_indices": [0]},
        log_path="/tmp/st-db-session-001.jsonl",
    )
    store.append_event(
        session_id,
        TranscriptEvent(
            event_type="user_message",
            timestamp="2026-03-05T00:00:01+00:00",
            data={"content": "hello"},
            sequence=0,
        ),
    )

    loaded = store.get_session(session_id)
    assert loaded is not None
    assert loaded["id"] == session_id
    assert len(loaded["events"]) == 1

    metadata = store.update_session_metadata(
        session_id, {"selected_mcp_server_indices": [0, 1]}
    )
    assert metadata["selected_mcp_server_indices"] == [0, 1]

    assert store.delete_session(session_id) is True
    assert store.get_session(session_id) is None

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.system, pytest.mark.db, pytest.mark.mcp, pytest.mark.slow]

