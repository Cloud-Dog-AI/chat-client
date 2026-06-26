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

import time
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from cloud_dog_chat_client.config import ConfigManager
from cloud_dog_chat_client.database.runtime import ChatDatabaseRuntime


def _version_table_ref(runtime: ChatDatabaseRuntime) -> str:
    cfg = runtime.migration_runner.config
    if cfg.version_table_schema:
        return f"{cfg.version_table_schema}.{cfg.version_table}"
    return cfg.version_table


def _current_revision(runtime: ChatDatabaseRuntime) -> str | None:
    table_ref = _version_table_ref(runtime)
    with runtime.engine.connect() as conn:
        rows = conn.execute(text(f"SELECT version_num FROM {table_ref}")).fetchall()
    if not rows:
        return None
    return str(rows[0][0])


def _write_temp_migration(script_location: Path, down_revision: str) -> tuple[str, Path]:
    revision = f"w23a_test_{time.time_ns()}"
    path = script_location / "versions" / f"{revision}_version_check.py"
    path.write_text(
        f'''"""temporary W23A version simulation migration"""
from __future__ import annotations
import pytest
from alembic import op
import sqlalchemy as sa

revision = "{revision}"
down_revision = "{down_revision}"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "_test_version_check",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("marker", sa.String(length=64), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("_test_version_check")
''',
        encoding="utf-8",
    )
    return revision, path
@pytest.mark.ST
@pytest.mark.cli
@pytest.mark.req("FR-009")


def test_st_db_03_migration_lifecycle_upgrade_downgrade_upgrade(env_file):
    cfg = ConfigManager(env_file=env_file)
    runtime = ChatDatabaseRuntime(cfg)
    try:
        baseline_revision = _current_revision(runtime)
        assert baseline_revision

        runtime.migration_runner.downgrade("base")
        assert _current_revision(runtime) is None

        runtime.migration_runner.upgrade("head")
        assert _current_revision(runtime) == baseline_revision
    finally:
        runtime.dispose()
@pytest.mark.ST
@pytest.mark.cli
@pytest.mark.req("FR-009")


def test_st_db_04_schema_versioning_simulation(env_file):
    cfg = ConfigManager(env_file=env_file)
    runtime = ChatDatabaseRuntime(cfg)
    script_location = Path(runtime.migration_runner.config.script_location)
    baseline_revision = _current_revision(runtime)
    assert baseline_revision

    _, temp_migration = _write_temp_migration(script_location, baseline_revision)
    try:
        runtime.migration_runner.upgrade("head")
        inspector = inspect(runtime.engine)
        assert "_test_version_check" in inspector.get_table_names()

        with runtime.engine.begin() as conn:
            conn.execute(text("INSERT INTO _test_version_check (marker) VALUES ('ok')"))
            count = conn.execute(text("SELECT COUNT(*) FROM _test_version_check")).scalar_one()
        assert count == 1

        runtime.migration_runner.downgrade(baseline_revision)
        inspector = inspect(runtime.engine)
        assert "_test_version_check" not in inspector.get_table_names()
    finally:
        if temp_migration.exists():
            temp_migration.unlink()
        runtime.migration_runner.upgrade("head")
        runtime.dispose()

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.system, pytest.mark.db, pytest.mark.slow]
