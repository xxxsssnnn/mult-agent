"""记忆相关 Celery 任务

worker 启动：celery -A app.celery_app worker --loglevel=info
Windows 本地调试需追加 --pool=solo
"""
import asyncio
from typing import List, Optional

import structlog

from app.core.celery_app import celery_app
from app.core.config import settings
from app.memory.consolidation import consolidate_memory

logger = structlog.get_logger(__name__)


@celery_app.task(
    name="memory.consolidate",
    bind=True,
    max_retries=settings.MEMORY_PERSISTENCE_RETRY,
    default_retry_delay=30,
    acks_late=True,
)
def consolidate_memory_task(
    self,
    session_id: str,
    user_id: Optional[str] = None,
    messages: Optional[List[dict]] = None,
):
    """异步执行记忆整合（摘要 + 结构化记忆入库）

    Args:
        session_id: 会话 ID
        user_id: 用户 ID（可空）
        messages: 消息批次 [{"role": "...", "content": "..."}]
    """
    try:
        result = asyncio.run(consolidate_memory(session_id, user_id, messages or []))
        logger.info("memory.consolidate_task.ok", session_id=session_id, result=result)
        return result
    except Exception as exc:
        logger.exception("memory.consolidate_task.failed", session_id=session_id)
        raise self.retry(exc=exc)
