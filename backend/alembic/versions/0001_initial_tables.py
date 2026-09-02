"""initial tables

Revision ID: 0001
Revises:
Create Date: 2026-09-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("email", sa.String(length=100), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=20), server_default="user"),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "agents",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("config", sa.JSON()),
        sa.Column("capabilities", sa.JSON()),
        sa.Column("status", sa.String(length=20), server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("session_id", sa.String(length=100), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("title", sa.String(length=200)),
        sa.Column("metadata", sa.JSON()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_conversations_session_id", "conversations", ["session_id"], unique=True)

    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("task_id", sa.String(length=100), nullable=False),
        sa.Column("parent_task_id", sa.Uuid(as_uuid=True), sa.ForeignKey("tasks.id")),
        sa.Column("agent_id", sa.Uuid(as_uuid=True), sa.ForeignKey("agents.id")),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("status", sa.String(length=20), server_default="pending"),
        sa.Column("priority", sa.Integer(), server_default="0"),
        sa.Column("input_data", sa.JSON()),
        sa.Column("output_data", sa.JSON()),
        sa.Column("error_message", sa.Text()),
        sa.Column("retry_count", sa.Integer(), server_default="0"),
        sa.Column("max_retries", sa.Integer(), server_default="3"),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("completed_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_tasks_task_id", "tasks", ["task_id"], unique=True)

    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(as_uuid=True), sa.ForeignKey("conversations.id")),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tool_calls", sa.JSON()),
        sa.Column("metadata", sa.JSON()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "memory_entries",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("session_id", sa.String(length=100)),
        sa.Column("namespace", sa.String(length=20), nullable=False, server_default="user"),
        sa.Column("memory_type", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("entity", sa.String(length=200)),
        sa.Column("strength", sa.Float(), server_default="0.5"),
        sa.Column("confidence", sa.Float(), server_default="0.5"),
        sa.Column("access_count", sa.Integer(), server_default="0"),
        sa.Column("last_accessed_at", sa.DateTime()),
        sa.Column("source_message_ids", sa.JSON()),
        sa.Column("expires_at", sa.DateTime()),
        sa.Column("archived_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_memory_entries_user_id", "memory_entries", ["user_id"])
    op.create_index("ix_memory_entries_session_id", "memory_entries", ["session_id"])
    op.create_index("ix_memory_entries_memory_type", "memory_entries", ["memory_type"])
    op.create_index("ix_memory_entries_expires_at", "memory_entries", ["expires_at"])
    op.create_index(
        "ix_memory_user_strength_updated",
        "memory_entries",
        ["user_id", "strength", "updated_at"],
    )


def downgrade() -> None:
    op.drop_table("memory_entries")
    op.drop_table("messages")
    op.drop_table("tasks")
    op.drop_table("conversations")
    op.drop_table("agents")
    op.drop_table("users")
