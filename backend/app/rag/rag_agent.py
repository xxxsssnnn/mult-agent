"""RAG Agent - 检索增强生成Agent"""

from typing import List, Optional, Dict, Any
from uuid import UUID, uuid4
from langchain_openai import ChatOpenAI
from langchain.schema import HumanMessage, SystemMessage
from app.agents.base import BaseAgent
from app.rag.document_processor import DocumentProcessor
from app.rag.vector_store import VectorStoreManager
from app.rag.retriever import SemanticRetriever
from app.rag.embedding_service import EmbeddingService
from app.core.config import settings
import structlog

logger = structlog.get_logger(__name__)


class RAGAgent(BaseAgent):
    """
    RAG Agent
    
    结合检索和生成的智能Agent：
    1. 接收用户问题
    2. 从向量数据库检索相关文档
    3. 将检索结果作为上下文提供给LLM
    4. LLM基于上下文生成答案
    """
    
    def __init__(self, 
                 agent_id: UUID, 
                 name: str = "RAGAgent",
                 config: Optional[Dict[str, Any]] = None):
        super().__init__(agent_id, name, config)
        
        # 初始化组件
        self.embedding_service = None
        self.vector_store = None
        self.retriever = None
        self.document_processor = None
        self.llm = None
        
        # 配置参数
        self.retrieval_k = config.get("retrieval_k", 5) if config else 5
        self.search_type = config.get("search_type", "similarity") if config else "similarity"
        
        logger.info(
            "RAGAgent initialized",
            agent_id=str(agent_id),
            name=name,
            retrieval_k=self.retrieval_k,
            search_type=self.search_type
        )
    
    async def initialize(self) -> bool:
        """初始化RAG Agent"""
        try:
            # 初始化Embedding服务
            self.embedding_service = EmbeddingService()
            
            # 初始化向量存储
            collection_name = self.config.get("collection_name", "rag_default") if self.config else "rag_default"
            persist_directory = self.config.get("persist_directory", "./chroma_db") if self.config else "./chroma_db"
            self.vector_store = VectorStoreManager(
                collection_name=collection_name,
                persist_directory=persist_directory,
                embedding_service=self.embedding_service
            )
            
            # 初始化检索器
            self.retriever = SemanticRetriever(
                vector_store=self.vector_store,
                embedding_service=self.embedding_service
            )
            
            # 初始化文档处理器
            self.document_processor = DocumentProcessor()
            
            # 初始化LLM
            if settings.OPENAI_API_KEY:
                self.llm = ChatOpenAI(
                    model=settings.OPENAI_MODEL or "gpt-3.5-turbo",
                    temperature=0.7,
                    openai_api_key=settings.OPENAI_API_KEY
                )
                logger.info("RAGAgent LLM initialized with OpenAI")
            else:
                logger.warning("OPENAI_API_KEY not set, RAG generation will be limited")
                self.llm = None
            
            self.is_initialized = True
            logger.info("RAGAgent initialized successfully")
            return True
            
        except Exception as e:
            logger.error("Failed to initialize RAGAgent", error=str(e))
            return False
    
    async def execute(self, task_input: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行RAG任务
        
        Args:
            task_input: 包含query和可选参数的字典
            
        Returns:
            包含答案和检索结果的字典
        """
        try:
            if not self.is_initialized:
                await self.initialize()
            
            query = task_input.get("query", "")
            if not query:
                return {
                    "success": False,
                    "error": "No query provided"
                }
            
            # 获取检索参数
            k = task_input.get("k", self.retrieval_k)
            search_type = task_input.get("search_type", self.search_type)
            
            # 步骤1: 检索相关文档
            logger.info("Starting retrieval", query=query, k=k)
            retrieved_docs = await self.retriever.retrieve(
                query=query,
                k=k,
                search_type=search_type
            )
            
            # 步骤2: 构建上下文
            context = self._build_context_from_docs(retrieved_docs)
            
            # 步骤3: 生成答案
            if self.llm and context:
                answer = await self._generate_answer(query, context)
            else:
                # 如果没有LLM或没有上下文，返回简单响应
                answer = self._generate_fallback_answer(query, retrieved_docs)
            
            # 构建响应
            result = {
                "success": True,
                "query": query,
                "answer": answer,
                "retrieved_documents": [
                    {
                        "content": doc.page_content[:200] + "..." if len(doc.page_content) > 200 else doc.page_content,
                        "metadata": doc.metadata,
                        "source": doc.metadata.get('source', 'Unknown')
                    }
                    for doc in retrieved_docs
                ],
                "num_retrieved": len(retrieved_docs),
                "context_length": len(context)
            }
            
            logger.info(
                "RAG execution completed",
                query_length=len(query),
                num_docs=len(retrieved_docs),
                answer_length=len(answer)
            )
            
            return result
            
        except Exception as e:
            logger.error("RAG execution failed", error=str(e))
            return {
                "success": False,
                "error": str(e)
            }
    
    def _build_context_from_docs(self, documents: List) -> str:
        """
        从检索到的文档构建上下文
        
        Args:
            documents: 检索到的文档列表
            
        Returns:
            格式化的上下文字符串
        """
        if not documents:
            return ""
        
        context_parts = []
        for i, doc in enumerate(documents, 1):
            source = doc.metadata.get('source', 'Unknown')
            page = doc.metadata.get('page', '')
            
            header = f"[Source {i}]"
            if source != 'Unknown':
                header += f" - {source}"
            if page:
                header += f" (Page {page})"
            
            context_parts.append(header)
            context_parts.append(doc.page_content)
            context_parts.append("-" * 80)
        
        return "\n\n".join(context_parts)
    
    async def _generate_answer(self, query: str, context: str) -> str:
        """
        使用LLM生成答案
        
        Args:
            query: 用户问题
            context: 检索到的上下文
            
        Returns:
            生成的答案
        """
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
                HumanMessage(content=user_prompt)
            ]
            
            response = await self.llm.ainvoke(messages)
            return response.content
            
        except Exception as e:
            logger.error("Answer generation failed", error=str(e))
            return f"抱歉，生成答案时出现错误：{str(e)}"
    
    def _generate_fallback_answer(self, query: str, documents: List) -> str:
        """
        生成降级答案（当LLM不可用时）
        
        Args:
            query: 用户问题
            documents: 检索到的文档
            
        Returns:
            简单的答案
        """
        if not documents:
            return "未找到相关文档。"
        
        # 返回第一个文档的部分内容作为参考
        first_doc = documents[0]
        content_preview = first_doc.page_content[:300] + "..." if len(first_doc.page_content) > 300 else first_doc.page_content
        
        return f"""找到 {len(documents)} 个相关文档。

最相关的文档内容预览：
{content_preview}

注意：由于未配置LLM，无法生成完整答案。请配置OPENAI_API_KEY以启用完整的RAG功能。"""
    
    async def ingest_documents(self, file_paths: List[str], 
                              metadata: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        导入文档到知识库
        
        Args:
            file_paths: 文件路径列表
            metadata: 元数据列表（可选）
            
        Returns:
            导入结果
        """
        try:
            if not self.is_initialized:
                await self.initialize()
            
            all_chunks = []
            for file_path in file_paths:
                chunks = await self.document_processor.process_file(file_path)
                all_chunks.extend(chunks)
            
            if not all_chunks:
                return {
                    "success": False,
                    "error": "No documents processed"
                }
            
            # 添加到向量存储
            doc_ids = await self.vector_store.add_documents(all_chunks, metadata)
            
            logger.info(
                "Documents ingested successfully",
                num_files=len(file_paths),
                num_chunks=len(all_chunks),
                num_ids=len(doc_ids)
            )
            
            return {
                "success": True,
                "num_files": len(file_paths),
                "num_chunks": len(all_chunks),
                "document_ids": doc_ids
            }
            
        except Exception as e:
            logger.error("Document ingestion failed", error=str(e))
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_knowledge_base_stats(self) -> Dict[str, Any]:
        """
        获取知识库统计信息
        
        Returns:
            统计信息字典
        """
        if not self.vector_store:
            return {"error": "Vector store not initialized"}
        
        stats = await self.vector_store.get_collection_stats()
        stats["agent_id"] = str(self.agent_id)
        stats["agent_name"] = self.name
        
        return stats
    
    def get_capabilities(self) -> List[str]:
        """获取Agent能力列表"""
        return [
            "document_ingestion",
            "semantic_search",
            "question_answering",
            "context_augmentation",
            "knowledge_base_management"
        ]
