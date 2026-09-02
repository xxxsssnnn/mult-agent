"""Celery 应用实例

docker-compose 中 worker 命令为 `celery -A app.celery_app worker`，
此文件即该入口。任务自动发现自 app.tasks 包。
"""
from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "multi_agent",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    # 任务确认语义：worker 挂掉后任务可重新投递，避免记忆数据丢失
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # 保证内存中可见性/执行顺序，记忆任务需要有序
    worker_prefetch_multiplier=1,
    task_ignore_result=False,
)

celery_app.autodiscover_tasks(["app.tasks"])
