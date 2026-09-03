from typing import Any, Dict, Optional
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.agents.coder import CoderAgent
from app.agents.reviewer import ReviewerAgent
from app.workflows.code_review import CodeReviewWorkflow
from app.workflows.task_planner import TaskPlannerWorkflow
from app.core.database import get_db
from app.core.deps import get_current_active_user
from app.models.user import User

router = APIRouter(prefix="/workflows", tags=["workflows"])


def _memory_state(
    workflow_input: Dict[str, Any],
    current_user: User,
    db: AsyncSession,
) -> Dict[str, Any]:
    """按请求组装工作流的会话记忆状态（未启用时为空 dict，行为不变）。

    请求可带：
      enable_memory: bool  是否启用工作流会话记忆（默认 False）
      session_id:   str    会话 ID（不传则自动生成，返回给客户端以便延续会话）
    """
    if not workflow_input.get("enable_memory", False):
        return {}
    session_id = str(workflow_input.get("session_id") or uuid4())
    return {
        "memory": {
            "session_id": session_id,
            "user_id": str(current_user.id),
            "db_session": db,
        }
    }


@router.post("/code-review")
async def execute_code_review_workflow(
    workflow_input: Dict[str, Any],
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """执行代码审查工作流（可启用会话记忆）"""
    try:
        # 创建Agent实例
        coder = CoderAgent(agent_id=uuid4(), name="WorkflowCoder")
        reviewer = ReviewerAgent(agent_id=uuid4(), name="WorkflowReviewer")
        
        # 初始化Agent
        await coder.initialize()
        await reviewer.initialize()
        
        # 创建工作流
        workflow = CodeReviewWorkflow(
            coder_agent=coder,
            reviewer_agent=reviewer,
            max_iterations=workflow_input.get("max_iterations", 3)
        )
        
        # 执行工作流（会话记忆配置合并进初始状态）
        initial_state: Dict[str, Any] = {
            "requirement": workflow_input.get("requirement", ""),
            "language": workflow_input.get("language", "python"),
            **_memory_state(workflow_input, current_user, db),
        }
        result = await workflow.execute(initial_state)
        
        return result
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Workflow execution failed: {str(e)}"
        )


@router.post("/task-planner")
async def execute_task_planner_workflow(
    workflow_input: Dict[str, Any],
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """执行任务规划工作流（可启用会话记忆）"""
    try:
        # 创建工作流
        workflow = TaskPlannerWorkflow()
        
        # 执行工作流
        initial_state: Dict[str, Any] = {
            "user_input": workflow_input.get("user_input", ""),
            **_memory_state(workflow_input, current_user, db),
        }
        result = await workflow.execute(initial_state)
        
        return result
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Workflow execution failed: {str(e)}"
        )


@router.get("/info")
async def get_workflow_info(current_user: User = Depends(get_current_active_user)):
    """获取可用工作流信息"""
    return {
        "workflows": [
            {
                "name": "code_review_workflow",
                "description": "Automated code generation and review with iterative refinement",
                "endpoint": "/api/v1/workflows/code-review"
            },
            {
                "name": "task_planner_workflow",
                "description": "Break down complex tasks and execute them sequentially",
                "endpoint": "/api/v1/workflows/task-planner"
            }
        ]
    }
