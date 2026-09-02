"""记忆管理API端点"""

from typing import Dict, Any, Optional, Literal
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.deps import get_current_active_user
from app.models.user import User
from app.memory import MemoryManager
import structlog

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/memory", tags=["memory"])


class MemoryEntryCreate(BaseModel):
    """手动添加记忆条目请求体"""
    content: str = Field(..., min_length=1, max_length=500, description="记忆内容")
    memory_type: Literal["fact", "preference", "procedural", "event", "summary"] = "fact"
    entity: Optional[str] = Field(None, max_length=200, description="主体实体（冲突检测用）")
    confidence: float = Field(0.8, ge=0.0, le=1.0)


@router.post("/session")
async def create_memory_session(
    session_data: Dict[str, Any],
    current_user: User = Depends(get_current_active_user)
):
    """
    创建新的记忆会话
    
    Args:
        session_data: 包含title等会话信息
        
    Returns:
        会话ID和初始化状态
    """
    session_id = str(uuid4())
    
    try:
        # 创建记忆管理器
        memory = MemoryManager(
            session_id=session_id,
            user_id=current_user.id
        )
        
        await memory.initialize()
        
        logger.info(
            "Memory session created",
            session_id=session_id,
            user_id=str(current_user.id)
        )
        
        return {
            "success": True,
            "session_id": session_id,
            "message": "Memory session initialized successfully"
        }
        
    except Exception as e:
        logger.error("Failed to create memory session", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create memory session: {str(e)}"
        )


@router.post("/{session_id}/message")
async def add_message_to_memory(
    session_id: str,
    message_data: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    添加消息到记忆
    
    Args:
        session_id: 会话ID
        message_data: 包含role和content的消息数据
        
    Returns:
        操作结果
    """
    role = message_data.get("role")
    content = message_data.get("content")
    
    if not role or not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required fields: role and content"
        )
    
    if role not in ["user", "assistant"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role must be 'user' or 'assistant'"
        )
    
    try:
        # 创建记忆管理器并加载历史
        memory = MemoryManager(
            session_id=session_id,
            user_id=current_user.id,
            db_session=db
        )
        await memory.initialize()
        
        # 添加消息
        metadata = message_data.get("metadata", {})
        await memory.add_message(role, content, metadata)
        
        # 获取当前上下文
        context = await memory.get_context()
        
        logger.info(
            "Message added to memory",
            session_id=session_id,
            role=role,
            content_length=len(content)
        )
        
        return {
            "success": True,
            "session_id": session_id,
            "role": role,
            "context_preview": context[:200] + "..." if len(context) > 200 else context
        }
        
    except Exception as e:
        logger.error("Failed to add message to memory", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to add message: {str(e)}"
        )


@router.get("/{session_id}/context")
async def get_memory_context(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    获取记忆的完整上下文
    
    Args:
        session_id: 会话ID
        
    Returns:
        包含短期和长期记忆的上下文
    """
    try:
        # 创建记忆管理器并加载历史
        memory = MemoryManager(
            session_id=session_id,
            user_id=current_user.id,
            db_session=db
        )
        await memory.initialize()
        
        # 获取上下文
        context = await memory.get_context()
        short_term = await memory.get_short_term_messages()
        long_term_summary = await memory.get_long_term_summary()
        
        # 获取统计信息
        stats = await memory.get_stats()
        
        logger.info(
            "Memory context retrieved",
            session_id=session_id,
            context_length=len(context)
        )
        
        return {
            "success": True,
            "session_id": session_id,
            "context": context,
            "short_term_messages": short_term,
            "long_term_summary": long_term_summary,
            "stats": stats
        }
        
    except Exception as e:
        logger.error("Failed to get memory context", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get memory context: {str(e)}"
        )


@router.get("/{session_id}/stats")
async def get_memory_stats(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    获取记忆统计信息
    
    Args:
        session_id: 会话ID
        
    Returns:
        统计信息
    """
    try:
        # 创建记忆管理器
        memory = MemoryManager(
            session_id=session_id,
            user_id=current_user.id,
            db_session=db
        )
        
        # 获取统计信息
        stats = await memory.get_stats()
        
        return {
            "success": True,
            "session_id": session_id,
            "stats": stats
        }
        
    except Exception as e:
        logger.error("Failed to get memory stats", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get memory stats: {str(e)}"
        )


@router.delete("/{session_id}")
async def delete_memory_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    删除记忆会话
    
    Args:
        session_id: 会话ID
        
    Returns:
        删除结果
    """
    try:
        # 创建记忆管理器
        memory = MemoryManager(
            session_id=session_id,
            user_id=current_user.id,
            db_session=db
        )
        
        # 清空记忆
        await memory.clear()
        
        logger.info(
            "Memory session deleted",
            session_id=session_id
        )
        
        return {
            "success": True,
            "session_id": session_id,
            "message": "Memory session deleted successfully"
        }
        
    except Exception as e:
        logger.error("Failed to delete memory session", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete memory session: {str(e)}"
        )


@router.post("/demo")
async def demo_memory_feature(
    demo_data: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    演示记忆功能
    
    这个端点展示如何使用记忆系统进行多轮对话
    
    Args:
        demo_data: 包含messages列表的演示数据
        
    Returns:
        演示结果，包含每轮对话后的记忆状态
    """
    messages = demo_data.get("messages", [])
    
    if not messages:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No messages provided for demo"
        )
    
    try:
        session_id = str(uuid4())
        
        # 创建记忆管理器
        memory = MemoryManager(
            session_id=session_id,
            user_id=current_user.id,
            db_session=db
        )
        await memory.initialize()
        
        results = []
        
        # 处理每条消息
        for i, msg in enumerate(messages):
            role = msg.get("role", "user")
            content = msg.get("content", "")
            
            # 添加消息到记忆
            await memory.add_message(role, content)
            
            # 获取当前上下文
            context = await memory.get_context()
            stats = await memory.get_stats()
            
            results.append({
                "step": i + 1,
                "message": {"role": role, "content": content},
                "context_preview": context[:300] + "..." if len(context) > 300 else context,
                "stats": stats
            })
        
        logger.info(
            "Memory demo completed",
            session_id=session_id,
            total_messages=len(messages)
        )
        
        return {
            "success": True,
            "session_id": session_id,
            "total_messages": len(messages),
            "results": results,
            "final_context": await memory.get_context(),
            "final_stats": await memory.get_stats()
        }
        
    except Exception as e:
        logger.error("Memory demo failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Demo failed: {str(e)}"
        )


# ---------- 跨会话记忆条目管理 ----------

def _entry_manager(session_id: str, user_id, db) -> MemoryManager:
    """构建面向记忆条目操作的 MemoryManager（无需加载历史，快速直查）"""
    return MemoryManager(session_id=session_id, user_id=user_id, db_session=db)


@router.get("/entries")
async def list_memory_entries(
    query: Optional[str] = None,
    memory_type: Optional[str] = None,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """列出或检索当前用户的记忆条目（跨会话，按质量排序）

    - 带 query: 关键词混合检索（相关度 + 强度 + 新鲜度）
    - 不带 query: 按记忆质量列出
    """
    try:
        manager = _entry_manager("__global__", current_user.id, db)
        if query:
            entries = await manager.search_memories(
                query=query, limit=limit, memory_type=memory_type
            )
        else:
            entries = await manager.get_memories(
                memory_type=memory_type, limit=limit
            )
        return {"success": True, "count": len(entries), "entries": entries}
    except Exception as e:
        logger.error("Failed to list memory entries", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list memory entries: {str(e)}",
        )


@router.post("/entries")
async def create_memory_entry(
    payload: MemoryEntryCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """手动添加一条记忆条目"""
    try:
        manager = _entry_manager("__manual__", current_user.id, db)
        entry = await manager.add_memory(
            content=payload.content,
            memory_type=payload.memory_type,
            entity=payload.entity,
            confidence=payload.confidence,
        )
        return {"success": True, "entry": entry}
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        )
    except Exception as e:
        logger.error("Failed to create memory entry", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create memory entry: {str(e)}",
        )


@router.delete("/entries/{memory_id}")
async def delete_memory_entry(
    memory_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """软删除一条记忆条目（保留审计轨迹）"""
    try:
        manager = _entry_manager("__manual__", current_user.id, db)
        deleted = await manager.delete_memory(memory_id)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Memory entry not found",
            )
        return {"success": True, "deleted": True}
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(e)
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to delete memory entry", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete memory entry: {str(e)}",
        )


@router.delete("/entries")
async def clear_memory_entries(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """遗忘权：归档当前用户的全部记忆条目（软删除，保留审计轨迹）"""
    try:
        manager = _entry_manager("__clear__", current_user.id, db)
        cleared = await manager.clear_memories()
        return {"success": True, "cleared": cleared}
    except Exception as e:
        logger.error("Failed to clear memory entries", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to clear memory entries: {str(e)}",
        )
