"""启发式记忆提取质量回归测试（无 LLM 降级路径）

验证：
1. 一次性指令/问题不被提取为长期记忆（避免记忆污染）
2. 真实偏好/事实仍被正确提取（不误伤）
3. 提取条目字段合法

通过 `python tests/test_memory_extractor_heuristic.py` 直接运行。
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["OPENAI_API_KEY"] = ""

from app.memory.extractor import MemoryExtractor  # noqa: E402

PASSED = []
FAILED = []


def ok(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f" | {detail}" if not condition else ""))


def extract_one(content):
    return MemoryExtractor._extract_heuristic([{"role": "user", "content": content}])


# ---------- 指令/问题不提取 ----------

def test_command_not_extracted():
    for text in (
        "请使用 Redis 做缓存",          # 含 fact marker，但是一次性指令
        "帮我解释 Python 装饰器",        # 含 fact marker
        "我希望你能帮我部署项目",        # 含 preference 词"希望"
    ):
        got = extract_one(text)
        ok(f"指令不提取: {text[:20]}", got == [], f"got={got}")


def test_question_not_extracted():
    for text in (
        "为什么 Python 比 Java 快",     # 问题，含 fact marker
        "如何优化 PostgreSQL 查询",     # 问题
    ):
        got = extract_one(text)
        ok(f"问题不提取: {text[:20]}", got == [], f"got={got}")


# ---------- 真实偏好/事实仍提取 ----------

def test_real_preference_extracted():
    got = extract_one("我喜欢用空格缩进而不是 Tab")
    ok("真实偏好仍提取", len(got) == 1 and got[0]["memory_type"] == "preference",
       f"got={got}")


def test_real_fact_extracted():
    got = extract_one("项目采用 FastAPI 和 PostgreSQL 作为技术栈")
    ok("真实事实仍提取", len(got) == 1 and got[0]["memory_type"] == "fact",
       f"got={got}")


def test_deployment_fact_extracted():
    got = extract_one("部署环境是 Linux 服务器")
    ok("部署事实仍提取", len(got) == 1 and got[0]["memory_type"] == "fact",
       f"got={got}")


# ---------- 字段合法 ----------

def test_fields_valid():
    got = extract_one("我习惯用 pytest 写测试")
    if not got:
        ok("字段合法：有提取结果", False, "empty")
        return
    e = got[0]
    ok("字段合法",
       e["memory_type"] in ("fact", "preference", "procedural")
       and e["content"] and 0 <= e["confidence"] <= 1,
       f"got={e}")


if __name__ == "__main__":
    test_command_not_extracted()
    test_question_not_extracted()
    test_real_preference_extracted()
    test_real_fact_extracted()
    test_deployment_fact_extracted()
    test_fields_valid()
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for f in FAILED:
        print(f"  FAILED: {f}")
    sys.exit(1 if FAILED else 0)
