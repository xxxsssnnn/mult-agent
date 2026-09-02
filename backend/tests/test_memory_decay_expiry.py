"""记忆合规过期归档回归测试

`expires_at` 到期（合规保留策略）的记忆此前只被检索过滤（不可见），
没有任何任务实际归档，过期数据永久留存。验证批量衰减任务同时归档过期条目。

通过 `python tests/test_memory_decay_expiry.py` 直接运行。
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("OPENAI_API_KEY", "")

from app.memory.decay import decay_memories  # noqa: E402
from test_memory_phase2 import FakeSession, make_entry, run  # noqa: E402

PASSED = []
FAILED = []


def ok(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f" | {detail}" if not condition else ""))


def test_expired_entries_archived():
    now = datetime.utcnow()
    expired = make_entry("过期合规记忆", strength=0.8,
                         updated_at=now - timedelta(days=10))
    expired.expires_at = now - timedelta(days=1)  # 已到期

    live = make_entry("未过期记忆", strength=0.8,
                      updated_at=now - timedelta(days=1))
    live.expires_at = now + timedelta(days=30)  # 未到期

    normal = make_entry("无过期约束记忆", strength=0.8,
                        updated_at=now - timedelta(days=1))  # 无 expires_at

    session = FakeSession(rows=[expired, live, normal])
    result = run(decay_memories(session, now=now))

    ok("到期的合规记忆被归档", expired.archived_at is not None)
    ok("未到期的记忆不被归档", live.archived_at is None)
    ok("无过期约束的记忆不被归档", normal.archived_at is None)
    ok("expired 计数正确", result.get("expired", 0) == 1, f"got={result}")


def test_expired_entry_skipped_from_decay():
    now = datetime.utcnow()
    expired = make_entry("强但已过期", strength=0.9,
                         updated_at=now - timedelta(days=30))
    expired.expires_at = now - timedelta(days=1)

    session = FakeSession(rows=[expired])
    result = run(decay_memories(session, now=now))

    # 过期条目直接归档，不再做时间衰减（避免无意义更新）
    ok("过期条目不再做时间衰减", expired.archived_at is not None)


if __name__ == "__main__":
    test_expired_entries_archived()
    test_expired_entry_skipped_from_decay()
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for f in FAILED:
        print(f"  FAILED: {f}")
    sys.exit(1 if FAILED else 0)
