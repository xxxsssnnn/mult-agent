"""语义检索器 - 租户作用域的文档检索（Enterprise RAG Phase 1/2）

- 所有检索方法都要求 user_id：向量查询只发生在该用户自己的 collection，杜绝跨租户泄漏
- 支持的检索策略：hybrid（默认，BM25+向量+RRF）/ similarity / score / mmr
"""

from typing import Any, Dict, List, Optional

from langchain.schema import Document

from app.rag.embedding_service import EmbeddingService
from app.rag.vector_store import VectorStoreManager
import structlog

logger = structlog.get_logger(__name__)


class SemanticRetriever:
    """
    语义检索器（按用户租户隔离）

    提供检索策略：
    - hybrid（默认）：BM25 词法 + 向量语义 双路召回 + RRF 融合
    - similarity：基础相似性搜索
    - score：带距离分数的相似性搜索
    - mmr：最大边际相关性搜索（相关性 + 多样性）
    """

    def __init__(
        self,
        vector_store: VectorStoreManager,
        embedding_service: Optional[EmbeddingService] = None,
    ):
        self.vector_store = vector_store
        self.embedding_service = embedding_service or vector_store.embedding_service

        logger.info(
            "SemanticRetriever initialized (tenant-aware)",
            collection_prefix=self.vector_store.collection_name_for("x")[:-1],
        )

    async def retrieve(
        self,
        query: str,
        user_id,
        k: int = 5,
        search_type: str = "similarity",
        filter_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Document]:
        """
        检索相关文档（仅限 user_id 自己的 collection）。

        Args:
            query: 查询文本
            user_id: 租户用户
            k: 返回结果数量
            search_type: 'hybrid' | 'similarity' | 'mmr' | 'score'
            filter_metadata: 元数据过滤（可附加，如指定单文档）

        Returns:
            相关文档列表
        """
        try:
            if search_type == "hybrid":
                results = await self.vector_store.hybrid_search(
                    user_id=user_id,
                    query=query,
                    k=k,
                    filter_metadata=filter_metadata,
                )
            elif search_type == "similarity":
                results = await self.vector_store.similarity_search(
                    user_id=user_id,
                    query=query,
                    k=k,
                    filter_metadata=filter_metadata,
                )
            elif search_type == "mmr":
                results = await self.vector_store.max_marginal_relevance_search(
                    user_id=user_id, query=query, k=k
                )
            elif search_type == "score":
                results_with_scores = await self.vector_store.similarity_search_with_score(
                    user_id=user_id, query=query, k=k
                )
                results = [doc for doc, _ in results_with_scores]
            else:
                raise ValueError(f"Unsupported search type: {search_type}")

            logger.info(
                "Documents retrieved",
                user_id=str(user_id),
                query_length=len(query),
                num_results=len(results),
                search_type=search_type,
                k=k,
            )
            return results
        except Exception as e:
            logger.error(
                "Retrieval failed",
                user_id=str(user_id),
                search_type=search_type,
                error=str(e),
            )
            raise

    def build_context(self, documents: List[Document]) -> str:
        """
        从文档列表构建上下文字符串（供 LLM 参考的引用式上下文）。
        """
        if not documents:
            return ""

        context_parts = []
        for i, doc in enumerate(documents, 1):
            metadata = doc.metadata or {}
            source = metadata.get("source") or metadata.get("filename") or "Unknown"
            page = metadata.get("page", "")

            header = f"[Document {i}] Source: {source}"
            if page:
                header += f", Page: {page}"

            context_parts.append(header)
            context_parts.append(doc.page_content)
            context_parts.append("-" * 80)

        return "\n\n".join(context_parts)

    def get_retrieval_strategies(self) -> List[str]:
        """实际支持的检索策略（与实现严格一致）"""
        return ["hybrid", "similarity", "score", "mmr"]
