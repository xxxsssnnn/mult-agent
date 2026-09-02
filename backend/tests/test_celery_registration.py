"""Celery worker/beat 任务接线回归测试（独立运行：python tests/test_celery_registration.py）

覆盖：
- worker 启动路径（loader.import_default_modules，等价 `celery -A app.celery_app worker`）
  必须注册 memory.* 任务——此前任务只被 API 进程内的 manager.py 延迟 import，
  worker 冷启动无法处理 beat 投递的定时任务
- beat_schedule 正确指向已注册任务，周期与配置一致
- 任务可靠性属性（acks_late / retry）不回退
"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.core.celery_app import celery_app  # noqa: E402
from app.core.config import settings  # noqa: E402

# 模拟 worker 启动：include + autodiscover 的默认模块导入路径，
# 与 `celery -A app.celery_app worker` 启动时的注册行为一致。
# 必须在任何断言前执行，保证后续全部检查基于 worker 视角。
celery_app.loader.import_default_modules()

PASSED = []
FAILED = []


def check(name, ok):
    if ok:
        PASSED.append(name)
    else:
        FAILED.append(name)
    print(("  [PASS] " if ok else "  [FAIL] ") + name)


def _registered_names():
    return set(celery_app.tasks.keys())


def test_worker_registers_memory_tasks():
    names = _registered_names()
    check("memory.consolidate 已注册", "memory.consolidate" in names)
    check("memory.decay_memories 已注册", "memory.decay_memories" in names)


def test_beat_schedule_matches_registered_task():
    schedule = celery_app.conf.beat_schedule
    entry = schedule.get("decay-memories-periodically")
    check("beat_schedule 含定时衰减条目", entry is not None)
    if entry is None:
        return
    check("beat 指向的任务名已注册", entry["task"] in _registered_names())
    check(
        "beat 周期与 MEMORY_DECAY_INTERVAL_SECONDS 一致",
        entry["schedule"] == settings.MEMORY_DECAY_INTERVAL_SECONDS,
    )


def test_decay_task_reliability_attrs():
    task = celery_app.tasks.get("memory.decay_memories")
    check("decay 任务对象存在", task is not None)
    if task is None:
        return
    check("decay 任务 acks_late=True（worker 挂掉可重投）", task.acks_late is True)
    check("decay 任务 max_retries>=3", (task.max_retries or 0) >= 3)
    check("decay 任务 default_retry_delay 已设", (task.default_retry_delay or 0) > 0)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    sys.exit(1 if FAILED else 0)
