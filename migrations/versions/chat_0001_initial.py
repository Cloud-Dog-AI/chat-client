"""chat-client cloud_dog_db baseline tables

Revision ID: chat_0001_initial
Revises:
Create Date: 2026-03-05 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "chat_0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("log_path", sa.String(length=1024), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_sessions")),
    )
    op.create_index(op.f("ix_chat_sessions_created_at"), "chat_sessions", ["created_at"], unique=False)
    op.create_index(op.f("ix_chat_sessions_deleted_at"), "chat_sessions", ["deleted_at"], unique=False)
    op.create_index(op.f("ix_chat_sessions_is_deleted"), "chat_sessions", ["is_deleted"], unique=False)
    op.create_index(op.f("ix_chat_sessions_tenant_id"), "chat_sessions", ["tenant_id"], unique=False)

    op.create_table(
        "chat_session_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("event_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], name=op.f("fk_chat_session_events_session_id_chat_sessions"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_session_events")),
        sa.UniqueConstraint("session_id", "sequence", name="uq_chat_session_events_sequence"),
    )
    op.create_index(op.f("ix_chat_session_events_created_at"), "chat_session_events", ["created_at"], unique=False)
    op.create_index(op.f("ix_chat_session_events_deleted_at"), "chat_session_events", ["deleted_at"], unique=False)
    op.create_index(op.f("ix_chat_session_events_is_deleted"), "chat_session_events", ["is_deleted"], unique=False)
    op.create_index(op.f("ix_chat_session_events_session_id"), "chat_session_events", ["session_id"], unique=False)
    op.create_index(op.f("ix_chat_session_events_tenant_id"), "chat_session_events", ["tenant_id"], unique=False)

    op.create_table(
        "chat_session_preferences",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("selected_mcp_server_indices_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], name=op.f("fk_chat_session_preferences_session_id_chat_sessions"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_session_preferences")),
        sa.UniqueConstraint("session_id", name="uq_chat_session_preferences_session_id"),
    )
    op.create_index(op.f("ix_chat_session_preferences_created_at"), "chat_session_preferences", ["created_at"], unique=False)
    op.create_index(op.f("ix_chat_session_preferences_deleted_at"), "chat_session_preferences", ["deleted_at"], unique=False)
    op.create_index(op.f("ix_chat_session_preferences_is_deleted"), "chat_session_preferences", ["is_deleted"], unique=False)
    op.create_index(op.f("ix_chat_session_preferences_session_id"), "chat_session_preferences", ["session_id"], unique=False)
    op.create_index(op.f("ix_chat_session_preferences_tenant_id"), "chat_session_preferences", ["tenant_id"], unique=False)

    op.create_table(
        "chat_audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("detail_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], name=op.f("fk_chat_audit_logs_session_id_chat_sessions"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_audit_logs")),
    )
    op.create_index(op.f("ix_chat_audit_logs_action"), "chat_audit_logs", ["action"], unique=False)
    op.create_index(op.f("ix_chat_audit_logs_created_at"), "chat_audit_logs", ["created_at"], unique=False)
    op.create_index(op.f("ix_chat_audit_logs_deleted_at"), "chat_audit_logs", ["deleted_at"], unique=False)
    op.create_index(op.f("ix_chat_audit_logs_is_deleted"), "chat_audit_logs", ["is_deleted"], unique=False)
    op.create_index(op.f("ix_chat_audit_logs_request_id"), "chat_audit_logs", ["request_id"], unique=False)
    op.create_index(op.f("ix_chat_audit_logs_session_id"), "chat_audit_logs", ["session_id"], unique=False)
    op.create_index(op.f("ix_chat_audit_logs_tenant_id"), "chat_audit_logs", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_chat_audit_logs_tenant_id"), table_name="chat_audit_logs")
    op.drop_index(op.f("ix_chat_audit_logs_session_id"), table_name="chat_audit_logs")
    op.drop_index(op.f("ix_chat_audit_logs_request_id"), table_name="chat_audit_logs")
    op.drop_index(op.f("ix_chat_audit_logs_is_deleted"), table_name="chat_audit_logs")
    op.drop_index(op.f("ix_chat_audit_logs_deleted_at"), table_name="chat_audit_logs")
    op.drop_index(op.f("ix_chat_audit_logs_created_at"), table_name="chat_audit_logs")
    op.drop_index(op.f("ix_chat_audit_logs_action"), table_name="chat_audit_logs")
    op.drop_table("chat_audit_logs")

    op.drop_index(
        op.f("ix_chat_session_preferences_tenant_id"),
        table_name="chat_session_preferences",
    )
    op.drop_index(
        op.f("ix_chat_session_preferences_session_id"),
        table_name="chat_session_preferences",
    )
    op.drop_index(
        op.f("ix_chat_session_preferences_is_deleted"),
        table_name="chat_session_preferences",
    )
    op.drop_index(
        op.f("ix_chat_session_preferences_deleted_at"),
        table_name="chat_session_preferences",
    )
    op.drop_index(
        op.f("ix_chat_session_preferences_created_at"),
        table_name="chat_session_preferences",
    )
    op.drop_table("chat_session_preferences")

    op.drop_index(op.f("ix_chat_session_events_tenant_id"), table_name="chat_session_events")
    op.drop_index(op.f("ix_chat_session_events_session_id"), table_name="chat_session_events")
    op.drop_index(op.f("ix_chat_session_events_is_deleted"), table_name="chat_session_events")
    op.drop_index(op.f("ix_chat_session_events_deleted_at"), table_name="chat_session_events")
    op.drop_index(op.f("ix_chat_session_events_created_at"), table_name="chat_session_events")
    op.drop_table("chat_session_events")

    op.drop_index(op.f("ix_chat_sessions_tenant_id"), table_name="chat_sessions")
    op.drop_index(op.f("ix_chat_sessions_is_deleted"), table_name="chat_sessions")
    op.drop_index(op.f("ix_chat_sessions_deleted_at"), table_name="chat_sessions")
    op.drop_index(op.f("ix_chat_sessions_created_at"), table_name="chat_sessions")
    op.drop_table("chat_sessions")
