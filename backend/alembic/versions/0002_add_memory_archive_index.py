"""add archive-filtered memory entry index

记忆检索主查询为：
  WHERE user_id = ? AND archived_at IS NULL
  ORDER BY strength DESC, updated_at DESC LIMIT n
旧复合索引 (user_id, strength, updated_at) 无法过滤归档行，
条目增长后每次检索都会扫描含归档的全量集合。
新增 (user_id, archived_at, strength, updated_at) 覆盖该查询路径。

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-02

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_memory_user_archived_strength_updated",
        "memory_entries",
        ["user_id", "archived_at", "strength", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_memory_user_archived_strength_updated",
        table_name="memory_entries",
    )
