from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from app.models.agent import Agent
from app.schemas.agent import AgentCreate, AgentUpdate, AgentResponse
from app.core.deps import get_current_active_user
from app.models.user import User

router = APIRouter(prefix="/agents", tags=["agents"])


def _owned_filter(current_user: User):
    """非 admin 仅能访问自己的 Agent；admin 可全量访问。

    存量无主数据（user_id IS NULL）对普通用户不可见，仅 admin 可见。
    """
    if current_user.role == "admin":
        return None
    return Agent.user_id == current_user.id


@router.post("/", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    agent_data: AgentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """创建新Agent"""
    new_agent = Agent(
        name=agent_data.name,
        type=agent_data.type,
        description=agent_data.description,
        config=agent_data.config,
        capabilities=agent_data.capabilities,
        status="active",
        user_id=current_user.id
    )
    
    db.add(new_agent)
    await db.flush()
    await db.refresh(new_agent)
    
    return new_agent


@router.get("/", response_model=List[AgentResponse])
async def list_agents(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """列出当前用户的 Agent（admin 可见全量）"""
    query = select(Agent)
    ownership = _owned_filter(current_user)
    if ownership is not None:
        query = query.where(ownership)
    result = await db.execute(query.offset(skip).limit(limit))
    agents = result.scalars().all()
    return agents


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """获取Agent详情"""
    stmt = select(Agent).where(Agent.id == agent_id)
    ownership = _owned_filter(current_user)
    if ownership is not None:
        stmt = stmt.where(ownership)
    result = await db.execute(stmt)
    agent = result.scalar_one_or_none()
    
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found"
        )
    
    return agent


@router.put("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: UUID,
    agent_data: AgentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """更新Agent"""
    stmt = select(Agent).where(Agent.id == agent_id)
    ownership = _owned_filter(current_user)
    if ownership is not None:
        stmt = stmt.where(ownership)
    result = await db.execute(stmt)
    agent = result.scalar_one_or_none()
    
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found"
        )
    
    # 更新字段
    update_data = agent_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(agent, field, value)
    
    await db.flush()
    await db.refresh(agent)
    
    return agent


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """删除Agent"""
    stmt = select(Agent).where(Agent.id == agent_id)
    ownership = _owned_filter(current_user)
    if ownership is not None:
        stmt = stmt.where(ownership)
    result = await db.execute(stmt)
    agent = result.scalar_one_or_none()
    
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found"
        )
    
    await db.delete(agent)
    return None


@router.post("/{agent_id}/execute")
async def execute_agent(
    agent_id: UUID,
    task_input: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """执行Agent任务"""
    stmt = select(Agent).where(Agent.id == agent_id)
    ownership = _owned_filter(current_user)
    if ownership is not None:
        stmt = stmt.where(ownership)
    result = await db.execute(stmt)
    agent = result.scalar_one_or_none()
    
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found"
        )
    
    if agent.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Agent is not active"
        )
    
    # 这里应该调用实际的Agent执行逻辑
    # 目前返回模拟结果
    return {
        "agent_id": str(agent_id),
        "status": "executing",
        "message": "Agent execution started"
    }
