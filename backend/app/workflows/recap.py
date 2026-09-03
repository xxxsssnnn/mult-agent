"""Workflow 执行复盘（task recap）——纯函数、零外部依赖，供离线测试

长任务（多 Agent / 多轮次）执行结束后生成结构化复盘：

- 客观描述执行结果（目标、轮次、子任务明细、成功/失败、结论）
- 可写入会话记忆（assistant 消息，kind=task_recap），让后续同会话请求
  get_context 时自然携带"上次任务做到哪、结果如何"
- 可随结果 metadata.recap 返回，也可由 API 层归档进 tasks 表留痕
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

WORKFLOW_LABELS = {
    "task_planner_workflow": "任务规划执行",
    "code_review_workflow": "代码生成与审查",
}


def build_recap(
    workflow_name: str,
    *,
    objective: str = "",
    success: bool = True,
    attempts: int = 1,
    iterations: Optional[int] = None,
    summary: Optional[Dict[str, int]] = None,
    tasks: Optional[List[Dict[str, Any]]] = None,
    notes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """组装结构化复盘（确定性字段 + 可选任务明细）。"""
    return {
        "workflow": workflow_name,
        "label": WORKFLOW_LABELS.get(workflow_name, workflow_name),
        "objective": (objective or "").strip(),
        "success": bool(success),
        "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "attempts": int(attempts),
        "iterations": int(iterations) if iterations is not None else None,
        "summary": dict(summary or {}),
        "tasks": [
            {
                "id": t.get("id"),
                "type": t.get("type") or t.get("task_type"),
                "status": t.get("status"),
                "summary": (t.get("summary") or t.get("title") or "")[:200],
            }
            for t in (tasks or [])
        ],
        "notes": list(notes or []),
    }


def format_recap(recap: Dict[str, Any]) -> str:
    """把结构化复盘渲染为可读文本（写入记忆 / 展示用）。"""
    label = recap.get("label") or recap.get("workflow") or "工作流"
    lines = [
        f"【{label} · 复盘】",
        f"- 时间：{recap.get('completed_at')}",
        f"- 目标：{recap.get('objective') or '（未提供）'}",
        f"- 结果：{'成功' if recap.get('success') else '失败'}",
        f"- 尝试次数：{recap.get('attempts', 1)}",
    ]
    if recap.get("iterations") is not None:
        lines.append(f"- 迭代轮次：{recap['iterations']}")

    summary = recap.get("summary") or {}
    if summary:
        parts = []
        if "total_tasks" in summary:
            parts.append(f"任务 {summary.get('total_tasks', 0)} 个")
        if "completed_tasks" in summary:
            parts.append(f"完成 {summary['completed_tasks']}")
        if "failed_tasks" in summary:
            parts.append(f"失败 {summary['failed_tasks']}")
        if "approved" in summary:
            parts.append(f"审查{'通过' if summary['approved'] else '未通过'}")
        if parts:
            lines.append(f"- 汇总：{'，'.join(parts)}")

    tasks = recap.get("tasks") or []
    if tasks:
        lines.append("- 子任务明细：")
        for t in tasks:
            t_type = t.get("type") or t.get("id") or "?"
            lines.append(f"  - [{t.get('status')}] {t_type}: {t.get('summary') or '-'}")

    notes = recap.get("notes") or []
    if notes:
        lines.append("- 备注：")
        for n in notes:
            lines.append(f"  - {n}")
    return "\n".join(lines)
