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
        
        # 初始化短期记忆
        window_size = getattr(settings, 'MEMORY_SHORT_TERM_WINDOW_SIZE', 5)
        self.short_term = ShortTermMemory(window_size=window_size)
        
        # 初始化长期记忆
        max_summary_length = getattr(settings, 'MEMORY_LONG_TERM_MAX_SUMMARY_LENGTH', 500)
        self.long_term = LongTermMemory(max_summary_length=max_summary_length)
        
        # 初始化持久化层（如果提供了db_session）
        self.persistence = MemoryPersistence(db_session) if db_session else None
        
        self.is_initialized = False
        
        logger.info(
            "MemoryManager initialized",
            session_id=session_id,
            short_term_window=window_size,
            persistence_enabled=self.persistence is not None
        )
    
    async def initialize(self) -> None:
        """
        初始化记忆，从数据库加载历史
        
        这会：
        1. 从数据库加载历史消息
        2. 将最近的消息加载到短期记忆
        3. 将摘要加载到长期记忆
        """
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
        
        # 如果有消息被移出短期窗口，处理它们（转移到长期记忆）
        if evicted_messages:
            logger.info(
                "Processing evicted messages from short-term window",
                count=len(evicted_messages)
            )
            # 将被移出的消息添加到长期记忆进行摘要和保存
            for msg in evicted_messages:
                msg_role = "user" if isinstance(msg, HumanMessage) else "assistant"
                await self.long_term.add_message(msg_role, msg.content)
        
        # 同时将所有消息添加到长期记忆（用于生成摘要）
        await self.long_term.add_message(role, content)
        
        # 持久化到数据库
        if self.persistence:
            try:
                # 确保会话存在
                conversation_id = await self.persistence.save_conversation(
                    self.session_id,
                    self.user_id
                )
                
                # 保存消息
                await self.persistence.save_message(
                    conversation_id,
                    role,
                    content,
                    metadata_=metadata
                )
                
                # 定期更新摘要
                summary_interval = getattr(settings, 'MEMORY_LONG_TERM_SUMMARY_INTERVAL', 10)
                stats = await self.persistence.get_conversation_stats(self.session_id)
                
                if stats.get("total_messages", 0) % summary_interval == 0:
                    current_summary = await self.long_term.get_summary()
                    if current_summary:
                        await self.persistence.save_summary(self.session_id, current_summary)
                        logger.info("Updated summary in database")
                
            except Exception as e:
                logger.error("Failed to persist message", error=str(e))
                # 不抛出异常，避免影响主流程
        
        logger.debug(
            "Message added to memory",
            role=role,
            content_length=len(content),
            short_term_count=self.short_term.get_message_count(),
            evicted_count=len(evicted_messages)
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
        
        # 添加短期记忆（最近的对话）
        short_context = await self.short_term.get_context_string()
        if short_context:
            parts.append(f"=== Recent Messages ===\n{short_context}")
        
        context = "\n\n".join(parts) if parts else ""
        
        logger.debug(
            "Context retrieved",
            total_length=len(context),
            has_summary=bool(long_summary),
            has_short_term=bool(short_context)
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
            "short_term_message_count": self.short_term.get_message_count(),
            "long_term_has_summary": self.long_term.has_summary(),
            "is_initialized": self.is_initialized
        }
        
        if self.persistence:
            db_stats = await self.persistence.get_conversation_stats(self.session_id)
            stats["database"] = db_stats
        
        return stats
    
    def __repr__(self):
        return f"MemoryManager(session_id={self.session_id}, initialized={self.is_initialized})"
