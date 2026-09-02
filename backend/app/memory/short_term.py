"""短期记忆模块 - 基于滑动窗口的智能记忆管理

改进版：避免语义丢失，采用"转移而非丢弃"策略
- 超出窗口的消息会被提取关键信息并转移到长期记忆
- 保证对话连贯性和完整性
"""

from typing import List, Dict, Optional, Callable
from langchain.memory import ConversationBufferWindowMemory
from langchain.schema import BaseMessage, HumanMessage, AIMessage
import structlog

logger = structlog.get_logger(__name__)


class ShortTermMemory:
    """
    短期记忆类（改进版）
    
    使用滑动窗口管理最近对话，但不会简单丢弃旧消息，
    而是通过回调机制通知上层进行关键信息提取和转移
    """
    
    def __init__(self, window_size: int = 5, on_message_evict=None):
        """
        初始化短期记忆
        
        Args:
            window_size: 保留的最近对话轮数（默认5轮）
            on_message_evict: 当消息被移出窗口时的回调函数
                             签名: async def callback(evicted_messages: List[BaseMessage]) -> None
                             用于将旧消息的关键信息转移到长期记忆
        """
        self.window_size = window_size
        self.on_message_evict = on_message_evict  # 消息驱逐回调
        self.evicted_messages_buffer = []  # 暂存被移出的消息，等待批量处理
        
        self.memory = ConversationBufferWindowMemory(
            k=window_size,
            return_messages=True,
            memory_key="chat_history"
        )
        logger.info("Short-term memory initialized", window_size=window_size)
    
    async def add_message(self, role: str, content: str) -> List[BaseMessage]:
        """
        添加消息到短期记忆
        
        Args:
            role: 消息角色 ('user' 或 'assistant')
            content: 消息内容
            
        Returns:
            被移出的消息列表（如果有的话），用于转移到长期记忆
        """
        if role == "user":
            message = HumanMessage(content=content)
        elif role == "assistant":
            message = AIMessage(content=content)
        else:
            logger.warning("Unknown role, skipping message", role=role)
            return []
        
        # 记录添加前的消息数量
        old_count = len(self.memory.chat_memory.messages)
        
        # 添加消息（LangChain会自动管理窗口大小）
        self.memory.chat_memory.add_message(message)
        
        # 检查是否有消息被移出窗口
        new_count = len(self.memory.chat_memory.messages)
        evicted_messages = []
        
        # 如果消息数量超过窗口限制，说明有消息被移除
        # LangChain的ConversationBufferWindowMemory会在内部自动移除
        # 我们需要检测并捕获这些被移除的消息
        if new_count > self.window_size * 2:  # *2 因为每轮对话有2条消息(user+assistant)
            # 获取当前所有消息
            all_messages = self.memory.chat_memory.messages
            # 保留最后 window_size*2 条消息
            kept_messages = all_messages[-(self.window_size * 2):]
            # 被移出的消息
            evicted_messages = all_messages[:-(self.window_size * 2)]
            
            # 重建内存中的消息列表（只保留需要的）
            self.memory.chat_memory.clear()
            for msg in kept_messages:
                self.memory.chat_memory.add_message(msg)
            
            logger.info(
                "Messages evicted from short-term window",
                evicted_count=len(evicted_messages),
                retained_count=len(kept_messages)
            )
        
        logger.debug(
            "Message added to short-term memory",
            role=role,
            content_length=len(content),
            total_messages=new_count,
            evicted=len(evicted_messages)
        )
        
        return evicted_messages
    
    async def get_messages(self) -> List[BaseMessage]:
        """
        获取所有短期记忆中的消息
        
        Returns:
            消息列表（LangChain BaseMessage对象）
        """
        messages = self.memory.chat_memory.messages
        logger.debug(
            "Retrieved short-term messages",
            count=len(messages)
        )
        return messages
    
    async def get_context_string(self) -> str:
        """
        获取格式化的上下文字符串
        
        Returns:
            格式化的对话历史字符串
        """
        # 使用LangChain内置方法获取格式化字符串
        context = self.memory.load_memory_variables({})
        chat_history = context.get("chat_history", [])
        
        # 转换为可读字符串
        lines = []
        for msg in chat_history:
            if isinstance(msg, HumanMessage):
                lines.append(f"User: {msg.content}")
            elif isinstance(msg, AIMessage):
                lines.append(f"Assistant: {msg.content}")
        
        return "\n".join(lines)
    
    async def clear(self) -> None:
        """清空短期记忆"""
        self.memory.clear()
        logger.info("Short-term memory cleared")
    
    def get_message_count(self) -> int:
        """获取当前消息数量"""
        return len(self.memory.chat_memory.messages)
    
    async def to_dict_list(self) -> List[Dict]:
        """
        将消息转换为字典列表格式
        
        Returns:
            字典列表，每个元素包含role和content
        """
        messages = await self.get_messages()
        result = []
        
        for msg in messages:
            if isinstance(msg, HumanMessage):
                result.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                result.append({"role": "assistant", "content": msg.content})
        
        return result
