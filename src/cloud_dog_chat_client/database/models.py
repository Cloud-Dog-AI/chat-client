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

from cloud_dog_db.models import (
    AuditMixin,
    PlatformBase,
    SoftDeleteMixin,
    TenantMixin,
    TimestampMixin,
)
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON


class ChatSession(
    PlatformBase, TimestampMixin, SoftDeleteMixin, TenantMixin, AuditMixin
):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    log_path: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
        default="",
    )


class ChatSessionEvent(
    PlatformBase, TimestampMixin, SoftDeleteMixin, TenantMixin, AuditMixin
):
    __tablename__ = "chat_session_events"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_chat_session_events_sequence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    event_timestamp: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    event_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )


class ChatSessionPreference(
    PlatformBase, TimestampMixin, SoftDeleteMixin, TenantMixin, AuditMixin
):
    __tablename__ = "chat_session_preferences"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            name="uq_chat_session_preferences_session_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    selected_mcp_server_indices_json: Mapped[list[int]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )


class ChatAuditLog(
    PlatformBase, TimestampMixin, SoftDeleteMixin, TenantMixin, AuditMixin
):
    __tablename__ = "chat_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("chat_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ok")
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    detail_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )


class ChatProfile(
    PlatformBase, TimestampMixin, SoftDeleteMixin, TenantMixin, AuditMixin
):
    __tablename__ = "chat_profiles"
    __table_args__ = (
        UniqueConstraint("profile_id", name="uq_chat_profiles_profile_id"),
        UniqueConstraint("name", "tenant_id", name="uq_chat_profiles_name_tenant"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    mcp_bindings_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    session_defaults_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    access_control_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )


class ChatUser(
    PlatformBase, TimestampMixin, SoftDeleteMixin, TenantMixin, AuditMixin
):
    __tablename__ = "chat_users"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_chat_users_user_id"),
        UniqueConstraint("email", "tenant_id", name="uq_chat_users_email_tenant"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    email: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="viewer")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )


class ChatGroup(
    PlatformBase, TimestampMixin, SoftDeleteMixin, TenantMixin, AuditMixin
):
    __tablename__ = "chat_groups"
    __table_args__ = (
        UniqueConstraint("group_id", name="uq_chat_groups_group_id"),
        UniqueConstraint("name", "tenant_id", name="uq_chat_groups_name_tenant"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    roles_json: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )


class ChatGroupMembership(
    PlatformBase, TimestampMixin, SoftDeleteMixin, TenantMixin, AuditMixin
):
    __tablename__ = "chat_group_memberships"
    __table_args__ = (
        UniqueConstraint(
            "chat_user_id",
            "chat_group_id",
            name="uq_chat_group_memberships_user_group",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("chat_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chat_group_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("chat_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class ChatAPIKey(
    PlatformBase, TimestampMixin, SoftDeleteMixin, TenantMixin, AuditMixin
):
    __tablename__ = "chat_api_keys"
    __table_args__ = (
        UniqueConstraint("key_id", name="uq_chat_api_keys_key_id"),
        UniqueConstraint("key_hash", name="uq_chat_api_keys_key_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    chat_user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("chat_users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    key_prefix: Mapped[str] = mapped_column(String(24), nullable=False, default="")
    key_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    scopes_json: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    is_revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    revoked_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )


class ChatConfigEvent(
    PlatformBase, TimestampMixin, SoftDeleteMixin, TenantMixin, AuditMixin
):
    __tablename__ = "chat_config_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
