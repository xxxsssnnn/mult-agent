"""记忆存储抽象接口"""
from abc import ABC, abstractmethod
from typing import Dict, List


class MemoryStore(ABC):
    """短期记忆存储抽象。

    键设计: mem:st:{namespace}:{session_id}
    值设计: 按时间顺序的消息列表 [{"role": "user|assistant", "content": "..."}]
    """

    @abstractmethod
    async def add_message(self, key: str, role: str, content: str) -> None:
        """追加一条消息（自动刷新 TTL）"""

    @abstractmethod
    async def get_messages(self, key: str) -> List[Dict[str, str]]:
        """按时间顺序返回全部消息"""

    @abstractmethod
    async def get_message_count(self, key: str) -> int:
        """返回消息条数"""

    @abstractmethod
    async def trim(self, key: str, keep: int) -> List[Dict[str, str]]:
        """裁剪到最近 keep 条消息，返回被移除的消息（按时间顺序）。

        用于滑动窗口的"转移而非丢弃"策略。
        """

    @abstractmethod
    async def clear(self, key: str) -> None:
        """清空指定键"""

    @abstractmethod
    async def ping(self) -> bool:
        """健康检查，用于启动时探测后端可用性"""

    @abstractmethod
    async def close(self) -> None:
        """释放资源（如 Redis 连接）"""
