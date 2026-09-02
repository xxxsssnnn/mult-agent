"""记忆存储工厂 - 按配置创建后端，支持自动降级"""
import structlog

from app.core.config import settings
from app.memory.stores.base import MemoryStore
from app.memory.stores.in_memory_store import InMemoryMemoryStore
from app.memory.stores.redis_store import RedisMemoryStore

logger = structlog.get_logger(__name__)


async def create_memory_store(logger_override=None) -> MemoryStore:
    """根据配置创建短期记忆存储后端。

    MEMORY_SHORT_TERM_STORE:
      - memory: 强制内存实现（本地开发/测试）
      - redis:  强制 Redis，不可用则抛出异常（不允许静默降级）
      - auto:   优先 Redis，探测失败自动降级内存并告警（默认）
    """
    log = logger_override or logger
    store_type = settings.MEMORY_SHORT_TERM_STORE

    if store_type == "memory":
        return InMemoryMemoryStore()

    redis_store = RedisMemoryStore()

    if store_type == "redis":
        if not await redis_store.ping():
            await redis_store.close()
            raise RuntimeError(
                "MEMORY_SHORT_TERM_STORE=redis but Redis is unavailable at "
                f"{settings.REDIS_URL}"
            )
        log.info("memory.store.redis.selected")
        return redis_store

    # auto 模式：探测 Redis，失败降级
    try:
        ok = await redis_store.ping()
    except Exception:
        ok = False

    if ok:
        log.info("memory.store.redis.selected")
        return redis_store

    await redis_store.close()
    log.warning(
        "memory.store.redis_unavailable_fallback_in_memory",
        redis_url=settings.REDIS_URL,
        hint="短期记忆将不跨实例共享；启动 Redis 或设置 MEMORY_SHORT_TERM_STORE=memory 可消除告警",
    )
    return InMemoryMemoryStore()
