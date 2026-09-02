"""长期记忆模块 - 基于摘要的历史对话记忆"""

from typing import List, Dict, Optional
from langchain.memory import ConversationSummaryMemory
from langchain.schema import BaseMessage, HumanMessage, AIMessage
from langchain_openai import ChatOpenAI
from app.core.config import settings
import structlog

logger = structlog.get_logger(__name__)


class LongTermMemory:
    """
    长期记忆类
    
    使用LLM对历史对话进行摘要，提取关键信息并持久化存储
    """
    
    def __init__(self, max_summary_length: int = 500):
        """
        初始化长期记忆
        
        Args:
            max_summary_length: 摘要最大长度（默认500字符）
        """
        self.max_summary_length = max_summary_length
        self.summary = ""
        
        # 初始化LLM用于生成摘要
        # 如果没有配置OpenAI API Key，使用mock模式
        if settings.OPENAI_API_KEY:
            self.llm = ChatOpenAI(
                model=settings.OPENAI_MODEL or "gpt-3.5-turbo",
                temperature=0.7,
                openai_api_key=settings.OPENAI_API_KEY
            )
            self.memory = ConversationSummaryMemory.from_llm(
                llm=self.llm,
                max_token_limit=2000,
                return_messages=True,
                memory_key="summary"
            )
            logger.info("Long-term memory initialized with LLM")
        else:
            # Mock模式：简单拼接最后几条消息作为摘要
            self.llm = None
            self.memory = None
            self.recent_messages = []
            logger.info("Long-term memory initialized in mock mode (no OpenAI API key)")
    
    async def add_message(self, role: str, content: str) -> None:
        """
        添加消息到长期记忆
        
        Args:
            role: 消息角色 ('user' 或 'assistant')
            content: 消息内容
        """
        if self.llm and self.memory:
            # 使用LangChain的摘要记忆
            if role == "user":
                message = HumanMessage(content=content)
            elif role == "assistant":
                message = AIMessage(content=content)
            else:
                logger.warning("Unknown role, skipping message", role=role)
                return
            
            self.memory.chat_memory.add_message(message)
            
            # 触发摘要更新（LangChain会自动处理）
            try:
                summary_context = self.memory.load_memory_variables({})
                new_summary = summary_context.get("summary", "")
                if new_summary:
                    self.summary = new_summary
            except Exception as e:
                logger.warning("Failed to update summary", error=str(e))
        else:
            # Mock模式：增量拼接摘要（含消息内容），受 max_summary_length 限制。
            # 旧实现把摘要覆盖为 "[Mock Summary] Recent N messages" 占位符：
            #  - 不包含任何消息内容，长期记忆对检索/展示无信息量
            #  - set_summary(旧摘要) 后 add_message 会覆盖旧摘要，增量累积失效
            self.recent_messages.append({"role": role, "content": content})
            # 只保留最近10条
            if len(self.recent_messages) > 10:
                self.recent_messages = self.recent_messages[-10:]

            line = f"{role}: {content}"
            self.summary = (self.summary + "\n" + line).strip()
            if len(self.summary) > self.max_summary_length:
                # 超长截断（保留头部，优先保留早期脉络）
                self.summary = self.summary[: self.max_summary_length]
        
        logger.debug(
            "Message added to long-term memory",
            role=role,
            content_length=len(content),
            summary_length=len(self.summary)
        )
    
    async def get_summary(self) -> str:
        """
        获取当前摘要
        
        Returns:
            对话历史摘要字符串
        """
        if self.llm and self.memory:
            try:
                context = self.memory.load_memory_variables({})
                self.summary = context.get("summary", self.summary)
            except Exception as e:
                logger.warning("Failed to load summary", error=str(e))
        
        logger.debug("Retrieved long-term summary", length=len(self.summary))
        return self.summary
    
    async def set_summary(self, summary: str) -> None:
        """
        手动设置摘要（用于从数据库加载）
        
        Args:
            summary: 摘要内容
        """
        self.summary = summary
        logger.info("Long-term summary set manually", length=len(summary))
    
    async def clear(self) -> None:
        """清空长期记忆"""
        if self.llm and self.memory:
            self.memory.clear()
        self.summary = ""
        self.recent_messages = []
        logger.info("Long-term memory cleared")
    
    def has_summary(self) -> bool:
        """检查是否有摘要"""
        return bool(self.summary.strip())
    
    async def to_dict(self) -> Dict:
        """
        将长期记忆转换为字典格式
        
        Returns:
            包含摘要和元数据的字典
        """
        return {
            "summary": await self.get_summary(),
            "type": "long_term",
            "has_llm": self.llm is not None
        }
