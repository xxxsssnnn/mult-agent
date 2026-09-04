"""add owner columns to agents and tasks

租户隔离 Phase 1：为 agents / tasks 补 user_id 归属列（可空 FK）。

SQLite 方言不支持 ALTER TABLE ADD COLUMN 携带 REFERENCES（alembic 需
batch 复制建表），因此用 batch_alter_table；PostgreSQL 下 batch 等价于
直接 ALTER，无额外开销。

存量数据决策：不 backfill。历史上无主记录（user_id IS NULL）保持可空，
普通用户对它们不可见（list/单条均过滤），仅 admin 全量可见，避免历史
数据在隔离上线瞬间跨用户泄漏；新数据由 API 层强制写入 owner。

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.add_column(
            sa.Column(
                "user_id",
                sa.Uuid(as_uuid=True),
                sa.ForeignKey("users.id", name="fk_agents_user_id_users"),
                nullable=True,
            )
        )
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(
            sa.Column(
                "user_id",
                sa.Uuid(as_uuid=True),
                sa.ForeignKey("users.id", name="fk_tasks_user_id_users"),
                nullable=True,
            )
        )
    op.create_index("ix_agents_user_id", "agents", ["user_id"])
    op.create_index("ix_tasks_user_id", "tasks", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_user_id", table_name="tasks")
    op.drop_index("ix_agents_user_id", table_name="agents")
    op.drop_column("tasks", "user_id")
    op.drop_column("agents", "user_id")
