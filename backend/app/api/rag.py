"""RAG API - 多租户企业级端点（Enterprise RAG Phase 1）

端点按登录用户强制租户隔离：
- 所有操作（导入/查询/统计/列表/删除/清空）都限定在当前用户的向量 collection 与
  RAGDocument 记录内，杜绝跨用户数据泄漏
- 领域异常映射为明确 HTTP 状态码（415/413/422/404/503），不再吞错返回 200
- 上传校验：扩展名白名单 + 大小上限 + 安全落盘（uuid 文件名，防路径穿越）
"""

import asyncio
import tempfile
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_active_user
from app.models.user import User
from app.rag import RAGAgent
from app.rag.exceptions import (
    DocumentNotFoundError,
    EmptyDocumentError,
    FileTooLargeError,
    RAGBackendError,
    RAGError,
    UnsupportedFileTypeError,
)
import structlog

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/rag", tags=["rag"])

# 全局 RAG Agent（无状态、按 user 隔离，可安全共享）
_rag_agent: Optional[RAGAgent] = None


# --------------------------------------------------------------------------- #
# 请求模型
# --------------------------------------------------------------------------- #


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="查询内容")
    k: Optional[int] = Field(None, ge=1, le=50, description="返回结果数量")
    search_type: Optional[str] = Field(
        None, pattern="^(hybrid|similarity|mmr|score)$", description="检索策略"
    )


# --------------------------------------------------------------------------- #
# Agent 获取 / 错误映射
# --------------------------------------------------------------------------- #


async def get_rag_agent() -> RAGAgent:
    """获取（必要时初始化）全局 RAG Agent"""
    global _rag_agent
    if _rag_agent is None or not _rag_agent.is_initialized:
        agent = RAGAgent(agent_id=uuid.uuid4(), name="GlobalRAGAgent")
        ok = await agent.initialize()
        if not ok:
            raise HTTPException(
                status_code=503,
                detail="RAG service is unavailable (initialization failed)",
            )
        _rag_agent = agent
    return _rag_agent


def _as_http_error(exc: Exception) -> HTTPException:
    """将 RAG 领域异常映射为 HTTPException（明确状态码）"""
    if isinstance(exc, UnsupportedFileTypeError):
        code = 415
    elif isinstance(exc, FileTooLargeError):
        code = 413
    elif isinstance(exc, EmptyDocumentError):
        code = 422
    elif isinstance(exc, DocumentNotFoundError):
        code = 404
    elif isinstance(exc, RAGBackendError):
        code = 503
    elif isinstance(exc, (RAGError, ValueError)):
        code = 400
    else:
        code = 500
    detail = str(exc) or exc.__class__.__name__
    return HTTPException(status_code=code, detail=detail)


def _check_extension(filename: str) -> str:
    """校验扩展名白名单，返回小写扩展名（含点）"""
    ext = Path(filename or "").suffix.lower()
    if ext not in settings.RAG_ALLOWED_EXTENSIONS:
        raise UnsupportedFileTypeError(
            f"Unsupported file type '{ext or 'none'}'. Allowed: {settings.RAG_ALLOWED_EXTENSIONS}"
        )
    return ext


async def _save_uploads(
    files: List[UploadFile],
) -> tuple[List[str], List[str], str]:
    """
    安全落盘上传文件。

    Returns:
        (磁盘路径列表, 原始展示名列表, 临时目录)
        失败时清理已写入文件并重抛。
    """
    max_bytes = settings.RAG_MAX_FILE_SIZE_MB * 1024 * 1024
    temp_dir = tempfile.mkdtemp(prefix="rag_upload_")
    paths: List[str] = []
    filenames: List[str] = []
    try:
        for file in files:
            # 原始文件名只用于展示与扩展名校验（清洗路径成分防穿越）
            original_name = Path(file.filename or "upload").name
            _check_extension(original_name)
            ext = Path(original_name).suffix.lower()

            # 磁盘文件名使用随机 uuid，绝不用原始文件名
            saved_path = Path(temp_dir) / f"{uuid.uuid4().hex}{ext}"
            size = 0
            with open(saved_path, "wb") as out:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise FileTooLargeError(
                            f"File '{original_name}' exceeds size limit "
                            f"({settings.RAG_MAX_FILE_SIZE_MB} MB)"
                        )
                    out.write(chunk)
            if size == 0:
                raise EmptyDocumentError(f"File '{original_name}' is empty")
            paths.append(str(saved_path))
            filenames.append(original_name)
        return paths, filenames, temp_dir
    except Exception:
        await _cleanup_uploads(paths, temp_dir)
        raise


async def _cleanup_uploads(paths: List[str], temp_dir: str) -> None:
    """清理临时文件与目录"""
    for p in paths:
        try:
            Path(p).unlink(missing_ok=True)
        except OSError:
            pass
    try:
        Path(temp_dir).rmdir()
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# 文档导入
# --------------------------------------------------------------------------- #


@router.post("/ingest", response_model=None)
async def ingest_documents(
    files: List[UploadFile] = File(...),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """
    上传并导入文档到当前用户的知识库（幂等：相同内容自动跳过）。

    Returns:
        {success, num_files, num_ingested, num_skipped, num_failed, results: [每文件状态]}
    """
    try:
        rag_agent = await get_rag_agent()
        paths, filenames, temp_dir = await _save_uploads(files)

        try:
            result = await rag_agent.ingest_documents(
                file_paths=paths,
                user_id=current_user.id,
                db=db,
                filenames=filenames,
            )
            logger.info(
                "Documents ingested via API",
                user_id=str(current_user.id),
                num_files=result["num_files"],
                num_ingested=result["num_ingested"],
                num_skipped=result["num_skipped"],
                num_failed=result["num_failed"],
            )
            return {"success": True, **result}
        finally:
            await _cleanup_uploads(paths, temp_dir)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Document ingestion failed", error=str(e))
        raise _as_http_error(e)


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #


@router.post("/query")
async def query_knowledge_base(
    payload: QueryRequest,
    current_user: User = Depends(get_current_active_user),
):
    """
    查询当前用户的知识库（检索仅限该用户的向量 collection）。

    Returns:
        答案与检索到的文档引用
    """
    try:
        rag_agent = await get_rag_agent()
        task_input = {
            "query": payload.query,
            "k": payload.k or rag_agent.retrieval_k,
            "search_type": payload.search_type or rag_agent.search_type,
        }
        result = await rag_agent.execute(task_input, user_id=current_user.id)
        logger.info(
            "Knowledge base queried",
            user_id=str(current_user.id),
            query_length=len(payload.query),
            num_retrieved=result.get("num_retrieved", 0),
        )
        return {"success": True, **result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Query failed", user_id=str(getattr(current_user, "id", None)), error=str(e))
        raise _as_http_error(e)


# --------------------------------------------------------------------------- #
# 文档管理
# --------------------------------------------------------------------------- #


@router.get("/documents")
async def list_documents(
    offset: int = 0,
    limit: int = 20,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """分页列出当前用户导入的文档（元数据，不触达向量库）"""
    try:
        rag_agent = await get_rag_agent()
        limit = min(max(limit, 1), settings.RAG_DOCS_PAGE_SIZE)
        offset = max(offset, 0)
        data = await rag_agent.list_documents(
            user_id=current_user.id, db=db, offset=offset, limit=limit
        )
        return {"success": True, **data}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to list documents", error=str(e))
        raise _as_http_error(e)


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """删除当前用户的单个文档（向量切块 + 元数据记录）"""
    try:
        rag_agent = await get_rag_agent()
        result = await rag_agent.delete_document(
            user_id=current_user.id,
            document_id=document_id,
            db=db,
        )
        return {"success": True, **result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to delete document", document_id=document_id, error=str(e))
        raise _as_http_error(e)


@router.delete("/clear")
async def clear_knowledge_base(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """清空当前用户的知识库（删除其 collection 与全部文档记录）"""
    try:
        rag_agent = await get_rag_agent()
        result = await rag_agent.delete_all_documents(
            user_id=current_user.id, db=db
        )
        logger.info(
            "Knowledge base cleared",
            user_id=str(current_user.id),
            documents_deleted=result.get("documents_deleted"),
        )
        return {"success": True, **result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to clear knowledge base", error=str(e))
        raise _as_http_error(e)


@router.get("/stats")
async def get_knowledge_base_stats(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """获取当前用户知识库统计（向量层 + 文档层）"""
    try:
        rag_agent = await get_rag_agent()
        stats = await rag_agent.get_knowledge_base_stats(
            user_id=current_user.id, db=db
        )
        return {"success": True, "stats": stats}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get stats", error=str(e))
        raise _as_http_error(e)


# --------------------------------------------------------------------------- #
# 演示 / 信息
# --------------------------------------------------------------------------- #


@router.post("/demo")
async def demo_rag_feature(
    query: Optional[str] = None,
    k: int = 3,
    current_user: User = Depends(get_current_active_user),
):
    """
    RAG 功能演示（针对当前用户自己的知识库）。

    说明：演示查询也需要先通过 /rag/ingest 向当前用户知识库上传文档。
    """
    try:
        rag_agent = await get_rag_agent()
        task_input = {
            "query": query or "什么是检索增强生成？",
            "k": k,
            "search_type": "hybrid",
        }
        result = await rag_agent.execute(task_input, user_id=current_user.id)
        result["demo_info"] = {
            "description": "这是 RAG 功能演示。请先通过 /api/v1/rag/ingest 向当前用户知识库上传文档。",
            "next_steps": [
                "1. POST /api/v1/rag/ingest 上传文档（幂等去重）",
                "2. POST /api/v1/rag/query 查询知识库",
                "3. GET  /api/v1/rag/documents 查看文档",
                "4. DELETE /api/v1/rag/documents/{id} 删除单文档",
            ],
        }
        return {"success": True, **result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Demo failed", error=str(e))
        raise _as_http_error(e)


@router.get("/info")
async def get_rag_info(
    current_user: User = Depends(get_current_active_user),
):
    """获取 RAG 系统信息（配置与能力，反映多租户隔离设计）"""
    rag_agent = await get_rag_agent()
    return {
        "success": True,
        "rag_system": {
            "version": "1.1",
            "tenant_isolation": {
                "mode": "per-user chroma collection",
                "collection_prefix": settings.RAG_COLLECTION_PREFIX,
                "note": "All operations scoped to current user (tenant).",
            },
            "components": {
                "document_processor": "LangChain Loaders + RecursiveCharacterTextSplitter",
                "embedding_service": (
                    rag_agent.embedding_service.get_model_info()
                    if rag_agent.embedding_service
                    else None
                ),
                "vector_store": "ChromaDB (tenant-aware)",
                "retriever": "Semantic Retriever (similarity / score / mmr)",
                "llm": "OpenAI GPT (if configured)",
                "document_registry": "SQLAlchemy RAGDocument (persistence + idempotency)",
            },
            "capabilities": rag_agent.get_capabilities(),
            "supported_file_types": settings.RAG_ALLOWED_EXTENSIONS,
            "search_strategies": (
                rag_agent.retriever.get_retrieval_strategies()
                if rag_agent.retriever
                else []
            ),
            "limits": {
                "max_file_size_mb": settings.RAG_MAX_FILE_SIZE_MB,
                "chunk_size": settings.RAG_CHUNK_SIZE,
                "chunk_overlap": settings.RAG_CHUNK_OVERLAP,
            },
            "endpoints": [
                {"path": "/api/v1/rag/ingest", "method": "POST", "description": "Upload and ingest documents (idempotent, per-user)"},
                {"path": "/api/v1/rag/query", "method": "POST", "description": "Query current user knowledge base"},
                {"path": "/api/v1/rag/documents", "method": "GET", "description": "List current user documents"},
                {"path": "/api/v1/rag/documents/{id}", "method": "DELETE", "description": "Delete a document"},
                {"path": "/api/v1/rag/stats", "method": "GET", "description": "Get knowledge base stats"},
                {"path": "/api/v1/rag/clear", "method": "DELETE", "description": "Clear current user knowledge base"},
                {"path": "/api/v1/rag/demo", "method": "POST", "description": "Demo RAG query"},
                {"path": "/api/v1/rag/info", "method": "GET", "description": "Get RAG system info"},
            ],
        },
    }
