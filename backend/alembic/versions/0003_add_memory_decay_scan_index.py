"""add batch-decay scan index on memory_entries

后台批量任务（Celery beat 定时衰减与合规过期归档）扫描：
  WHERE archived_at IS NULL AND strength IS NOT NULL
现有索引均以 user_id 开头（检索路径），批处理不带 user_id，
无法命中而需要全表扫描。条目含归档后增长，
此索引让批处理只扫描活跃子集 (archived_at, strength, updated_at)。

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-02

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_memory_archived_strength_updated",
        "memory_entries",
        ["archived_at", "strength", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_memory_archived_strength_updated",
        table_name="memory_entries",
    )
