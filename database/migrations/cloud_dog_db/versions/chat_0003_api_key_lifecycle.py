"""chat-client API key lifecycle columns

Revision ID: chat_0003_api_key_lifecycle
Revises: chat_0002_config_crud
Create Date: 2026-06-02 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "chat_0003_api_key_lifecycle"
down_revision = "chat_0002_config_crud"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_api_keys",
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
    )
    op.add_column(
        "chat_api_keys",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(op.f("ix_chat_api_keys_status"), "chat_api_keys", ["status"], unique=False)
    op.create_index(op.f("ix_chat_api_keys_expires_at"), "chat_api_keys", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_chat_api_keys_expires_at"), table_name="chat_api_keys")
    op.drop_index(op.f("ix_chat_api_keys_status"), table_name="chat_api_keys")
    op.drop_column("chat_api_keys", "expires_at")
    op.drop_column("chat_api_keys", "status")
