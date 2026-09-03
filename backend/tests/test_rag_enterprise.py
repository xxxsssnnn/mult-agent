"""RAG 企业级改造（Phase 1）回归测试

覆盖红线能力：
- 多租户隔离：每用户独立 collection / 检索互不可见 / 删除与清空只作用于本用户
- 幂等导入：同用户相同内容文档只导入一次（sha256 去重）
- 强制 user_id：无租户上下文的 execute/ingest 一律拒绝
- 文档管理作用域：跨用户访问他人文档 -> DocumentNotFoundError

通过 `python tests/test_rag_enterprise.py` 直接运行（退出码非 0 表示失败）。
"""
import asyncio
import os
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("OPENAI_API_KEY", "")

from langchain.schema import Document  # noqa: E402

from app.rag.document_processor import DocumentProcessor  # noqa: E402
from app.rag.exceptions import DocumentNotFoundError, RAGError  # noqa: E402
from app.rag.rag_agent import RAGAgent  # noqa: E402
from app.rag.vector_store import VectorStoreManager  # noqa: E402

run = asyncio.run

PASSED = []
FAILED = []


def ok(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(
        f"  [{'PASS' if condition else 'FAIL'}] {name}"
        + (f" | {detail}" if not condition else "")
    )


# --------------------------------------------------------------------------- #
# 测试替身
# --------------------------------------------------------------------------- #


class FakeVectorStore:
    """内存版多租户向量库：按 user_id 分区存储切块"""

    collection_name_for = staticmethod(VectorStoreManager.collection_name_for)

    def __init__(self):
        # {user_id(str): {doc_id(str): [Document]}}
        self.by_user = {}
        self.deleted_doc_keys = []
        self.embedding_service = SimpleNamespace(
            model_type="fake",
            get_model_info=lambda: {"model_type": "fake", "model_name": "fake"},
            get_embedding_dimension=lambda: 4,
        )

    def _all_docs(self, user_id):
        return [
            doc
            for doc_map in self.by_user.get(str(user_id), {}).values()
            for doc in doc_map
        ]

    async def add_chunks(self, user_id, doc_id, chunks, base_metadata=None):
        key = str(user_id)
        doc_key = str(doc_id)
        bucket = self.by_user.setdefault(key, {})
        bucket[doc_key] = bucket.get(doc_key, [])
        for chunk in chunks:
            md = dict(chunk.metadata or {})
            md.update(base_metadata or {})
            md["user_id"] = str(user_id)
            md["doc_id"] = str(doc_id)
            md["collection"] = self.collection_name_for(user_id)
            bucket[doc_key].append(Document(page_content=chunk.page_content, metadata=md))
        return len(chunks)

    async def similarity_search(self, user_id, query, k=5, filter_metadata=None):
        docs = self._all_docs(user_id)
        if filter_metadata:
            doc_id = filter_metadata.get("doc_id")
            docs = [d for d in docs if d.metadata.get("doc_id") == str(doc_id)]
        # 简化相关性：命中 query 单词数多的排前；全部词都不中则返回空
        words = [w for w in query.split() if w]
        scored = sorted(
            docs,
            key=lambda d: sum(1 for w in words if w in d.page_content),
            reverse=True,
        )
        scored = [d for d in scored if any(w in d.page_content for w in words)]
        return scored[:k]

    async def similarity_search_with_score(self, user_id, query, k=5):
        return [(d, 0.5) for d in await self.similarity_search(user_id, query, k)]

    async def max_marginal_relevance_search(self, user_id, query, k=5, fetch_k=20):
        return await self.similarity_search(user_id, query, k)

    async def hybrid_search(self, user_id, query, k=5, filter_metadata=None):
        # Fake 无词法层：hybrid 视同语义路（隔离语义不变）
        return await self.similarity_search(user_id, query, k, filter_metadata)

    async def delete_document_chunks(self, user_id, doc_id):
        bucket = self.by_user.get(str(user_id), {})
        if str(doc_id) in bucket:
            self.deleted_doc_keys.append(str(doc_id))
            del bucket[str(doc_id)]
            return True
        return False

    async def delete_user_collection(self, user_id):
        self.by_user.pop(str(user_id), None)
        return True

    async def count(self, user_id):
        return len(self._all_docs(user_id))

    async def collection_stats(self, user_id):
        return {
            "collection_name": self.collection_name_for(user_id),
            "chunk_count": await self.count(user_id),
            "embedding_model": "fake",
            "embedding_dimension": 4,
            "persist_directory": ":memory:",
        }


class FakeDocumentRepo:
    """内存版 RAGDocument 仓储"""

    def __init__(self):
        self.records = []
        self.db = SimpleNamespace(commit=_make_noop_commit())

    async def find_by_checksum(self, user_id, checksum):
        for r in self.records:
            if str(r.user_id) == str(user_id) and r.checksum == checksum:
                return r
        return None

    async def create(self, **kwargs):
        record = SimpleNamespace(
            id=uuid.uuid4(),
            user_id=kwargs["user_id"],
            filename=kwargs["filename"],
            file_type=kwargs["file_type"],
            checksum=kwargs["checksum"],
            collection_name=kwargs["collection_name"],
            chunk_count=kwargs.get("chunk_count", 0),
            status=kwargs.get("status", "indexed"),
            error_message=kwargs.get("error_message"),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        self.records.append(record)
        return record

    async def get_for_user(self, user_id, document_id):
        for r in self.records:
            if str(r.user_id) == str(user_id) and str(r.id) == str(document_id):
                return r
        return None

    async def list_documents(self, user_id, offset=0, limit=20):
        rows = [r for r in self.records if str(r.user_id) == str(user_id)]
        return len(rows), rows[offset : offset + limit]

    async def delete(self, record):
        self.records.remove(record)

    async def delete_all_for_user(self, user_id):
        before = len(self.records)
        self.records = [r for r in self.records if str(r.user_id) != str(user_id)]
        return before - len(self.records)

    async def count_for_user(self, user_id):
        return sum(1 for r in self.records if str(r.user_id) == str(user_id))


def _make_noop_commit():
    async def _commit():
        return None

    return _commit


def make_agent(store=None, repo=None, processor=None):
    agent = RAGAgent(agent_id=uuid.uuid4(), name="TestRAGAgent")
    agent.is_initialized = True
    agent.retrieval_k = 5
    agent.search_type = "similarity"
    store = store or FakeVectorStore()
    processor = processor or DocumentProcessor(chunk_size=500, chunk_overlap=50)
    agent.configure_components(
        vector_store=store,
        document_repo=repo or FakeDocumentRepo(),
        document_processor=processor,
        llm=None,
    )
    return agent, store


def make_txt(content, suffix=".txt"):
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


CONTENT_A = (
    "企业级 RAG 系统设计要点 混合检索 重排 多租户隔离 评估 幂等导入 向量存储 检索增强生成 "
    "ChromaDB 语义检索 上下文增强 答案引用"
)
CONTENT_B = (
    "量子计算原理 叠加态 纠缠 量子比特 退相干 量子门 噪声容错 表面码 量子算法 Shor Grover"
)


# --------------------------------------------------------------------------- #
# 用例
# --------------------------------------------------------------------------- #


def test_collection_name_isolated_per_user():
    print("== collection 命名按用户隔离 ==")
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    name_a = VectorStoreManager.collection_name_for(user_a)
    name_b = VectorStoreManager.collection_name_for(user_b)
    ok("不同用户产生不同 collection", name_a != name_b, f"{name_a} vs {name_b}")
    ok("collection 名含前缀", name_a.startswith("rag_"), name_a)
    ok("collection 名不含连接符（合法 Chroma 名）", "-" not in name_a, name_a)
    ok("同一用户 collection 名确定", name_a == VectorStoreManager.collection_name_for(user_a))


def test_execute_requires_user_id():
    print("== 无 user_id 拒绝执行（租户硬约束） ==")
    agent, _ = make_agent()
    try:
        run(agent.execute({"query": "什么是 RAG？"}))
        ok("缺失 user_id 被拒绝", False, "execute 未抛异常")
    except RAGError:
        ok("缺失 user_id 被拒绝", True)
    except Exception as e:  # noqa: BLE001
        ok("缺失 user_id 被拒绝", False, f"抛出非 RAGError: {e!r}")


def test_tenant_isolation_ingest_and_retrieve():
    print("== 多租户隔离：A/B 互不可见 ==")
    store = FakeVectorStore()
    agent_a, _ = make_agent(store=store)
    agent_b, _ = make_agent(store=store)

    user_a, user_b = uuid.uuid4(), uuid.uuid4()

    path_a = make_txt(CONTENT_A)
    path_b = make_txt(CONTENT_B)
    try:
        res_a = run(agent_a.ingest_documents([path_a], user_id=user_a, db=None))
        res_b = run(agent_b.ingest_documents([path_b], user_id=user_b, db=None))
        ok("A 导入成功", res_a["num_ingested"] == 1, str(res_a))
        ok("B 导入成功", res_b["num_ingested"] == 1, str(res_b))

        # 各自检索只能命中自己的文档
        out_a = run(agent_a.execute({"query": CONTENT_A[:60], "k": 5}, user_id=user_a))
        out_b = run(agent_b.execute({"query": CONTENT_B[:60], "k": 5}, user_id=user_b))
        ok("A 检索到自己文档", out_a["num_retrieved"] > 0)
        ok("B 检索到自己文档", out_b["num_retrieved"] > 0)

        # B 用 A 的内容查询也得不到 A 的文档（物理隔离）
        out_b_again = run(agent_a.execute({"query": CONTENT_B[:60], "k": 5}, user_id=user_a))
        ok("A 检索不到 B 的文档", out_b_again["num_retrieved"] == 0, str(out_b_again))

        all_a = store.by_user.get(str(user_a), {})
        all_b = store.by_user.get(str(user_b), {})
        ok("A/B 向量数据分桶隔离", len(all_a) == 1 and len(all_b) == 1)
    finally:
        Path(path_a).unlink(missing_ok=True)
        Path(path_b).unlink(missing_ok=True)


def test_idempotent_duplicate_skipped():
    print("== 幂等导入：同用户相同内容只导入一次 ==")
    store = FakeVectorStore()
    agent, _ = make_agent(store=store)
    user = uuid.uuid4()

    path_1 = make_txt(CONTENT_A)
    path_2 = make_txt(CONTENT_A)  # 内容相同但文件名/路径不同
    try:
        res_1 = run(agent.ingest_documents([path_1], user_id=user, db=None))
        res_2 = run(agent.ingest_documents([path_2], user_id=user, db=None))

        ok("首次导入 ingested", res_1["num_ingested"] == 1, str(res_1))
        ok(
            "重复导入 skipped_duplicate",
            res_2["results"][0]["status"] == "skipped_duplicate",
            str(res_2),
        )
        # 向量库中该用户只应有一份
        ok("向量库未重复写入", run(store.count(user)) > 0)
        # 通过相同 document_id 断言只入一份：检查 doc_map 只有一个 key
        doc_map = store.by_user[str(user)]
        ok("文档记录唯一", len(doc_map) == 1, f"docs={len(doc_map)}")
    finally:
        Path(path_1).unlink(missing_ok=True)
        Path(path_2).unlink(missing_ok=True)


def test_document_management_scoped_to_user():
    print("== 文档列表/删除按用户作用域 ==")
    store = FakeVectorStore()
    agent, _ = make_agent(store=store)
    user_a, user_b = uuid.uuid4(), uuid.uuid4()

    path = make_txt(CONTENT_A)
    try:
        res = run(agent.ingest_documents([path], user_id=user_a, db=None))
        doc_id = res["results"][0]["document_id"]
        ok("A 导入成功且返回 document_id", doc_id is not None, str(res))

        listed_a = run(agent.list_documents(user_id=user_a, db=object()))
        listed_b = run(agent.list_documents(user_id=user_b, db=object()))
        ok("A 能看到自己的文档", listed_a["total"] == 1, str(listed_a))
        ok("B 看不到 A 的文档", listed_b["total"] == 0, str(listed_b))

        # B 删除 A 的文档 -> 404 语义（DocumentNotFoundError）
        try:
            run(agent.delete_document(user_id=user_b, document_id=doc_id, db=object()))
            ok("B 删除 A 的文档被拒绝", False, "未抛 DocumentNotFoundError")
        except DocumentNotFoundError:
            ok("B 删除 A 的文档被拒绝", True)

        # A 删除自己的文档成功
        del_res = run(agent.delete_document(user_id=user_a, document_id=doc_id, db=object()))
        ok("A 删除自己的文档成功", del_res["deleted"] is True)
        listed_a2 = run(agent.list_documents(user_id=user_a, db=object()))
        ok("删除后 A 文档列表为空", listed_a2["total"] == 0, str(listed_a2))
    finally:
        Path(path).unlink(missing_ok=True)


def test_clear_only_affects_own_user():
    print("== 清空只影响当前用户 ==")
    store = FakeVectorStore()
    agent, _ = make_agent(store=store)
    user_a, user_b = uuid.uuid4(), uuid.uuid4()

    path_a = make_txt(CONTENT_A)
    path_b = make_txt(CONTENT_B)
    try:
        run(agent.ingest_documents([path_a], user_id=user_a, db=None))
        run(agent.ingest_documents([path_b], user_id=user_b, db=None))

        run(agent.delete_all_documents(user_id=user_a, db=object()))

        listed_a = run(agent.list_documents(user_id=user_a, db=object()))
        listed_b = run(agent.list_documents(user_id=user_b, db=object()))
        ok("清空后 A 无文档", listed_a["total"] == 0, str(listed_a))
        ok("B 的文档不受影响", listed_b["total"] == 1, str(listed_b))
    finally:
        Path(path_a).unlink(missing_ok=True)
        Path(path_b).unlink(missing_ok=True)


def test_checksum_consistency():
    print("== sha256 摘要一致性 ==")
    path_1 = make_txt(CONTENT_A)
    path_2 = make_txt(CONTENT_A)
    path_3 = make_txt(CONTENT_B)
    try:
        checksum_1 = RAGAgent._compute_checksum(path_1)
        checksum_2 = RAGAgent._compute_checksum(path_2)
        checksum_3 = RAGAgent._compute_checksum(path_3)
        ok("相同内容摘要一致", checksum_1 == checksum_2)
        ok("不同内容摘要不同", checksum_1 != checksum_3)
        ok("sha256 长度 64", len(checksum_1) == 64)
    finally:
        Path(path_1).unlink(missing_ok=True)
        Path(path_2).unlink(missing_ok=True)
        Path(path_3).unlink(missing_ok=True)


def test_retrieval_never_crosses_tenant():
    print("== 检索不跨租户（内容级验证） ==")
    store = FakeVectorStore()
    agent_a, _ = make_agent(store=store)
    agent_b, _ = make_agent(store=store)
    user_a, user_b = uuid.uuid4(), uuid.uuid4()

    path_a = make_txt(CONTENT_A)
    path_b = make_txt(CONTENT_B)
    try:
        run(agent_a.ingest_documents([path_a], user_id=user_a, db=None))
        run(agent_b.ingest_documents([path_b], user_id=user_b, db=None))

        out = run(agent_a.execute({"query": CONTENT_A[:80], "k": 5}, user_id=user_a))
        owners = {d["metadata"].get("user_id") for d in out["retrieved_documents"]}
        ok(
            "检索结果全部属于发起用户",
            owners == {str(user_a)},
            f"owners={owners}",
        )
    finally:
        Path(path_a).unlink(missing_ok=True)
        Path(path_b).unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


if __name__ == "__main__":
    test_collection_name_isolated_per_user()
    test_execute_requires_user_id()
    test_tenant_isolation_ingest_and_retrieve()
    test_idempotent_duplicate_skipped()
    test_document_management_scoped_to_user()
    test_clear_only_affects_own_user()
    test_checksum_consistency()
    test_retrieval_never_crosses_tenant()

    print("")
    if FAILED:
        print(f"FAILED ({len(FAILED)}): {FAILED}")
        sys.exit(1)
    print(f"ALL PASSED ({len(PASSED)} assertions)")
    sys.exit(0)
