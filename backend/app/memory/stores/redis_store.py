"""Redis 记忆存储 - 生产环境短期记忆后端

数据结构: Redis List（RPUSH 追加 / LRANGE 读取，保证顺序）
TTL: 每次写入刷新过期时间，实现"活跃会话不淘汰"
特性: 多实例共享，无需会话粘性依赖
"""
import json
from typing import Dict, List, Optional

import structlog
from redis.asyncio import Redis

from app.core.config import settings
from app.memory.stores.base import MemoryStore

logger = structlog.get_logger(__name__)

# 消息编码分隔（避免 JSON 转义开销，同时保持可读）
_SEP = "\u0001"


class RedisMemoryStore(MemoryStore):
    def __init__(self, url: Optional[str] = None, ttl: Optional[int] = None):
        self.url = url or settings.REDIS_URL
        self.ttl = ttl or settings.MEMORY_SHORT_TERM_TTL
        self._client: Redis | None = None

    def _get_client(self) -> Redis:
        if self._client is None:
            self._client = Redis.from_url(
                self.url,
                decode_responses=True,
                # 快速失败：无 Redis 时避免长阻塞（auto 降级探测用）
                socket_connect_timeout=1,
                socket_timeout=2,
            )
        return self._client

    async def add_message(self, key: str, role: str, content: str) -> None:
        client = self._get_client()
        payload = json.dumps({"role": role, "content": content}, ensure_ascii=False)
        await client.rpush(key, payload)
        await client.expire(key, self.ttl)

    def _decode_item(self, raw: str) -> Optional[Dict[str, str]]:
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            # 兼容旧格式: "role\u0001content"
            if _SEP in raw:
                role, content = raw.split(_SEP, 1)
                return {"role": role, "content": content}
            logger.warning("memory.redis.unparsable_item")
            return None

    async def get_messages(self, key: str) -> List[Dict[str, str]]:
        client = self._get_client()
        raw_items = await client.lrange(key, 0, -1)
        return [msg for msg in (self._decode_item(raw) for raw in raw_items) if msg]

    async def get_message_count(self, key: str) -> int:
        client = self._get_client()
        return await client.llen(key)

    async def trim(self, key: str, keep: int) -> List[Dict[str, str]]:
        client = self._get_client()
        count = await client.llen(key)
        if count <= keep:
            return []
        remove_count = count - keep
        raw_items = await client.lpop(key, remove_count)
        if not raw_items:
            return []
        return [msg for msg in (self._decode_item(raw) for raw in raw_items) if msg]

    async def clear(self, key: str) -> None:
        client = self._get_client()
        await client.delete(key)

    async def ping(self) -> bool:
        try:
            client = self._get_client()
            return bool(await client.ping())
        except Exception:
            return False

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except AttributeError:
                await self._client.close()
            except Exception:
                logger.warning("memory.redis.close.failed")
            self._client = None
