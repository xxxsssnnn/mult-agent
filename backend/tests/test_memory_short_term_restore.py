"""短期记忆窗口恢复幂等性回归测试

验证：持久化 store（Redis 模式）下，每次请求都从 DB 恢复窗口不会
重复累积——store 已有窗口时跳过恢复。通过
`python tests/test_memory_short_term_restore.py` 直接运行。
"""
import asyncio
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

TEST_DB = "./_test_short_term.db"
for f in (TEST_DB,):
    if os.path.exists(f):
        os.remove(f)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB}"
os.environ["OPENAI_API_KEY"] = ""
os.environ["MEMORY_SHORT_TERM_STORE"] = "memory"
os.environ["MEMORY_SHORT_TERM_WINDOW_SIZE"] = "5"

from app.main import app  # noqa: E402,F401
from app.core.database import AsyncSessionLocal, Base, engine  # noqa: E402
from app.memory.manager import MemoryManager  # noqa: E402
from app.memory.stores.in_memory_store import InMemoryMemoryStore  # noqa: E402
from app.memory.stores.base import MemoryStore  # noqa: E402

PASSED = []
FAILED = []


def ok(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f" | {detail}" if not condition else ""))


class RecordingMemoryStore(MemoryStore):
    """包装内存 store，记录 add_message 调用次数，模拟持久化 store（跨请求存活）"""

    def __init__(self):
        self.inner = InMemoryMemoryStore()
        self.add_count = 0

    async def add_message(self, key, role, content):
        self.add_count += 1
        await self.inner.add_message(key, role, content)

    async def get_messages(self, key):
        return await self.inner.get_messages(key)

    async def get_message_count(self, key):
        return await self.inner.get_message_count(key)

    async def trim(self, key, keep):
        return await self.inner.trim(key, keep)

    async def clear(self, key):
        return await self.inner.clear(key)

    async def ping(self):
        return True

    async def close(self):
        return None


async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def test_no_duplicate_restore():
    sid = "short_" + uuid.uuid4().hex[:8]
    store = RecordingMemoryStore()

    # 第一次请求：恢复窗口（store 空）+ 新增消息
    db1 = AsyncSessionLocal()
    try:
        with patch(
            "app.memory.stores.create_memory_store", AsyncMock(return_value=store)
        ):
            mm1 = MemoryManager(session_id=sid, db_session=db1)
            await mm1.initialize()
            await mm1.add_message("user", "hi")
            await mm1.add_message("assistant", "hello")
    finally:
        await db1.close()

    first_adds = store.add_count
    ok("首个请求写入窗口（2 条新消息 + 恢复）", first_adds >= 2)

    # 第二次请求（模拟跨请求/跨 worker）：store 已有窗口，不应重复恢复
    db2 = AsyncSessionLocal()
    try:
        with patch(
            "app.memory.stores.create_memory_store", AsyncMock(return_value=store)
        ):
            mm2 = MemoryManager(session_id=sid, db_session=db2)
            await mm2.initialize()
    finally:
        await db2.close()

    ok("store 已有窗口时恢复被跳过（无重复累积）",
       store.add_count == first_adds, f"first={first_adds} now={store.add_count}")

    # 窗口内容应保持正确的最近消息顺序
    msgs = await store.get_messages(f"mem:st:session:{sid}")
    contents = [m["content"] for m in msgs]
    ok("窗口内容无重复且顺序正确",
       contents == ["hi", "hello"], f"got={contents}")


async def run():
    await setup_db()
    await test_no_duplicate_restore()


if __name__ == "__main__":
    asyncio.run(run())
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for f in (TEST_DB,):
        if os.path.exists(f):
            os.remove(f)
    sys.exit(1 if FAILED else 0)
