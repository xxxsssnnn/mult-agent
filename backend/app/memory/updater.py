"""记忆更新策略 - 去重、强化、弱化与冲突处理

核心规则（以 user + memory_type + entity 为冲突判定维度）:
- 无现有记忆 → 新建（strength=0.8）
- 内容相似（归一化后相同/包含）→ 强化: strength += 0.1（上限 1.0）
- 内容冲突（同主体不同内容，如偏好改变）→ 弱化旧条目（strength * 0.5）
  并新建新条目（保留历史供审计与溯源）
"""

from datetime import datetime
from typing import List, Dict, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.common import normalize_user_id
from app.models.memory_entry import MemoryEntry

logger = structlog.get_logger(__name__)

STRENGTH_NEW = 0.8
STRENGTH_STRENGTHEN_DELTA = 0.1
STRENGTH_CONFLICT_DECAY = 0.5
VALID_TYPES = ("fact", "preference", "procedural")


def _content_similar(a: str, b: str) -> bool:
    """归一化后比较两条内容是否表达同一记忆"""
    a = a.strip().rstrip("。.!！~ ")
    b = b.strip().rstrip("。.!！~ ")
    return bool(a) and (a == b or a in b or b in a)


async def _find_existing(
    session: AsyncSession,
    user_id,
    memory_type: str,
    entity: Optional[str],
) -> Optional[MemoryEntry]:
    """查找同用户、同类型、同主体的现存有效记忆（取强度最高者）"""
    query = select(MemoryEntry).where(
        MemoryEntry.memory_type == memory_type,
        MemoryEntry.archived_at.is_(None),
    )
    if user_id is not None:
        query = query.where(MemoryEntry.user_id == user_id)
    else:
        query = query.where(MemoryEntry.user_id.is_(None))
    if entity:
        query = query.where(MemoryEntry.entity == entity)
    query = query.order_by(
        MemoryEntry.strength.desc(), MemoryEntry.updated_at.desc()
    ).limit(1)
    result = await session.execute(query)
    return result.scalar_one_or_none()


async def apply_memory_updates(
    session: AsyncSession,
    user_id,
    session_id: str,
    entries: List[Dict],
) -> List[MemoryEntry]:
    """应用提取的记忆条目：去重 / 强化 / 冲突处理

    Args:
        session: 数据库会话（由调用方负责 commit）
        user_id: 用户 ID（可为 None 或 UUID 字符串）
        session_id: 来源会话 ID
        entries: 提取器输出 [{"memory_type", "entity", "content", "confidence"}]

    Returns:
        本次新增或更新的 MemoryEntry 列表（已 add 到 session）
    """
    if not entries:
        return []
    user_id = normalize_user_id(user_id)
    results = []
    for data in entries:
        memory_type = data.get("memory_type")
        if memory_type not in VALID_TYPES:
            continue
        content = (data.get("content") or "").strip()
        if not content:
            continue
        entity = (data.get("entity") or "").strip() or None
        try:
            confidence = float(data.get("confidence", 0.6))
        except (TypeError, ValueError):
            confidence = 0.6
        confidence = max(0.0, min(1.0, confidence))

        existing = await _find_existing(session, user_id, memory_type, entity)
        if existing is None:
            entry = MemoryEntry(
                user_id=user_id,
                session_id=session_id,
                namespace="user" if user_id else "session",
                memory_type=memory_type,
                content=content,
                entity=entity,
                strength=STRENGTH_NEW,
                confidence=confidence,
            )
            session.add(entry)
            results.append(entry)
            continue

        if _content_similar(existing.content, content):
            # 同一记忆再次出现：强化
            existing.strength = min(
                1.0, (existing.strength or 0.5) + STRENGTH_STRENGTHEN_DELTA
            )
            existing.confidence = max(existing.confidence or 0.5, confidence)
            existing.updated_at = datetime.utcnow()
            results.append(existing)
        else:
            # 冲突：弱化旧条目（保留历史），新建新条目
            existing.strength = (existing.strength or 0.5) * STRENGTH_CONFLICT_DECAY
            existing.updated_at = datetime.utcnow()
            entry = MemoryEntry(
                user_id=user_id,
                session_id=session_id,
                namespace="user" if user_id else "session",
                memory_type=memory_type,
                content=content,
                entity=entity,
                strength=STRENGTH_NEW,
                confidence=confidence,
            )
            session.add(entry)
            results.append(entry)

    logger.info(
        "memory.updates_applied",
        applied=len(results),
        user_id=str(user_id),
        session_id=session_id,
    )
    return results
