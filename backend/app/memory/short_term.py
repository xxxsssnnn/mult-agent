"""短期记忆模块 - 基于滑动窗口 + 可插拔存储后端

企业级改造（Phase 1）:
- 存储层抽象为 MemoryStore（Redis / 内存双实现，多实例共享）
- 保持"转移而非丢弃"策略：窗口溢出的消息被捕获并返回给上层
- 接口向后兼容：window_size / memory.k / add_message 返回 LangChain 消息
"""

from typing import Dict, List, Optional

from langchain.schema import BaseMessage, HumanMessage, AIMessage
import structlog

from app.memory.stores import MemoryStore
from app.memory.stores.in_memory_store import InMemoryMemoryStore

logger = structlog.get_logger(__name__)


class _MemoryCompat:
    """兼容 shim：memory.k 读写映射到 window_size（保持 LangChain 风格访问）"""

    def __init__(self, owner: "ShortTermMemory"):
        self._owner = owner

    @property
    def k(self) -> int:
        return self._owner.window_size

    @k.setter
    def k(self, value: int) -> None:
        self._owner.window_size = int(value)


class ShortTermMemory:
    """短期记忆类（改进版）

    使用滑动窗口管理最近对话，窗口溢出的消息通过返回值交给上层
    转移到长期记忆/持久化，避免语义丢失。
    """

    def __init__(
        self,
        window_size: int = 5,
        on_message_evict=None,
        store: Optional[MemoryStore] = None,
        namespace: str = "default",
        session_id: str = "default",
    ):
        """
        Args:
            window_size: 保留的最近对话轮数（每轮 user+assistant 两条消息）
            on_message_evict: 消息驱逐回调（保留兼容，当前由调用方处理返回值）
            store: 存储后端（默认内存；Redis 由 MemoryManager.initialize 注入）
            namespace: 记忆命名空间（user/team/session）
            session_id: 会话 ID
        """
        self.window_size = window_size
        self.on_message_evict = on_message_evict
        self.namespace = namespace
        self.session_id = session_id
        self._store: MemoryStore = store or InMemoryMemoryStore()
        self._compat = _MemoryCompat(self)

        logger.info(
            "Short-term memory initialized",
            window_size=window_size,
            store_type=type(self._store).__name__,
        )

    @property
    def memory(self) -> _MemoryCompat:
        return self._compat

    @property
    def store_key(self) -> str:
        return f"mem:st:{self.namespace}:{self.session_id}"

    async def set_store(self, store: MemoryStore) -> None:
        """运行时替换存储后端（仅切换引用，不触碰已有数据）"""
        old_store = self._store
        self._store = store
        if old_store is not store:
            await old_store.close()
        logger.info(
            "Short-term memory store replaced",
            store_type=type(store).__name__,
        )

    async def add_message(self, role: str, content: str) -> List[BaseMessage]:
        """添加消息到短期记忆。

        Returns:
            被移出窗口的消息列表（LangChain 消息对象），供上层转移到长期记忆
        """
        if role not in ("user", "assistant"):
            logger.warning("Unknown role, skipping message", role=role)
            return []

        await self._store.add_message(self.store_key, role, content)

        # 窗口裁剪：超过 window_size*2 条时移出最早的
        count = await self._store.get_message_count(self.store_key)
        keep = self.window_size * 2
        evicted = []
        if count > keep:
            evicted_dicts = await self._store.trim(self.store_key, keep)
            evicted = [
                self._to_langchain_msg(m["role"], m["content"])
                for m in evicted_dicts
            ]
            logger.info(
                "Messages evicted from short-term window",
                evicted_count=len(evicted),
                retained_count=keep,
            )

        logger.debug(
            "Message added to short-term memory",
            role=role,
            content_length=len(content),
            total_messages=count,
            evicted=len(evicted),
        )
        return evicted

    @staticmethod
    def _to_langchain_msg(role: str, content: str) -> BaseMessage:
        if role == "assistant":
            return AIMessage(content=content)
        return HumanMessage(content=content)

    async def get_messages(self) -> List[BaseMessage]:
        """获取短期记忆中的全部消息（LangChain 消息对象）"""
        dicts = await self._store.get_messages(self.store_key)
        return [self._to_langchain_msg(m["role"], m["content"]) for m in dicts]

    async def get_context_string(self) -> str:
        """获取格式化的上下文字符串"""
        dicts = await self._store.get_messages(self.store_key)
        lines = [f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}" for m in dicts]
        return "\n".join(lines)

    async def clear(self) -> None:
        """清空短期记忆"""
        await self._store.clear(self.store_key)
        logger.info("Short-term memory cleared")

    async def get_message_count(self) -> int:
        """获取当前消息数量"""
        return await self._store.get_message_count(self.store_key)

    async def to_dict_list(self) -> List[Dict[str, str]]:
        """将消息转换为字典列表格式 [{"role","content"}]"""
        return await self._store.get_messages(self.store_key)
