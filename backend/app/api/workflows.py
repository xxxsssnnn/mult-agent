from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
import structlog
from app.agents.coder import CoderAgent
from app.agents.reviewer import ReviewerAgent
from app.workflows.code_review import CodeReviewWorkflow
from app.workflows.task_planner import TaskPlannerWorkflow
from app.core.database import get_db
from app.core.deps import get_current_active_user
from app.models.task import Task
from app.models.user import User

logger = structlog.get_logger(__name__)

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


async def _archive_run(
    db: AsyncSession,
    label: str,
    objective: str,
    success: bool,
    recap: Optional[Dict[str, Any]],
    detail: Dict[str, Any],
    subtasks: Optional[List[Dict[str, Any]]] = None,
) -> Optional[str]:
    """尽力而为地把 workflow 执行（复盘）归档进 tasks 表，失败不阻断主流程。

    长任务自动留痕：父记录为一次执行复盘，子记录为 TaskPlanner 各子任务。
    Returns: 父任务 task_id（可用于 /tasks 查询），归档失败返回 None。
    """
    parent_task_id = f"wf-{uuid4().hex[:12]}"
    try:
        now = datetime.utcnow()
        parent = Task(
            task_id=parent_task_id,
            title=f"[{label}] {(objective or '').strip()[:150]}",
            description="Workflow 自动归档：长任务执行复盘留痕",
            status="completed" if success else "failed",
            input_data={"workflow_label": label, "objective": objective},
            output_data={"recap": recap, "detail": detail},
            completed_at=now,
        )
        db.add(parent)
        await db.flush()

        for item in subtasks or []:
            status_ = item.get("status", "pending")
            db.add(Task(
                task_id=f"{parent_task_id}-{int(item.get('seq', 0)):03d}",
                parent_task_id=parent.id,
                title=(item.get("title") or item.get("type") or "subtask")[:150],
                status=status_,
                input_data={"task_type": item.get("type")},
                output_data=item.get("detail"),
                completed_at=now if status_ in ("completed", "failed") else None,
            ))
        await db.commit()
        return parent_task_id
    except Exception as e:  # noqa: BLE001
        logger.warning("workflow.archive_failed", error=str(e))
        await db.rollback()
        return None


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
        
        # 长任务自动归档（尽力而为；可通过 archive=false 关闭）
        if workflow_input.get("archive", True):
            meta = result.get("metadata") or {}
            await _archive_run(
                db,
                label="代码审查",
                objective=workflow_input.get("requirement", ""),
                success=result.get("success", False),
                recap=meta.get("recap"),
                detail={
                    "approved": result.get("approved"),
                    "iterations": result.get("iterations"),
                    "code_length": len(result.get("code") or ""),
                    "review_length": len(result.get("review") or ""),
                },
            )
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
        
        # 长任务自动归档：父任务为执行复盘，子任务为各子任务执行明细
        if workflow_input.get("archive", True):
            meta = result.get("metadata") or {}
            subtasks = [
                {
                    "seq": i,
                    "type": t.get("task_type"),
                    "title": t.get("title"),
                    "status": t.get("status"),
                    "detail": res,
                }
                for i, (t, res) in enumerate(
                    zip(result.get("tasks", []), result.get("results", []))
                )
            ]
            await _archive_run(
                db,
                label="任务规划",
                objective=workflow_input.get("user_input", ""),
                success=result.get("success", False),
                recap=meta.get("recap"),
                detail={
                    "status": result.get("status"),
                    "total_tasks": len(subtasks),
                    "completed": meta.get("completed_tasks"),
                    "failed": meta.get("failed_tasks"),
                },
                subtasks=subtasks,
            )
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
