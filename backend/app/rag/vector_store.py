"""向量存储模块 - ChromaDB 多租户集成（Enterprise RAG Phase 1）

企业级改造要点：
- 物理租户隔离：每用户独立 collection `rag_{user_id_hex}`，检索永不跨租户
- 切块级审计：每条切块元数据携带 user_id / doc_id / collection，支持按文档整删
- 异步安全：所有同步 Chroma 调用经 asyncio.to_thread 委托 + 内部锁串行化，避免阻塞事件循环
- 后端不可用：统一抛 RAGBackendError，由上层映射 503
"""

import asyncio
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

import chromadb
from chromadb.config import Settings as ChromaSettings
from langchain.schema import Document
from langchain_community.vectorstores import Chroma

from app.core.config import settings
from app.rag.embedding_service import EmbeddingService
from app.rag.exceptions import RAGBackendError
from app.rag.fusion import reciprocal_rank_fusion
from app.rag.lexical import LexicalIndex
import structlog

logger = structlog.get_logger(__name__)


def _collection_name_for(user_id) -> str:
    """按用户生成 Chroma collection 名：rag_{user_id_hex}"""
    if isinstance(user_id, UUID):
        hex_id = user_id.hex
    else:
        hex_id = str(user_id).replace("-", "")
    return f"{settings.RAG_COLLECTION_PREFIX}{hex_id}"


class VectorStoreManager:
    """
    多租户向量存储管理器

    ChromaDB 持久化目录内按用户分区：
    - 写入、删除、检索全部限定在用户自己的 collection 内
    - 单例共享 embedding_service 与 chroma client
    """

    def __init__(
        self,
        persist_directory: Optional[str] = None,
        embedding_service: Optional[EmbeddingService] = None,
        chroma_client: Optional[Any] = None,
    ):
        """
        Args:
            persist_directory: Chroma 持久化目录（默认取 settings.RAG_PERSIST_DIRECTORY）
            embedding_service: Embedding 服务（可选，默认自动创建）
            chroma_client: 外部注入的 chroma client（测试用；None 时自建 PersistentClient）
        """
        self.persist_directory = persist_directory or settings.RAG_PERSIST_DIRECTORY
        self.embedding_service = embedding_service or EmbeddingService()
        self.chroma_client = chroma_client
        self._owns_client = chroma_client is None
        # 串行化向量库操作（Chroma 客户端非线程安全）
        self._lock = asyncio.Lock()
        # 每用户 langchain wrapper 缓存
        self._wrappers: Dict[str, Chroma] = {}
        # 每用户词法索引（BM25，与向量层同步维护；重启后首次 hybrid 查询懒重建）
        self.lexical = LexicalIndex()

        if self.chroma_client is None:
            try:
                self.chroma_client = chromadb.PersistentClient(
                    path=self.persist_directory,
                    settings=ChromaSettings(anonymized_telemetry=False),
                )
                logger.info(
                    "VectorStoreManager initialized (tenant-aware)",
                    persist_directory=self.persist_directory,
                    collection_prefix=settings.RAG_COLLECTION_PREFIX,
                    embedding_model=self.embedding_service.model_type,
                )
            except Exception as e:  # pragma: no cover - 依赖环境
                logger.error(
                    "Failed to initialize Chroma client",
                    error=str(e),
                    persist_directory=self.persist_directory,
                )
                self.chroma_client = None

    @staticmethod
    def collection_name_for(user_id) -> str:
        """获取用户对应的 Chroma collection 名（供 RAGDocument 记录归属）"""
        return _collection_name_for(user_id)

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #

    def _ensure_available(self) -> None:
        if self.chroma_client is None:
            raise RAGBackendError("Chroma vector store is not available")

    def _wrapper(self, collection_name: str) -> Chroma:
        """获取（必要时创建）某用户的 langchain Chroma 包装器"""
        self._ensure_available()
        if collection_name not in self._wrappers:
            try:
                self.chroma_client.get_or_create_collection(name=collection_name)
                self._wrappers[collection_name] = Chroma(
                    client=self.chroma_client,
                    collection_name=collection_name,
                    embedding_function=self.embedding_service.embeddings,
                )
            except Exception as e:
                logger.error("Failed to create collection", collection=collection_name, error=str(e))
                raise RAGBackendError(f"Failed to access vector collection: {e}") from e
        return self._wrappers[collection_name]

    def _has_collection(self, collection_name: str) -> bool:
        try:
            return any(c.name == collection_name for c in self.chroma_client.list_collections())
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # 写入 / 删除
    # ------------------------------------------------------------------ #

    async def add_chunks(
        self,
        user_id,
        doc_id,
        chunks: List[Document],
        base_metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        将文档切块向量化入库（限定在用户自己的 collection）。

        Args:
            user_id: 租户用户
            doc_id: 文档级 ID（写入每条切块 metadata，支撑按文档删除）
            chunks: 切块文档列表
            base_metadata: 需注入每条切块的基础元数据（如 filename）

        Returns:
            入库切块数
        """
        if not chunks:
            return 0

        collection_name = _collection_name_for(user_id)

        async with self._lock:
            wrapper = self._wrapper(collection_name)
            # 为每条切块生成稳定的 chunk_id（混合检索 RRF 对齐锚点），
            # 并注入租户/文档归属元数据后统一入库与同步词法索引
            ids: List[str] = []
            merged_docs: List[Document] = []
            for chunk in chunks:
                chunk_id = str(uuid4())
                md = dict(chunk.metadata or {})
                md.update(base_metadata or {})
                md["user_id"] = str(user_id)
                md["doc_id"] = str(doc_id)
                md["collection"] = collection_name
                md["chunk_id"] = chunk_id
                ids.append(chunk_id)
                merged_docs.append(Document(page_content=chunk.page_content, metadata=md))

            def _add() -> None:
                wrapper.add_documents(documents=merged_docs, ids=ids)

            try:
                await asyncio.to_thread(_add)
                # 向量写入成功后同步词法层，保证两路索引一致
                await asyncio.to_thread(
                    self.lexical.add_document, user_id, doc_id, ids, merged_docs
                )
            except Exception as e:
                logger.error(
                    "Failed to add chunks to vector store",
                    collection=collection_name,
                    num_chunks=len(chunks),
                    error=str(e),
                )
                raise RAGBackendError(f"Failed to add chunks to vector store: {e}") from e

            logger.info(
                "Chunks added to vector store",
                collection=collection_name,
                doc_id=str(doc_id),
                num_chunks=len(chunks),
            )
            return len(chunks)

    async def delete_document_chunks(self, user_id, doc_id) -> bool:
        """
        按文档级 doc_id 删除该文档全部切块（限定在用户自己的 collection）。
        """
        collection_name = _collection_name_for(user_id)
        if not self._has_collection(collection_name):
            return False

        async with self._lock:
            try:
                collection = self.chroma_client.get_collection(name=collection_name)
                collection.delete(where={"doc_id": str(doc_id)})
                self.lexical.remove_document(user_id, doc_id)
                logger.info(
                    "Document chunks deleted",
                    collection=collection_name,
                    doc_id=str(doc_id),
                )
                return True
            except Exception as e:
                logger.error("Failed to delete document chunks", doc_id=str(doc_id), error=str(e))
                raise RAGBackendError(f"Failed to delete document chunks: {e}") from e

    async def delete_user_collection(self, user_id) -> bool:
        """
        删除用户整个 collection（清空知识库时使用）。
        """
        collection_name = _collection_name_for(user_id)

        async with self._lock:
            if not self._has_collection(collection_name):
                self._wrappers.pop(collection_name, None)
                return True
            try:
                await asyncio.to_thread(
                    self.chroma_client.delete_collection, name=collection_name
                )
                self._wrappers.pop(collection_name, None)
                self.lexical.clear_user(user_id)
                logger.info("User collection deleted", collection=collection_name)
                return True
            except Exception as e:
                logger.error("Failed to delete user collection", collection=collection_name, error=str(e))
                raise RAGBackendError(f"Failed to delete user collection: {e}") from e

    # ------------------------------------------------------------------ #
    # 检索
    # ------------------------------------------------------------------ #

    async def similarity_search(
        self,
        user_id,
        query: str,
        k: int = 5,
        filter_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Document]:
        """相似性检索（仅限该用户 collection）"""
        collection_name = _collection_name_for(user_id)

        async with self._lock:
            wrapper = self._wrapper(collection_name)
            kwargs: Dict[str, Any] = {"k": k}
            if filter_metadata:
                kwargs["filter"] = filter_metadata

            def _search():
                return wrapper.similarity_search(query=query, **kwargs)

            try:
                results = await asyncio.to_thread(_search)
            except Exception as e:
                logger.error("Similarity search failed", collection=collection_name, error=str(e))
                raise RAGBackendError(f"Similarity search failed: {e}") from e

            logger.debug(
                "Similarity search completed",
                collection=collection_name,
                num_results=len(results),
                k=k,
            )
            return results

    async def similarity_search_with_score(
        self, user_id, query: str, k: int = 5
    ) -> List[Tuple[Document, float]]:
        """带距离分数的相似性检索（分数越低越相似）"""
        collection_name = _collection_name_for(user_id)

        async with self._lock:
            wrapper = self._wrapper(collection_name)

            def _search():
                return wrapper.similarity_search_with_score(query=query, k=k)

            try:
                results = await asyncio.to_thread(_search)
            except Exception as e:
                logger.error("Similarity search with score failed", collection=collection_name, error=str(e))
                raise RAGBackendError(f"Similarity search with score failed: {e}") from e
            return results

    async def max_marginal_relevance_search(
        self, user_id, query: str, k: int = 5, fetch_k: int = 20
    ) -> List[Document]:
        """MMR 检索（相关性 + 多样性均衡）"""
        collection_name = _collection_name_for(user_id)

        async with self._lock:
            wrapper = self._wrapper(collection_name)

            def _search():
                return wrapper.max_marginal_relevance_search(query=query, k=k, fetch_k=fetch_k)

            try:
                results = await asyncio.to_thread(_search)
            except Exception as e:
                logger.error("MMR search failed", collection=collection_name, error=str(e))
                raise RAGBackendError(f"MMR search failed: {e}") from e
            return results

    async def hybrid_search(
        self,
        user_id,
        query: str,
        k: int = 5,
        filter_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Document]:
        """混合检索：BM25 词法路 + 向量语义路 + RRF 融合（仅限该用户 collection）。

        词法索引优先与写入同步维护；进程重启后首次查询从 Chroma 懒重建，
        保证 hybrid 长期可用而不依赖"再次 ingest 触发重建"。
        """
        collection_name = _collection_name_for(user_id)

        async with self._lock:
            wrapper = self._wrapper(collection_name)
            self._ensure_lexical_loaded(user_id)

            fetch_k = max(k * settings.RAG_HYBRID_FETCH_MULTIPLIER, k)
            rrf_k = settings.RAG_HYBRID_RRF_K
            doc_id_filter = (filter_metadata or {}).get("doc_id")

            def _semantic() -> List[Document]:
                kwargs: Dict[str, Any] = {"k": fetch_k}
                if filter_metadata:
                    kwargs["filter"] = filter_metadata
                return wrapper.similarity_search(query=query, **kwargs)

            try:
                semantic_docs = await asyncio.to_thread(_semantic)
                lexical_docs: List[Document] = []
                if settings.RAG_LEXICAL_ENABLED:
                    lexical_docs = await asyncio.to_thread(
                        self.lexical.search,
                        user_id,
                        query,
                        max(fetch_k, k),
                        doc_id_filter,
                    )
            except Exception as e:
                logger.error("Hybrid search failed", collection=collection_name, error=str(e))
                raise RAGBackendError(f"Hybrid search failed: {e}") from e

            fused = self._fuse_rankings(semantic_docs, lexical_docs, k, rrf_k)
            logger.debug(
                "Hybrid search completed",
                collection=collection_name,
                num_results=len(fused),
                k=k,
                semantic_hits=len(semantic_docs),
                lexical_hits=len(lexical_docs),
            )
            return fused

    # ------------------------------------------------------------------ #
    # 词法索引懒加载与融合
    # ------------------------------------------------------------------ #

    def _ensure_lexical_loaded(self, user_id) -> None:
        """进程重启后词法索引为空：首次 hybrid 查询时从 Chroma 重建（持锁调用）。"""
        if self.lexical.is_loaded(user_id):
            return
        collection_name = _collection_name_for(user_id)
        if not self._has_collection(collection_name):
            return
        try:
            collection = self.chroma_client.get_collection(name=collection_name)
            data = collection.get(include=["documents", "metadatas"])
            ids = data.get("ids") or []
            documents = data.get("documents") or []
            metadatas = data.get("metadatas") or []
            if not ids:
                self.lexical.mark_loaded(user_id)
                return
            chunks = [
                Document(page_content=documents[i], metadata=metadatas[i] or {})
                for i in range(len(ids))
            ]
            self.lexical.add_all(user_id, ids, chunks)
            logger.info(
                "Lexical index rebuilt from collection",
                collection=collection_name,
                num_chunks=len(ids),
            )
        except Exception as e:
            # 重建失败不阻断检索：hybrid 降级为纯语义路
            logger.warning(
                "Failed to rebuild lexical index, degrading to semantic only",
                collection=collection_name,
                error=str(e),
            )

    def _fuse_rankings(
        self,
        semantic_docs: List[Document],
        lexical_docs: List[Document],
        k: int,
        rrf_k: int,
    ) -> List[Document]:
        """RRF 融合两路结果（以 chunk_id 对齐；缺 chunk_id 时降级纯语义路）。"""
        if not lexical_docs:
            return semantic_docs[:k]
        doc_by_id: Dict[str, Document] = {}
        semantic_ids: List[str] = []
        for doc in semantic_docs:
            cid = (doc.metadata or {}).get("chunk_id")
            if not cid:
                # 旧数据无 chunk_id，无法对齐融合：按文档顺序拼接降级
                return (semantic_docs + lexical_docs)[:k]
            semantic_ids.append(cid)
            doc_by_id[cid] = doc
        lexical_ids: List[str] = []
        for doc in lexical_docs:
            cid = (doc.metadata or {}).get("chunk_id")
            if not cid:
                continue
            lexical_ids.append(cid)
            doc_by_id.setdefault(cid, doc)

        fused = reciprocal_rank_fusion([semantic_ids, lexical_ids], rrf_k)
        ranked_docs: List[Document] = []
        for cid, _score in fused:
            doc = doc_by_id.get(cid)
            if doc is not None:
                ranked_docs.append(doc)
            if len(ranked_docs) >= k:
                break
        return ranked_docs

    # ------------------------------------------------------------------ #
    # 统计
    # ------------------------------------------------------------------ #

    async def count(self, user_id) -> int:
        """当前用户 collection 的切块总数（无 collection 返回 0）"""
        collection_name = _collection_name_for(user_id)
        if not self._has_collection(collection_name):
            return 0
        try:
            collection = self.chroma_client.get_collection(name=collection_name)
            return collection.count()
        except Exception as e:
            logger.error("Failed to count collection", collection=collection_name, error=str(e))
            raise RAGBackendError(f"Failed to count collection: {e}") from e

    async def collection_stats(self, user_id) -> Dict[str, Any]:
        """用户向量库统计信息"""
        collection_name = _collection_name_for(user_id)
        chunk_count = await self.count(user_id)
        return {
            "collection_name": collection_name,
            "chunk_count": chunk_count,
            "embedding_model": self.embedding_service.model_type,
            "embedding_dimension": self.embedding_service.get_embedding_dimension(),
            "persist_directory": self.persist_directory,
        }
