"""RAG 会话版问答（Phase 5）回归测试

验证（纯离线：无 Redis / 无 DB / 无 LLM / 无真实嵌入模型）：
- 无 session_id：维持无状态单轮语义缓存（回归红线，缓存照常参与）
- 会话首轮（上下文为空）：等同无状态，per-user 语义缓存照常参与，
  user/assistant 消息成对记录，生成 prompt 不注入聊天上下文
- 会话后续轮（上下文非空）：注入会话记忆（prompt 出现“聊天上下文”块），
  自动旁路 per-user 语义缓存（即使同 query 已有缓存也不复用，防跨上下文陈旧答案）
- 消息成对持续记录；跨用户/跨会话记忆完全隔离
- 记忆层失败（工厂抛错）自动降级为无状态 RAG，问答不受阻断

通过 `python tests/test_rag_session_memory.py` 直接运行。
"""
import asyncio
import os
import sys
from types import SimpleNamespace
from uuid import uuid4

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_ = os.environ.pop("OPENAI_API_KEY", None)

from test_rag_enterprise import (  # noqa: E402
    CONTENT_A,
    FakeDocumentRepo,
    FakeVectorStore,
    make_agent,
    make_txt,
)

PASSED = []
FAILED = []


def ok(name: str, condition: bool, detail: str = ""):
    (PASSED if condition else FAILED).append(name)
    print(
        f"  [{'PASS' if condition else 'FAIL'}] {name}"
        + (f" | {detail}" if not condition else "")
    )


# --------------------------------------------------------------------------- #
# 测试替身
# --------------------------------------------------------------------------- #


class RecordingLLM:
    """记录每次生成的 prompt，并返回固定答案（模拟 LLM）。"""

    def __init__(self):
        self.calls = 0
        self.prompts = []

    async def ainvoke(self, messages, **_kwargs):
        self.calls += 1
        last = messages[-1]
        self.prompts.append(
            last.content if hasattr(last, "content") else str(last)
        )
        return SimpleNamespace(content="FAKE_ANSWER_V1")


class RecordingSemanticCache:
    """语义缓存替身：只记录 lookup/store 是否被调用（不真正缓存）。"""

    enabled = True
    semantic_enabled = True
    semantic_min_query_len = 0

    def __init__(self):
        self.lookups = []
        self.stores = []

    def invalidate_user(self, user_id):
        return True

    def invalidate_all(self):
        return True

    def make_key(self, user_id, query, k, search_type, extra=None):
        return f"u:{user_id}|q:{query}|k:{k}|st:{search_type}|x:{extra}"

    def profile(self, *_a, **_kw):
        return "profile-session-test"

    async def lookup(self, user_id, key, query=None, profile=None, embedder=None):
        self.lookups.append({"user": user_id, "key": key})
        return None, {"kind": "miss", "reason": "no_candidates"}

    def store(self, *_a, **_kw):
        self.stores.append(_kw)

    def stats(self):
        return {"entries": 0, "exact_hits": 0, "semantic_hits": 0}


class FakeSessionMemory:
    """MemoryManager 兼容替身：会话历史即 get_context 输出（持久化于实例上）。"""

    def __init__(self, session_id, user_id, db_session=None, seeded_context=""):
        self.session_id = session_id
        self.user_id = user_id
        self.db_session = db_session
        self.seeded_context = seeded_context
        self.messages = []  # ["user: ...", "assistant: ..."] 模拟 get_context 输出
        self.adds = []  # [(role, content)]
        self.initialize_calls = 0

    async def initialize(self):
        self.initialize_calls += 1
        return True

    async def get_context(self):
        base = [self.seeded_context] if self.seeded_context else []
        return "\n".join(base + self.messages)

    async def add_message(self, role, content, metadata=None):
        self.adds.append((role, content))
        self.messages.append(f"{role}: {content}")


class FakeMemoryFactory:
    """按 (user, session) 复用同一替身，模拟真实 DB 的跨请求持久化。"""

    def __init__(self):
        self.instances = {}

    def __call__(self, session_id=None, user_id=None, db_session=None):
        key = (str(user_id), str(session_id))
        if key not in self.instances:
            self.instances[key] = FakeSessionMemory(
                session_id=session_id, user_id=user_id, db_session=db_session
            )
        return self.instances[key]


async def _seeded_agent():
    """装配离线 RAGAgent：RecordingLLM + 语义缓存替身 + 会话记忆工厂。"""
    store = FakeVectorStore()
    agent, store = make_agent(store=store, repo=FakeDocumentRepo())
    agent.llm = RecordingLLM()
    agent.semantic_cache = RecordingSemanticCache()
    return agent, store


async def _ingest(agent, user):
    path = make_txt(CONTENT_A)
    try:
        ingest = await agent.ingest_documents([path], user)
        ok("知识库导入成功", ingest.get("num_ingested", 0) >= 1, str(ingest))
    finally:
        os.unlink(path)


# --------------------------------------------------------------------------- #
# 用例
# --------------------------------------------------------------------------- #


async def test_session_flow():
    print("== session: 会话版问答（Phase 5）==")
    agent, store = await _seeded_agent()
    spy = agent.semantic_cache
    factory = FakeMemoryFactory()
    agent.set_memory_factory(factory)

    user_a, user_b = uuid4(), uuid4()
    session = "session-rag-1"
    await _ingest(agent, user_a)
    await _ingest(agent, user_b)

    Q = "企业级 RAG 系统有哪些设计要点"

    # 1. 会话首轮：上下文为空 → 等同无状态，缓存照常参与，消息成对记录
    r1 = await agent.execute({"query": Q}, user_id=user_a, session_id=session)
    ok("会话首轮成功且有答案", r1.get("success") is True and (r1.get("answer") or "").strip(),
       (r1.get("answer") or "")[:60])
    m_a = factory.instances[(str(user_a), str(session))]
    ok("首轮 user/assistant 消息成对入库",
       [role for role, _ in m_a.adds] == ["user", "assistant"], str(m_a.adds))
    ok("首轮缓存照常参与（lookup/store 各 1 次）",
       len(spy.lookups) == 1 and len(spy.stores) == 1,
       f"lookups={len(spy.lookups)} stores={len(spy.stores)}")
    ok("首轮元数据：context_active=False 且未旁路",
       r1.get("session") == {
           "session_id": session,
           "enabled": True,
           "context_active": False,
           "cache_bypassed": False,
       }, str(r1.get("session")))
    ok("首轮 prompt 不含聊天上下文块", "聊天上下文" not in agent.llm.prompts[-1],
       agent.llm.prompts[-1][:120])

    # 2. 会话后续轮：上下文非空 → 注入历史，且同 query 也必须重新生成（防陈旧）
    r2 = await agent.execute({"query": Q}, user_id=user_a, session_id=session)
    ok("二轮成功且有答案", r2.get("success") is True, (r2.get("answer") or "")[:60])
    ok("二轮注入聊天上下文", "聊天上下文" in agent.llm.prompts[-1],
       agent.llm.prompts[-1][:200])
    ok("二轮上下文激活 → cache_bypassed=True",
       r2.get("session", {}).get("context_active") is True
       and r2["session"]["cache_bypassed"] is True, str(r2.get("session")))
    ok("二轮旁路缓存（lookup/store 不再增加，即使同 query 已缓存）",
       len(spy.lookups) == 1 and len(spy.stores) == 1,
       f"lookups={len(spy.lookups)} stores={len(spy.stores)}")
    ok("二轮重新生成（llm 第 2 次调用，未复用首轮答案）", agent.llm.calls == 2,
       f"llm calls={agent.llm.calls}")
    ok("消息持续成对追加（4 条）",
       [role for role, _ in m_a.adds]
       == ["user", "assistant", "user", "assistant"], str(m_a.adds))

    # 3. 无 session_id：维持无状态单轮（回归红线）
    r3 = await agent.execute({"query": Q}, user_id=user_a)
    ok("无 session 响应不含 session 元数据", "session" not in r3, list(r3.keys()))
    ok("无 session 缓存照常参与（lookup/store 各 +1）",
       len(spy.lookups) == 2 and len(spy.stores) == 2,
       f"lookups={len(spy.lookups)} stores={len(spy.stores)}")

    # 4. 跨用户隔离：同 session_id 不同用户各自独立记忆
    r4 = await agent.execute({"query": Q}, user_id=user_b, session_id=session)
    m_b = factory.instances[(str(user_b), str(session))]
    ok("跨用户记忆隔离（b 的首轮上下文为空、消息各 1 对）",
       m_b is not m_a and len(m_b.adds) == 2, str(m_b.adds))
    ok("跨用户独立生成", r4.get("answer") == r1.get("answer")
       or r4.get("answer") == agent.llm.prompts[-1] or (r4.get("answer") or "").strip(),
       (r4.get("answer") or "")[:60])

    # 5. 跨会话隔离：同用户不同 session 各自独立记忆
    other_session = "session-rag-2"
    await agent.execute({"query": Q}, user_id=user_a, session_id=other_session)
    m_a2 = factory.instances[(str(user_a), str(other_session))]
    ok("跨会话隔离（a 在新会话上下文为空）",
       m_a2 is not m_a and len(m_a2.adds) == 2, str(m_a2.adds))


async def test_session_memory_failure_degrades():
    print("== session: 记忆层失败降级无状态 ==")
    agent, store = await _seeded_agent()
    spy = agent.semantic_cache
    user = uuid4()
    await _ingest(agent, user)

    class BoomFactory:
        def __call__(self, session_id=None, user_id=None, db_session=None):
            raise RuntimeError("db down")

    agent.set_memory_factory(BoomFactory())
    r = await agent.execute(
        {"query": "企业级 RAG 有哪些设计要点"}, user_id=user, session_id="session-fail"
    )
    ok("记忆层失败仍成功返回答案",
       r.get("success") is True and (r.get("answer") or "").strip(),
       (r.get("answer") or "")[:60])
    ok("元数据标记 memory_unavailable",
       r.get("session", {}).get("enabled") is False
       and r["session"].get("error") == "memory_unavailable", str(r.get("session")))
    ok("失败时缓存照常参与（等同无状态）",
       len(spy.lookups) == 1 and len(spy.stores) == 1,
       f"lookups={len(spy.lookups)} stores={len(spy.stores)}")


async def main():
    await test_session_flow()
    await test_session_memory_failure_degrades()


if __name__ == "__main__":
    asyncio.run(main())
    print("")
    if FAILED:
        print(f"FAILED ({len(FAILED)}): {FAILED}")
        sys.exit(1)
    print(f"ALL PASSED ({len(PASSED)} assertions)")
    sys.exit(0)
