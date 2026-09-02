"""内存记忆存储 - 默认/降级实现

进程内字典存储，重启即失效。用于：
- 未配置 Redis 的本地开发环境
- Redis 不可用时的自动降级
"""
from typing import Dict, List

from app.memory.stores.base import MemoryStore


class InMemoryMemoryStore(MemoryStore):
    def __init__(self):
        self._data: Dict[str, List[Dict[str, str]]] = {}

    async def add_message(self, key: str, role: str, content: str) -> None:
        self._data.setdefault(key, []).append({"role": role, "content": content})

    async def get_messages(self, key: str) -> List[Dict[str, str]]:
        return list(self._data.get(key, []))

    async def get_message_count(self, key: str) -> int:
        return len(self._data.get(key, []))

    async def trim(self, key: str, keep: int) -> List[Dict[str, str]]:
        items = self._data.get(key, [])
        if len(items) <= keep:
            return []
        evicted = items[: len(items) - keep]
        self._data[key] = items[len(items) - keep:]
        return evicted

    async def clear(self, key: str) -> None:
        self._data.pop(key, None)

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        self._data.clear()
