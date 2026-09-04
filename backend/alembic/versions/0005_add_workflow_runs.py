"""add workflow_runs table

Workflow 运行台账：run 级实体 + 增量 checkpoint（JSON）。
支撑断点恢复（resume_run_id）与运行状态查询。

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=50), nullable=False,
                  server_default="workflow"),
        sa.Column("objective", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False,
                  server_default="running"),
        sa.Column("checkpoint", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("run_id", name="uq_workflow_runs_run_id"),
    )
    op.create_index("ix_workflow_runs_user_id", "workflow_runs", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_workflow_runs_user_id", table_name="workflow_runs")
    op.drop_table("workflow_runs")
