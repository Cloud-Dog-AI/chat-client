"""chat-client config CRUD tables

Revision ID: chat_0002_config_crud
Revises: chat_0001_initial
Create Date: 2026-03-20 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "chat_0002_config_crud"
down_revision = "chat_0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_profiles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("profile_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=1024), nullable=False),
        sa.Column("mcp_bindings_json", sa.JSON(), nullable=False),
        sa.Column("session_defaults_json", sa.JSON(), nullable=False),
        sa.Column("access_control_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_profiles")),
        sa.UniqueConstraint("profile_id", name="uq_chat_profiles_profile_id"),
        sa.UniqueConstraint("name", "tenant_id", name="uq_chat_profiles_name_tenant"),
    )
    op.create_index(op.f("ix_chat_profiles_created_at"), "chat_profiles", ["created_at"], unique=False)
    op.create_index(op.f("ix_chat_profiles_deleted_at"), "chat_profiles", ["deleted_at"], unique=False)
    op.create_index(op.f("ix_chat_profiles_is_deleted"), "chat_profiles", ["is_deleted"], unique=False)
    op.create_index(op.f("ix_chat_profiles_profile_id"), "chat_profiles", ["profile_id"], unique=False)
    op.create_index(op.f("ix_chat_profiles_tenant_id"), "chat_profiles", ["tenant_id"], unique=False)

    op.create_table(
        "chat_users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_users")),
        sa.UniqueConstraint("user_id", name="uq_chat_users_user_id"),
        sa.UniqueConstraint("email", "tenant_id", name="uq_chat_users_email_tenant"),
    )
    op.create_index(op.f("ix_chat_users_created_at"), "chat_users", ["created_at"], unique=False)
    op.create_index(op.f("ix_chat_users_deleted_at"), "chat_users", ["deleted_at"], unique=False)
    op.create_index(op.f("ix_chat_users_is_deleted"), "chat_users", ["is_deleted"], unique=False)
    op.create_index(op.f("ix_chat_users_tenant_id"), "chat_users", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_chat_users_user_id"), "chat_users", ["user_id"], unique=False)

    op.create_table(
        "chat_groups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("group_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=1024), nullable=False),
        sa.Column("roles_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_groups")),
        sa.UniqueConstraint("group_id", name="uq_chat_groups_group_id"),
        sa.UniqueConstraint("name", "tenant_id", name="uq_chat_groups_name_tenant"),
    )
    op.create_index(op.f("ix_chat_groups_created_at"), "chat_groups", ["created_at"], unique=False)
    op.create_index(op.f("ix_chat_groups_deleted_at"), "chat_groups", ["deleted_at"], unique=False)
    op.create_index(op.f("ix_chat_groups_group_id"), "chat_groups", ["group_id"], unique=False)
    op.create_index(op.f("ix_chat_groups_is_deleted"), "chat_groups", ["is_deleted"], unique=False)
    op.create_index(op.f("ix_chat_groups_tenant_id"), "chat_groups", ["tenant_id"], unique=False)

    op.create_table(
        "chat_group_memberships",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("chat_user_id", sa.Integer(), nullable=False),
        sa.Column("chat_group_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["chat_group_id"], ["chat_groups.id"], name=op.f("fk_chat_group_memberships_chat_group_id_chat_groups"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_user_id"], ["chat_users.id"], name=op.f("fk_chat_group_memberships_chat_user_id_chat_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_group_memberships")),
        sa.UniqueConstraint("chat_user_id", "chat_group_id", name="uq_chat_group_memberships_user_group"),
    )
    op.create_index(op.f("ix_chat_group_memberships_chat_group_id"), "chat_group_memberships", ["chat_group_id"], unique=False)
    op.create_index(op.f("ix_chat_group_memberships_chat_user_id"), "chat_group_memberships", ["chat_user_id"], unique=False)
    op.create_index(op.f("ix_chat_group_memberships_created_at"), "chat_group_memberships", ["created_at"], unique=False)
    op.create_index(op.f("ix_chat_group_memberships_deleted_at"), "chat_group_memberships", ["deleted_at"], unique=False)
    op.create_index(op.f("ix_chat_group_memberships_is_deleted"), "chat_group_memberships", ["is_deleted"], unique=False)
    op.create_index(op.f("ix_chat_group_memberships_tenant_id"), "chat_group_memberships", ["tenant_id"], unique=False)

    op.create_table(
        "chat_api_keys",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key_id", sa.String(length=64), nullable=False),
        sa.Column("chat_user_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("key_prefix", sa.String(length=24), nullable=False),
        sa.Column("key_hash", sa.String(length=128), nullable=False),
        sa.Column("scopes_json", sa.JSON(), nullable=False),
        sa.Column("is_revoked", sa.Boolean(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["chat_user_id"], ["chat_users.id"], name=op.f("fk_chat_api_keys_chat_user_id_chat_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_api_keys")),
        sa.UniqueConstraint("key_hash", name="uq_chat_api_keys_key_hash"),
        sa.UniqueConstraint("key_id", name="uq_chat_api_keys_key_id"),
    )
    op.create_index(op.f("ix_chat_api_keys_chat_user_id"), "chat_api_keys", ["chat_user_id"], unique=False)
    op.create_index(op.f("ix_chat_api_keys_created_at"), "chat_api_keys", ["created_at"], unique=False)
    op.create_index(op.f("ix_chat_api_keys_deleted_at"), "chat_api_keys", ["deleted_at"], unique=False)
    op.create_index(op.f("ix_chat_api_keys_is_deleted"), "chat_api_keys", ["is_deleted"], unique=False)
    op.create_index(op.f("ix_chat_api_keys_key_id"), "chat_api_keys", ["key_id"], unique=False)
    op.create_index(op.f("ix_chat_api_keys_tenant_id"), "chat_api_keys", ["tenant_id"], unique=False)

    op.create_table(
        "chat_config_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_config_events")),
    )
    op.create_index(op.f("ix_chat_config_events_created_at"), "chat_config_events", ["created_at"], unique=False)
    op.create_index(op.f("ix_chat_config_events_deleted_at"), "chat_config_events", ["deleted_at"], unique=False)
    op.create_index(op.f("ix_chat_config_events_entity_id"), "chat_config_events", ["entity_id"], unique=False)
    op.create_index(op.f("ix_chat_config_events_entity_type"), "chat_config_events", ["entity_type"], unique=False)
    op.create_index(op.f("ix_chat_config_events_event_type"), "chat_config_events", ["event_type"], unique=False)
    op.create_index(op.f("ix_chat_config_events_is_deleted"), "chat_config_events", ["is_deleted"], unique=False)
    op.create_index(op.f("ix_chat_config_events_tenant_id"), "chat_config_events", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_table("chat_config_events")
    op.drop_table("chat_api_keys")
    op.drop_table("chat_group_memberships")
    op.drop_table("chat_groups")
    op.drop_table("chat_users")
    op.drop_table("chat_profiles")
