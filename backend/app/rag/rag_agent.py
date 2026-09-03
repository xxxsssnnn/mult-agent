"""RAG Agent - 多租户检索增强生成 Agent（Enterprise RAG Phase 1/2/3/4）

企业级改造要点：
- 租户隔离：execute/检索强制 user_id（缺失即拒绝），向量操作限定在用户自己的 collection
- 文档级生命周期：导入即幂等（sha256 去重）并持久化元数据，支持列表/删除/清空
- 错误语义：领域错误抛 RAGError 族，由 API 层映射状态码，不再吞错返回 success:false
- 混合检索：hybrid = BM25 词法 + 向量语义 + RRF 融合（默认策略）
- 语义缓存：查询→答案按用户作用域缓存，知识库变更事件失效 + TTL 兜底
- 查询转换（Phase 4）：LLM 多查询扩展，多变体召回后 RRF 融合，覆盖单查询漏检
- 两阶段重排（Phase 3）：先放大召回，再 LLM 点级打分截断到 top-k
- 可测试性：组件可注入（configure_components），方便以 fake 替换真实后端
"""

from typing import Any, Dict, List, Optional
from pathlib import Path
from uuid import UUID, uuid4

from langchain.schema import Document, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.agents.base import BaseAgent
from app.core.config import settings
from app.rag.cache import SemanticCache
from app.rag.document_processor import DocumentProcessor
from app.rag.query_transformer import LLMQueryTransformer
from app.rag.reranker import LLMReranker
from app.rag.embedding_service import EmbeddingService
from app.rag.exceptions import (
    DocumentNotFoundError,
    EmptyDocumentError,
    RAGError,
)
from app.rag.repository import RAGDocumentRepository
from app.rag.retriever import SemanticRetriever
from app.rag.vector_store import VectorStoreManager
import structlog

logger = structlog.get_logger(__name__)


class RAGAgent(BaseAgent):
    """
    多租户 RAG Agent

    1. 接收用户问题（必须携带 user_id）
    2. 仅在该用户自己的向量 collection 中检索相关文档
    3. 将检索结果作为上下文提供给 LLM
    4. LLM 基于上下文生成答案
    """

    def __init__(
        self,
        agent_id: UUID,
        name: str = "RAGAgent",
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(agent_id, name, config)

        # 组件占位（initialize 或 configure_components 填充）
        self.embedding_service: Optional[EmbeddingService] = None
        self.vector_store: Optional[VectorStoreManager] = None
        self.retriever: Optional[SemanticRetriever] = None
        self.document_processor: Optional[DocumentProcessor] = None
        self.llm: Optional[ChatOpenAI] = None
        # 文档记录仓储（测试可注入内存实现）
        self.document_repo: Optional[RAGDocumentRepository] = None

        # 检索参数（config 优先，其次 settings 全局配置）
        config = config or {}
        self.retrieval_k = config.get("retrieval_k", settings.RAG_RETRIEVAL_K)
        self.search_type = config.get("search_type", settings.RAG_SEARCH_TYPE)
        # 语义缓存（每用户作用域；知识库变更事件失效 + TTL 兜底）
        self.semantic_cache = SemanticCache(
            enabled=settings.RAG_CACHE_ENABLED,
            ttl_seconds=settings.RAG_CACHE_TTL_SECONDS,
            max_entries_per_user=settings.RAG_CACHE_MAX_ENTRIES_PER_USER,
        )

        # 两阶段重排：先放大召回，再 LLM 点级打分截断到 top-k。
        # 开关默认开，但仅当装配了 LLM 时才会真正生效（见 LLMReranker.active）
        self.rerank_enabled = config.get("rerank_enabled", settings.RAG_RERANK_ENABLED)
        self.rerank_candidate_multiplier = config.get(
            "rerank_candidate_multiplier", settings.RAG_RERANK_CANDIDATE_MULTIPLIER
        )
        self.rerank_max_candidates = config.get(
            "rerank_max_candidates", settings.RAG_RERANK_MAX_CANDIDATES
        )
        self.reranker: Optional[LLMReranker] = LLMReranker(
            llm=None,
            enabled=self.rerank_enabled,
            max_doc_chars=settings.RAG_RERANK_MAX_DOC_CHARS,
        )

        # 查询转换（Phase 4）：LLM 多查询扩展。开关默认开，但仅当装配了
        # LLM 且查询长度达标时才真正生效（见 LLMQueryTransformer.active）
        self.transform_enabled = config.get(
            "transform_enabled", settings.RAG_TRANSFORM_ENABLED
        )
        self.transform_num_variants = config.get(
            "transform_num_variants", settings.RAG_TRANSFORM_NUM_VARIANTS
        )
        self.transformer: Optional[LLMQueryTransformer] = LLMQueryTransformer(
            llm=None,
            enabled=self.transform_enabled,
            num_variants=self.transform_num_variants,
            min_query_len=settings.RAG_TRANSFORM_MIN_QUERY_LEN,
        )

        logger.info(
            "RAGAgent initialized",
            agent_id=str(agent_id),
            name=name,
            retrieval_k=self.retrieval_k,
            search_type=self.search_type,
            rerank_enabled=self.rerank_enabled,
            transform_enabled=self.transform_enabled,
        )

    # ------------------------------------------------------------------ #
    # 初始化 / 组件装配
    # ------------------------------------------------------------------ #

    async def initialize(self) -> bool:
        """初始化 RAG Agent（Embedding / 向量库 / 检索器 / 文档处理器 / LLM）"""
        try:
            self.embedding_service = EmbeddingService(
                model_type=settings.RAG_EMBEDDING_MODEL_TYPE,
                model_name=settings.RAG_EMBEDDING_MODEL_NAME or None,
            )

            persist_directory = (
                (self.config or {}).get("persist_directory")
                or settings.RAG_PERSIST_DIRECTORY
            )
            self.vector_store = VectorStoreManager(
                persist_directory=persist_directory,
                embedding_service=self.embedding_service,
            )

            self.retriever = SemanticRetriever(
                vector_store=self.vector_store,
                embedding_service=self.embedding_service,
            )

            self.document_processor = DocumentProcessor(
                chunk_size=settings.RAG_CHUNK_SIZE,
                chunk_overlap=settings.RAG_CHUNK_OVERLAP,
            )

            if settings.OPENAI_API_KEY:
                self.llm = ChatOpenAI(
                    model=settings.OPENAI_MODEL or "gpt-3.5-turbo",
                    temperature=0.7,
                    openai_api_key=settings.OPENAI_API_KEY,
                )
                logger.info("RAGAgent LLM initialized with OpenAI")
            else:
                logger.warning(
                    "OPENAI_API_KEY not set, RAG generation will fall back to previews"
                )
                self.llm = None

            # 重排器 / 转换器共享同一 LLM（未配置时 active=False，自动旁路）
            self.reranker.llm = self.llm
            self.transformer.llm = self.llm
            self.is_initialized = True
            logger.info("RAGAgent initialized successfully")
            return True
        except Exception as e:
            logger.error("Failed to initialize RAGAgent", error=str(e))
            self.is_initialized = False
            return False

    def configure_components(self, **components) -> None:
        """
        注入/替换组件（测试或扩展用）。

        Args:
            embedding_service / vector_store / retriever / document_processor /
            llm / document_repo：替换对应的内部组件
        """
        for name in (
            "embedding_service",
            "vector_store",
            "retriever",
            "document_processor",
            "llm",
            "document_repo",
        ):
            if name in components:
                setattr(self, name, components[name])
        if self.vector_store and self.retriever is None:
            self.retriever = SemanticRetriever(vector_store=self.vector_store)
        # 重排器 / 转换器跟随注入的 LLM（测试注入 llm=Mock 后自动启用）
        if self.reranker is not None:
            self.reranker.llm = self.llm
        if self.transformer is not None:
            self.transformer.llm = self.llm

    async def _ensure_ready(self) -> None:
        if not self.is_initialized:
            ok = await self.initialize()
            if not ok:
                raise RAGError("RAG service is not available (initialization failed)")

    @staticmethod
    def _require_user_id(user_id) -> UUID:
        """租户隔离硬约束：任何操作都必须携带 user_id"""
        if user_id is None:
            raise RAGError(
                "user_id is required for RAG operations (tenant isolation enforced)"
            )
        return user_id

    # ------------------------------------------------------------------ #
    # 执行链路
    # ------------------------------------------------------------------ #

    async def execute(self, task_input: Dict[str, Any], user_id=None) -> Dict[str, Any]:
        """
        执行 RAG 查询（严格限定在该用户自己的向量 collection 内）。

        Args:
            task_input: 含 query / k / search_type / include_full_documents
            user_id: 租户用户（缺失时从 task_input["user_id"] 读取）

        Returns:
            包含答案与检索结果的字典（调用方负责捕获领域异常）。
            include_full_documents=True 时额外携带 full_documents（检索命中的全文
            片段列表，供 RAGAS 评估等使用）；该字段不写入语义缓存。
        """
        await self._ensure_ready()
        user_id = self._require_user_id(user_id or task_input.get("user_id"))

        query = (task_input.get("query") or "").strip()
        if not query:
            raise ValueError("No query provided")

        k = int(task_input.get("k", self.retrieval_k))
        search_type = task_input.get("search_type", self.search_type)
        rerank_on = self._rerank_active()
        transform_on = self._transform_active() and len(query) >= (
            self.transformer.min_query_len if self.transformer is not None else 0
        )

        # 0. 语义缓存：命中直接返回（每用户作用域）。
        #    key 区分管道差异（重排 / 查询转换）：不同管道产出不同答案，不能互相复用
        cache_key = None
        if self.semantic_cache is not None:
            cache_key = self.semantic_cache.make_key(
                user_id,
                query,
                k,
                search_type,
                extra="|".join(
                    tag
                    for tag in (
                        "rerank" if rerank_on else "",
                        "transform" if transform_on else "",
                    )
                    if tag
                ),
            )
            cached = self.semantic_cache.get(user_id, cache_key)
            if cached is not None:
                cached["cache"] = {
                    "enabled": self.semantic_cache.enabled,
                    "hit": True,
                    "key": cache_key,
                }
                logger.info("RAG answer cache hit", user_id=str(user_id), query=query)
                return cached

        # 1. 查询转换（Phase 4）：LLM 多查询扩展。
        #    变体列表恒以原文开头（基线召回不劣化）；未启用时即 [query]
        transform_variants = [query]
        if transform_on:
            transform_variants = await self.transformer.transform(query)

        # 2. 第一阶段召回（仅该用户 collection）。
        #    重排开启时放大候选数留足重排空间；关闭时与最终 k 一致，行为不变
        stage1_k = (
            min(k * self.rerank_candidate_multiplier, self.rerank_max_candidates)
            if rerank_on
            else k
        )
        variant_count = len(transform_variants)
        logger.info(
            "Starting retrieval (stage 1 recall)",
            user_id=str(user_id),
            query=query,
            k=k,
            stage1_k=stage1_k,
            search_type=search_type,
            rerank=rerank_on,
            query_transform=variant_count > 1,
            variant_count=variant_count,
        )
        if variant_count > 1:
            # 多变体：每路独立召回，RRF 融合去重（同一片段多路命中只算一次）
            per_variant_k = max(1, int(stage1_k / variant_count))
            variant_lists: List[List[Document]] = []
            for variant in transform_variants:
                variant_lists.append(
                    await self.retriever.retrieve(
                        query=variant,
                        user_id=user_id,
                        k=per_variant_k,
                        search_type=search_type,
                    )
                )
            retrieved_docs = self._rrf_merge_variants(variant_lists, top=stage1_k)
        else:
            retrieved_docs = await self.retriever.retrieve(
                query=query, user_id=user_id, k=stage1_k, search_type=search_type
            )
        stage1_count = len(retrieved_docs)

        # 2. 第二阶段重排：LLM 打分排序并截断到 top-k（失败自动降级原序）
        rerank_scores = None
        if rerank_on and stage1_count > 1:
            retrieved_docs = await self.reranker.rerank(
                query, retrieved_docs, k=k
            )
            rerank_scores = self.reranker.last_scores
            logger.info(
                "Stage-2 rerank completed",
                user_id=str(user_id),
                candidates=stage1_count,
                final=len(retrieved_docs),
            )
        elif stage1_k > k and stage1_count > k:
            # 未启用重排但召回多于 k（如配置放大但 LLM 缺失）：保守截断到 k
            retrieved_docs = retrieved_docs[:k]

        # 3. 构建上下文
        context = self.retriever.build_context(retrieved_docs)

        # 4. 生成答案
        if self.llm and context:
            answer = await self._generate_answer(query, context)
        else:
            answer = self._generate_fallback_answer(query, retrieved_docs)

        result = {
            "success": True,
            "query": query,
            "answer": answer,
            "retrieved_documents": [
                {
                    "content": (
                        doc.page_content[:200] + "..."
                        if len(doc.page_content) > 200
                        else doc.page_content
                    ),
                    "metadata": doc.metadata,
                    "source": doc.metadata.get("source", "Unknown"),
                }
                for doc in retrieved_docs
            ],
            "num_retrieved": len(retrieved_docs),
            "context_length": len(context),
            "transformation": {
                "enabled": transform_on,
                "variants": transform_variants,
                "variant_count": len(transform_variants),
            },
            "rerank": {
                "enabled": rerank_on,
                "candidates": stage1_count,
                "final": len(retrieved_docs),
                "scores": rerank_scores,
            },
            "cache": {
                "enabled": bool(self.semantic_cache and self.semantic_cache.enabled),
                "hit": False,
                "key": cache_key,
            },
        }
        # 评估扩展（可选）：携带模型实际看到的全文片段，不参与缓存快照
        if task_input.get("include_full_documents"):
            result["full_documents"] = [doc.page_content for doc in retrieved_docs]

        # 5. 回填缓存（仅缓存可复现的快照字段，命中时再覆写 cache 状态）
        if self.semantic_cache is not None and cache_key:
            snapshot = {
                key: result[key]
                for key in (
                    "success",
                    "query",
                    "answer",
                    "retrieved_documents",
                    "num_retrieved",
                    "context_length",
                    "transformation",
                    "rerank",
                )
            }
            self.semantic_cache.put(user_id, cache_key, snapshot)

        logger.info(
            "RAG execution completed",
            user_id=str(user_id),
            query_length=len(query),
            num_docs=len(retrieved_docs),
            answer_length=len(answer),
            query_variants=variant_count,
            cache_hit=False,
        )
        return result

    async def _generate_answer(self, query: str, context: str) -> str:
        """使用 LLM 生成答案（仅基于提供的上下文，禁止编造）"""
        system_prompt = """你是一个专业的问答助手。请基于提供的上下文信息来回答用户的问题。

要求：
1. 只使用上下文中的信息来回答问题
2. 如果上下文中没有相关信息，请明确说明"根据提供的资料，无法找到相关信息"
3. 回答要准确、简洁、有条理
4. 引用信息来源时，请注明是哪一个来源
5. 不要编造或推测上下文之外的信息"""

        user_prompt = f"""上下文信息：
{context}

用户问题：
{query}

请基于以上上下文信息回答问题："""

        try:
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ]
            response = await self.llm.ainvoke(messages)
            return response.content
        except Exception as e:
            logger.error("Answer generation failed", error=str(e))
            return f"抱歉，生成答案时出现错误：{str(e)}"

    def _generate_fallback_answer(self, query: str, documents: List[Document]) -> str:
        """降级答案（LLM 不可用或无检索结果时）"""
        if not documents:
            return "未找到相关文档。"

        first_doc = documents[0]
        content_preview = (
            first_doc.page_content[:300] + "..."
            if len(first_doc.page_content) > 300
            else first_doc.page_content
        )
        return f"""找到 {len(documents)} 个相关文档。

最相关的文档内容预览：
{content_preview}

注意：由于未配置 LLM，无法生成完整答案。请配置 OPENAI_API_KEY 以启用完整的 RAG 功能。"""

    # ------------------------------------------------------------------ #
    # 文档导入（幂等 + 持久化）
    # ------------------------------------------------------------------ #

    async def ingest_documents(
        self,
        file_paths: List[str],
        user_id=None,
        db=None,
        filenames: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        逐文件导入文档：sha256 幂等去重 → 切块 → 入库 → 持久化元数据。

        Args:
            file_paths: 磁盘文件路径列表（由 API 层安全落盘）
            user_id: 租户用户
            db: 数据库会话（None 时跳过文档记录持久化，幂等失效）
            filenames: 原始展示文件名（与 file_paths 一一对应；缺省取路径名）

        Returns:
            per-file 结果字典：
            {success, num_ingested, num_skipped, num_failed, results: [
                {filename, status: ingested|skipped_duplicate|failed,
                 document_id, chunk_count, error}
            ]}
        """
        await self._ensure_ready()
        user_id = self._require_user_id(user_id)

        if db is not None and self.document_repo is None:
            self.document_repo = RAGDocumentRepository(db)

        results: List[Dict[str, Any]] = []
        num_ingested = num_skipped = num_failed = 0

        for index, file_path in enumerate(file_paths):
            display_name = self._display_name(filenames, index, file_path)
            item: Dict[str, Any] = {
                "filename": display_name,
                "status": "failed",
                "document_id": None,
                "chunk_count": 0,
                "error": None,
            }
            try:
                checksum = self._compute_checksum(file_path)

                # 幂等：同一用户相同内容已成功导入 → 跳过
                if self.document_repo is not None:
                    existing = await self.document_repo.find_by_checksum(user_id, checksum)
                    if existing is not None and existing.status == "indexed":
                        item.update(
                            status="skipped_duplicate",
                            document_id=str(existing.id),
                            chunk_count=existing.chunk_count,
                        )
                        num_skipped += 1
                        results.append(item)
                        logger.info(
                            "Duplicate document skipped (idempotent)",
                            user_id=str(user_id),
                            document_id=str(existing.id),
                        )
                        continue

                # 切块
                chunks = await self.document_processor.process_file(file_path)
                if not chunks:
                    raise EmptyDocumentError(
                        f"Document parsed to zero chunks: {display_name}"
                    )

                # 入库（用户 collection）
                doc_id = uuid4()
                base_metadata = {"filename": display_name, "source": display_name}
                await self.vector_store.add_chunks(
                    user_id=user_id,
                    doc_id=doc_id,
                    chunks=chunks,
                    base_metadata=base_metadata,
                )

                # 持久化元数据（重复失败的旧记录→更新；否则新建）
                collection_name = VectorStoreManager.collection_name_for(user_id)
                record_id = None
                if self.document_repo is not None and existing is not None:
                    existing.status = "indexed"
                    existing.error_message = None
                    existing.chunk_count = len(chunks)
                    existing.collection_name = collection_name
                    existing.filename = display_name
                    await self.document_repo.db.commit()
                    record_id = existing.id
                elif self.document_repo is not None:
                    record = await self.document_repo.create(
                        user_id=user_id,
                        filename=display_name,
                        file_type=Path(file_path).suffix.lower() or ".txt",
                        checksum=checksum,
                        collection_name=collection_name,
                        chunk_count=len(chunks),
                        status="indexed",
                    )
                    record_id = record.id

                item.update(
                    status="ingested",
                    chunk_count=len(chunks),
                    document_id=str(record_id) if record_id is not None else None,
                )
                num_ingested += 1

            except Exception as e:
                # 记录失败（领域异常信息保留供审计）
                item["error"] = str(e)
                num_failed += 1
                logger.error(
                    "Document ingestion failed",
                    user_id=str(user_id),
                    filename=display_name,
                    error=str(e),
                )
                if self.document_repo is not None and db is not None:
                    try:
                        await self.document_repo.create(
                            user_id=user_id,
                            filename=display_name,
                            file_type=Path(file_path).suffix.lower() or ".txt",
                            checksum=self._compute_checksum(file_path),
                            collection_name=VectorStoreManager.collection_name_for(user_id),
                            chunk_count=0,
                            status="failed",
                            error_message=str(e)[:2000],
                        )
                    except Exception as db_error:  # pragma: no cover
                        logger.error("Failed to persist failed-document record", error=str(db_error))

            results.append(item)

        # 知识库已变更：使该用户语义缓存全部失效，避免陈旧答案
        if num_ingested > 0:
            self._invalidate_user_cache(user_id)

        logger.info(
            "Documents ingested (batch complete)",
            user_id=str(user_id),
            num_files=len(file_paths),
            num_ingested=num_ingested,
            num_skipped=num_skipped,
            num_failed=num_failed,
        )
        return {
            "success": num_failed == 0,
            "num_files": len(file_paths),
            "num_ingested": num_ingested,
            "num_skipped": num_skipped,
            "num_failed": num_failed,
            "results": results,
        }

    @staticmethod
    def _display_name(filenames: Optional[List[str]], index: int, file_path: str) -> str:
        """展示文件名：优先原始名（并清洗掉路径成分）"""
        if filenames and index < len(filenames) and filenames[index]:
            return Path(filenames[index]).name or Path(file_path).name
        return Path(file_path).name

    @staticmethod
    def _compute_checksum(file_path: str) -> str:
        """计算文件 sha256 摘要（分块读取防内存峰值）"""
        import hashlib

        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            for block in iter(lambda: f.read(65536), b""):
                hasher.update(block)
        return hasher.hexdigest()

    # ------------------------------------------------------------------ #
    # 文档管理（列表 / 删除 / 清空 / 统计）
    # ------------------------------------------------------------------ #

    async def list_documents(
        self, user_id=None, db=None, offset: int = 0, limit: int = 20
    ) -> Dict[str, Any]:
        """分页列出当前用户文档（仅元数据，不触达向量库）"""
        user_id = self._require_user_id(user_id)
        if db is None:
            raise RAGError("Database session is required to list documents")
        repo = self.document_repo or RAGDocumentRepository(db)
        total, rows = await repo.list_documents(user_id, offset, limit)
        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "documents": [self._document_summary(row) for row in rows],
        }

    async def delete_document(self, user_id=None, document_id=None, db=None) -> Dict[str, Any]:
        """删除单文档：向量切块 + 元数据记录"""
        user_id = self._require_user_id(user_id)
        if db is None:
            raise RAGError("Database session is required to delete a document")
        repo = self.document_repo or RAGDocumentRepository(db)

        record = await repo.get_for_user(user_id, document_id)
        if record is None:
            raise DocumentNotFoundError(
                f"Document {document_id} not found or not owned by user {user_id}"
            )

        # 先清向量（可能 collection 不存在，忽略），再删记录
        await self.vector_store.delete_document_chunks(user_id, record.id)
        await repo.delete(record)

        logger.info(
            "Document deleted",
            user_id=str(user_id),
            document_id=str(record.id),
            filename=record.filename,
        )
        self._invalidate_user_cache(user_id)
        return {
            "deleted": True,
            "document_id": str(record.id),
            "filename": record.filename,
            "chunk_count": record.chunk_count,
        }

    async def delete_all_documents(self, user_id=None, db=None) -> Dict[str, Any]:
        """清空当前用户知识库（删除其 collection 与全部记录）"""
        user_id = self._require_user_id(user_id)
        await self.vector_store.delete_user_collection(user_id)
        deleted_count = 0
        if db is not None:
            repo = self.document_repo or RAGDocumentRepository(db)
            deleted_count = await repo.delete_all_for_user(user_id)
        self._invalidate_user_cache(user_id)
        return {"deleted": True, "user_id": str(user_id), "documents_deleted": deleted_count}

    async def get_knowledge_base_stats(self, user_id=None, db=None) -> Dict[str, Any]:
        """知识库统计：向量层统计 + DB 文档层统计"""
        user_id = self._require_user_id(user_id)
        stats: Dict[str, Any] = await self.vector_store.collection_stats(user_id)
        stats["agent_id"] = str(self.agent_id)
        stats["agent_name"] = self.name

        if db is not None:
            repo = self.document_repo or RAGDocumentRepository(db)
            total, rows = await repo.list_documents(user_id, 0, 100000)
            stats["documents"] = {
                "total": total,
                "chunks_total": sum(r.chunk_count for r in rows),
            }
            stats["supported_formats"] = (
                self.document_processor.get_supported_formats()
                if self.document_processor
                else []
            )
        return stats

    @staticmethod
    def _document_summary(record) -> Dict[str, Any]:
        return {
            "id": str(record.id),
            "filename": record.filename,
            "file_type": record.file_type,
            "checksum": record.checksum,
            "chunk_count": record.chunk_count,
            "status": record.status,
            "error_message": record.error_message,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        }

    def get_capabilities(self) -> List[str]:
        """获取 Agent 能力列表"""
        return [
            "document_ingestion",
            "semantic_search",
            "hybrid_search",  # BM25 + 向量 + RRF（默认策略）
            "question_answering",
            "context_augmentation",
            "knowledge_base_management",
            "tenant_isolation",
            "semantic_cache",
            "reranking",  # 两阶段重排（LLM 点级，需配置 LLM 生效）
            "query_transformation",  # 查询转换（LLM 多查询扩展，需配置 LLM 生效）
        ]

    def _rerank_active(self) -> bool:
        """重排是否在本 Agent 真正参与链路"""
        return bool(
            self.reranker is not None
            and self.reranker.active
            and self.reranker.llm is not None
        )

    def _transform_active(self) -> bool:
        """查询转换是否在本 Agent 真正参与链路"""
        return bool(self.transformer is not None and self.transformer.active)

    @staticmethod
    def _doc_merge_key(doc: Document) -> str:
        """跨查询变体召回结果的去重身份：优先 chunk_id，缺失时用内容摘要"""
        chunk_id = (doc.metadata or {}).get("chunk_id")
        if chunk_id:
            return f"chunk:{chunk_id}"
        import hashlib

        return "hash:" + hashlib.sha1(
            (doc.page_content or "").encode("utf-8")
        ).hexdigest()

    def _rrf_merge_variants(
        self,
        variant_lists: List[List[Document]],
        top: int,
    ) -> List[Document]:
        """多查询变体召回结果的 RRF 融合 + 去重。

        同一片段在多个变体下命中 → 得分叠加（提升召回鲁棒性）；
        排序稳定：同分保持先到顺序；返回前 top 个。
        """
        rrf_k = settings.RAG_HYBRID_RRF_K
        scores: Dict[str, float] = {}
        first_seen: Dict[str, Document] = {}
        for ranked in variant_lists:
            for rank, doc in enumerate(ranked):
                key = self._doc_merge_key(doc)
                if key not in first_seen:
                    first_seen[key] = doc
                scores[key] = scores.get(key, 0.0) + 1.0 / (rrf_k + rank + 1)
        ordered_keys = sorted(first_seen.keys(), key=scores.get, reverse=True)[:top]
        return [first_seen[key] for key in ordered_keys]

    def _invalidate_user_cache(self, user_id) -> None:
        """知识库变更后使该用户语义缓存失效"""
        if self.semantic_cache is not None:
            self.semantic_cache.invalidate_user(user_id)
