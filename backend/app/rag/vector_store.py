"""向量存储模块 - ChromaDB集成"""

from typing import List, Optional, Dict, Any
from uuid import uuid4
import chromadb
from chromadb.config import Settings as ChromaSettings
from langchain_community.vectorstores import Chroma
from langchain.schema import Document
from app.rag.embedding_service import EmbeddingService
from app.core.config import settings
import structlog

logger = structlog.get_logger(__name__)


class VectorStoreManager:
    """
    向量存储管理器
    
    使用ChromaDB作为向量数据库，支持：
    - 文档向量化存储
    - 相似性搜索
    - 元数据过滤
    - 集合管理
    """
    
    def __init__(self, 
                 collection_name: str = "default",
                 persist_directory: str = "./chroma_db",
                 embedding_service: Optional[EmbeddingService] = None):
        """
        初始化向量存储管理器
        
        Args:
            collection_name: 集合名称
            persist_directory: 持久化目录
            embedding_service: Embedding服务实例（可选）
        """
        self.collection_name = collection_name
        self.persist_directory = persist_directory
        self.embedding_service = embedding_service or EmbeddingService()
        
        # 初始化ChromaDB客户端
        self.chroma_client = chromadb.PersistentClient(
            path=persist_directory,
            settings=ChromaSettings(
                anonymized_telemetry=False
            )
        )
        
        # 获取或创建集合
        self.collection = self.chroma_client.get_or_create_collection(
            name=collection_name,
            metadata={"description": f"RAG collection: {collection_name}"}
        )
        
        # 初始化LangChain Chroma包装器
        self.vectorstore = Chroma(
            client=self.chroma_client,
            collection_name=collection_name,
            embedding_function=self._get_embedding_function(),
            persist_directory=persist_directory
        )
        
        logger.info(
            "VectorStoreManager initialized",
            collection_name=collection_name,
            persist_directory=persist_directory,
            embedding_model=self.embedding_service.model_type
        )
    
    def _get_embedding_function(self):
        """获取LangChain兼容的embedding函数"""
        return self.embedding_service.embeddings
    
    async def add_documents(self, documents: List[Document], 
                          metadatas: Optional[List[Dict[str, Any]]] = None) -> List[str]:
        """
        添加文档到向量存储
        
        Args:
            documents: 文档列表
            metadatas: 元数据列表（可选）
            
        Returns:
            添加的文档ID列表
        """
        if not documents:
            return []
        
        # 生成唯一ID
        ids = [str(uuid4()) for _ in documents]
        
        try:
            # 使用LangChain Chroma添加文档
            self.vectorstore.add_documents(
                documents=documents,
                ids=ids,
                metadatas=metadatas
            )
            
            logger.info(
                "Documents added to vector store",
                num_documents=len(documents),
                collection=self.collection_name
            )
            
            return ids
        except Exception as e:
            logger.error("Failed to add documents", error=str(e))
            raise
    
    async def delete_documents(self, document_ids: List[str]) -> bool:
        """
        删除文档
        
        Args:
            document_ids: 要删除的文档ID列表
            
        Returns:
            是否成功删除
        """
        if not document_ids:
            return False
        
        try:
            self.collection.delete(ids=document_ids)
            
            logger.info(
                "Documents deleted from vector store",
                num_deleted=len(document_ids),
                collection=self.collection_name
            )
            
            return True
        except Exception as e:
            logger.error("Failed to delete documents", error=str(e))
            return False
    
    async def similarity_search(self, query: str, k: int = 5, 
                               filter_metadata: Optional[Dict[str, Any]] = None) -> List[Document]:
        """
        相似性搜索
        
        Args:
            query: 查询文本
            k: 返回结果数量
            filter_metadata: 元数据过滤条件（可选）
            
        Returns:
            相似的文档列表
        """
        try:
            # 构建搜索参数
            search_kwargs = {"k": k}
            if filter_metadata:
                search_kwargs["filter"] = filter_metadata
            
            # 执行相似性搜索
            results = self.vectorstore.similarity_search(
                query=query,
                **search_kwargs
            )
            
            logger.debug(
                "Similarity search completed",
                query_length=len(query),
                num_results=len(results),
                k=k
            )
            
            return results
        except Exception as e:
            logger.error("Similarity search failed", error=str(e))
            raise
    
    async def similarity_search_with_score(self, query: str, k: int = 5) -> List[tuple]:
        """
        带分数的相似性搜索
        
        Args:
            query: 查询文本
            k: 返回结果数量
            
        Returns:
            (文档, 分数) 元组列表，分数越低越相似
        """
        try:
            results = self.vectorstore.similarity_search_with_score(
                query=query,
                k=k
            )
            
            logger.debug(
                "Similarity search with score completed",
                query_length=len(query),
                num_results=len(results)
            )
            
            return results
        except Exception as e:
            logger.error("Similarity search with score failed", error=str(e))
            raise
    
    async def max_marginal_relevance_search(self, query: str, k: int = 5,
                                           fetch_k: int = 20) -> List[Document]:
        """
        最大边际相关性搜索（MMR）
        
        MMR在相关性和多样性之间取得平衡，避免返回过于相似的结果
        
        Args:
            query: 查询文本
            k: 返回结果数量
            fetch_k: 初始检索数量
            
        Returns:
            文档列表
        """
        try:
            results = self.vectorstore.max_marginal_relevance_search(
                query=query,
                k=k,
                fetch_k=fetch_k
            )
            
            logger.debug(
                "MMR search completed",
                query_length=len(query),
                num_results=len(results),
                k=k,
                fetch_k=fetch_k
            )
            
            return results
        except Exception as e:
            logger.error("MMR search failed", error=str(e))
            raise
    
    async def get_collection_stats(self) -> Dict[str, Any]:
        """
        获取集合统计信息
        
        Returns:
            统计信息字典
        """
        try:
            count = self.collection.count()
            
            return {
                "collection_name": self.collection_name,
                "document_count": count,
                "embedding_dimension": self.embedding_service.get_embedding_dimension(),
                "embedding_model": self.embedding_service.model_type,
                "persist_directory": self.persist_directory
            }
        except Exception as e:
            logger.error("Failed to get collection stats", error=str(e))
            raise
    
    async def delete_collection(self) -> bool:
        """
        删除整个集合
        
        Returns:
            是否成功删除
        """
        try:
            self.chroma_client.delete_collection(name=self.collection_name)
            
            logger.info(
                "Collection deleted",
                collection_name=self.collection_name
            )
            
            return True
        except Exception as e:
            logger.error("Failed to delete collection", error=str(e))
            return False
    
    async def update_document(self, document_id: str, document: Document,
                             metadata: Optional[Dict[str, Any]] = None) -> bool:
        """
        更新文档
        
        Args:
            document_id: 文档ID
            document: 新文档内容
            metadata: 新元数据（可选）
            
        Returns:
            是否成功更新
        """
        try:
            # 先删除旧文档
            await self.delete_documents([document_id])
            
            # 再添加新文档
            await self.add_documents([document], [metadata] if metadata else None)
            
            logger.info(
                "Document updated",
                document_id=document_id,
                collection=self.collection_name
            )
            
            return True
        except Exception as e:
            logger.error("Failed to update document", error=str(e))
            return False
    
    def get_supported_operations(self) -> List[str]:
        """获取支持的操作列表"""
        return [
            "add_documents",
            "delete_documents",
            "similarity_search",
            "similarity_search_with_score",
            "max_marginal_relevance_search",
            "update_document",
            "get_collection_stats",
            "delete_collection"
        ]
