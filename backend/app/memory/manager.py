"""记忆管理器 - 统一管理短期和长期记忆"""

from typing import List, Dict, Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from langchain.schema import HumanMessage, AIMessage
import structlog

from app.memory.short_term import ShortTermMemory
from app.memory.long_term import LongTermMemory
from app.memory.persistence import MemoryPersistence
from app.core.config import settings

logger = structlog.get_logger(__name__)


class MemoryManager:
    """
    记忆管理器
    
    协调短期记忆、长期记忆和持久化存储，提供统一的记忆访问接口
    """
    
    def __init__(self, session_id: str, user_id: Optional[UUID] = None, 
                 db_session: Optional[AsyncSession] = None):
        """
        初始化记忆管理器
        
        Args:
            session_id: 会话ID
            user_id: 用户ID（可选）
            db_session: 数据库会话（可选，用于持久化）
        """
        self.session_id = session_id
        self.user_id = user_id
        self.db_session = db_session
        
        # 初始化短期记忆（默认内存后端，initialize 时按配置升级为 Redis）
        window_size = getattr(settings, 'MEMORY_SHORT_TERM_WINDOW_SIZE', 5)
        self.short_term = ShortTermMemory(
            window_size=window_size,
            namespace="session",
            session_id=session_id,
        )
        
        # 初始化长期记忆
        max_summary_length = getattr(settings, 'MEMORY_LONG_TERM_MAX_SUMMARY_LENGTH', 500)
        self.long_term = LongTermMemory(max_summary_length=max_summary_length)
        
        # 初始化持久化层（如果提供了db_session）
        self.persistence = MemoryPersistence(db_session) if db_session else None
        
        # 待整合（consolidation）的消息批次，达到阈值后异步处理
        self._consolidation_pending: List[Dict] = []
        # Redis 可用时走 Celery 异步；不可用时（本地开发）内联降级
        self._use_celery = False
        self.is_initialized = False
        
        logger.info(
            "MemoryManager initialized",
            session_id=session_id,
            short_term_window=window_size,
            persistence_enabled=self.persistence is not None,
            consolidation_enabled=settings.MEMORY_CONSOLIDATION_ENABLED,
        )
    
    async def initialize(self) -> None:
        """
        初始化记忆，从数据库加载历史
        
        这会：
        1. 从数据库加载历史消息
        2. 将最近的消息加载到短期记忆
        3. 将摘要加载到长期记忆
        """
        # 按配置初始化短期记忆存储后端（auto 模式：Redis 不可用自动降级内存）
        from app.memory.stores import create_memory_store
        from app.memory.stores.redis_store import RedisMemoryStore
        store = await create_memory_store()
        await self.short_term.set_store(store)
        # 仅当 Redis 真正可用时才使用 Celery 异步（避免本地无 broker 时任务静默丢失）
        self._use_celery = isinstance(store, RedisMemoryStore)

        if not self.persistence:
            logger.warning("No persistence layer, skipping initialization")
            self.is_initialized = True
            return
        
        try:
            # 确保会话存在
            await self.persistence.save_conversation(
                self.session_id, 
                self.user_id
            )
            
            # 加载历史消息
            messages = await self.persistence.load_messages(self.session_id)
            
            if messages:
                logger.info(f"Loading {len(messages)} historical messages")
                
                # 将消息添加到短期记忆（只添加最近的）
                window_size = getattr(settings, 'MEMORY_SHORT_TERM_WINDOW_SIZE', 5)
                recent_messages = messages[-window_size * 2:]  # 取最后N轮（user+assistant）
                
                for msg in recent_messages:
                    await self.short_term.add_message(msg["role"], msg["content"])
                
                # 加载或生成摘要
                summary = await self.persistence.load_summary(self.session_id)
                if summary:
                    await self.long_term.set_summary(summary)
                    logger.info("Loaded existing summary from database")
                elif len(messages) > 10:
                    # 如果有足够多的历史消息但没有摘要，生成一个简单摘要
                    simple_summary = f"Historical conversation with {len(messages)} messages"
                    await self.long_term.set_summary(simple_summary)
                    await self.persistence.save_summary(self.session_id, simple_summary)
            
            # 恢复待整合的消息批次（跨请求持续累积，达到批大小即触发）
            try:
                pending = await self.persistence.load_pending_consolidation(
                    self.session_id
                )
                if pending:
                    self._consolidation_pending = pending
                    logger.info(
                        "Restored pending consolidation batch",
                        session_id=self.session_id,
                        pending=len(pending),
                    )
            except Exception as e:
                logger.warning(
                    "Failed to restore pending consolidation",
                    error=str(e),
                    session_id=self.session_id,
                )

            self.is_initialized = True
            logger.info("MemoryManager initialized successfully")
            
        except Exception as e:
            logger.error("Failed to initialize memory", error=str(e))
            raise
    
    async def add_message(self, role: str, content: str, 
                         metadata: Optional[Dict] = None) -> None:
        """
        添加消息到记忆
        
        Args:
            role: 消息角色 ('user' 或 'assistant')
            content: 消息内容
            metadata: 元数据（可选）
        """
        # 添加到短期记忆，并获取被移出的消息
        evicted_messages = await self.short_term.add_message(role, content)

        # 记录待整合的消息（新消息 + 被驱逐消息），供异步 consolidation 处理
        self._consolidation_pending.append({"role": role, "content": content})
        for msg in evicted_messages:
            msg_role = "user" if isinstance(msg, HumanMessage) else "assistant"
            self._consolidation_pending.append({"role": msg_role, "content": msg.content})

        # 同步更新长期摘要（保留既有行为；LLM 摘要开销将随 P2 迁移至异步）
        await self.long_term.add_message(role, content)

        # 持久化到数据库（persistence 内部带重试，失败告警但不中断主流程）
        if self.persistence:
            try:
                conversation_id = await self.persistence.save_conversation(
                    self.session_id,
                    self.user_id
                )
                await self.persistence.save_message(
                    conversation_id,
                    role,
                    content,
                    metadata_=metadata
                )

                # 定期同步摘要落库（consolidation 管道也会异步更新）
                summary_interval = getattr(settings, 'MEMORY_LONG_TERM_SUMMARY_INTERVAL', 10)
                stats = await self.persistence.get_conversation_stats(self.session_id)
                if stats.get("total_messages", 0) % summary_interval == 0:
                    current_summary = await self.long_term.get_summary()
                    if current_summary:
                        await self.persistence.save_summary(self.session_id, current_summary)
                        logger.info("Updated summary in database")
            except Exception as e:
                logger.error(
                    "Failed to persist message",
                    error=str(e),
                    session_id=self.session_id,
                    role=role,
                )

        # 触发异步 consolidation（批量达到阈值或有消息被驱逐时）。
        # 批次持久化在会话 metadata_，跨请求累积；未达阈值时写回，达到阈值时清空。
        should_trigger = (
            settings.MEMORY_CONSOLIDATION_ENABLED
            and len(self._consolidation_pending)
            >= settings.MEMORY_CONSOLIDATION_BATCH_SIZE
        ) or evicted_messages
        if should_trigger:
            batch = list(self._consolidation_pending)
            self._consolidation_pending.clear()
            if self.persistence:
                try:
                    await self.persistence.save_pending_consolidation(
                        self.session_id, self._consolidation_pending
                    )
                except Exception:
                    logger.warning(
                        "Failed to clear pending consolidation",
                        session_id=self.session_id,
                    )
            await self._trigger_consolidation(batch)
        elif self.persistence:
            try:
                await self.persistence.save_pending_consolidation(
                    self.session_id, self._consolidation_pending
                )
            except Exception:
                logger.warning(
                    "Failed to persist pending consolidation",
                    session_id=self.session_id,
                )

        logger.debug(
            "Message added to memory",
            role=role,
            content_length=len(content),
            short_term_count=await self.short_term.get_message_count(),
            evicted_count=len(evicted_messages)
        )

    async def _trigger_consolidation(self, messages: List[Dict]) -> None:
        """触发异步记忆整合（Redis 可用时走 Celery，否则内联降级）"""
        if not self._use_celery:
            await self._consolidate_inline(messages)
            return

        try:
            from app.tasks.memory_tasks import consolidate_memory_task
            consolidate_memory_task.delay(
                self.session_id,
                str(self.user_id) if self.user_id else None,
                messages,
            )
            logger.info(
                "memory.consolidation.queued",
                session_id=self.session_id,
                messages=len(messages),
            )
        except Exception as exc:
            # 投递异常（如 broker 连接问题）：内联降级执行
            logger.warning(
                "memory.consolidation.fallback_inline",
                session_id=self.session_id,
                error=str(exc),
            )
            await self._consolidate_inline(messages)

    async def _consolidate_inline(self, messages: List[Dict]) -> None:
        """内联执行记忆整合（API 进程内，供本地/降级场景）"""
        try:
            from app.memory.consolidation import consolidate_memory
            await consolidate_memory(self.session_id, self.user_id, messages)
        except Exception:
            logger.exception(
                "memory.consolidation.inline_failed",
                session_id=self.session_id,
            )

    async def get_context(self) -> str:
        """
        获取完整的记忆上下文（短期+长期）
        
        Returns:
            格式化的上下文字符串，适合传递给LLM
        """
        parts = []
        
        # 添加长期记忆摘要
        long_summary = await self.long_term.get_summary()
        if long_summary:
            parts.append(f"=== Conversation Summary ===\n{long_summary}\n")

        # 添加相关记忆条目（跨会话混合检索，需数据库会话）
        if self.db_session and settings.MEMORY_RETRIEVAL_ENABLED:
            try:
                from app.memory.retriever import MemoryRetriever
                retriever = MemoryRetriever(top_k=settings.MEMORY_RETRIEVAL_TOP_K)
                memories = await retriever.retrieve(
                    self.db_session,
                    self.user_id,
                    query=None,
                )
                if memories:
                    mem_lines = [
                        f"- [{m['memory_type']}] {m['content']}"
                        for m in memories
                    ]
                    parts.append("=== Relevant Memories ===\n" + "\n".join(mem_lines))
            except Exception:
                logger.warning(
                    "Failed to retrieve memories for context",
                    session_id=self.session_id,
                )

        # 添加短期记忆（最近的对话）
        short_context = await self.short_term.get_context_string()
        if short_context:
            parts.append(f"=== Recent Messages ===\n{short_context}")
        
        context = "\n\n".join(parts) if parts else ""
        
        logger.debug(
            "Context retrieved",
            total_length=len(context),
            has_summary=bool(long_summary),
            has_short_term=bool(short_context),
            has_memories="=== Relevant Memories ===" in context,
        )
        
        return context
    
    async def get_short_term_messages(self, limit: int = 5) -> List[Dict]:
        """
        获取短期记忆消息
        
        Args:
            limit: 限制返回的消息数量
            
        Returns:
            消息列表
        """
        all_messages = await self.short_term.to_dict_list()
        return all_messages[-limit * 2:] if all_messages else []  # N轮 = 2N条消息
    
    async def get_long_term_summary(self) -> str:
        """
        获取长期记忆摘要
        
        Returns:
            摘要字符串
        """
        return await self.long_term.get_summary()

    # ---------- 记忆条目管理（跨会话，需 db_session） ----------

    def _require_db(self) -> None:
        if not self.db_session:
            raise RuntimeError("Memory entry operations require a db_session")

    async def search_memories(
        self,
        query: str,
        limit: int = 5,
        memory_type: Optional[str] = None,
        offset: int = 0,
    ) -> List[Dict]:
        """跨会话检索相关记忆条目（混合打分，支持分页）"""
        self._require_db()
        from app.memory.retriever import MemoryRetriever
        retriever = MemoryRetriever(top_k=limit)
        return await retriever.retrieve(
            self.db_session,
            self.user_id,
            query=query,
            memory_type=memory_type,
            limit=limit,
            offset=offset,
        )

    async def get_memories(
        self,
        memory_type: Optional[str] = None,
        limit: int = 50,
        include_archived: bool = False,
        offset: int = 0,
    ) -> List[Dict]:
        """列出记忆条目（按质量排序，支持分页）"""
        self._require_db()
        from app.memory.retriever import MemoryRetriever
        retriever = MemoryRetriever(top_k=limit)
        # include_archived 场景需独立查询（retriever 仅返回有效记忆）
        if include_archived:
            from app.memory.common import normalize_user_id
            from sqlalchemy import select
            from app.models.memory_entry import MemoryEntry
            stmt = select(MemoryEntry)
            if memory_type:
                stmt = stmt.where(MemoryEntry.memory_type == memory_type)
            if self.user_id is not None:
                stmt = stmt.where(
                    MemoryEntry.user_id == normalize_user_id(self.user_id)
                )
            stmt = stmt.order_by(MemoryEntry.updated_at.desc()).limit(limit).offset(offset)
            result = await self.db_session.execute(stmt)
            entries = result.scalars().all()
            return [MemoryRetriever._to_dict(m, 0.0) for m in entries]
        return await retriever.retrieve(
            self.db_session,
            self.user_id,
            memory_type=memory_type,
            limit=limit,
            offset=offset,
        )

    async def get_memory_stats(self) -> Dict:
        """记忆条目统计（用户级、跨会话）：总量/归档/按类型分布"""
        self._require_db()
        from app.memory.common import normalize_user_id
        from app.models.memory_entry import MemoryEntry
        from sqlalchemy import func, select

        uid = normalize_user_id(self.user_id)
        base = (MemoryEntry.user_id == uid) if uid is not None else MemoryEntry.user_id.is_(None)

        total = (
            await self.db_session.execute(
                select(func.count(MemoryEntry.id)).where(
                    base, MemoryEntry.archived_at.is_(None)
                )
            )
        ).scalar() or 0
        archived = (
            await self.db_session.execute(
                select(func.count(MemoryEntry.id)).where(
                    base, MemoryEntry.archived_at.is_not(None)
                )
            )
        ).scalar() or 0

        rows = (
            await self.db_session.execute(
                select(
                    MemoryEntry.memory_type,
                    func.count(MemoryEntry.id),
                    func.avg(MemoryEntry.strength),
                    func.sum(MemoryEntry.access_count),
                )
                .where(base, MemoryEntry.archived_at.is_(None))
                .group_by(MemoryEntry.memory_type)
            )
        ).all()
        by_type = {
            r[0]: {
                "count": r[1],
                "avg_strength": round(float(r[2] or 0.0), 3),
                "total_access_count": int(r[3] or 0),
            }
            for r in rows
        }
        logger.info(
            "memory.stats.computed",
            user_id=str(self.user_id),
            total=total,
            archived=archived,
        )
        return {"total": total, "archived": archived, "by_type": by_type}

    async def add_memory(
        self,
        content: str,
        memory_type: str = "fact",
        entity: Optional[str] = None,
        confidence: float = 0.8,
    ) -> Dict:
        """手动添加一条记忆条目"""
        self._require_db()
        from app.memory.common import normalize_user_id
        from app.models.memory_entry import MemoryEntry
        from datetime import datetime

        if memory_type not in ("fact", "preference", "procedural", "event", "summary"):
            raise ValueError(f"Invalid memory_type: {memory_type}")

        entry = MemoryEntry(
            user_id=normalize_user_id(self.user_id),
            session_id=self.session_id,
            namespace="user" if self.user_id else "session",
            memory_type=memory_type,
            content=content.strip()[:500],
            entity=(entity or "").strip() or None,
            strength=0.8,
            confidence=max(0.0, min(1.0, confidence)),
        )
        self.db_session.add(entry)
        await self.db_session.commit()
        await self.db_session.refresh(entry)
        await self._index_vector_entries([entry])
        logger.info(
            "memory.entry.added",
            memory_id=str(entry.id),
            memory_type=memory_type,
            session_id=self.session_id,
        )
        return {
            "id": str(entry.id),
            "memory_type": entry.memory_type,
            "content": entry.content,
            "entity": entry.entity,
            "strength": entry.strength,
            "confidence": entry.confidence,
        }

    async def delete_memory(self, memory_id: str) -> bool:
        """软删除一条记忆条目（archived_at 标记，保留审计）"""
        self._require_db()
        from app.memory.common import normalize_user_id
        from app.models.memory_entry import MemoryEntry
        from sqlalchemy import select

        result = await self.db_session.execute(
            select(MemoryEntry).where(MemoryEntry.id == memory_id)
        )
        entry = result.scalar_one_or_none()
        if entry is None:
            return False
        # 校验归属
        if self.user_id is not None and entry.user_id != normalize_user_id(self.user_id):
            raise PermissionError("Cannot delete memory entry owned by another user")
        entry.archived_at = datetime.utcnow()
        entry.updated_at = datetime.utcnow()
        await self.db_session.commit()
        await self._remove_vector_entries([entry.id])
        logger.info(
            "memory.entry.deleted",
            memory_id=memory_id,
            session_id=self.session_id,
        )
        return True

    async def _index_vector_entries(self, entries) -> None:
        """同步记忆条目到向量索引（失败仅告警，不阻断）"""
        if not settings.MEMORY_VECTOR_ENABLED or not entries:
            return
        try:
            import asyncio
            from app.memory.vector_store import memory_vector_store
            await asyncio.to_thread(memory_vector_store.index_entries, entries)
        except Exception:  # noqa: BLE001
            logger.warning("memory.vector.sync_failed")

    async def _remove_vector_entries(self, ids) -> None:
        """同步移除记忆条目向量索引（失败仅告警，不阻断）"""
        if not settings.MEMORY_VECTOR_ENABLED or not ids:
            return
        try:
            from app.memory.vector_store import memory_vector_store
            memory_vector_store.remove_entries(ids)
        except Exception:  # noqa: BLE001
            logger.warning("memory.vector.remove_sync_failed")

    async def clear_memories(self) -> int:
        """遗忘权：归档当前用户的全部记忆条目（软删除，保留审计）"""
        self._require_db()
        from app.memory.common import normalize_user_id
        from app.models.memory_entry import MemoryEntry
        from sqlalchemy import select

        stmt = select(MemoryEntry).where(MemoryEntry.archived_at.is_(None))
        if self.user_id is not None:
            stmt = stmt.where(MemoryEntry.user_id == normalize_user_id(self.user_id))
        result = await self.db_session.execute(stmt)
        entries = result.scalars().all()
        for entry in entries:
            entry.archived_at = datetime.utcnow()
            entry.updated_at = datetime.utcnow()
        await self.db_session.commit()
        await self._remove_vector_entries([e.id for e in entries])
        logger.info(
            "memory.entries.cleared",
            count=len(entries),
            user_id=str(self.user_id),
        )
        return len(entries)

    async def save_to_db(self) -> None:
        """
        强制保存到数据库
        
        通常在会话结束时调用，确保所有记忆都已持久化
        """
        if not self.persistence:
            logger.warning("No persistence layer available")
            return
        
        try:
            # 更新摘要
            summary = await self.long_term.get_summary()
            if summary:
                await self.persistence.save_summary(self.session_id, summary)

            # 会话结束时清空并整合剩余待处理消息
            if self._consolidation_pending:
                batch = list(self._consolidation_pending)
                self._consolidation_pending.clear()
                await self._trigger_consolidation(batch)

            logger.info("Memory saved to database")
            
        except Exception as e:
            logger.error("Failed to save memory to database", error=str(e))
            raise
    
    async def clear(self) -> None:
        """清空所有记忆"""
        await self.short_term.clear()
        await self.long_term.clear()
        
        if self.persistence:
            await self.persistence.delete_conversation(self.session_id)
        
        logger.info("All memory cleared")
    
    async def get_stats(self) -> Dict:
        """
        获取记忆统计信息
        
        Returns:
            统计信息字典
        """
        stats = {
            "session_id": self.session_id,
            "short_term_message_count": await self.short_term.get_message_count(),
            "long_term_has_summary": self.long_term.has_summary(),
            "is_initialized": self.is_initialized,
            "consolidation_pending": len(self._consolidation_pending),
        }
        
        if self.persistence:
            db_stats = await self.persistence.get_conversation_stats(self.session_id)
            stats["database"] = db_stats
        
        return stats
    
    def __repr__(self):
        return f"MemoryManager(session_id={self.session_id}, initialized={self.is_initialized})"
