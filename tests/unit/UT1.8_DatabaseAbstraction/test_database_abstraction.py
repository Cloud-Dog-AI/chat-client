import pytest
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

from cloud_dog_db import SyncSessionManager, build_sync_engine
from cloud_dog_db.health.probes import probe_database
from sqlalchemy import text

from cloud_dog_chat_client.config import ConfigManager
from cloud_dog_chat_client.database.db_config import get_database_settings
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_ut_db_01_settings_bridge_and_engine_factory(env_file, monkeypatch, tmp_path: Path):
    db_path = tmp_path / "ut_db_01.sqlite3"
    monkeypatch.setenv("CLOUD_DOG_DB__DIALECT", "sqlite")
    monkeypatch.setenv("CLOUD_DOG_DB__DATABASE", str(db_path))

    cfg = ConfigManager(env_file=env_file)
    settings = get_database_settings(cfg)
    assert Path(str(settings.database)).resolve() == db_path.resolve()
    engine = build_sync_engine(settings)
    try:
        probe = probe_database(engine)
        assert bool(probe.get("ok")) is True
    finally:
        engine.dispose()
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_ut_db_02_sync_session_manager_provides_working_session(
    env_file, monkeypatch, tmp_path: Path
):
    db_path = tmp_path / "ut_db_02.sqlite3"
    monkeypatch.setenv("CLOUD_DOG_DB__DIALECT", "sqlite")
    monkeypatch.setenv("CLOUD_DOG_DB__DATABASE", str(db_path))

    cfg = ConfigManager(env_file=env_file)
    settings = get_database_settings(cfg)
    assert Path(str(settings.database)).resolve() == db_path.resolve()
    engine = build_sync_engine(settings)
    sessions = SyncSessionManager(engine)

    try:
        with sessions.session() as db:
            value = db.execute(text("SELECT 1")).scalar_one()
        assert int(value) == 1
    finally:
        engine.dispose()

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.unit, pytest.mark.db, pytest.mark.fast]
