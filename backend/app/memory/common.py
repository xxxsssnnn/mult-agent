"""记忆模块公共工具"""

from uuid import UUID

import structlog

logger = structlog.get_logger(__name__)


def normalize_user_id(user_id):
    """把 Celery 传递的 str user_id 转回 UUID，无法解析则置 None。

    兼容三种输入：None、UUID 实例、UUID 字符串。
    """
    if user_id is None:
        return None
    if isinstance(user_id, UUID):
        return user_id
    try:
        return UUID(str(user_id))
    except (ValueError, TypeError, AttributeError):
        logger.warning("memory.invalid_user_id", user_id=str(user_id))
        return None
