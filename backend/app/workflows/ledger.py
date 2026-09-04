"""Workflow 运行台账：workflow_runs 表读写。

AsyncSession 直用（与 API 请求同事务）。按 user_id 租户隔离。
写入失败不在此吞异常——由调用方按"尽力而为"处理（引擎 on_settle 钩子
已在 engine 侧兜底）；读取失败照常抛出，避免静默回退造成困惑。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow_run import WorkflowRun


def _row_meta(row: WorkflowRun) -> Dict[str, Any]:
    """行元数据摘要（不含完整 checkpoint，供列表/概览）。"""
    cp: Dict[str, Any] = row.checkpoint or {}
    return {
        "run_id": row.run_id,
        "label": row.label,
        "objective": row.objective,
        "status": row.status,
        "error_message": row.error_message,
        "task_count": len(cp.get("tasks") or []),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


class SQLRunLedger:
    """按用户隔离的 workflow 运行台账。"""

    def __init__(self, db: AsyncSession, user_id: str) -> None:
        self._db = db
        self._user_id = str(user_id)

    async def create(self, run_id: str, *, label: str, objective: str) -> None:
        """新建 running 状态台账行。"""
        self._db.add(WorkflowRun(
            run_id=run_id,
            user_id=self._user_id,
            label=label,
            objective=objective,
            status="running",
        ))
        await self._db.flush()

    async def save_checkpoint(self, run_id: str, checkpoint: Dict[str, Any]) -> None:
        """幂等 upsert checkpoint；台账行缺失时按 checkpoint 元数据补建。"""
        row = await self._get_row(run_id)
        if row is None:
            self._db.add(WorkflowRun(
                run_id=run_id,
                user_id=self._user_id,
                label=str(checkpoint.get("label") or "workflow"),
                objective=str(checkpoint.get("objective") or ""),
                status=str(checkpoint.get("status") or "running"),
                checkpoint=checkpoint,
            ))
        else:
            row.checkpoint = checkpoint
            row.status = checkpoint.get("status", row.status or "running")
        await self._db.flush()

    async def finalize(self, run_id: str, *, status: str,
                       error: Optional[str] = None) -> None:
        """收尾：标记终态 + completed_at（尽力而为；无台账则跳过）。"""
        row = await self._get_row(run_id)
        if row is None:
            return
        row.status = status
        row.error_message = error
        row.completed_at = datetime.utcnow()
        await self._db.flush()

    async def load_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """返回该用户 run 的 checkpoint dict（断点恢复用）；无则 None。"""
        row = await self._get_row(run_id)
        if row is None or not row.checkpoint:
            return None
        return row.checkpoint

    async def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        """概览查询：元数据 + 完整 checkpoint（如存在）。"""
        row = await self._get_row(run_id)
        if row is None:
            return None
        meta = _row_meta(row)
        if row.checkpoint:
            meta["checkpoint"] = row.checkpoint
        return meta

    async def list(self, *, limit: int = 20,
                   status: Optional[str] = None) -> List[Dict[str, Any]]:
        """本人台账按 updated_at 倒序（可加 status 过滤）。"""
        stmt = (
            select(WorkflowRun)
            .where(WorkflowRun.user_id == self._user_id)
            .order_by(WorkflowRun.updated_at.desc())
            .limit(limit)
        )
        if status:
            stmt = stmt.where(WorkflowRun.status == status)
        rows = (await self._db.execute(stmt)).scalars().all()
        return [_row_meta(r) for r in rows]

    async def _get_row(self, run_id: str) -> Optional[WorkflowRun]:
        stmt = select(WorkflowRun).where(
            WorkflowRun.user_id == self._user_id,
            WorkflowRun.run_id == run_id,
        )
        return (await self._db.execute(stmt)).scalar_one_or_none()
