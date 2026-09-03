"""RAG 检索质量（Enterprise Phase 2）回归测试

覆盖：
- RRF 融合：多路召回合并、排序、k 截断
- BM25 词法索引：中英文分词、相关性排序、幂等覆盖、文档删除/清空同步、doc 过滤
- 语义缓存：命中/TTL/LRU 裁剪/每用户隔离/事件失效/开关旁路
- Agent 端到端：hybrid 默认策略可用、缓存命中返回一致答案、知识库变更自动失效

通过 `python tests/test_rag_hybrid_cache.py` 直接运行。
"""
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("OPENAI_API_KEY", "")

from langchain.schema import Document  # noqa: E402

from app.rag.cache import SemanticCache  # noqa: E402
from app.rag.fusion import reciprocal_rank_fusion  # noqa: E402
from app.rag.lexical import LexicalIndex, tokenize  # noqa: E402

# 复用 enterprise 测试替身
from test_rag_enterprise import (  # noqa: E402
    CONTENT_A,
    FakeDocumentRepo,
    FakeVectorStore,
    make_agent,
    make_txt,
)

run = None  # asyncio.run alias set in main

PASSED = []
FAILED = []


def ok(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(
        f"  [{'PASS' if condition else 'FAIL'}] {name}"
        + (f" | {detail}" if not condition else "")
    )


# --------------------------------------------------------------------------- #
# RRF 融合
# --------------------------------------------------------------------------- #


def test_rrf_fusion():
    print("== RRF 融合 ==")
    # 语义路 top: A,B,C；词法路 top: X,B,C → B 双路命中应升到首位
    fused = reciprocal_rank_fusion(
        [["A", "B", "C", "D"], ["X", "B", "C", "Y"]], rrf_k=60
    )
    ids = [cid for cid, _ in fused]
    ok("双路命中项排前", ids[0] == "B", str(ids))
    ok("仅一路命中的词法独有项被召回", "X" in ids, str(ids))
    ok("RRF 排序无重复", len(ids) == len(set(ids)), str(ids))
    ok("id 缺失行被跳过", reciprocal_rank_fusion([["A", None, "B"]])[0][0] == "A")
    rrf_small = reciprocal_rank_fusion([["A", "B"], ["A"]], rrf_k=1)
    # A: 路1 rank0 1/2 + 路2 rank0 1/2 = 1.0；B: 路1 rank1 1/3
    ok("rrf_k 越小平滑度越低", abs(rrf_small[0][1] - 1.0) < 1e-9)


# --------------------------------------------------------------------------- #
# 词法索引
# --------------------------------------------------------------------------- #


def test_tokenize():
    print("== 分词（中英文混合） ==")
    toks = tokenize("RAG 系统设计 多租户 API 2026")
    ok("英文单词保留", "rag" in toks and "api" in toks)
    ok("中文单字切分", "多" in toks and "租" in toks and "户" in toks)
    ok("数字串保留", "2026" in toks)
    ok("单字符英文噪音剔除", "a" not in tokenize("a test"))
    ok("小写归一", "RAG" not in toks and "rag" in toks)


def test_lexical_bm25_ranking_and_lifecycle():
    print("== BM25 相关性与生命周期 ==")
    index = LexicalIndex()
    user = uuid.uuid4()

    hit_doc_id = uuid.uuid4()
    other_doc_id = uuid.uuid4()
    hit_chunk = Document(
        page_content="ChromaDB 混合检索 RRF 融合 BM25 词法索引 语义向量 企业级实现",
        metadata={},
    )
    partial_chunk = Document(
        page_content="今天天气不错 我们讨论项目进度 下周发布",
        metadata={},
    )

    index.add_document(user, hit_doc_id, ["c-hit-1"], [hit_chunk])
    index.add_document(user, other_doc_id, ["c-part-1"], [partial_chunk])

    results = index.search(user, "混合检索 RRF 融合", k=5)
    ok("含查询词的切块排第一", results and results[0].metadata["chunk_id"] == "c-hit-1")
    ok("返回 Document 携带 chunk_id/doc_id", results[0].metadata["doc_id"] == str(hit_doc_id))
    ok("词法切块数与文档数正确", index.chunk_count(user) == 2)

    # doc 过滤：只看某文档
    filtered = index.search(user, "ChromaDB", k=5, doc_id_filter=other_doc_id)
    ok("doc_id 过滤生效", all(d.metadata["doc_id"] == str(other_doc_id) for d in filtered))

    # 幂等覆盖：同一 doc 再 add 不应产生重复切块
    index.add_document(user, hit_doc_id, ["c-hit-1b"], [hit_chunk])
    ok("同 doc 覆盖不重复", index.chunk_count(user) == 2, str(index.chunk_count(user)))

    # 删除单个文档
    index.remove_document(user, other_doc_id)
    ok("删除文档后词法同步", index.chunk_count(user) == 1)
    ok("删除后查不到该文档内容", index.search(user, "天气", k=5) == [])

    # 清空用户
    index.clear_user(user)
    ok("清空用户词法", index.chunk_count(user) == 0 and not index.is_loaded(user))


def test_lexical_add_all_rebuild():
    print("== 词法批量重建（重启后从向量库恢复路径） ==")
    index = LexicalIndex()
    user = uuid.uuid4()
    chunks = [
        Document(page_content="恢复 重建 切块 A", metadata={"doc_id": "doc-a"}),
        Document(page_content="恢复 重建 切块 B", metadata={"doc_id": "doc-b"}),
    ]
    index.add_all(user, ["id-1", "id-2"], chunks)
    ok("add_all 写入并标记 loaded", index.is_loaded(user) and index.chunk_count(user) == 2)
    res = index.search(user, "恢复 重建", k=5)
    ok("重建后立即可检索", len(res) == 2, str(len(res)))
    removed = index.remove_document(user, "doc-a")
    ok("按 doc_id 删除分组数据", removed == 1)


# --------------------------------------------------------------------------- #
# 语义缓存
# --------------------------------------------------------------------------- #


def test_cache_basic_and_tenant_isolation():
    print("== 缓存读写与每用户隔离 ==")
    cache = SemanticCache(enabled=True, ttl_seconds=0, max_entries_per_user=10)
    u_a, u_b = uuid.uuid4(), uuid.uuid4()
    key = SemanticCache.make_key(u_a, "什么是 RAG", 5, "hybrid")
    ok("缓存未命中返回 None", cache.get(u_a, key) is None)
    cache.put(u_a, key, {"answer": "answer-a"})
    got = cache.get(u_a, key)
    ok("命中返回 payload", got and got["answer"] == "answer-a")
    ok("返回的是深拷贝", got is not cache.get(u_a, key))
    ok("B 用户看不到 A 的缓存", cache.get(u_b, key) is None)
    cache.invalidate_user(u_a)
    ok("失效后不再命中", cache.get(u_a, key) is None)
    ok("失效不影响其它用户", cache.get(u_b, SemanticCache.make_key(u_b, "x", 5, "hybrid")) is None)


def test_cache_ttl_and_lru():
    print("== 缓存 TTL 与 LRU 裁剪 ==")
    cache = SemanticCache(enabled=True, ttl_seconds=0, max_entries_per_user=3)
    user = uuid.uuid4()
    for i in range(5):
        cache.put(user, f"k{i}", {"v": i})
    stats = cache.stats()
    ok("超容量裁剪到上限", stats["entries"] == 3, str(stats))
    ok("LRU 淘汰最旧", cache.get(user, "k0") is None and cache.get(user, "k1") is None)
    ok("最近写入仍在", cache.get(user, "k4") is not None)

    ttl_cache = SemanticCache(enabled=True, ttl_seconds=1, max_entries_per_user=10)
    ttl_cache.put(user, "tk", {"v": 1})
    time.sleep(1.05)
    ok("TTL 过期不命中", ttl_cache.get(user, "tk") is None)


def test_cache_disabled_noop():
    print("== 缓存关闭时旁路 ==")
    cache = SemanticCache(enabled=False, ttl_seconds=0, max_entries_per_user=10)
    user = uuid.uuid4()
    cache.put(user, "k", {"answer": 1})
    ok("关闭状态不写入/不命中", cache.get(user, "k") is None)
    ok("关闭状态 make_key 仍可用", len(SemanticCache.make_key(user, "q", 3, "hybrid")) == 40)


# --------------------------------------------------------------------------- #
# Agent 端到端（hybrid + 缓存 + 事件失效）
# --------------------------------------------------------------------------- #


def test_agent_hybrid_and_cache_flow():
    print("== Agent：hybrid 默认策略 + 缓存命中 ==")
    import asyncio

    store = FakeVectorStore()
    agent, _ = make_agent(store=store)
    agent.search_type = "hybrid"
    user = uuid.uuid4()

    path = make_txt(CONTENT_A)
    try:
        run(agent.ingest_documents([path], user_id=user, db=None))

        task = {"query": CONTENT_A[:60], "k": 5}
        first = run(agent.execute(task, user_id=user))
        ok("首次执行 cache.hit=False", first["cache"]["hit"] is False, str(first["cache"]))
        ok("首次执行答案生成", bool(first["answer"]) and first["num_retrieved"] > 0)

        second = run(agent.execute(task, user_id=user))
        ok("二次执行缓存命中", second["cache"]["hit"] is True, str(second["cache"]))
        ok("命中答案与首次一致", second["answer"] == first["answer"])
        ok("命中返回同样检索结果", second["num_retrieved"] == first["num_retrieved"])

        stats = agent.semantic_cache.stats()
        ok("缓存命中率记录", stats["hits"] >= 1 and stats["misses"] >= 1, str(stats))

        # 新文档导入触发失效 → 再次查询应为 miss
        path_b = make_txt(CONTENT_A + " 新增章节 补充内容")
        try:
            run(agent.ingest_documents([path_b], user_id=user, db=None))
        finally:
            Path(path_b).unlink(missing_ok=True)
        third = run(agent.execute(task, user_id=user))
        ok("知识库变更后缓存自动失效", third["cache"]["hit"] is False, str(third["cache"]))
    finally:
        Path(path).unlink(missing_ok=True)


def test_agent_cache_isolated_per_user():
    print("== Agent：缓存按用户隔离 ==")
    import asyncio

    store = FakeVectorStore()
    agent_a, _ = make_agent(store=store)
    agent_b, _ = make_agent(store=store)
    agent_a.search_type = "hybrid"
    agent_b.search_type = "hybrid"
    user_a, user_b = uuid.uuid4(), uuid.uuid4()

    path = make_txt(CONTENT_A)
    try:
        run(agent_a.ingest_documents([path], user_id=user_a, db=None))
        run(agent_b.ingest_documents([path], user_id=user_b, db=None))
        task = {"query": CONTENT_A[:60], "k": 5}
        first_a = run(agent_a.execute(task, user_id=user_a))
        second_b = run(agent_b.execute(task, user_id=user_b))
        ok("B 首次查询不受 A 缓存影响", second_b["cache"]["hit"] is False)
        # A 再次查询命中（A 的缓存未被 B 破坏）
        second_a = run(agent_a.execute(task, user_id=user_a))
        ok("A 缓存独立可用", second_a["cache"]["hit"] is True)
        # B 清空知识库 → A 缓存不受影响
        run(agent_b.delete_all_documents(user_id=user_b, db=object()))
        third_a = run(agent_a.execute(task, user_id=user_a))
        ok("B 清库不影响 A 缓存", third_a["cache"]["hit"] is True)
    finally:
        Path(path).unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


if __name__ == "__main__":
    import asyncio

    run = asyncio.run
    test_rrf_fusion()
    test_tokenize()
    test_lexical_bm25_ranking_and_lifecycle()
    test_lexical_add_all_rebuild()
    test_cache_basic_and_tenant_isolation()
    test_cache_ttl_and_lru()
    test_cache_disabled_noop()
    test_agent_hybrid_and_cache_flow()
    test_agent_cache_isolated_per_user()

    print("")
    if FAILED:
        print(f"FAILED ({len(FAILED)}): {FAILED}")
        sys.exit(1)
    print(f"ALL PASSED ({len(PASSED)} assertions)")
    sys.exit(0)
