"""记忆持久化模块 - 负责记忆的数据库存储和加载"""

from typing import List, Dict, Optional
from uuid import UUID
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import structlog

from app.models.conversation import Conversation, Message
from app.core.database import get_db

logger = structlog.get_logger(__name__)


class MemoryPersistence:
    """
    记忆持久化类
    
    负责将对话历史保存到数据库，并支持从数据库加载
    """
    
    def __init__(self, db_session: AsyncSession):
        """
        初始化持久化层
        
        Args:
            db_session: 数据库会话
        """
        self.db = db_session
        logger.info("Memory persistence initialized")
    
    async def save_conversation(self, session_id: str, user_id: Optional[UUID] = None, 
                               title: Optional[str] = None) -> UUID:
        """
        保存或更新会话
        
        Args:
            session_id: 会话ID
            user_id: 用户ID（可选）
            title: 会话标题（可选）
            
        Returns:
            会话的UUID
        """
        # 检查会话是否已存在
        result = await self.db.execute(
            select(Conversation).where(Conversation.session_id == session_id)
        )
        conversation = result.scalar_one_or_none()
        
        if conversation:
            # 更新现有会话
            if title:
                conversation.title = title
            conversation.updated_at = datetime.utcnow()
            logger.info("Conversation updated", session_id=session_id)
        else:
            # 创建新会话
            conversation = Conversation(
                session_id=session_id,
                user_id=user_id,
                title=title or f"Conversation {session_id[:8]}",
                metadata_={}
            )
            self.db.add(conversation)
            logger.info("Conversation created", session_id=session_id)
        
        await self.db.commit()
        await self.db.refresh(conversation)
        
        return conversation.id
    
    async def save_message(self, conversation_id: UUID, role: str, content: str,
                          tool_calls: Optional[Dict] = None, 
                          metadata_: Optional[Dict] = None) -> UUID:
        """
        保存消息到数据库
        
        Args:
            conversation_id: 会话ID
            role: 消息角色 ('user' 或 'assistant')
            content: 消息内容
            tool_calls: 工具调用信息（可选）
            metadata_: 元数据（可选）
            
        Returns:
            消息的UUID
        """
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            tool_calls=tool_calls,
            metadata_=metadata_ or {}
        )
        
        self.db.add(message)
        await self.db.commit()
        await self.db.refresh(message)
        
        logger.debug(
            "Message saved to database",
            conversation_id=str(conversation_id),
            role=role,
            content_length=len(content)
        )
        
        return message.id
    
    async def load_messages(self, session_id: str, limit: Optional[int] = None) -> List[Dict]:
        """
        从数据库加载消息
        
        Args:
            session_id: 会话ID
            limit: 限制返回的消息数量（可选）
            
        Returns:
            消息列表，每个元素包含role、content等字段
        """
        # 查找会话
        result = await self.db.execute(
            select(Conversation).where(Conversation.session_id == session_id)
        )
        conversation = result.scalar_one_or_none()
        
        if not conversation:
            logger.warning("Conversation not found", session_id=session_id)
            return []
        
        # 查询消息
        query = select(Message).where(Message.conversation_id == conversation.id).order_by(Message.created_at)
        
        if limit:
            query = query.limit(limit)
        
        result = await self.db.execute(query)
        messages = result.scalars().all()
        
        # 转换为字典格式
        message_list = [
            {
                "id": str(msg.id),
                "role": msg.role,
                "content": msg.content,
                "tool_calls": msg.tool_calls,
                "metadata": msg.metadata_,
                "created_at": msg.created_at.isoformat() if msg.created_at else None
            }
            for msg in messages
        ]
        
        logger.info(
            "Messages loaded from database",
            session_id=session_id,
            count=len(message_list)
        )
        
        return message_list
    
    async def load_summary(self, session_id: str) -> Optional[str]:
        """
        从数据库加载会话摘要
        
        Args:
            session_id: 会话ID
            
        Returns:
            摘要字符串，如果不存在则返回None
        """
        result = await self.db.execute(
            select(Conversation).where(Conversation.session_id == session_id)
        )
        conversation = result.scalar_one_or_none()
        
        if not conversation:
            return None
        
        # 从metadata中提取summary
        metadata = conversation.metadata_ or {}
        summary = metadata.get("summary")
        
        if summary:
            logger.debug("Summary loaded from database", length=len(summary))
        
        return summary
    
    async def save_summary(self, session_id: str, summary: str) -> None:
        """
        保存摘要到数据库
        
        Args:
            session_id: 会话ID
            summary: 摘要内容
        """
        result = await self.db.execute(
            select(Conversation).where(Conversation.session_id == session_id)
        )
        conversation = result.scalar_one_or_none()
        
        if not conversation:
            logger.warning("Conversation not found for saving summary", session_id=session_id)
            return
        
        # 更新metadata中的summary
        if not conversation.metadata_:
            conversation.metadata_ = {}
        
        conversation.metadata_["summary"] = summary
        conversation.updated_at = datetime.utcnow()
        
        await self.db.commit()
        
        logger.info("Summary saved to database", session_id=session_id, length=len(summary))
    
    async def delete_conversation(self, session_id: str) -> bool:
        """
        删除会话及其所有消息
        
        Args:
            session_id: 会话ID
            
        Returns:
            是否成功删除
        """
        result = await self.db.execute(
            select(Conversation).where(Conversation.session_id == session_id)
        )
        conversation = result.scalar_one_or_none()
        
        if not conversation:
            logger.warning("Conversation not found for deletion", session_id=session_id)
            return False
        
        # 删除相关消息
        await self.db.execute(
            Message.__table__.delete().where(Message.conversation_id == conversation.id)
        )
        
        # 删除会话
        await self.db.delete(conversation)
        await self.db.commit()
        
        logger.info("Conversation deleted", session_id=session_id)
        return True
    
    async def get_conversation_stats(self, session_id: str) -> Dict:
        """
        获取会话统计信息
        
        Args:
            session_id: 会话ID
            
        Returns:
            统计信息字典
        """
        result = await self.db.execute(
            select(Conversation).where(Conversation.session_id == session_id)
        )
        conversation = result.scalar_one_or_none()
        
        if not conversation:
            return {"exists": False}
        
        # 统计消息数量
        msg_result = await self.db.execute(
            select(Message).where(Message.conversation_id == conversation.id)
        )
        messages = msg_result.scalars().all()
        
        user_count = sum(1 for msg in messages if msg.role == "user")
        assistant_count = sum(1 for msg in messages if msg.role == "assistant")
        
        return {
            "exists": True,
            "total_messages": len(messages),
            "user_messages": user_count,
            "assistant_messages": assistant_count,
            "created_at": conversation.created_at.isoformat() if conversation.created_at else None,
            "updated_at": conversation.updated_at.isoformat() if conversation.updated_at else None
        }
