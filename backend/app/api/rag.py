"""RAG API端点"""

from typing import List, Optional, Dict, Any, UploadFile, File
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from fastapi.responses import JSONResponse
import tempfile
import os
from pathlib import Path

from app.rag import RAGAgent, DocumentProcessor, VectorStoreManager, EmbeddingService
from app.core.deps import get_current_active_user
from app.models.user import User
import structlog

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/rag", tags=["rag"])

# 全局RAG Agent实例（简化版，生产环境应该使用依赖注入）
_rag_agent = None


async def get_rag_agent() -> RAGAgent:
    """获取或创建RAG Agent实例"""
    global _rag_agent
    
    if _rag_agent is None or not _rag_agent.is_initialized:
        _rag_agent = RAGAgent(
            agent_id=uuid4(),
            name="GlobalRAGAgent",
            config={
                "retrieval_k": 5,
                "search_type": "similarity",
                "collection_name": "rag_default",
                "persist_directory": "./chroma_db"
            }
        )
        await _rag_agent.initialize()
    
    return _rag_agent


@router.post("/ingest")
async def ingest_documents(
    files: List[UploadFile] = File(...),
    current_user: User = Depends(get_current_active_user)
):
    """
    上传并导入文档到知识库
    
    Args:
        files: 上传的文件列表
        
    Returns:
        导入结果
    """
    try:
        rag_agent = await get_rag_agent()
        
        # 保存上传的文件到临时目录
        temp_dir = tempfile.mkdtemp()
        file_paths = []
        
        try:
            for file in files:
                file_path = os.path.join(temp_dir, file.filename)
                with open(file_path, "wb") as f:
                    content = await file.read()
                    f.write(content)
                file_paths.append(file_path)
            
            # 导入文档
            result = await rag_agent.ingest_documents(file_paths)
            
            logger.info(
                "Documents ingested via API",
                user_id=str(current_user.id),
                num_files=len(files),
                success=result.get("success", False)
            )
            
            return result
            
        finally:
            # 清理临时文件
            for file_path in file_paths:
                if os.path.exists(file_path):
                    os.remove(file_path)
            if os.path.exists(temp_dir):
                os.rmdir(temp_dir)
                
    except Exception as e:
        logger.error("Document ingestion failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to ingest documents: {str(e)}"
        )


@router.post("/query")
async def query_knowledge_base(
    query_data: Dict[str, Any],
    current_user: User = Depends(get_current_active_user)
):
    """
    查询知识库
    
    Args:
        query_data: 包含query、k、search_type等参数的字典
        
    Returns:
        查询结果
    """
    try:
        rag_agent = await get_rag_agent()
        
        # 执行RAG查询
        result = await rag_agent.execute(query_data)
        
        logger.info(
            "Knowledge base queried",
            user_id=str(current_user.id),
            query_length=len(query_data.get("query", "")),
            success=result.get("success", False)
        )
        
        return result
        
    except Exception as e:
        logger.error("Query failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Query failed: {str(e)}"
        )


@router.get("/stats")
async def get_knowledge_base_stats(
    current_user: User = Depends(get_current_active_user)
):
    """
    获取知识库统计信息
    
    Returns:
        统计信息
    """
    try:
        rag_agent = await get_rag_agent()
        stats = await rag_agent.get_knowledge_base_stats()
        
        return {
            "success": True,
            "stats": stats
        }
        
    except Exception as e:
        logger.error("Failed to get stats", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get stats: {str(e)}"
        )


@router.delete("/clear")
async def clear_knowledge_base(
    current_user: User = Depends(get_current_active_user)
):
    """
    清空知识库
    
    Returns:
        操作结果
    """
    try:
        rag_agent = await get_rag_agent()
        
        if rag_agent.vector_store:
            success = await rag_agent.vector_store.delete_collection()
            
            if success:
                # 重新初始化向量存储
                rag_agent.vector_store = VectorStoreManager(
                    collection_name="rag_default",
                    persist_directory="./chroma_db",
                    embedding_service=rag_agent.embedding_service
                )
                rag_agent.retriever = SemanticRetriever(
                    vector_store=rag_agent.vector_store,
                    embedding_service=rag_agent.embedding_service
                )
                
                logger.info("Knowledge base cleared", user_id=str(current_user.id))
                
                return {
                    "success": True,
                    "message": "Knowledge base cleared successfully"
                }
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to clear knowledge base"
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Vector store not initialized"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to clear knowledge base", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to clear knowledge base: {str(e)}"
        )


@router.post("/demo")
async def demo_rag_feature(
    demo_data: Dict[str, Any],
    current_user: User = Depends(get_current_active_user)
):
    """
    RAG功能演示
    
    Args:
        demo_data: 包含示例问题和配置
        
    Returns:
        演示结果
    """
    try:
        rag_agent = await get_rag_agent()
        
        # 示例问题
        sample_queries = [
            "什么是机器学习？",
            "Python有哪些主要特点？",
            "如何优化数据库性能？"
        ]
        
        query = demo_data.get("query", sample_queries[0])
        k = demo_data.get("k", 3)
        
        # 执行查询
        result = await rag_agent.execute({
            "query": query,
            "k": k,
            "search_type": "similarity"
        })
        
        # 添加演示说明
        result["demo_info"] = {
            "description": "这是RAG功能演示。在实际使用中，您需要先上传文档到知识库，然后才能进行查询。",
            "sample_queries": sample_queries,
            "next_steps": [
                "1. 使用 /api/v1/rag/ingest 上传文档",
                "2. 使用 /api/v1/rag/query 查询知识库",
                "3. 使用 /api/v1/rag/stats 查看知识库统计"
            ]
        }
        
        return result
        
    except Exception as e:
        logger.error("Demo failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Demo failed: {str(e)}"
        )


@router.get("/info")
async def get_rag_info(
    current_user: User = Depends(get_current_active_user)
):
    """
    获取RAG系统信息
    
    Returns:
        RAG系统配置和能力信息
    """
    rag_agent = await get_rag_agent()
    
    return {
        "success": True,
        "rag_system": {
            "version": "1.0",
            "components": {
                "document_processor": "LangChain Document Loaders + Text Splitters",
                "embedding_service": rag_agent.embedding_service.get_model_info() if rag_agent.embedding_service else None,
                "vector_store": "ChromaDB",
                "retriever": "Semantic Retriever with multiple strategies",
                "llm": "OpenAI GPT (if configured)"
            },
            "capabilities": rag_agent.get_capabilities(),
            "supported_file_types": [".pdf", ".txt", ".docx", ".doc", ".md"],
            "search_strategies": ["similarity", "mmr", "score", "hybrid"],
            "endpoints": [
                {"path": "/api/v1/rag/ingest", "method": "POST", "description": "Upload and ingest documents"},
                {"path": "/api/v1/rag/query", "method": "POST", "description": "Query knowledge base"},
                {"path": "/api/v1/rag/stats", "method": "GET", "description": "Get knowledge base statistics"},
                {"path": "/api/v1/rag/clear", "method": "DELETE", "description": "Clear knowledge base"},
                {"path": "/api/v1/rag/demo", "method": "POST", "description": "Demo RAG feature"},
                {"path": "/api/v1/rag/info", "method": "GET", "description": "Get RAG system info"}
            ]
        }
    }
