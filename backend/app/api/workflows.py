from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
import structlog
from app.agents.coder import CoderAgent
from app.agents.reviewer import ReviewerAgent
from app.workflows.code_review import CodeReviewWorkflow
from app.workflows.task_planner import TaskPlannerWorkflow
from app.workflows.answer_store import workflow_answer_store
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
    user_id: Optional[str] = None,
) -> Optional[str]:
    """尽力而为地把 workflow 执行（复盘）归档进 tasks 表，失败不阻断主流程。

    长任务自动留痕：父记录为一次执行复盘，子记录为 TaskPlanner 各子任务。
    归档成功后，若提供了 user_id，则把执行答案向量化（语义索引，供检索）。
    Returns: 父任务 task_id（可用于 /tasks 查询），归档失败返回 None。
    """
    parent_task_id = f"wf-{uuid4().hex[:12]}"
    title = f"[{label}] {(objective or '').strip()[:150]}"
    try:
        # 归档行归属执行者，防止 tasks 表出现跨用户可见的无主记录
        try:
            owner_id = UUID(user_id) if user_id else None
        except (ValueError, TypeError):
            owner_id = None
        now = datetime.utcnow()
        parent = Task(
            task_id=parent_task_id,
            title=title,
            description="Workflow 自动归档：长任务执行复盘留痕",
            status="completed" if success else "failed",
            input_data={"workflow_label": label, "objective": objective},
            output_data={"recap": recap, "detail": detail},
            completed_at=now,
            user_id=owner_id,
        )
        db.add(parent)
        await db.flush()

        sub_task_ids: List[str] = []
        for item in subtasks or []:
            status_ = item.get("status", "pending")
            sub_task_id = f"{parent_task_id}-{int(item.get('seq', 0)):03d}"
            sub_task_ids.append(sub_task_id)
            db.add(Task(
                task_id=sub_task_id,
                parent_task_id=parent.id,
                title=(item.get("title") or item.get("type") or "subtask")[:150],
                status=status_,
                input_data={"task_type": item.get("type")},
                output_data=item.get("detail"),
                completed_at=now if status_ in ("completed", "failed") else None,
                user_id=owner_id,
            ))
        await db.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("workflow.archive_failed", error=str(e))
        await db.rollback()
        return None

    # 归档落库成功后再做语义索引（尽力而为，失败仅告警不阻断返回）
    if user_id:
        try:
            indexed = await workflow_answer_store.index_run_async(
                user_id=user_id,
                task_id=parent_task_id,
                title=title,
                workflow_label=label,
                objective=objective,
                success=success,
                recap=recap,
                detail=detail,
                subtasks=[
                    {**item, "task_id": sid}
                    for item, sid in zip(subtasks or [], sub_task_ids)
                ],
            )
            if indexed:
                logger.info("workflow.answer_indexed", task_id=parent_task_id, docs=indexed)
        except Exception as e:  # noqa: BLE001
            logger.warning("workflow.answer_index_failed", task_id=parent_task_id, error=str(e))
    return parent_task_id


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
                user_id=str(current_user.id),
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
                user_id=str(current_user.id),
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


@router.get("/answers/search")
async def semantic_search_workflow_answers(
    query: str = Query(..., min_length=1, max_length=500, description="自然语言问题"),
    limit: int = Query(5, ge=1, le=20, description="返回条数"),
    workflow_label: Optional[str] = Query(None, description="按工作流筛选，如：任务规划 / 代码审查"),
    status: Optional[str] = Query(None, description="按结果状态筛选：completed / failed"),
    current_user: User = Depends(get_current_active_user),
):
    """语义检索历史 workflow 执行答案（执行档案向量索引）。

    仅返回当前用户归档时建立的索引（user_id 隔离）。索引后端不可用时
    返回 available=False，不报错，便于上层 UI 优雅降级。
    """
    if not workflow_answer_store.available:
        return {
            "success": True,
            "available": False,
            "query": query,
            "count": 0,
            "results": [],
            "detail": workflow_answer_store.error or "workflow 答案语义索引未启用",
        }
    results = await workflow_answer_store.search_async(
        user_id=str(current_user.id),
        query=query,
        top_k=limit,
        workflow_label=workflow_label,
        status=status,
    )
    return {
        "success": True,
        "available": True,
        "query": query,
        "count": len(results),
        "results": results,
    }
