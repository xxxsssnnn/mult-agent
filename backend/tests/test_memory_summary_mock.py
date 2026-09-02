"""LongTermMemory mock 模式（无 LLM）摘要质量回归测试

验证四件事：
1. mock 摘要包含消息内容（而非 "[Mock Summary]..." 占位符）
2. 增量摘要保留旧摘要（set_summary 后 add_message 不再覆盖为占位符）
3. 摘要受 max_summary_length 限制
4. consolidation.build_session_summary 增量拼接旧+新批次

通过 `python tests/test_memory_summary_mock.py` 直接运行。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["OPENAI_API_KEY"] = ""
os.environ["MEMORY_LONG_TERM_MAX_SUMMARY_LENGTH"] = "500"

from app.memory.consolidation import build_session_summary  # noqa: E402
from app.memory.long_term import LongTermMemory  # noqa: E402

PASSED = []
FAILED = []


def ok(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f" | {detail}" if not condition else ""))


async def test_mock_summary_contains_content():
    m = LongTermMemory()
    await m.add_message("user", "我想学习Python编程")
    await m.add_message("assistant", "Python 适合初学者")
    s = await m.get_summary()
    ok("摘要包含消息内容", "python" in s.lower() and "学习" in s, f"got={s}")
    ok("摘要不是占位符", "[Mock Summary]" not in s, f"got={s}")


async def test_mock_summary_incremental():
    m = LongTermMemory()
    await m.set_summary("旧摘要：之前聊过部署")
    await m.add_message("user", "现在聊数据库")
    s = await m.get_summary()
    ok("旧摘要保留（不被覆盖）", s.startswith("旧摘要"), f"got={s}")
    ok("新内容追加到摘要", "数据库" in s, f"got={s}")


async def test_summary_respects_max_length():
    m = LongTermMemory(max_summary_length=50)
    for i in range(5):
        await m.add_message("user", f"这是第{i}条很长很长的消息内容用于测试截断行为")
    s = await m.get_summary()
    ok("摘要长度不超过上限", len(s) <= 50, f"len={len(s)}")
    ok("摘要仍有实际内容", len(s) > 0, f"len={len(s)}")


async def test_build_session_summary_incremental():
    s = await build_session_summary(
        "旧摘要", [{"role": "user", "content": "新的关键信息"}]
    )
    ok("增量摘要含旧内容", "旧摘要" in s, f"got={s}")
    ok("增量摘要含新内容", "新的关键信息" in s, f"got={s}")


async def run():
    await test_mock_summary_contains_content()
    await test_mock_summary_incremental()
    await test_summary_respects_max_length()
    await test_build_session_summary_incremental()


if __name__ == "__main__":
    asyncio.run(run())
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for f in FAILED:
        print(f"  FAILED: {f}")
    sys.exit(1 if FAILED else 0)
