"""Phase 2 记忆提取/更新/检索单元测试

不依赖真实数据库：
- 提取器：启发式模式（无 OPENAI_API_KEY）
- 更新策略：使用 FakeSession 模拟查询结果
- 检索器：使用 FakeSession 返回内存中的 MemoryEntry 实例

可通过 `python tests/test_memory_phase2.py` 直接运行，也可被 pytest 收集。
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta
from uuid import uuid4

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("OPENAI_API_KEY", "")

from app.memory.extractor import MemoryExtractor
from app.memory.updater import _content_similar, apply_memory_updates
from app.memory.retriever import MemoryRetriever, _freshness, _relevance, _tokenize
from app.models.memory_entry import MemoryEntry

PASSED = []


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    PASSED.append(condition)
    print(f"[{status}] {name}")
    if not condition:
        raise AssertionError(f"Test failed: {name}")


def run(coro):
    return asyncio.run(coro)


# ---------- Fake 数据库会话 ----------

class FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return FakeScalars(self._rows)


class FakeSession:
    """模拟 AsyncSession：execute 返回预设结果，add 记录新增对象"""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.added = []

    async def execute(self, stmt):
        # 有 update 类语句（检索命中统计）时无需特殊处理
        return FakeResult(self.rows)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def rollback(self):
        pass


def make_entry(content, memory_type="fact", strength=0.5, entity=None, updated_at=None):
    return MemoryEntry(
        id=uuid4(),
        user_id=None,
        session_id="s1",
        namespace="user",
        memory_type=memory_type,
        content=content,
        entity=entity,
        strength=strength,
        confidence=0.6,
        access_count=0,
        updated_at=updated_at or datetime.utcnow(),
    )


# ---------- 提取器 ----------

def test_extractor_heuristic_preference():
    extractor = MemoryExtractor()
    entries = run(extractor.extract([
        {"role": "user", "content": "我偏好用 pytest 来写单元测试"},
    ]))
    check("启发式提取出 preference", any(e["memory_type"] == "preference" for e in entries))
    check("启发式 confidence 合理", all(0 <= e["confidence"] <= 1 for e in entries))


def test_extractor_heuristic_fact():
    extractor = MemoryExtractor()
    entries = run(extractor.extract([
        {"role": "user", "content": "我们项目用的是 Python FastAPI，部署在云端服务器"},
    ]))
    check("启发式提取出 fact", any(e["memory_type"] == "fact" for e in entries))


def test_extractor_ignores_smalltalk():
    extractor = MemoryExtractor()
    entries = run(extractor.extract([
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好，有什么可以帮你？"},
    ]))
    check("寒暄消息不产生记忆条目", len(entries) == 0)


def test_extractor_parse_response_json():
    raw = '[{"memory_type": "fact", "entity": "db", "content": "使用 PostgreSQL", "confidence": 0.9}]'
    parsed = MemoryExtractor._parse_response(raw)
    check("解析普通 JSON 数组", len(parsed) == 1 and parsed[0]["entity"] == "db")

    wrapped = '```json\n[{"memory_type": "preference", "entity": "x", "content": "偏好 Rust", "confidence": 0.8}]\n```'
    parsed2 = MemoryExtractor._parse_response(wrapped)
    check("解析代码块包裹的 JSON", len(parsed2) == 1 and parsed2[0]["memory_type"] == "preference")

    bad = MemoryExtractor._parse_response("抱歉，我无法回答")
    check("非法响应返回空列表", bad == [])


# ---------- 更新策略 ----------

def test_updater_create_new():
    session = FakeSession(rows=None)
    applied = run(apply_memory_updates(session, None, "s1", [
        {"memory_type": "fact", "entity": "test_framework", "content": "使用 pytest", "confidence": 0.9},
    ]))
    check("无冲突时新建条目", len(session.added) == 1 and len(applied) == 1)
    check("新建条目初始强度 0.8", abs(session.added[0].strength - 0.8) < 1e-6)


def test_updater_strengthens_similar():
    existing = make_entry("使用 pytest 写测试", memory_type="preference", entity="test_framework", strength=0.5)
    session = FakeSession(rows=[existing])
    applied = run(apply_memory_updates(session, None, "s1", [
        {"memory_type": "preference", "entity": "test_framework", "content": "使用 pytest 写测试", "confidence": 0.9},
    ]))
    check("相同内容强化现有条目", len(applied) == 1 and applied[0] is existing)
    check("强度从 0.5 强化到 0.6", abs(existing.strength - 0.6) < 1e-6)


def test_updater_conflict_decays_old_and_adds_new():
    existing = make_entry("偏好使用 pytest", memory_type="preference", entity="test_framework", strength=0.8)
    session = FakeSession(rows=[existing])
    applied = run(apply_memory_updates(session, None, "s1", [
        {"memory_type": "preference", "entity": "test_framework", "content": "改用 unittest 了", "confidence": 0.9},
    ]))
    check("冲突时旧条目弱化", abs(existing.strength - 0.4) < 1e-6)
    check("冲突时新建新条目", len(session.added) == 1)
    check("新条目使用新内容", session.added[0].content == "改用 unittest 了")


def test_content_similar():
    check("完全相同", _content_similar("使用 pytest", "使用 pytest"))
    check("互相包含", _content_similar("使用 pytest 写测试", "使用 pytest"))
    check("不同内容判为冲突", not _content_similar("使用 pytest", "使用 unittest"))


# ---------- 检索器 ----------

def test_retriever_tokenize_and_relevance():
    tokens = _tokenize("pytest 测试框架")
    check("中文双字词与英文词被分词", "pytest" in tokens and "测试" in tokens)
    rel = _relevance("我们使用 pytest 做单元测试", tokens)
    check("查询词命中率大于 0", rel > 0)


def test_retriever_freshness():
    now = datetime.utcnow()
    check("新鲜记忆新鲜度接近 1", _freshness(now) > 0.9)
    old = now - timedelta(days=14)
    check("两周前记忆新鲜度低于 0.5", _freshness(old) < 0.5)


def test_retriever_ranks_by_query():
    candidate_a = make_entry("用户偏好使用 pytest", memory_type="preference", strength=0.5, entity="pytest")
    candidate_b = make_entry("项目部署在阿里云 ECS", memory_type="fact", strength=0.9, entity="deploy")
    session = FakeSession(rows=[candidate_a, candidate_b])
    results = run(MemoryRetriever(top_k=5).retrieve(session, None, query="pytest"))
    check("查询 pytest 时偏好条目排第一", results[0]["content"].startswith("用户偏好"))
    check("返回结果带 score 字段", "score" in results[0])


def test_retriever_ranks_by_strength_without_query():
    weak = make_entry("次要信息", strength=0.2)
    strong = make_entry("重要信息", strength=0.9)
    session = FakeSession(rows=[weak, strong])
    results = run(MemoryRetriever(top_k=5).retrieve(session, None, query=None))
    check("无查询时按记忆质量排序", results[0]["content"] == "重要信息")


def test_retriever_respects_limit():
    rows = [make_entry(f"记忆 {i}", strength=0.1 + i / 10) for i in range(10)]
    session = FakeSession(rows=rows)
    results = run(MemoryRetriever(top_k=10).retrieve(session, None, query=None, limit=3))
    check("limit 生效", len(results) == 3)


# ---------- 入口 ----------

def main():
    tests = [
        test_extractor_heuristic_preference,
        test_extractor_heuristic_fact,
        test_extractor_ignores_smalltalk,
        test_extractor_parse_response_json,
        test_updater_create_new,
        test_updater_strengthens_similar,
        test_updater_conflict_decays_old_and_adds_new,
        test_content_similar,
        test_retriever_tokenize_and_relevance,
        test_retriever_freshness,
        test_retriever_ranks_by_query,
        test_retriever_ranks_by_strength_without_query,
        test_retriever_respects_limit,
    ]
    for t in tests:
        t()
    total = len(PASSED)
    passed = sum(PASSED)
    print(f"\n===== {passed}/{total} checks passed =====")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
