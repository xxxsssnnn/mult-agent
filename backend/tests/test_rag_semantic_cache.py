"""语义缓存升级回归测试（真·语义命中层）

验证（纯离线：无 Redis / 无 DB / 无 LLM / 无真实嵌入模型）：

单元层（SemanticCache.lookup / store）：
- 精确未命中时对同用户同管道 profile 条目做余弦比较，近似问法可命中（kind=semantic）
- 精确命中零嵌入开销（embedder 懒调用，仅未命中时触发）
- profile 隔离 / 租户隔离 / 阈值保护 / 维度不一致跳过 / TTL 清扫 / LRU / 统计

Agent 端到端：
- 首次问法 miss → 生成 → 回填（带 query+embedding）
- 改述问法 semantic 命中：跳过检索直接复用答案，annotation 含 kind/matched_query/score
- 原句精确重复 exact 命中；无关问法 below_threshold 未命中
- 不同用户完全隔离

通过 `python tests/test_rag_semantic_cache.py` 直接运行。
"""
import asyncio
import hashlib
import os
import sys
import tempfile
import time
from uuid import uuid4

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_ = os.environ.pop("OPENAI_API_KEY", None)

from app.rag.cache import SemanticCache, _cosine  # noqa: E402

PASSED = []
FAILED = []


def ok(name: str, condition: bool, detail: str = ""):
    (PASSED if condition else FAILED).append(name)
    print(
        f"  [{'PASS' if condition else 'FAIL'}] {name}"
        + (f" | {detail}" if not condition else "")
    )


# --------------------------------------------------------------------------- #
# 确定性嵌入（离线可用）：字符 + 二元组特征哈希 → L2 归一化向量
# 语义相近句（共享大量字符/二元组）余弦更高；无关句更低
# --------------------------------------------------------------------------- #

DIM = 64


def _embed(text: str, dim: int = DIM):
    vec = [0.0] * dim

    def add(key: str, weight: float):
        h = int(hashlib.sha1(key.encode("utf-8")).hexdigest(), 16)
        vec[h % dim] += weight

    for ch in text:
        add("c:" + ch, 1.0)
    for i in range(len(text) - 1):
        add("b:" + text[i:i + 2], 2.0)
    norm = (sum(x * x for x in vec) ** 0.5) or 1.0
    return [x / norm for x in vec]


class FakeEmbeddingService:
    """Agent 端到端用：async embed_text，统计调用次数验证懒嵌入"""

    def __init__(self):
        self.calls = 0

    async def embed_text(self, text):
        self.calls += 1
        return _embed(text)

    async def embed_texts(self, texts):
        return [_embed(t) for t in texts]

    def get_embedding_dimension(self):
        return DIM

    def get_model_info(self):
        return {"model_type": "fake", "model_name": "fake-embed", "embedding_dimension": DIM}


# 语义相近的问题对 / 无关问题
Q_A = "订单系统如何实现退款功能流程"
Q_B = "订单系统怎样完成退款操作流程"   # 改述：语义等价，文本不同
Q_Z = "今天的天气适合去哪里游玩"         # 与订单无关

PROFILE_1 = "similarity|5|"
PROFILE_2 = "similarity|5|rerank"


def _pair_similarity():
    return _cosine(_embed(Q_A), _embed(Q_B))


# --------------------------------------------------------------------------- #
# 单元：SemanticCache 语义命中层
# --------------------------------------------------------------------------- #


async def test_unit_semantic_hit_and_exact_fastpath():
    print("== unit: 语义命中 / 精确命中零嵌入 ==")
    sim_ab = _pair_similarity()
    sim_z = _cosine(_embed(Q_A), _embed(Q_Z))
    ok("相近问法区分度 sanity", sim_ab > sim_z + 0.05,
       f"sim_ab={sim_ab:.3f} sim_z={sim_z:.3f}")
    # 离线无真实模型标定：把阈值取在相近/无关相似度中间，保证稳定可命中
    thr_hit = (sim_ab + sim_z) / 2

    cache = SemanticCache(enabled=True, ttl_seconds=0, max_entries_per_user=20,
                          semantic_enabled=True, semantic_threshold=thr_hit)
    user = uuid4()

    async def embedder(text):
        return _embed(text)

    # 1. 首次：精确未命中 + 语义扫描无候选 → miss
    key_a = cache.make_key(user, Q_A, 5, "similarity")
    payload_a, match_a = await cache.lookup(
        user, key_a, query=Q_A, profile=PROFILE_1, embedder=embedder
    )
    ok("首次 miss", payload_a is None and match_a["reason"] == "no_candidates",
       str(match_a))
    ok("首次带回查询向量供回填", bool(match_a.get("embedding")))

    cache.store(user, key_a, {"answer": "答案A", "query": Q_A},
                query=Q_A, profile=PROFILE_1, embedding=_embed(Q_A))

    # 2. 改述问题：语义命中（文本不同、key 不同）
    key_b = cache.make_key(user, Q_B, 5, "similarity")
    payload_b, match_b = await cache.lookup(
        user, key_b, query=Q_B, profile=PROFILE_1, embedder=embedder
    )
    ok("改述语义命中", payload_b == {"answer": "答案A", "query": Q_A}
       and match_b["kind"] == "semantic", str(match_b))
    ok("语义命中携带相似度", thr_hit <= match_b["score"] <= 1.0,
       f"score={match_b.get('score')}")
    ok("语义命中回传原文", match_b["query"] == Q_A)

    # 3. 原文精确重复：exact 命中且 embedder 不被调用（零嵌入开销）
    calls_before = 0
    async def counting_embedder(text):
        nonlocal calls_before
        calls_before += 1
        return _embed(text)

    payload_a2, match_a2 = await cache.lookup(
        user, key_a, query=Q_A, profile=PROFILE_1, embedder=counting_embedder
    )
    ok("原文精确命中", payload_a2 == {"answer": "答案A", "query": Q_A},
       str(payload_a2))
    ok("精确命中 kind=exact", match_a2["kind"] == "exact")
    ok("精确命中不触发嵌入", calls_before == 0, f"embedder calls={calls_before}")

    stats = cache.stats()
    ok("统计：精确 1 语义 1 未命中 1",
       stats["exact_hits"] == 1 and stats["semantic_hits"] == 1 and stats["misses"] == 1,
       str(stats))


async def test_unit_isolation_and_guards():
    print("== unit: profile/租户隔离 + 阈值/维度/TTL/开关 ==")
    user_a, user_b = uuid4(), uuid4()
    cache = SemanticCache(enabled=True, ttl_seconds=0, max_entries_per_user=20,
                          semantic_enabled=True, semantic_threshold=0.7)

    async def embedder(text):
        return _embed(text)

    key_a = cache.make_key(user_a, Q_A, 5, "similarity")
    cache.store(user_a, key_a, {"answer": "A"}, query=Q_A,
                profile=PROFILE_1, embedding=_embed(Q_A))

    # profile 不同：同文本同用户但管道不同 → 不可复用
    key_b_p2 = cache.make_key(user_a, Q_B, 5, "similarity", extra="rerank")
    p2, m2 = await cache.lookup(user_a, key_b_p2, query=Q_B,
                                profile=PROFILE_2, embedder=embedder)
    ok("profile 不同不可复用", p2 is None and m2["reason"] == "no_candidates", str(m2))

    # 租户不同：完全隔离
    key_b_u2 = cache.make_key(user_b, Q_B, 5, "similarity")
    p3, m3 = await cache.lookup(user_b, key_b_u2, query=Q_B,
                                profile=PROFILE_1, embedder=embedder)
    ok("租户隔离", p3 is None and m3["reason"] == "no_candidates", str(m3))

    # 无关问题低于阈值：不串答案，记录近失
    key_z = cache.make_key(user_a, Q_Z, 5, "similarity")
    p4, m4 = await cache.lookup(user_a, key_z, query=Q_Z,
                                profile=PROFILE_1, embedder=embedder)
    ok("低于阈值不命中", p4 is None and m4["reason"] == "below_threshold", str(m4))
    ok("近失统计", cache.stats()["near_misses"] >= 1, str(cache.stats()))

    # 维度不一致：候选跳过 → no_candidates
    cache2 = SemanticCache(enabled=True, ttl_seconds=0, max_entries_per_user=20)
    key_a2 = cache2.make_key(user_a, Q_A, 5, "similarity")
    cache2.store(user_a, key_a2, {"answer": "A"}, query=Q_A,
                 profile=PROFILE_1, embedding=_embed(Q_A, dim=8))
    key_b2 = cache2.make_key(user_a, Q_B, 5, "similarity")
    p5, m5 = await cache2.lookup(user_a, key_b2, query=Q_B,
                                 profile=PROFILE_1, embedder=embedder)
    ok("维度不一致跳过", p5 is None and m5["reason"] == "no_candidates", str(m5))

    # 语义开关关闭：退化为纯精确（请求级 miss 不扫语义）
    cache3 = SemanticCache(enabled=True, ttl_seconds=0, max_entries_per_user=20,
                           semantic_enabled=False)
    key_a3 = cache3.make_key(user_a, Q_A, 5, "similarity")
    cache3.store(user_a, key_a3, {"answer": "A"}, query=Q_A,
                 profile=PROFILE_1, embedding=_embed(Q_A))
    key_b3 = cache3.make_key(user_a, Q_B, 5, "similarity")
    p6, m6 = await cache3.lookup(user_a, key_b3, query=Q_B,
                                 profile=PROFILE_1, embedder=embedder)
    ok("语义关闭走 miss", p6 is None and m6["reason"] == "semantic_disabled", str(m6))


async def test_unit_lru_ttl_legacy_contract():
    print("== unit: LRU / TTL / 向后兼容契约 ==")
    cache = SemanticCache(enabled=True, ttl_seconds=3600, max_entries_per_user=2,
                          semantic_enabled=True, semantic_threshold=0.5)
    user = uuid4()
    # legacy put/get 契约不变
    cache.put(user, "k1", {"v": 1})
    cache.put(user, "k2", {"v": 2})
    cache.put(user, "k3", {"v": 3})  # 触发 LRU：k1 被挤出
    ok("LRU 裁剪到上限", cache.stats()["entries"] == 2, str(cache.stats()))
    ok("旧条目被挤出", cache.get(user, "k1") is None)
    ok("新条目可精确取回", cache.get(user, "k3") == {"v": 3})

    cache2 = SemanticCache(enabled=True, ttl_seconds=3600, max_entries_per_user=20,
                           semantic_enabled=True, semantic_threshold=0.5)
    # TTL 过期：语义扫描中清扫过期候选 → no_candidates
    cache2._bucket(user)["dead"] = {"ts": time.monotonic() - 99999, "payload": {"a": 1},
                                     "query": Q_A, "profile": PROFILE_1,
                                     "embedding": _embed(Q_A)}
    async def embedder(text):
        return _embed(text)
    key = cache2.make_key(user, Q_A, 5, "similarity")
    p, m = await cache2.lookup(user, key, query=Q_A, profile=PROFILE_1,
                               embedder=embedder)
    ok("TTL 清扫过期候选", p is None and m["reason"] == "no_candidates", str(m))
    ok("过期条目被移除", "dead" not in cache2._bucket(user))


async def test_unit_disabled_noop():
    print("== unit: disabled 完全旁路 ==")
    cache = SemanticCache(enabled=False, ttl_seconds=0, max_entries_per_user=10)
    cache.store(uuid4(), "k", {"v": 1})
    async def embedder(text):
        return _embed(text)
    p, m = await cache.lookup(uuid4(), "k", query=Q_A, profile=PROFILE_1,
                              embedder=embedder)
    ok("disabled lookup 直接 miss", p is None and m["reason"] == "disabled", str(m))
    ok("disabled 不存条目", cache.stats()["entries"] == 0)


# --------------------------------------------------------------------------- #
# Agent 端到端：改述命中（复用企业套件 Fake 组件）
# --------------------------------------------------------------------------- #


async def test_agent_semantic_reuse():
    print("== agent: 改述语义命中 ==")
    from test_rag_enterprise import (
        CONTENT_A,
        FakeDocumentRepo,
        FakeVectorStore,
        make_agent,
        make_txt,
    )

    store = FakeVectorStore()
    agent, store = make_agent(store=store, repo=FakeDocumentRepo())
    fake_emb = FakeEmbeddingService()
    agent.embedding_service = fake_emb
    # 用相近/无关问法的实时相似度设置阈值（介于二者之间，离线确定）
    sim_ab = _pair_similarity()
    sim_z = _cosine(_embed(Q_A), _embed(Q_Z))
    agent.semantic_cache.semantic_threshold = (sim_ab + sim_z) / 2

    user_a, user_b = uuid4(), uuid4()
    path = make_txt(CONTENT_A)
    try:
        ingest = await agent.ingest_documents([path], user_a)
        ok("知识库导入成功", ingest.get("num_ingested", 0) >= 1, str(ingest))
    finally:
        os.unlink(path)

    # 1. 首次问法：cache miss → 检索/生成 → 回填
    first = await agent.execute({"query": Q_A}, user_id=user_a)
    ok("首次执行成功", first.get("success") is True, str(first.get("answer"))[:80])
    ok("首次为缓存未命中", first["cache"]["hit"] is False
       and first["cache"]["reason"] == "no_candidates", str(first["cache"]))
    emb_calls_after_first = fake_emb.calls
    ok("首次仅一次嵌入（扫描+回填复用）", emb_calls_after_first == 1,
       f"calls={emb_calls_after_first}")

    # 2. 改述问法：semantic 命中，跳过检索直接复用答案
    second = await agent.execute({"query": Q_B}, user_id=user_a)
    ok("改述语义命中", second["cache"]["hit"] is True
       and second["cache"]["kind"] == "semantic", str(second["cache"]))
    ok("命中原文回填", second["cache"]["matched_query"] == Q_A
       and second["query"] == Q_B, str(second["cache"]))
    ok("语义命中复用原答案", second["answer"] == first["answer"])
    ok("改述嵌入一次（扫语义用）", fake_emb.calls == emb_calls_after_first + 1,
       f"calls={fake_emb.calls}")

    # 3. 原文精确重复：exact 命中，零嵌入
    calls_before_exact = fake_emb.calls
    third = await agent.execute({"query": Q_A}, user_id=user_a)
    ok("原句精确命中", third["cache"]["hit"] is True
       and third["cache"]["kind"] == "exact", str(third["cache"]))
    ok("精确命中零嵌入开销", fake_emb.calls == calls_before_exact,
       f"calls={fake_emb.calls}")

    # 4. 无关问法：语义扫描找到候选但低于阈值 → 不串答案
    unrelated = await agent.execute({"query": "服务器 部署 网络 故障"}, user_id=user_a)
    ok("无关问法不命中", unrelated["cache"]["hit"] is False
       and unrelated["cache"]["reason"] == "below_threshold", str(unrelated["cache"]))

    # 5. 不同用户完全隔离：同样的改述问法需重新检索
    other = await agent.execute({"query": Q_B}, user_id=user_b)
    ok("租户隔离（新用户 miss）", other["cache"]["hit"] is False)

    stats = agent.semantic_cache.stats()
    ok("统计：语义命中≥1 且精确命中≥1",
       stats["semantic_hits"] >= 1 and stats["exact_hits"] >= 1, str(stats))


async def main():
    await test_unit_semantic_hit_and_exact_fastpath()
    await test_unit_isolation_and_guards()
    await test_unit_lru_ttl_legacy_contract()
    await test_unit_disabled_noop()
    await test_agent_semantic_reuse()


if __name__ == "__main__":
    asyncio.run(main())
    print("")
    if FAILED:
        print(f"FAILED ({len(FAILED)}): {FAILED}")
        sys.exit(1)
    print(f"ALL PASSED ({len(PASSED)} assertions)")
    sys.exit(0)
