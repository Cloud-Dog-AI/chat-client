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

from typing import Any

from cloud_dog_db import MigrationRunner, SyncSessionManager, build_sync_engine
from cloud_dog_db.health.probes import probe_database
from cloud_dog_db.migrations.runner import MigrationConfig

from ..config import ConfigManager
from ..storage_fs import parent_dir, resolve_path, storage_for_root
from .config_store import ConfigStore
from .db_config import get_database_settings
from .store import ChatSessionStore


class ChatDatabaseRuntime:
    """Database runtime wrapper for chat-client persistence and health."""

    def __init__(self, config: ConfigManager):
        """Initialise ChatDatabaseRuntime state and dependencies."""
        self.config = config
        self.settings = get_database_settings(config)
        self._prepare_sqlite_path()
        self.engine = build_sync_engine(self.settings)
        self.sessions = SyncSessionManager(self.engine)

        tenant_id = str(config.get("db.tenant_id") or "default")
        actor = str(config.get("db.actor") or "chat-client")
        self.store = ChatSessionStore(
            self.sessions,
            tenant_id=tenant_id,
            actor=actor,
        )
        self.config_store = ConfigStore(
            self.sessions,
            tenant_id=tenant_id,
            actor=actor,
        )

        preferred_migrations_root = (
            config.project_root / "database" / "migrations" / "cloud_dog_db"
        ).resolve()
        migrations_root = (
            preferred_migrations_root
            if preferred_migrations_root.exists()
            else (config.project_root / "migrations").resolve()
        )
        versions_root = (migrations_root / "versions").resolve()
        self.migration_runner = MigrationRunner(
            MigrationConfig(
                script_location=str(migrations_root),
                sqlalchemy_url=self.settings.to_sync_url(),
                version_table="chat_alembic_version",
                version_table_schema=self.settings.schema_name,
                version_locations=str(versions_root),
            )
        )

        self._startup_order: list[str] = []
        self._initialise()

    def _prepare_sqlite_path(self) -> None:
        """Internal helper to prepare sqlite path for this module."""
        dialect = str(getattr(self.settings, "dialect", "") or "").strip().lower()
        if "sqlite" not in dialect:
            return

        database_value = str(getattr(self.settings, "database", "") or "").strip()
        if not database_value or database_value == ":memory:":
            return

        db_path = resolve_path(database_value, base_dir=str(self.config.project_root))
        storage_for_root(parent_dir(db_path))
        self.settings.database = db_path

    def _initialise(self) -> None:
        # Startup order: config -> settings -> engine -> session -> migrate -> health -> ready
        """Internal helper to initialise for this module."""
        self._startup_order.append("config")
        self._startup_order.append("settings")
        self._startup_order.append("engine")
        self._startup_order.append("session")
        self.migration_runner.upgrade("head")
        self._startup_order.append("migrate_head")
        self._ensure_idam_role_tables()
        self._startup_order.append("idam_role_tables")
        self.config_store.ensure_webui_conformance_seed(
            admin_api_key=str(self.config.get("client_api.admin_api_key") or "").strip()
        )
        self._startup_order.append("webui_conformance_seed")
        self.config_store.ensure_roles_seed()
        self._startup_order.append("roles_seed")
        probe_database(self.engine)
        self._startup_order.append("health")
        self._startup_order.append("ready")

    def _ensure_idam_role_tables(self) -> None:
        """Create the canonical cloud_dog_idam role tables (W28A-876 Gate 4b).

        Ensures the shared roles / permissions / role_permissions tables exist so
        the PS-71 §IW3A Roles page (/api/v1/admin/roles) is backed by the
        SqlAlchemyRoleStore. Only the role-related tables are created here; the
        other idam tables are not part of this service's schema. Idempotent.
        """
        from cloud_dog_idam.storage.sqlalchemy.models import (  # type: ignore[import-not-found,import-untyped]
            PermissionORM,
            RoleORM,
            RolePermissionORM,
        )

        RoleORM.metadata.create_all(
            bind=self.engine,
            checkfirst=True,
            tables=[
                RoleORM.__table__,
                PermissionORM.__table__,
                RolePermissionORM.__table__,
            ],
        )

    def probe(self) -> dict[str, Any]:
        """Handle probe for the current runtime context."""
        try:
            probe = probe_database(self.engine)
            return {
                "status": "ok" if bool(probe.get("ok")) else "error",
                "result": probe.get("result"),
            }
        except Exception as exc:
            return {
                "status": "error",
                "error": str(exc),
            }

    def startup_order(self) -> list[str]:
        """Handle startup order for the current runtime context."""
        return list(self._startup_order)

    def dispose(self) -> None:
        """Handle dispose for the current runtime context."""
        self.engine.dispose()
