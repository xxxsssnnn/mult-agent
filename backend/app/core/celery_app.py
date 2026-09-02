"""Celery 应用实例

docker-compose 中 worker 命令为 `celery -A app.celery_app worker`，
此文件即该入口。

任务注册：celery_app 构造时 include 指定任务模块，
worker/beat 启动即导入注册（autodiscover 只找 <pkg>.tasks 模块，
无法发现 app.tasks.memory_tasks；若仅靠 API 进程内延迟 import，
worker 冷启动将收到未注册的 memory.* 任务）。
"""
from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "multi_agent",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.tasks.memory_tasks"],
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

# 定时调度（由 beat 服务执行，见 docker-compose 中 beat 服务）
celery_app.conf.beat_schedule = {
    "decay-memories-periodically": {
        "task": "memory.decay_memories",
        "schedule": settings.MEMORY_DECAY_INTERVAL_SECONDS,
    },
}

celery_app.autodiscover_tasks(["app.tasks"])
