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
UT coverage for F-RF-07-L2 — ConfigStore in-transaction membership consistency.

Contract:
    ``ConfigStore.create_user`` and ``ConfigStore.create_group`` MUST include
    ``group_ids`` / ``member_user_ids`` in the dict they return when those were
    supplied in the payload. The SyncSessionManager runs with
    ``autoflush=False`` so the helpers that insert the membership rows MUST
    flush before the serialisation query executes.

Related:
    - Source: ``cloud_dog_chat_client/database/config_store.py``
      (``_replace_user_memberships`` / ``_replace_group_memberships``)
    - Finding: F-RF-07-L2
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from cloud_dog_db import DatabaseSettings, PlatformBase, SyncSessionManager, build_sync_engine

# Import the ORM models so they register against PlatformBase.metadata before
# create_all runs below.
import cloud_dog_chat_client.database.models  # noqa: F401
from cloud_dog_chat_client.database.config_store import ConfigStore, ConfigStoreError
from cloud_dog_idam.storage.sqlalchemy.models import (  # type: ignore[import-untyped]
    PermissionORM,
    RoleORM,
    RolePermissionORM,
)


def _fresh_store(tmp_path: Path, name: str) -> tuple[ConfigStore, SyncSessionManager]:
    """Build a ConfigStore backed by a throw-away sqlite file."""
    db_path = tmp_path / f"{name}.sqlite3"
    settings = DatabaseSettings(dialect="sqlite", database=str(db_path))
    engine = build_sync_engine(settings)
    PlatformBase.metadata.create_all(engine)
    RoleORM.metadata.create_all(
        bind=engine,
        checkfirst=True,
        tables=[RoleORM.__table__, PermissionORM.__table__, RolePermissionORM.__table__],
    )
    sessions = SyncSessionManager(engine)
    store = ConfigStore(sessions, tenant_id="default", actor="ut-rf-07-l2")
    return store, sessions
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("CS-007")


def test_ut_config_store_01_create_user_response_reflects_group_ids(tmp_path: Path) -> None:
    """F-RF-07-L2: POST /users response MUST include the persisted group_ids."""
    store, sessions = _fresh_store(tmp_path, "cfg_store_user")
    try:
        # Pre-create the group so the membership insert has a FK target.
        store.create_group({"group_id": "grp-alpha", "name": "Alpha"})

        user = store.create_user(
            {
                "user_id": "u-alpha",
                "display_name": "User Alpha",
                "email": "alpha@example.com",
                "role": "admin",
                "status": "active",
                "group_ids": ["grp-alpha"],
            }
        )

        # The immediate POST response payload MUST reflect the group assignment.
        assert user.get("user_id") == "u-alpha"
        assert user.get("group_ids") == [
            "grp-alpha"
        ], "POST response MUST reflect group_ids supplied in payload (F-RF-07-L2)"

        # And a fresh GET MUST see the same persisted state.
        fetched = store.get_user("u-alpha")
        assert fetched is not None
        assert fetched.get("group_ids") == ["grp-alpha"]
    finally:
        sessions.engine.dispose()
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_ut_config_store_02_create_group_response_reflects_member_user_ids(tmp_path: Path) -> None:
    """F-RF-07-L2: POST /groups response MUST include the persisted member_user_ids."""
    store, sessions = _fresh_store(tmp_path, "cfg_store_group")
    try:
        store.create_user(
            {
                "user_id": "u-beta",
                "display_name": "User Beta",
                "email": "beta@example.com",
                "role": "viewer",
                "status": "active",
            }
        )

        group = store.create_group(
            {
                "group_id": "grp-beta",
                "name": "Beta",
                "member_user_ids": ["u-beta"],
            }
        )

        assert group.get("group_id") == "grp-beta"
        assert group.get("member_user_ids") == [
            "u-beta"
        ], "POST response MUST reflect member_user_ids supplied in payload (F-RF-07-L2)"

        fetched = store.get_group("grp-beta")
        assert fetched is not None
        assert fetched.get("member_user_ids") == ["u-beta"]
    finally:
        sessions.engine.dispose()
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_ut_config_store_03_update_user_response_reflects_group_ids(tmp_path: Path) -> None:
    """F-RF-07-L2: PUT /users response MUST include the persisted group_ids."""
    store, sessions = _fresh_store(tmp_path, "cfg_store_user_update")
    try:
        store.create_group({"group_id": "grp-gamma", "name": "Gamma"})
        store.create_user(
            {
                "user_id": "u-gamma",
                "display_name": "User Gamma",
                "email": "gamma@example.com",
                "role": "viewer",
                "status": "active",
            }
        )

        user = store.update_user(
            "u-gamma",
            {"group_ids": ["grp-gamma"]},
        )
        assert user.get("group_ids") == ["grp-gamma"]

        # And removing memberships is also reflected immediately.
        user_cleared = store.update_user("u-gamma", {"group_ids": []})
        assert user_cleared.get("group_ids") == []
    finally:
        sessions.engine.dispose()
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_ut_config_store_04_webui_conformance_seed_is_idempotent_and_redacted(tmp_path: Path) -> None:
    """W28A-727: startup seed must persist admin/group/API-key metadata safely."""
    store, sessions = _fresh_store(tmp_path, "cfg_store_webui_seed")
    try:
        first = store.ensure_webui_conformance_seed(admin_api_key="<api-key>")
        second = store.ensure_webui_conformance_seed(admin_api_key="<api-key>")

        assert first == second
        assert first["user"]["user_id"] == "admin"
        assert first["user"]["role"] == "admin"
        assert first["group"]["group_id"] == "administrators"
        assert first["group"]["member_user_ids"] == ["admin"]
        assert first["api_key_record"] == "present"

        users = store.list_users()
        groups = store.list_groups()
        api_keys = store.list_api_keys()

        assert [item["user_id"] for item in users] == ["admin"]
        assert [item["group_id"] for item in groups] == ["administrators"]
        assert [item["key_id"] for item in api_keys] == ["client_api.admin_api_key"]
        assert "api_key" not in api_keys[0], "seeded API-key metadata must not expose the raw secret"
        assert api_keys[0]["user_id"] == "admin"
        assert api_keys[0]["scopes"] == ["*", "admin", "config:write"]

        principal = store.resolve_api_key("ut-local-admin-key")
        assert principal is not None
        assert principal["user_id"] == "admin"
        assert principal["role"] == "admin"
    finally:
        sessions.engine.dispose()
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_ut_config_store_05_webui_conformance_seed_tolerates_parallel_startup(tmp_path: Path) -> None:
    """Docker all-mode startup may seed from multiple server processes concurrently."""
    store, sessions = _fresh_store(tmp_path, "cfg_store_webui_seed_parallel")
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(
                pool.map(
                    lambda _: store.ensure_webui_conformance_seed(admin_api_key="<api-key>"),
                    range(4),
                )
            )

        assert all(item["user"]["user_id"] == "admin" for item in results)
        assert all(item["group"]["group_id"] == "administrators" for item in results)
        assert [item["user_id"] for item in store.list_users()] == ["admin"]
        assert [item["group_id"] for item in store.list_groups()] == ["administrators"]
        assert [item["key_id"] for item in store.list_api_keys()] == ["client_api.admin_api_key"]
    finally:
        sessions.engine.dispose()
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-007")


def test_ut_config_store_06_ps_idam_baseline_roles_seeded_and_protected(tmp_path: Path) -> None:
    """PS-IDAM role cascade requires six undeletable baseline roles."""
    store, sessions = _fresh_store(tmp_path, "cfg_store_ps_idam_roles")
    try:
        store.ensure_roles_seed()

        roles = {item["name"]: item for item in store.list_roles()}
        expected = {"admin", "group-admin", "user", "restricted", "job-control", "audit-log"}
        assert expected <= set(roles)
        assert all(roles[name]["baseline"] is True for name in expected)
        assert roles["admin"]["permissions"] == ["*"]
        assert "jobs.control" in roles["job-control"]["permissions"]
        assert "logs.read.all" in roles["audit-log"]["permissions"]
        assert roles["restricted"]["permissions"] == []

        for name in sorted(expected):
            with pytest.raises(ConfigStoreError) as exc:
                store.delete_role(str(roles[name]["role_id"]))
            assert exc.value.status == 403
            assert "baseline role cannot be deleted" in str(exc.value)
    finally:
        sessions.engine.dispose()


pytestmark = [pytest.mark.unit, pytest.mark.db, pytest.mark.fast]
