"""记忆并发与幂等回归测试（独立运行：python tests/test_memory_concurrency.py）

覆盖四类真实风险：
1. 并发 pending 合并不丢失（lost update 防护）
2. claim 原子领取：并发触发 consolidation 时只有一个请求真正执行
3. consolidation 幂等：同一批次重复整合不产生重复 event 条目
4. consolidation 失败回队：批次不因处理失败而丢失

使用独立临时 SQLite 库，不污染本地 multi_agent.db。
"""
import asyncio
import os
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

TEST_DB = "./_test_concurrency.db"
for f in (TEST_DB,):
    if os.path.exists(f):
        os.remove(f)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB}"
os.environ["OPENAI_API_KEY"] = ""
os.environ["MEMORY_CONSOLIDATION_ENABLED"] = "true"
os.environ["MEMORY_CONSOLIDATION_BATCH_SIZE"] = "5"
os.environ["MEMORY_SHORT_TERM_WINDOW_SIZE"] = "5"

from unittest.mock import patch  # noqa: E402

from sqlalchemy import select  # noqa: E402

# 触发全部模型注册（含 users 表）
from app.main import app  # noqa: E402,F401
from app.core.database import AsyncSessionLocal, Base, engine  # noqa: E402
from app.memory.consolidation import consolidate_memory  # noqa: E402
from app.memory.manager import MemoryManager  # noqa: E402
from app.memory.persistence import MemoryPersistence  # noqa: E402
from app.models.memory_entry import MemoryEntry  # noqa: E402

PASSED = []
FAILED = []


def check(name, ok, detail=""):
    if ok:
        PASSED.append(name)
    else:
        FAILED.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" | {detail}" if not ok else ""))


async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def fresh_conversation(prefix="conc"):
    sid = f"{prefix}_{uuid.uuid4().hex[:8]}"
    db = AsyncSessionLocal()
    try:
        await MemoryPersistence(db).save_conversation(sid, None)
    finally:
        await db.close()
    return sid


async def test_concurrent_save_no_loss():
    print("[1] 并发 pending 合并不丢失")
    sid = await fresh_conversation()
    N = 10

    async def one(i):
        db = AsyncSessionLocal()
        try:
            await MemoryPersistence(db).save_pending_consolidation(
                sid, [{"role": "user", "content": f"msg-{i}"}]
            )
        finally:
            await db.close()

    await asyncio.gather(*[one(i) for i in range(N)])

    db = AsyncSessionLocal()
    try:
        batch = await MemoryPersistence(db).claim_pending_consolidation(sid)
    finally:
        await db.close()
    contents = sorted(m["content"] for m in batch)
    expected = sorted(f"msg-{i}" for i in range(N))
    check("并发写入全部保留（无 lost update）", contents == expected, f"got={contents}")

    db = AsyncSessionLocal()
    try:
        again = await MemoryPersistence(db).claim_pending_consolidation(sid)
    finally:
        await db.close()
    check("claim 后批次已清空", again == [], f"got={again}")


async def test_claim_only_one_wins():
    print("[2] claim 原子领取：并发触发仅一个成功")
    sid = await fresh_conversation()
    db = AsyncSessionLocal()
    try:
        await MemoryPersistence(db).save_pending_consolidation(
            sid, [{"role": "user", "content": f"m{i}"} for i in range(5)]
        )
    finally:
        await db.close()

    async def claim():
        db = AsyncSessionLocal()
        try:
            return await MemoryPersistence(db).claim_pending_consolidation(sid)
        finally:
            await db.close()

    results = await asyncio.gather(*[claim() for _ in range(3)])
    non_empty = [r for r in results if r]
    check("仅一个请求领取到批次", len(non_empty) == 1, f"non_empty={len(non_empty)}")
    check("领取到完整批次", len(non_empty[0]) == 5 if non_empty else False,
          f"len={len(non_empty[0]) if non_empty else 0}")


async def test_consolidate_idempotent():
    print("[3] consolidation 幂等：重复整合不产生重复 event")
    sid = await fresh_conversation("conc")
    msgs = [{"role": "user", "content": f"hello-{i}"} for i in range(3)]
    await consolidate_memory(sid, None, msgs)
    await consolidate_memory(sid, None, msgs)

    db = AsyncSessionLocal()
    try:
        result = await db.execute(
            select(MemoryEntry).where(
                MemoryEntry.session_id == sid,
                MemoryEntry.memory_type == "event",
            )
        )
        events = list(result.scalars().all())
    finally:
        await db.close()
    check("重复整合 event 条数不翻倍", len(events) == 3, f"events={len(events)}")


async def test_failed_consolidation_requeued():
    print("[4] consolidation 失败回队：批次不丢失")
    sid = await fresh_conversation()

    async def boom(session_id, user_id, messages):
        raise RuntimeError("boom")

    with patch("app.memory.consolidation.consolidate_memory", side_effect=boom):
        db = AsyncSessionLocal()
        mm = MemoryManager(session_id=sid, db_session=db)
        await mm.initialize()
        for i in range(5):
            await mm.add_message("user", f"boom-{i}")
        await db.close()

    db = AsyncSessionLocal()
    try:
        batch = await MemoryPersistence(db).claim_pending_consolidation(sid)
    finally:
        await db.close()
    check("失败批次回队可再次领取", len(batch) == 5, f"len={len(batch)}")


async def run():
    await setup_db()
    await test_concurrent_save_no_loss()
    await test_claim_only_one_wins()
    await test_consolidate_idempotent()
    await test_failed_consolidation_requeued()


if __name__ == "__main__":
    asyncio.run(run())
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for f in (TEST_DB,):
        if os.path.exists(f):
            os.remove(f)
    sys.exit(1 if FAILED else 0)
