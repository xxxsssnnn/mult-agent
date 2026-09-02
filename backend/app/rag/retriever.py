"""语义检索器 - 基于向量相似性的文档检索"""

from typing import List, Optional, Dict, Any
from langchain.schema import Document
from app.rag.vector_store import VectorStoreManager
from app.rag.embedding_service import EmbeddingService
import structlog

logger = structlog.get_logger(__name__)


class SemanticRetriever:
    """
    语义检索器
    
    提供多种检索策略：
    - 基础相似性搜索
    - 带分数的相似性搜索
    - MMR（最大边际相关性）搜索
    - 混合检索（结合关键词和语义）
    """
    
    def __init__(self, 
                 vector_store: VectorStoreManager,
                 embedding_service: Optional[EmbeddingService] = None):
        """
        初始化语义检索器
        
        Args:
            vector_store: 向量存储管理器
            embedding_service: Embedding服务（可选）
        """
        self.vector_store = vector_store
        self.embedding_service = embedding_service or vector_store.embedding_service
        
        logger.info(
            "SemanticRetriever initialized",
            collection=vector_store.collection_name
        )
    
    async def retrieve(self, query: str, k: int = 5, 
                      search_type: str = "similarity",
                      filter_metadata: Optional[Dict[str, Any]] = None) -> List[Document]:
        """
        检索相关文档
        
        Args:
            query: 查询文本
            k: 返回结果数量
            search_type: 搜索类型 ('similarity', 'mmr', 'score')
            filter_metadata: 元数据过滤条件
            
        Returns:
            相关文档列表
        """
        try:
            if search_type == "similarity":
                results = await self.vector_store.similarity_search(
                    query=query,
                    k=k,
                    filter_metadata=filter_metadata
                )
            elif search_type == "mmr":
                results = await self.vector_store.max_marginal_relevance_search(
                    query=query,
                    k=k
                )
            elif search_type == "score":
                results_with_scores = await self.vector_store.similarity_search_with_score(
                    query=query,
                    k=k
                )
                # 只返回文档，忽略分数
                results = [doc for doc, score in results_with_scores]
            else:
                raise ValueError(f"Unsupported search type: {search_type}")
            
            logger.info(
                "Documents retrieved",
                query_length=len(query),
                num_results=len(results),
                search_type=search_type,
                k=k
            )
            
            return results
        except Exception as e:
            logger.error("Retrieval failed", error=str(e))
            raise
    
    async def retrieve_with_context(self, query: str, k: int = 5,
                                   include_scores: bool = False) -> Dict[str, Any]:
        """
        检索文档并构建上下文
        
        Args:
            query: 查询文本
            k: 返回结果数量
            include_scores: 是否包含相似度分数
            
        Returns:
            包含文档和上下文的字典
        """
        try:
            if include_scores:
                results_with_scores = await self.vector_store.similarity_search_with_score(
                    query=query,
                    k=k
                )
                documents = [doc for doc, score in results_with_scores]
                scores = [score for doc, score in results_with_scores]
            else:
                documents = await self.retrieve(query, k=k)
                scores = None
            
            # 构建上下文文本
            context_text = self._build_context(documents)
            
            result = {
                "query": query,
                "num_results": len(documents),
                "documents": [
                    {
                        "content": doc.page_content,
                        "metadata": doc.metadata,
                        "score": scores[i] if scores else None
                    }
                    for i, doc in enumerate(documents)
                ],
                "context": context_text
            }
            
            logger.info(
                "Context built successfully",
                query_length=len(query),
                context_length=len(context_text),
                num_documents=len(documents)
            )
            
            return result
        except Exception as e:
            logger.error("Failed to build context", error=str(e))
            raise
    
    def _build_context(self, documents: List[Document]) -> str:
        """
        从文档列表构建上下文字符串
        
        Args:
            documents: 文档列表
            
        Returns:
            格式化的上下文字符串
        """
        if not documents:
            return ""
        
        context_parts = []
        for i, doc in enumerate(documents, 1):
            # 添加文档来源信息
            source = doc.metadata.get('source', 'Unknown')
            page = doc.metadata.get('page', '')
            
            header = f"[Document {i}] Source: {source}"
            if page:
                header += f", Page: {page}"
            
            context_parts.append(header)
            context_parts.append(doc.page_content)
            context_parts.append("-" * 80)
        
        return "\n\n".join(context_parts)
    
    async def hybrid_search(self, query: str, k: int = 5,
                           keyword_weight: float = 0.3,
                           semantic_weight: float = 0.7) -> List[Document]:
        """
        混合搜索（结合关键词和语义搜索）
        
        注意：这是一个简化的实现，实际生产环境可以使用更复杂的混合策略
        
        Args:
            query: 查询文本
            k: 返回结果数量
            keyword_weight: 关键词权重
            semantic_weight: 语义权重
            
        Returns:
            混合排序后的文档列表
        """
        # 目前先使用纯语义搜索
        # TODO: 实现真正的混合搜索（需要BM25等关键词检索支持）
        logger.warning(
            "Hybrid search not fully implemented, using semantic search only"
        )
        
        return await self.retrieve(query, k=k, search_type="similarity")
    
    async def rerank_results(self, query: str, documents: List[Document],
                            top_k: int = 5) -> List[Document]:
        """
        对检索结果重新排序
        
        Args:
            query: 查询文本
            documents: 原始文档列表
            top_k: 返回前K个结果
            
        Returns:
            重新排序后的文档列表
        """
        # 简化实现：根据文档长度和内容相关性简单排序
        # TODO: 使用Cross-Encoder等高级重排序模型
        
        if not documents:
            return []
        
        # 计算简单的相关性分数（基于查询词匹配）
        scored_docs = []
        query_words = set(query.lower().split())
        
        for doc in documents:
            doc_text = doc.page_content.lower()
            # 计算查询词在文档中的出现频率
            match_count = sum(1 for word in query_words if word in doc_text)
            score = match_count / len(query_words) if query_words else 0
            scored_docs.append((doc, score))
        
        # 按分数排序
        scored_docs.sort(key=lambda x: x[1], reverse=True)
        
        # 返回前K个
        reranked = [doc for doc, score in scored_docs[:top_k]]
        
        logger.info(
            "Results reranked",
            original_count=len(documents),
            final_count=len(reranked)
        )
        
        return reranked
    
    def get_retrieval_strategies(self) -> List[str]:
        """获取支持的检索策略"""
        return [
            "similarity",      # 基础相似性搜索
            "mmr",             # 最大边际相关性
            "score",           # 带分数的相似性搜索
            "hybrid",          # 混合搜索（待完善）
        ]
