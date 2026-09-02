"""记忆衰减与遗忘机制

防止记忆无限膨胀与过时，两条执行路径互补:
- 惰性衰减（检索时顺带执行）: 每次检索对候选记忆应用衰减，无需定时任务
- 定时衰减（Celery beat）: 周期批量衰减并归档低强度记忆

衰减规则:
- strength 按记忆年龄指数衰减: strength *= exp(-rate * age_days)
- 命中次数（access_count）越多的记忆衰减越慢（使用频率是重要性信号）
- 低于归档阈值的记忆被软归档（archived_at），进入审计保留区而非物理删除
"""

import math
from datetime import datetime
from typing import Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.memory_entry import MemoryEntry

logger = structlog.get_logger(__name__)


def apply_decay(
    entry: MemoryEntry,
    now: Optional[datetime] = None,
) -> MemoryEntry:
    """对单条记忆应用时间衰减（就地修改 strength）

    Args:
        entry: 记忆条目
        now: 当前时间（测试注入用）
    """
    now = now or datetime.utcnow()
    last = entry.updated_at or entry.created_at or now
    age_days = max(0.0, (now - last).total_seconds() / 86400)
    if age_days <= 0:
        return entry

    # 命中次数越多，衰减越慢
    hit_factor = 1.0 / (1.0 + (entry.access_count or 0))
    rate = settings.MEMORY_DECAY_RATE * hit_factor
    decayed = (entry.strength or 0.5) * math.exp(-rate * age_days)
    entry.strength = round(max(0.0, min(1.0, decayed)), 4)
    return entry


async def decay_memories(
    session: AsyncSession,
    user_id=None,
    now: Optional[datetime] = None,
) -> dict:
    """批量衰减记忆强度并归档低强度记忆

    Args:
        session: 数据库会话（由调用方负责 commit）
        user_id: 限定用户（None 表示全部）
        now: 当前时间（测试注入用）

    Returns:
        {"scanned": n, "decayed": n, "archived": m}
    """
    from app.memory.common import normalize_user_id

    now = now or datetime.utcnow()
    user_id = normalize_user_id(user_id)

    query = select(MemoryEntry).where(
        MemoryEntry.archived_at.is_(None),
        MemoryEntry.strength.is_not(None),
    )
    if user_id is not None:
        query = query.where(MemoryEntry.user_id == user_id)
    result = await session.execute(query)
    entries = result.scalars().all()

    archived = 0
    for entry in entries:
        apply_decay(entry, now)
        if entry.strength < settings.MEMORY_DECAY_ARCHIVE_BELOW:
            entry.archived_at = now
            archived += 1
        entry.updated_at = now

    logger.info(
        "memory.decay.applied",
        scanned=len(entries),
        archived=archived,
        user_id=str(user_id),
    )
    return {
        "scanned": len(entries),
        "decayed": len(entries),
        "archived": archived,
    }
