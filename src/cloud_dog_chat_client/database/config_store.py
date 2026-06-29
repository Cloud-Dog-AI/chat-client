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

import copy
import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any

from cloud_dog_db.session.session_manager import SyncSessionManager
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from cloud_dog_idam.domain.models import Role  # type: ignore[import-not-found,import-untyped]
from cloud_dog_idam.rbac import role_catalog as _role_catalog  # type: ignore[import-not-found,import-untyped]
from cloud_dog_idam.storage.sqlalchemy.role_store import (  # type: ignore[import-not-found,import-untyped]
    BaselineRoleProtected,
    SqlAlchemyRoleStore,
)

from .models import (
    ChatAPIKey,
    ChatAuditLog,
    ChatConfigEvent,
    ChatGroup,
    ChatGroupMembership,
    ChatProfile,
    ChatUser,
)


def _utc_now() -> datetime:
    """Return the current UTC timestamp for this module."""
    return datetime.now(timezone.utc)


def _safe_dict(value: Any) -> dict[str, Any]:
    """Normalise arbitrary input to a dictionary payload."""
    if isinstance(value, dict):
        return copy.deepcopy(value)
    return {}


def _safe_list(value: Any) -> list[Any]:
    """Normalise arbitrary input to a list payload."""
    if isinstance(value, list):
        return copy.deepcopy(value)
    return []


def _safe_str(value: Any, default: str = "") -> str:
    """Normalise arbitrary input to a stripped string value."""
    return str(value or default).strip()


# req: FR-007
_PS_IDAM_BASELINE_ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {"*"},
    "group-admin": {
        "a2a.access",
        "apidocs.access",
        "apikeys.manage_own",
        "apikeys.read_own",
        "config.read",
        "idam.groups.read",
        "idam.groups.write",
        "idam.rbac.read",
        "idam.rbac.write",
        "idam.users.read",
        "logs.read",
        "mcp.access",
        "profiles.read",
        "resources:read",
        "webui.access",
    },
    "user": {
        "a2a.access",
        "apidocs.access",
        "apikeys.manage_own",
        "apikeys.read_own",
        "config.read",
        "idam.users.read",
        "logs.read",
        "mcp.access",
        "profiles.read",
        "resources:read",
        "webui.access",
    },
    "restricted": set(),
    "job-control": {"jobs.control", "jobs.read"},
    "audit-log": {"idam.audit.read", "logs.read.all"},
}
_PS_IDAM_BASELINE_ROLE_NAMES = frozenset(_PS_IDAM_BASELINE_ROLE_PERMISSIONS)


def _baseline_role_permissions() -> dict[str, set[str]]:
    """Return the PS-IDAM baseline role catalog required by this service."""
    package_roles = getattr(_role_catalog, "BASELINE_ROLE_PERMISSIONS", {}) or {}
    if isinstance(package_roles, dict) and _PS_IDAM_BASELINE_ROLE_NAMES.issubset(
        set(str(name) for name in package_roles)
    ):
        return {
            str(name): {str(permission) for permission in permissions}
            for name, permissions in package_roles.items()
        }
    merged = {
        name: set(permissions)
        for name, permissions in _PS_IDAM_BASELINE_ROLE_PERMISSIONS.items()
    }
    if isinstance(package_roles, dict):
        for name, permissions in package_roles.items():
            role_name = str(name)
            if role_name not in merged:
                merged[role_name] = {str(permission) for permission in permissions}
    return merged


class ConfigStoreError(RuntimeError):
    """Structured config-store error carrying an HTTP status and code."""

    def __init__(self, code: str, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class ConfigStore:
    """Persist and retrieve config CRUD entities for chat-client."""
    # Covers: CFG-01, CFG-02, CFG-03, CFG-04, CFG-06, CFG-08, CFG-09, CFG-10, CFG-12

    def __init__(
        self,
        session_manager: SyncSessionManager,
        *,
        tenant_id: str = "default",
        actor: str = "chat-client",
    ):
        """Initialise ConfigStore state and dependencies."""
        self._sessions = session_manager
        self._tenant_id = _safe_str(tenant_id, "default") or "default"
        self._actor = _safe_str(actor, "chat-client") or "chat-client"

    def ensure_webui_conformance_seed(self, *, admin_api_key: str | None = None) -> dict[str, Any]:
        """Ensure durable PS-71 WebUI seed records without exposing raw secrets."""
        last_error: IntegrityError | None = None
        for _attempt in range(3):
            try:
                return self._ensure_webui_conformance_seed_once(admin_api_key=admin_api_key)
            except IntegrityError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError("webui conformance seed failed without an exception")

    def _ensure_webui_conformance_seed_once(self, *, admin_api_key: str | None = None) -> dict[str, Any]:
        """Run one seed attempt; caller retries if another process won the race."""
        with self._sessions.session() as db:
            user = db.execute(select(ChatUser).where(ChatUser.user_id == "admin")).scalar_one_or_none()
            if user is None:
                user = ChatUser(
                    user_id="admin",
                    display_name="platform-admin",
                    email="admin@example.invalid",
                    role="admin",
                    status="active",
                    metadata_json={"seed": "webui_conformance"},
                    tenant_id=self._tenant_id,
                    created_by=self._actor,
                    updated_by=self._actor,
                )
                db.add(user)
                db.flush()
            else:
                user.display_name = user.display_name or "platform-admin"
                user.email = user.email or "admin@example.invalid"
                user.role = "admin"
                user.status = "active"
                user.metadata_json = {**_safe_dict(user.metadata_json), "seed": "webui_conformance"}
                user.is_deleted = False
                user.deleted_at = None
                user.tenant_id = self._tenant_id
                user.updated_by = self._actor

            group = db.execute(select(ChatGroup).where(ChatGroup.group_id == "administrators")).scalar_one_or_none()
            if group is None:
                group = ChatGroup(
                    group_id="administrators",
                    name="WebUI Administrators",
                    description="Durable administrators group for WebUI conformance.",
                    roles_json=["admin"],
                    metadata_json={"seed": "webui_conformance"},
                    tenant_id=self._tenant_id,
                    created_by=self._actor,
                    updated_by=self._actor,
                )
                db.add(group)
                db.flush()
            else:
                group.name = group.name or "WebUI Administrators"
                group.description = group.description or "Durable administrators group for WebUI conformance."
                roles = set(_safe_str(item) for item in _safe_list(group.roles_json) if _safe_str(item))
                roles.add("admin")
                group.roles_json = sorted(roles)
                group.metadata_json = {**_safe_dict(group.metadata_json), "seed": "webui_conformance"}
                group.is_deleted = False
                group.deleted_at = None
                group.tenant_id = self._tenant_id
                group.updated_by = self._actor

            membership = db.execute(
                select(ChatGroupMembership)
                .where(ChatGroupMembership.chat_user_id == int(user.id))
                .where(ChatGroupMembership.chat_group_id == int(group.id))
            ).scalar_one_or_none()
            if membership is None:
                membership = ChatGroupMembership(
                    chat_user_id=int(user.id),
                    chat_group_id=int(group.id),
                    tenant_id=self._tenant_id,
                    created_by=self._actor,
                    updated_by=self._actor,
                )
                db.add(membership)
            else:
                membership.is_deleted = False
                membership.deleted_at = None
                membership.tenant_id = self._tenant_id
                membership.updated_by = self._actor

            api_key_status = "not_configured"
            raw_admin_key = _safe_str(admin_api_key)
            if raw_admin_key:
                key_hash = hashlib.sha256(raw_admin_key.encode("utf-8")).hexdigest()
                api_key = db.execute(
                    select(ChatAPIKey).where(ChatAPIKey.key_id == "client_api.admin_api_key")
                ).scalar_one_or_none()
                if api_key is None:
                    api_key = db.execute(select(ChatAPIKey).where(ChatAPIKey.key_hash == key_hash)).scalar_one_or_none()
                if api_key is None:
                    api_key = ChatAPIKey(
                        key_id="client_api.admin_api_key",
                        chat_user_id=int(user.id),
                        name="client_api.admin_api_key",
                        key_prefix="client_api.admin",
                        key_hash=key_hash,
                        scopes_json=["*", "admin", "config:write"],
                        metadata_json={"seed": "webui_conformance", "source": "client_api.admin_api_key"},
                        tenant_id=self._tenant_id,
                        created_by=self._actor,
                        updated_by=self._actor,
                    )
                    db.add(api_key)
                else:
                    api_key.key_id = "client_api.admin_api_key"
                    api_key.chat_user_id = int(user.id)
                    api_key.name = "client_api.admin_api_key"
                    api_key.key_prefix = "client_api.admin"
                    api_key.key_hash = key_hash
                    api_key.scopes_json = ["*", "admin", "config:write"]
                    api_key.metadata_json = {
                        **_safe_dict(api_key.metadata_json),
                        "seed": "webui_conformance",
                        "source": "client_api.admin_api_key",
                    }
                    api_key.is_revoked = False
                    api_key.revoked_at = None
                    api_key.is_deleted = False
                    api_key.deleted_at = None
                    api_key.tenant_id = self._tenant_id
                    api_key.updated_by = self._actor
                api_key_status = "present"

            db.flush()
            user_result = self._user_to_dict(user, group_ids=["administrators"])
            group_result = self._group_to_dict(group, member_user_ids=["admin"])
            self._write_audit(
                db=db,
                action="config_webui_conformance_seed_ensured",
                detail={
                    "user": user_result,
                    "group": group_result,
                    "api_key_record": api_key_status,
                },
            )
            return {
                "user": user_result,
                "group": group_result,
                "api_key_record": api_key_status,
            }

    def _entity_query(self, model):
        """Build a tenant-filtered active-row query for the given model."""
        return (
            select(model)
            .where(model.tenant_id == self._tenant_id)
            .where(model.is_deleted.is_(False))
        )

    def _write_audit(
        self,
        *,
        db,
        action: str,
        detail: dict[str, Any],
        status: str = "ok",
    ) -> None:
        """Persist an audit event for config CRUD operations."""
        db.add(
            ChatAuditLog(
                session_id=None,
                action=_safe_str(action, "unknown"),
                status=_safe_str(status, "ok") or "ok",
                detail_json=_safe_dict(detail),
                tenant_id=self._tenant_id,
                created_by=self._actor,
                updated_by=self._actor,
            )
        )

    def _write_event(
        self,
        *,
        db,
        event_type: str,
        entity_type: str,
        entity_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Persist an A2A config event for downstream consumers."""
        db.add(
            ChatConfigEvent(
                event_type=_safe_str(event_type, "unknown"),
                entity_type=_safe_str(entity_type, "unknown"),
                entity_id=_safe_str(entity_id, "unknown"),
                payload_json=_safe_dict(payload),
                tenant_id=self._tenant_id,
                created_by=self._actor,
                updated_by=self._actor,
            )
        )

    def record_audit_log(
        self,
        *,
        action: str,
        status: str,
        detail: dict[str, Any],
        actor: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist a redacted WebUI/audit event outside CRUD mutations."""
        with self._sessions.session() as db:
            row = ChatAuditLog(
                session_id=None,
                action=_safe_str(action, "unknown"),
                status=_safe_str(status, "ok") or "ok",
                request_id=_safe_str(request_id),
                detail_json=_safe_dict(detail),
                tenant_id=self._tenant_id,
                created_by=_safe_str(actor, self._actor) or self._actor,
                updated_by=_safe_str(actor, self._actor) or self._actor,
            )
            db.add(row)
            db.flush()
            return {
                "id": int(row.id),
                "action": row.action,
                "status": row.status,
                "request_id": row.request_id,
                "created_by": row.created_by,
            }

    def _profile_to_dict(self, row: ChatProfile) -> dict[str, Any]:
        """Serialise a ChatProfile row for API responses."""
        return {
            "id": int(row.id),
            "profile_id": row.profile_id,
            "name": row.name,
            "description": _safe_str(row.description),
            "mcp_bindings": _safe_list(row.mcp_bindings_json),
            "session_defaults": _safe_dict(row.session_defaults_json),
            "access_control": _safe_dict(row.access_control_json),
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }

    def _user_to_dict(self, row: ChatUser, *, group_ids: list[str]) -> dict[str, Any]:
        """Serialise a ChatUser row for API responses."""
        return {
            "id": int(row.id),
            "user_id": row.user_id,
            "display_name": _safe_str(row.display_name),
            "email": _safe_str(row.email),
            "role": _safe_str(row.role, "viewer") or "viewer",
            "status": _safe_str(row.status, "active") or "active",
            "group_ids": list(group_ids),
            "metadata": _safe_dict(row.metadata_json),
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }

    def _group_to_dict(self, row: ChatGroup, *, member_user_ids: list[str]) -> dict[str, Any]:
        """Serialise a ChatGroup row for API responses."""
        return {
            "id": int(row.id),
            "group_id": row.group_id,
            "name": row.name,
            "description": _safe_str(row.description),
            "roles": [str(item) for item in _safe_list(row.roles_json) if str(item).strip()],
            "member_user_ids": list(member_user_ids),
            "metadata": _safe_dict(row.metadata_json),
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }

    def _api_key_to_dict(self, row: ChatAPIKey, *, user_id: str | None) -> dict[str, Any]:
        """Serialise a ChatAPIKey row for API responses without exposing the secret."""
        return {
            "id": int(row.id),
            "key_id": row.key_id,
            "user_id": _safe_str(user_id) or None,
            "name": _safe_str(row.name),
            "key_prefix": _safe_str(row.key_prefix),
            "scopes": [str(item) for item in _safe_list(row.scopes_json) if str(item).strip()],
            "is_revoked": bool(row.is_revoked),
            "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
            "metadata": _safe_dict(row.metadata_json),
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }

    def _membership_group_ids(self, db, *, user_row_ids: list[int]) -> dict[int, list[str]]:
        """Resolve group identifiers for the provided user row ids."""
        if not user_row_ids:
            return {}
        memberships = (
            db.execute(
                self._entity_query(ChatGroupMembership).where(
                    ChatGroupMembership.chat_user_id.in_(user_row_ids)
                )
            )
            .scalars()
            .all()
        )
        if not memberships:
            return {row_id: [] for row_id in user_row_ids}
        group_row_ids = sorted({int(item.chat_group_id) for item in memberships})
        groups = (
            db.execute(self._entity_query(ChatGroup).where(ChatGroup.id.in_(group_row_ids)))
            .scalars()
            .all()
        )
        group_lookup = {int(item.id): item.group_id for item in groups}
        out = {row_id: [] for row_id in user_row_ids}
        for membership in memberships:
            gid = group_lookup.get(int(membership.chat_group_id))
            if gid:
                out.setdefault(int(membership.chat_user_id), []).append(gid)
        return out

    def _membership_user_ids(self, db, *, group_row_ids: list[int]) -> dict[int, list[str]]:
        """Resolve user identifiers for the provided group row ids."""
        if not group_row_ids:
            return {}
        memberships = (
            db.execute(
                self._entity_query(ChatGroupMembership).where(
                    ChatGroupMembership.chat_group_id.in_(group_row_ids)
                )
            )
            .scalars()
            .all()
        )
        if not memberships:
            return {row_id: [] for row_id in group_row_ids}
        user_row_ids = sorted({int(item.chat_user_id) for item in memberships})
        users = (
            db.execute(self._entity_query(ChatUser).where(ChatUser.id.in_(user_row_ids)))
            .scalars()
            .all()
        )
        user_lookup = {int(item.id): item.user_id for item in users}
        out = {row_id: [] for row_id in group_row_ids}
        for membership in memberships:
            uid = user_lookup.get(int(membership.chat_user_id))
            if uid:
                out.setdefault(int(membership.chat_group_id), []).append(uid)
        return out

    def list_profiles(self) -> list[dict[str, Any]]:
        """List persisted chat profiles."""
        with self._sessions.session() as db:
            rows = db.execute(self._entity_query(ChatProfile).order_by(ChatProfile.name.asc())).scalars().all()
            return [self._profile_to_dict(row) for row in rows]

    def get_profile(self, profile_id: str) -> dict[str, Any] | None:
        """Return one profile by its stable identifier."""
        with self._sessions.session() as db:
            row = db.execute(
                self._entity_query(ChatProfile).where(ChatProfile.profile_id == _safe_str(profile_id))
            ).scalar_one_or_none()
            return None if row is None else self._profile_to_dict(row)

    def create_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create a new profile and emit audit/A2A events."""
        with self._sessions.session() as db:
            profile_id = _safe_str(payload.get("profile_id")) or f"profile-{secrets.token_hex(4)}"
            profile_name = _safe_str(payload.get("name")) or "Unnamed Profile"
            profile = db.execute(
                select(ChatProfile).where(ChatProfile.tenant_id == self._tenant_id).where(
                    (ChatProfile.profile_id == profile_id) | (ChatProfile.name == profile_name)
                )
            ).scalar_one_or_none()
            created = profile is None
            if profile is None:
                profile = ChatProfile(
                    profile_id=profile_id,
                    name=profile_name,
                    description=_safe_str(payload.get("description")),
                    mcp_bindings_json=[item for item in _safe_list(payload.get("mcp_bindings")) if isinstance(item, dict)],
                    session_defaults_json=_safe_dict(payload.get("session_defaults")),
                    access_control_json=_safe_dict(payload.get("access_control")),
                    tenant_id=self._tenant_id,
                    created_by=self._actor,
                    updated_by=self._actor,
                )
                db.add(profile)
                db.flush()
            else:
                profile.is_deleted = False
                profile.deleted_at = None
                profile.profile_id = profile_id
                profile.name = profile_name
                profile.description = _safe_str(payload.get("description"))
                profile.mcp_bindings_json = [
                    item for item in _safe_list(payload.get("mcp_bindings")) if isinstance(item, dict)
                ]
                profile.session_defaults_json = _safe_dict(payload.get("session_defaults"))
                profile.access_control_json = _safe_dict(payload.get("access_control"))
                profile.updated_by = self._actor
            result = self._profile_to_dict(profile)
            action = "config_profile_created" if created else "config_profile_updated"
            event_type = "profile.created" if created else "profile.updated"
            self._write_audit(db=db, action=action, detail={"profile": result})
            self._write_event(
                db=db,
                event_type=event_type,
                entity_type="profile",
                entity_id=profile.profile_id,
                payload=result,
            )
            return result

    def update_profile(self, profile_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Update an existing profile and emit audit/A2A events."""
        with self._sessions.session() as db:
            row = db.execute(
                self._entity_query(ChatProfile).where(ChatProfile.profile_id == _safe_str(profile_id))
            ).scalar_one_or_none()
            if row is None:
                raise KeyError(f"Unknown profile: {profile_id}")
            if payload.get("name") is not None:
                row.name = _safe_str(payload.get("name")) or row.name
            if payload.get("description") is not None:
                row.description = _safe_str(payload.get("description"))
            if payload.get("mcp_bindings") is not None:
                row.mcp_bindings_json = [
                    item for item in _safe_list(payload.get("mcp_bindings")) if isinstance(item, dict)
                ]
            if payload.get("session_defaults") is not None:
                row.session_defaults_json = _safe_dict(payload.get("session_defaults"))
            if payload.get("access_control") is not None:
                row.access_control_json = _safe_dict(payload.get("access_control"))
            row.updated_by = self._actor
            result = self._profile_to_dict(row)
            self._write_audit(db=db, action="config_profile_updated", detail={"profile": result})
            self._write_event(
                db=db,
                event_type="profile.updated",
                entity_type="profile",
                entity_id=row.profile_id,
                payload=result,
            )
            return result

    def delete_profile(self, profile_id: str) -> dict[str, Any]:
        """Soft-delete a profile and emit audit/A2A events."""
        with self._sessions.session() as db:
            row = db.execute(
                self._entity_query(ChatProfile).where(ChatProfile.profile_id == _safe_str(profile_id))
            ).scalar_one_or_none()
            if row is None:
                raise KeyError(f"Unknown profile: {profile_id}")
            result = self._profile_to_dict(row)
            row.is_deleted = True
            row.deleted_at = _utc_now()
            row.updated_by = self._actor
            self._write_audit(db=db, action="config_profile_deleted", detail={"profile": result})
            self._write_event(
                db=db,
                event_type="profile.deleted",
                entity_type="profile",
                entity_id=row.profile_id,
                payload=result,
            )
            return result

    def list_users(self) -> list[dict[str, Any]]:
        """List persisted chat users with their group assignments."""
        with self._sessions.session() as db:
            rows = db.execute(self._entity_query(ChatUser).order_by(ChatUser.user_id.asc())).scalars().all()
            memberships = self._membership_group_ids(db, user_row_ids=[int(item.id) for item in rows])
            return [self._user_to_dict(row, group_ids=memberships.get(int(row.id), [])) for row in rows]

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        """Return a user by stable identifier."""
        with self._sessions.session() as db:
            row = db.execute(
                self._entity_query(ChatUser).where(ChatUser.user_id == _safe_str(user_id))
            ).scalar_one_or_none()
            if row is None:
                return None
            memberships = self._membership_group_ids(db, user_row_ids=[int(row.id)])
            return self._user_to_dict(row, group_ids=memberships.get(int(row.id), []))

    def create_user(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create a new user and optional memberships."""
        with self._sessions.session() as db:
            user_id = _safe_str(payload.get("user_id")) or f"user-{secrets.token_hex(4)}"
            email = _safe_str(payload.get("email"))
            stmt = self._entity_query(ChatUser).where(ChatUser.user_id == user_id)
            if email:
                stmt = self._entity_query(ChatUser).where(
                    (ChatUser.user_id == user_id) | (ChatUser.email == email)
                )
            row = db.execute(stmt).scalar_one_or_none()
            created = row is None
            if row is None:
                row = ChatUser(
                    user_id=user_id,
                    display_name=_safe_str(payload.get("display_name")),
                    email=email,
                    role=_safe_str(payload.get("role"), "viewer") or "viewer",
                    status=_safe_str(payload.get("status"), "active") or "active",
                    metadata_json=_safe_dict(payload.get("metadata")),
                    tenant_id=self._tenant_id,
                    created_by=self._actor,
                    updated_by=self._actor,
                )
                db.add(row)
                db.flush()
            else:
                row.user_id = user_id
                row.display_name = _safe_str(payload.get("display_name"))
                row.email = email
                row.role = _safe_str(payload.get("role"), row.role) or row.role
                row.status = _safe_str(payload.get("status"), row.status) or row.status
                row.metadata_json = _safe_dict(payload.get("metadata"))
                row.updated_by = self._actor
            self._replace_user_memberships(db, row, _safe_list(payload.get("group_ids")))
            memberships = self._membership_group_ids(db, user_row_ids=[int(row.id)])
            result = self._user_to_dict(row, group_ids=memberships.get(int(row.id), []))
            action = "config_user_created" if created else "config_user_updated"
            event_type = "user.created" if created else "user.updated"
            self._write_audit(db=db, action=action, detail={"user": result})
            self._write_event(
                db=db,
                event_type=event_type,
                entity_type="user",
                entity_id=row.user_id,
                payload=result,
            )
            return result

    def update_user(self, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Update an existing user and optional memberships."""
        with self._sessions.session() as db:
            row = db.execute(
                self._entity_query(ChatUser).where(ChatUser.user_id == _safe_str(user_id))
            ).scalar_one_or_none()
            if row is None:
                raise KeyError(f"Unknown user: {user_id}")
            if payload.get("display_name") is not None:
                row.display_name = _safe_str(payload.get("display_name"))
            if payload.get("email") is not None:
                row.email = _safe_str(payload.get("email"))
            if payload.get("role") is not None:
                row.role = _safe_str(payload.get("role"), row.role) or row.role
            if payload.get("status") is not None:
                row.status = _safe_str(payload.get("status"), row.status) or row.status
            if payload.get("metadata") is not None:
                row.metadata_json = _safe_dict(payload.get("metadata"))
            if payload.get("group_ids") is not None:
                self._replace_user_memberships(db, row, _safe_list(payload.get("group_ids")))
            row.updated_by = self._actor
            memberships = self._membership_group_ids(db, user_row_ids=[int(row.id)])
            result = self._user_to_dict(row, group_ids=memberships.get(int(row.id), []))
            self._write_audit(db=db, action="config_user_updated", detail={"user": result})
            self._write_event(
                db=db,
                event_type="user.updated",
                entity_type="user",
                entity_id=row.user_id,
                payload=result,
            )
            return result

    def delete_user(self, user_id: str) -> dict[str, Any]:
        """Soft-delete a user and cascade membership deletion."""
        with self._sessions.session() as db:
            row = db.execute(
                self._entity_query(ChatUser).where(ChatUser.user_id == _safe_str(user_id))
            ).scalar_one_or_none()
            if row is None:
                raise KeyError(f"Unknown user: {user_id}")
            result = self.get_user(row.user_id)
            assert result is not None
            db.execute(delete(ChatGroupMembership).where(ChatGroupMembership.chat_user_id == int(row.id)))
            row.is_deleted = True
            row.deleted_at = _utc_now()
            row.updated_by = self._actor
            self._write_audit(db=db, action="config_user_deleted", detail={"user": result})
            self._write_event(
                db=db,
                event_type="user.deleted",
                entity_type="user",
                entity_id=row.user_id,
                payload=result,
            )
            return result

    def _replace_user_memberships(self, db, row: ChatUser, group_ids: list[Any]) -> None:
        """Replace group memberships for a user from stable group identifiers.

        F-RF-07-L2: the SyncSessionManager runs with ``autoflush=False``, so the
        newly added ChatGroupMembership rows are not visible to subsequent SELECTs
        in the same transaction (notably ``_membership_group_ids``). Without an
        explicit flush the POST /users response reports ``group_ids: []`` even
        though the rows do commit at context exit. Flush at the end so the
        serialised response reflects the real state atomically.
        """
        db.execute(delete(ChatGroupMembership).where(ChatGroupMembership.chat_user_id == int(row.id)))
        cleaned = [_safe_str(item) for item in group_ids if _safe_str(item)]
        if not cleaned:
            db.flush()
            return
        groups = (
            db.execute(self._entity_query(ChatGroup).where(ChatGroup.group_id.in_(cleaned)))
            .scalars()
            .all()
        )
        for group in groups:
            db.add(
                ChatGroupMembership(
                    chat_user_id=int(row.id),
                    chat_group_id=int(group.id),
                    tenant_id=self._tenant_id,
                    created_by=self._actor,
                    updated_by=self._actor,
                )
            )
        db.flush()

    def list_groups(self) -> list[dict[str, Any]]:
        """List groups and their member user identifiers."""
        with self._sessions.session() as db:
            rows = db.execute(self._entity_query(ChatGroup).order_by(ChatGroup.group_id.asc())).scalars().all()
            memberships = self._membership_user_ids(db, group_row_ids=[int(item.id) for item in rows])
            return [self._group_to_dict(row, member_user_ids=memberships.get(int(row.id), [])) for row in rows]

    def get_group(self, group_id: str) -> dict[str, Any] | None:
        """Return a group by stable identifier."""
        with self._sessions.session() as db:
            row = db.execute(
                self._entity_query(ChatGroup).where(ChatGroup.group_id == _safe_str(group_id))
            ).scalar_one_or_none()
            if row is None:
                return None
            memberships = self._membership_user_ids(db, group_row_ids=[int(row.id)])
            return self._group_to_dict(row, member_user_ids=memberships.get(int(row.id), []))

    def create_group(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create a group and optional memberships."""
        with self._sessions.session() as db:
            group_id = _safe_str(payload.get("group_id")) or f"group-{secrets.token_hex(4)}"
            group_name = _safe_str(payload.get("name")) or "Unnamed Group"
            row = db.execute(
                self._entity_query(ChatGroup).where(
                    (ChatGroup.group_id == group_id) | (ChatGroup.name == group_name)
                )
            ).scalar_one_or_none()
            created = row is None
            if row is None:
                row = ChatGroup(
                    group_id=group_id,
                    name=group_name,
                    description=_safe_str(payload.get("description")),
                    roles_json=[_safe_str(item) for item in _safe_list(payload.get("roles")) if _safe_str(item)],
                    metadata_json=_safe_dict(payload.get("metadata")),
                    tenant_id=self._tenant_id,
                    created_by=self._actor,
                    updated_by=self._actor,
                )
                db.add(row)
                db.flush()
            else:
                row.group_id = group_id
                row.name = group_name
                row.description = _safe_str(payload.get("description"))
                row.roles_json = [_safe_str(item) for item in _safe_list(payload.get("roles")) if _safe_str(item)]
                row.metadata_json = _safe_dict(payload.get("metadata"))
                row.updated_by = self._actor
            self._replace_group_memberships(db, row, _safe_list(payload.get("member_user_ids")))
            memberships = self._membership_user_ids(db, group_row_ids=[int(row.id)])
            result = self._group_to_dict(row, member_user_ids=memberships.get(int(row.id), []))
            action = "config_group_created" if created else "config_group_updated"
            event_type = "group.created" if created else "group.updated"
            self._write_audit(db=db, action=action, detail={"group": result})
            self._write_event(
                db=db,
                event_type=event_type,
                entity_type="group",
                entity_id=row.group_id,
                payload=result,
            )
            return result

    def update_group(self, group_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Update a group and optional memberships."""
        with self._sessions.session() as db:
            row = db.execute(
                self._entity_query(ChatGroup).where(ChatGroup.group_id == _safe_str(group_id))
            ).scalar_one_or_none()
            if row is None:
                raise KeyError(f"Unknown group: {group_id}")
            if payload.get("name") is not None:
                row.name = _safe_str(payload.get("name")) or row.name
            if payload.get("description") is not None:
                row.description = _safe_str(payload.get("description"))
            if payload.get("roles") is not None:
                row.roles_json = [_safe_str(item) for item in _safe_list(payload.get("roles")) if _safe_str(item)]
            if payload.get("metadata") is not None:
                row.metadata_json = _safe_dict(payload.get("metadata"))
            if payload.get("member_user_ids") is not None:
                self._replace_group_memberships(db, row, _safe_list(payload.get("member_user_ids")))
            row.updated_by = self._actor
            memberships = self._membership_user_ids(db, group_row_ids=[int(row.id)])
            result = self._group_to_dict(row, member_user_ids=memberships.get(int(row.id), []))
            self._write_audit(db=db, action="config_group_updated", detail={"group": result})
            self._write_event(
                db=db,
                event_type="group.updated",
                entity_type="group",
                entity_id=row.group_id,
                payload=result,
            )
            return result

    def delete_group(self, group_id: str) -> dict[str, Any]:
        """Soft-delete a group and clear memberships."""
        with self._sessions.session() as db:
            row = db.execute(
                self._entity_query(ChatGroup).where(ChatGroup.group_id == _safe_str(group_id))
            ).scalar_one_or_none()
            if row is None:
                raise KeyError(f"Unknown group: {group_id}")
            result = self.get_group(row.group_id)
            assert result is not None
            db.execute(delete(ChatGroupMembership).where(ChatGroupMembership.chat_group_id == int(row.id)))
            row.is_deleted = True
            row.deleted_at = _utc_now()
            row.updated_by = self._actor
            self._write_audit(db=db, action="config_group_deleted", detail={"group": result})
            self._write_event(
                db=db,
                event_type="group.deleted",
                entity_type="group",
                entity_id=row.group_id,
                payload=result,
            )
            return result

    def _replace_group_memberships(self, db, row: ChatGroup, member_user_ids: list[Any]) -> None:
        """Replace memberships for a group from stable user identifiers.

        F-RF-07-L2: identical rationale to ``_replace_user_memberships``. With
        ``autoflush=False`` the freshly ``db.add()``-ed membership rows are
        invisible to the follow-up ``_membership_user_ids`` query, so the
        POST /groups response payload omits ``member_user_ids`` even though the
        rows are actually committed. Flush here to make the write visible
        in-transaction.
        """
        db.execute(delete(ChatGroupMembership).where(ChatGroupMembership.chat_group_id == int(row.id)))
        cleaned = [_safe_str(item) for item in member_user_ids if _safe_str(item)]
        if not cleaned:
            db.flush()
            return
        users = (
            db.execute(self._entity_query(ChatUser).where(ChatUser.user_id.in_(cleaned)))
            .scalars()
            .all()
        )
        for user in users:
            db.add(
                ChatGroupMembership(
                    chat_user_id=int(user.id),
                    chat_group_id=int(row.id),
                    tenant_id=self._tenant_id,
                    created_by=self._actor,
                    updated_by=self._actor,
                )
            )
        db.flush()

    def create_api_key(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create a new API key and return the clear-text secret once."""
        with self._sessions.session() as db:
            target_user_id = _safe_str(payload.get("user_id"))
            user_row = None
            if target_user_id:
                user_row = db.execute(
                    self._entity_query(ChatUser).where(ChatUser.user_id == target_user_id)
                ).scalar_one_or_none()
                if user_row is None:
                    raise KeyError(f"Unknown user: {target_user_id}")
            raw_key = f"chatcfg_{secrets.token_urlsafe(24)}"
            digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
            key_id = _safe_str(payload.get("key_id")) or f"key-{secrets.token_hex(4)}"
            row = ChatAPIKey(
                key_id=key_id,
                chat_user_id=int(user_row.id) if user_row is not None else None,
                name=_safe_str(payload.get("name")) or key_id,
                key_prefix=raw_key[:16],
                key_hash=digest,
                scopes_json=[_safe_str(item) for item in _safe_list(payload.get("scopes")) if _safe_str(item)],
                metadata_json=_safe_dict(payload.get("metadata")),
                tenant_id=self._tenant_id,
                created_by=self._actor,
                updated_by=self._actor,
            )
            db.add(row)
            db.flush()
            result = self._api_key_to_dict(row, user_id=target_user_id or None)
            result["api_key"] = raw_key
            self._write_audit(db=db, action="config_api_key_created", detail={"api_key": {k: v for k, v in result.items() if k != "api_key"}})
            self._write_event(
                db=db,
                event_type="api_key.created",
                entity_type="api_key",
                entity_id=row.key_id,
                payload={k: v for k, v in result.items() if k != "api_key"},
            )
            return result

    def list_api_keys(self, *, user_id: str | None = None) -> list[dict[str, Any]]:
        """List API keys, optionally filtered to one user."""
        with self._sessions.session() as db:
            stmt = self._entity_query(ChatAPIKey).order_by(ChatAPIKey.created_at.desc())
            rows = db.execute(stmt).scalars().all()
            user_lookup: dict[int, str] = {}
            user_ids = sorted({int(item.chat_user_id) for item in rows if item.chat_user_id is not None})
            if user_ids:
                users = db.execute(self._entity_query(ChatUser).where(ChatUser.id.in_(user_ids))).scalars().all()
                user_lookup = {int(item.id): item.user_id for item in users}
            out = []
            for row in rows:
                row_user_id = user_lookup.get(int(row.chat_user_id)) if row.chat_user_id is not None else None
                if user_id and row_user_id != user_id:
                    continue
                out.append(self._api_key_to_dict(row, user_id=row_user_id))
            return out

    def revoke_api_key(self, key_id: str) -> dict[str, Any]:
        """Revoke an API key and emit audit/A2A events."""
        with self._sessions.session() as db:
            row = db.execute(
                self._entity_query(ChatAPIKey).where(ChatAPIKey.key_id == _safe_str(key_id))
            ).scalar_one_or_none()
            if row is None:
                raise KeyError(f"Unknown API key: {key_id}")
            user_id = None
            if row.chat_user_id is not None:
                user = db.execute(self._entity_query(ChatUser).where(ChatUser.id == int(row.chat_user_id))).scalar_one_or_none()
                user_id = user.user_id if user is not None else None
            row.is_revoked = True
            row.revoked_at = _utc_now()
            row.updated_by = self._actor
            result = self._api_key_to_dict(row, user_id=user_id)
            self._write_audit(db=db, action="config_api_key_revoked", detail={"api_key": result})
            self._write_event(
                db=db,
                event_type="api_key.revoked",
                entity_type="api_key",
                entity_id=row.key_id,
                payload=result,
            )
            return result

    def resolve_api_key(self, raw_key: str) -> dict[str, Any] | None:
        """Resolve a presented API key into an auth principal payload."""
        digest = hashlib.sha256(_safe_str(raw_key).encode("utf-8")).hexdigest()
        with self._sessions.session() as db:
            row = db.execute(
                self._entity_query(ChatAPIKey)
                .where(ChatAPIKey.key_hash == digest)
                .where(ChatAPIKey.is_revoked.is_(False))
            ).scalar_one_or_none()
            if row is None:
                return None
            user_row = None
            if row.chat_user_id is not None:
                user_row = db.execute(
                    self._entity_query(ChatUser).where(ChatUser.id == int(row.chat_user_id))
                ).scalar_one_or_none()
            # W28A-582: Reject keys whose owner user is disabled
            if user_row is not None and _safe_str(getattr(user_row, "status", "active")) in ("disabled", "locked"):
                return None
            role = "viewer"
            user_id = user_row.user_id if user_row is not None else row.key_id
            if user_row is not None and _safe_str(user_row.role):
                role = _safe_str(user_row.role, "viewer") or "viewer"
            if user_row is not None:
                membership_roles = self._group_roles_for_user(db, int(user_row.id))
                if "admin" in membership_roles:
                    role = "admin"
            scopes = [str(item) for item in _safe_list(row.scopes_json) if str(item).strip()]
            if "*" in scopes or "config:write" in scopes or "admin" in scopes:
                role = "admin"
            return {
                "user_id": user_id,
                "role": role,
                "actor": user_row.display_name if user_row and _safe_str(user_row.display_name) else user_id,
                "key_fingerprint": f"sha256:{digest[:12]}",
                "key_id": row.key_id,
                "scopes": scopes,
            }

    def _group_roles_for_user(self, db, user_row_id: int) -> set[str]:
        """Resolve effective group roles for a user row."""
        memberships = (
            db.execute(
                self._entity_query(ChatGroupMembership).where(ChatGroupMembership.chat_user_id == int(user_row_id))
            )
            .scalars()
            .all()
        )
        if not memberships:
            return set()
        group_row_ids = sorted({int(item.chat_group_id) for item in memberships})
        groups = db.execute(self._entity_query(ChatGroup).where(ChatGroup.id.in_(group_row_ids))).scalars().all()
        out: set[str] = set()
        for group in groups:
            for role in _safe_list(group.roles_json):
                if _safe_str(role):
                    out.add(_safe_str(role))
        return out

    def list_events(self, *, after_id: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        """Return config change events for the A2A feed."""
        bounded_limit = max(1, min(int(limit or 100), 500))
        with self._sessions.session() as db:
            query = self._entity_query(ChatConfigEvent).where(
                ChatConfigEvent.id > int(after_id or 0)
            )
            if int(after_id or 0) <= 0:
                rows = (
                    db.execute(
                        query
                        .order_by(ChatConfigEvent.id.desc())
                        .limit(bounded_limit)
                    )
                    .scalars()
                    .all()
                )
                rows.reverse()
            else:
                rows = (
                    db.execute(
                        query
                        .order_by(ChatConfigEvent.id.asc())
                        .limit(bounded_limit)
                    )
                    .scalars()
                    .all()
                )
            return [
                {
                    "id": int(row.id),
                    "event_type": row.event_type,
                    "entity_type": row.entity_type,
                    "entity_id": row.entity_id,
                    "payload": _safe_dict(row.payload_json),
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]

    # ----- Roles (PS-71 §IW3A; canonical cloud_dog_idam role store) -----------
    # W28A-876 Gate 4b: roles are persisted through the shared
    # cloud_dog_idam SqlAlchemyRoleStore (roles / permissions / role_permissions
    # tables) rather than chat-client's bespoke ChatUser/ChatGroup tables, so the
    # PS-71 §IW3A.1 Roles page is backed by the canonical platform role catalogue.
    def _role_store(self, db) -> SqlAlchemyRoleStore:
        """Build a SqlAlchemyRoleStore bound to the given session."""
        return SqlAlchemyRoleStore(db)

    # req: FR-007
    def ensure_roles_seed(self) -> None:
        """Seed all PS-IDAM baseline roles (IW3A.4). Idempotent."""
        with self._sessions.session() as db:
            store = self._role_store(db)
            store.seed_baseline()
            for name, permissions in _baseline_role_permissions().items():
                existing = store.get_by_name(name)
                if existing is None:
                    store.save(
                        Role(
                            name=name,
                            description=f"Baseline {name} role",
                            permissions=set(permissions),
                        )
                    )

    # req: FR-007
    def list_roles(self) -> list[dict[str, Any]]:
        """Return all roles in the PS-71 §IW3A.1 shape (seeds baseline first)."""
        with self._sessions.session() as db:
            store = self._role_store(db)
            store.seed_baseline()
            for name, permissions in _baseline_role_permissions().items():
                if store.get_by_name(name) is None:
                    store.save(
                        Role(
                            name=name,
                            description=f"Baseline {name} role",
                            permissions=set(permissions),
                        )
                    )
            rows = store.list_response()
            for row in rows:
                row["baseline"] = str(row.get("name") or "") in _PS_IDAM_BASELINE_ROLE_NAMES
            return rows

    def get_role(self, role_id: str) -> dict[str, Any] | None:
        """Return one role in the IW3A.1 shape, or None when unknown."""
        with self._sessions.session() as db:
            for row in self._role_store(db).list_response():
                if row["role_id"] == role_id:
                    return row
            return None

    def create_role(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create one role with its permission set."""
        name = _safe_str(payload.get("name"))
        if not name:
            raise ConfigStoreError("VALIDATION_ERROR", "name is required", status=400)
        permissions = {
            _safe_str(item) for item in _safe_list(payload.get("permissions")) if _safe_str(item)
        }
        with self._sessions.session() as db:
            store = self._role_store(db)
            if store.get_by_name(name) is not None:
                raise ConfigStoreError(
                    "CONFLICT", f"role already exists: {name}", status=409
                )
            role = store.save(
                Role(
                    name=name,
                    description=_safe_str(payload.get("description")),
                    permissions=permissions,
                )
            )
            result = {
                "role_id": role.role_id,
                "name": role.name,
                "description": role.description,
                "permissions": sorted(role.permissions),
            }
            self._write_audit(db=db, action="config_role_created", detail={"role": result})
            self._write_event(
                db=db,
                event_type="config.role.create",
                entity_type="role",
                entity_id=role.role_id,
                payload=result,
            )
            return result

    def update_role(self, role_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Update one role's description and/or permission set."""
        raw_permissions = payload.get("permissions")
        permissions = (
            {_safe_str(item) for item in _safe_list(raw_permissions) if _safe_str(item)}
            if raw_permissions is not None
            else None
        )
        with self._sessions.session() as db:
            store = self._role_store(db)
            if store.get(role_id) is None:
                raise ConfigStoreError(
                    "NOT_FOUND", f"unknown role: {role_id}", status=404
                )
            role = store.update(
                role_id,
                description=payload.get("description"),
                permissions=permissions,
            )
            result = {
                "role_id": role.role_id,
                "name": role.name,
                "description": role.description,
                "permissions": sorted(role.permissions),
            }
            self._write_audit(db=db, action="config_role_updated", detail={"role": result})
            self._write_event(
                db=db,
                event_type="config.role.update",
                entity_type="role",
                entity_id=role.role_id,
                payload=result,
            )
            return result

    # req: FR-007
    def delete_role(self, role_id: str) -> dict[str, Any]:
        """Delete one role. PS-IDAM baseline roles are protected (403)."""
        with self._sessions.session() as db:
            store = self._role_store(db)
            role = store.get(role_id)
            if role is not None and role.name in _PS_IDAM_BASELINE_ROLE_NAMES:
                raise ConfigStoreError(
                    "FORBIDDEN",
                    f"baseline role cannot be deleted: {role.name}",
                    status=403,
                )
            try:
                removed = store.delete(role_id)
            except BaselineRoleProtected as exc:
                raise ConfigStoreError(
                    "FORBIDDEN",
                    f"baseline role cannot be deleted: {exc}",
                    status=403,
                ) from exc
            if not removed:
                raise ConfigStoreError(
                    "NOT_FOUND", f"unknown role: {role_id}", status=404
                )
            result = {"role_id": role_id, "deleted": True}
            self._write_audit(db=db, action="config_role_deleted", detail={"role": result})
            self._write_event(
                db=db,
                event_type="config.role.delete",
                entity_type="role",
                entity_id=role_id,
                payload=result,
            )
            return result
