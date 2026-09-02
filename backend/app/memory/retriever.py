"""记忆检索器 - 混合检索（关键词相关度 + 记忆强度 + 时间新鲜度 + 向量语义）

打分公式:
    score = 0.35 * relevance + 0.2 * strength + 0.15 * freshness + 0.3 * vector_sim

- 向量相似度分量仅在 MEMORY_VECTOR_ENABLED 且向量库可用时参与
  （不可用自动降级，不影响其余分量）
- 无查询时按记忆质量（strength/新鲜度）排序，用于上下文注入
"""

import asyncio
import re
from datetime import datetime
from typing import List, Dict, Optional

import structlog
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.memory.common import normalize_user_id
from app.models.memory_entry import MemoryEntry

logger = structlog.get_logger(__name__)

W_RELEVANCE = 0.35
W_STRENGTH = 0.2
W_FRESHNESS = 0.15
W_VECTOR = 0.3
FRESHNESS_HALF_LIFE_DAYS = 7.0


def _tokenize(text: str) -> set:
    """粗粒度分词：英文单词（≥2 字符）+ 中文连续二字以上片段"""
    if not text:
        return set()
    text = text.lower()
    return set(re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fa5]{2}", text))


def _relevance(content: str, tokens: set) -> float:
    """查询词在内容中的命中率（0~1）"""
    content_tokens = _tokenize(content)
    if not tokens or not content_tokens:
        return 0.0
    return len(tokens & content_tokens) / max(1, len(tokens))


def _freshness(updated_at: Optional[datetime]) -> float:
    """时间衰减新鲜度（半衰期 7 天）"""
    if not updated_at:
        return 0.5
    age_days = (datetime.utcnow() - updated_at).total_seconds() / 86400
    return 2 ** (-age_days / FRESHNESS_HALF_LIFE_DAYS)


class MemoryRetriever:
    """跨会话记忆检索器"""

    def __init__(self, top_k: int = 5):
        self.top_k = max(1, top_k)

    async def retrieve(
        self,
        session: AsyncSession,
        user_id,
        query: Optional[str] = None,
        session_id: Optional[str] = None,
        memory_type: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[Dict]:
        """检索记忆条目

        Args:
            session: 数据库会话
            user_id: 用户 ID（None 表示系统级/匿名记忆）
            query: 查询文本；None/空串时按记忆质量排序返回
            session_id: 限定来源会话（None 表示跨会话检索）
            memory_type: 限定记忆类型（fact/preference/procedural/...）
            limit: 返回条数上限（默认 top_k）
            offset: 分页偏移

        Returns:
            记忆字典列表，含 score 排序字段
        """
        user_id = normalize_user_id(user_id)
        stmt = select(MemoryEntry).where(
            MemoryEntry.archived_at.is_(None),
            or_(
                MemoryEntry.expires_at.is_(None),
                MemoryEntry.expires_at > datetime.utcnow(),
            ),
        )
        if user_id is not None:
            stmt = stmt.where(MemoryEntry.user_id == user_id)
        else:
            stmt = stmt.where(MemoryEntry.user_id.is_(None))
        if session_id:
            stmt = stmt.where(MemoryEntry.session_id == session_id)
        if memory_type:
            stmt = stmt.where(MemoryEntry.memory_type == memory_type)

        result = await session.execute(stmt)
        candidates = result.scalars().all()

        # 惰性衰减：检索时顺带对候选记忆应用时间衰减（无需定时任务）
        if settings.MEMORY_DECAY_ENABLED:
            from app.memory.decay import apply_decay
            for m in candidates:
                apply_decay(m)

        # 向量语义命中（不可用/失败时自动降级为空）
        vector_hits: Dict[str, float] = {}
        if query and settings.MEMORY_VECTOR_ENABLED:
            try:
                from app.memory.vector_store import memory_vector_store
                hits = await asyncio.to_thread(
                    memory_vector_store.search, query, 50, user_id
                )
                vector_hits = {str(k): v for k, v in (hits or [])}
            except Exception:  # noqa: BLE001
                vector_hits = {}

        tokens = _tokenize(query or "")
        scored = []
        for m in candidates:
            relevance = _relevance(m.content, tokens) if tokens else 0.5
            score = (
                W_RELEVANCE * relevance
                + W_STRENGTH * (m.strength or 0.5)
                + W_FRESHNESS * _freshness(m.updated_at)
                + W_VECTOR * vector_hits.get(str(m.id), 0.0)
            )
            scored.append((score, m))

        scored.sort(key=lambda x: x[0], reverse=True)
        page_size = limit or self.top_k
        picked = scored[offset : offset + page_size]

        # 更新命中统计（失败不影响主流程）
        hit_ids = [m.id for _, m in picked]
        if hit_ids:
            try:
                await session.execute(
                    update(MemoryEntry)
                    .where(MemoryEntry.id.in_(hit_ids))
                    .values(
                        access_count=MemoryEntry.access_count + 1,
                        last_accessed_at=datetime.utcnow(),
                    )
                )
                await session.commit()
            except Exception:
                await session.rollback()
                logger.warning("memory.retriever.access_stats_failed")

        logger.info(
            "memory.retrieved",
            query=query or "",
            candidates=len(candidates),
            returned=len(picked),
            user_id=str(user_id),
        )
        return [self._to_dict(m, s) for s, m in picked]

    @staticmethod
    def _to_dict(m: MemoryEntry, score: float) -> Dict:
        return {
            "id": str(m.id),
            "memory_type": m.memory_type,
            "content": m.content,
            "entity": m.entity,
            "strength": m.strength,
            "confidence": m.confidence,
            "access_count": m.access_count,
            "session_id": m.session_id,
            "score": round(score, 4),
            "updated_at": m.updated_at.isoformat() if m.updated_at else None,
        }
