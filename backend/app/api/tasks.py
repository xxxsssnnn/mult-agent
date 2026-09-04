from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
from app.core.database import get_db
from app.models.task import Task
from app.models.agent import Agent
from app.schemas.task import TaskCreate, TaskUpdate, TaskResponse
from app.core.deps import get_current_active_user
from app.models.user import User
import uuid as uuid_module

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _owned_filter(current_user: User):
    """非 admin 仅能访问自己的任务；admin 可全量访问。

    存量无主数据（user_id IS NULL）对普通用户不可见，仅 admin 可见，
    避免隔离修复前的历史数据跨用户泄漏。
    """
    if current_user.role == "admin":
        return None
    return Task.user_id == current_user.id


@router.post("/", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    task_data: TaskCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """创建新任务"""
    task_id = str(uuid_module.uuid4())
    
    # 仅允许编排自己名下（admin 除外）的 Agent，越权引用一律 404
    if task_data.agent_id is not None:
        agent_result = await db.execute(
            select(Agent).where(Agent.id == task_data.agent_id)
        )
        agent = agent_result.scalar_one_or_none()
        if (
            agent is None
            or (agent.user_id != current_user.id and current_user.role != "admin")
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent not found"
            )

    new_task = Task(
        task_id=task_id,
        title=task_data.title,
        description=task_data.description,
        priority=task_data.priority,
        agent_id=task_data.agent_id,
        input_data=task_data.input_data,
        status="pending",
        user_id=current_user.id
    )
    
    db.add(new_task)
    await db.flush()
    await db.refresh(new_task)
    
    return new_task


@router.get("/", response_model=List[TaskResponse])
async def list_tasks(
    skip: int = 0,
    limit: int = 100,
    status_filter: str = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """列出当前用户的任务（admin 可见全量）"""
    query = select(Task)
    ownership = _owned_filter(current_user)
    if ownership is not None:
        query = query.where(ownership)

    if status_filter:
        query = query.where(Task.status == status_filter)
    
    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    tasks = result.scalars().all()
    
    return tasks


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """获取任务详情"""
    stmt = select(Task).where(Task.id == task_id)
    ownership = _owned_filter(current_user)
    if ownership is not None:
        stmt = stmt.where(ownership)
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found"
        )
    
    return task


@router.put("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: UUID,
    task_data: TaskUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """更新任务"""
    stmt = select(Task).where(Task.id == task_id)
    ownership = _owned_filter(current_user)
    if ownership is not None:
        stmt = stmt.where(ownership)
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found"
        )
    
    # 更新字段
    update_data = task_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(task, field, value)
    
    if task.status in ["completed", "failed"]:
        task.completed_at = datetime.utcnow()
    
    await db.flush()
    await db.refresh(task)
    
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """删除任务"""
    stmt = select(Task).where(Task.id == task_id)
    ownership = _owned_filter(current_user)
    if ownership is not None:
        stmt = stmt.where(ownership)
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found"
        )
    
    await db.delete(task)
    return None


@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """取消任务"""
    stmt = select(Task).where(Task.id == task_id)
    ownership = _owned_filter(current_user)
    if ownership is not None:
        stmt = stmt.where(ownership)
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found"
        )
    
    if task.status in ["completed", "failed", "cancelled"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Task cannot be cancelled"
        )
    
    task.status = "cancelled"
    task.completed_at = datetime.utcnow()
    
    await db.flush()
    await db.refresh(task)
    
    return {"status": "cancelled", "task_id": str(task_id)}
