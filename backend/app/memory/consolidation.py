"""记忆整合（Consolidation）模块

把短期记忆溢出的消息/批次消息转化为：
1. 增量会话摘要（写回 conversations.metadata_.summary）
2. 结构化记忆条目（memory_entries，event 类型，用于溯源/审计/后续检索）

该模块同时服务于两种运行环境：
- Celery worker 进程（异步）
- API 进程内联降级（broker 不可用时由 manager 直接 await）

使用独立的 NullPool 数据库引擎，避免不同 event loop 之间共享连接池。
"""
import structlog
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.memory.common import normalize_user_id
from app.memory.long_term import LongTermMemory
from app.models.conversation import Conversation
from app.models.memory_entry import MemoryEntry

logger = structlog.get_logger(__name__)


def _make_session():
    """创建独立的数据库会话（NullPool 引擎，兼容不同 event loop）"""
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, session_factory()


async def build_session_summary(existing_summary: str, messages: list) -> str:
    """增量生成会话摘要，复用 LongTermMemory 的 LLM / mock 逻辑"""
    memory = LongTermMemory(
        max_summary_length=settings.MEMORY_LONG_TERM_MAX_SUMMARY_LENGTH
    )
    if existing_summary:
        await memory.set_summary(existing_summary)
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role and content:
            await memory.add_message(role, content)
    return await memory.get_summary()


async def save_event_entries(session, session_id: str, user_id, messages: list) -> list:
    """把消息批次保存为 event 类型记忆条目（溯源/审计）"""
    entries = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if not content:
            continue
        entry = MemoryEntry(
            user_id=user_id,
            session_id=session_id,
            namespace="session",
            memory_type="event",
            content=f"{role}: {content}",
            strength=0.5,
            confidence=0.5,
        )
        session.add(entry)
        entries.append(entry)
    return entries


async def _extract_and_apply(session, user_id, session_id, messages: list) -> list:
    """提取结构化记忆条目并应用更新策略（去重/强化/冲突处理）。

    提取失败不阻断 consolidation 主流程（降级为仅事件记忆）。
    """
    try:
        from app.memory.extractor import MemoryExtractor
        from app.memory.updater import apply_memory_updates

        extractor = MemoryExtractor()
        extracted = await extractor.extract(messages)
        applied = await apply_memory_updates(session, user_id, session_id, extracted)
        return applied
    except Exception:
        logger.exception(
            "memory.consolidation.extract_failed",
            session_id=session_id,
        )
        return []


async def consolidate_memory(
    session_id: str,
    user_id=None,
    messages: Optional[List[dict]] = None,
) -> dict:
    """记忆整合入口

    Args:
        session_id: 会话 ID
        user_id: 用户 ID（可空）
        messages: 待整合的消息批次，格式 [{"role": "...", "content": "..."}]

    Returns:
        整合结果摘要 dict
    """
    messages = messages or []
    if not messages:
        return {"session_id": session_id, "status": "noop"}

    user_id = normalize_user_id(user_id)
    engine, session = _make_session()
    try:
        # 1. 加载现有会话与摘要
        result = await session.execute(
            select(Conversation).where(Conversation.session_id == session_id)
        )
        conv = result.scalar_one_or_none()
        existing_summary = ""
        if conv and conv.metadata_:
            existing_summary = conv.metadata_.get("summary", "")

        # 2. 增量生成摘要
        new_summary = await build_session_summary(existing_summary, messages)

        # 3. 更新会话摘要（不存在则创建会话）
        if conv is None:
            conv = Conversation(session_id=session_id, user_id=user_id, title=session_id)
            session.add(conv)
        meta = dict(conv.metadata_ or {})
        meta["summary"] = new_summary
        conv.metadata_ = meta

        # 4. 事件记忆条目入库（溯源/审计）
        await save_event_entries(session, session_id, user_id, messages)

        # 5. 提取结构化记忆（fact/preference/procedural）并按更新策略入库
        extracted = await _extract_and_apply(session, user_id, session_id, messages)

        await session.commit()
        logger.info(
            "memory.consolidated",
            session_id=session_id,
            messages=len(messages),
            summary_len=len(new_summary),
            extracted_memories=len(extracted),
        )
        return {
            "session_id": session_id,
            "status": "ok",
            "summary_len": len(new_summary),
            "extracted_memories": len(extracted),
        }
    except Exception:
        await session.rollback()
        logger.exception("memory.consolidate.failed", session_id=session_id)
        raise
    finally:
        await session.close()
        await engine.dispose()
