"""Workflow checkpoint 纯函数：build / extract / sanitize。

零外部依赖、纯 JSON 可序列化（不 import LLM / DB / LangGraph），
供运行台账持久化与断点恢复使用。语义对应
docs/superpowers/plans/2026-09-04-workflow-run-ledger-resume.md。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

# checkpoint 结构版本：字段变更时递增，用于恢复时兼容判断
CHECKPOINT_VERSION = 1

# 任务定义中的业务字段白名单（其余标量扩展字段也会保留）
_TASK_ALLOWED = (
    "id",
    "title",
    "description",
    "task_type",
    "priority",
    "dependencies",
    "status",
)


def _json_safe(value: Any) -> bool:
    """值（含嵌套）是否可 JSON 序列化：函数/对象等瞬态一律剔除。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, (list, tuple)):
        return all(_json_safe(v) for v in value)
    if isinstance(value, dict):
        return all(_json_safe(k) and _json_safe(v) for k, v in value.items())
    return False


def _sanitize_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """清洗单个任务：保留业务字段与可序列化标量，剔除瞬态对象。"""
    out: Dict[str, Any] = {}
    for key, value in task.items():
        if key in _TASK_ALLOWED:
            out[key] = value
        elif _json_safe(value):
            out[key] = value
    return out


def build_checkpoint(
    *,
    run_id: str,
    label: str,
    objective: str,
    tasks: List[Dict[str, Any]],
    partial: Dict[str, Any],
) -> Dict[str, Any]:
    """由引擎部分快照组装可持久化的 checkpoint dict。

    tasks 会被清洗（剔除瞬态字段）；partial 为引擎 on_settle 快照
    （含 results/attempts/order/running/pending），这里只取 results/attempts。
    """
    return {
        "version": CHECKPOINT_VERSION,
        "run_id": run_id,
        "label": label,
        "objective": objective,
        "status": "running",
        "tasks": [_sanitize_task(t) for t in tasks],
        "results": partial.get("results", {}),
        "attempts": partial.get("attempts", {}),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }


def extract_resume(checkpoint: Dict[str, Any]) -> Dict[str, Any]:
    """从 checkpoint 提取引擎 resume 参数（已终态 results + attempts）。"""
    return {
        "results": checkpoint.get("results", {}),
        "attempts": checkpoint.get("attempts", {}),
    }


def _coerce_int(value: Any) -> Any:
    """数值字符串（JSON 往返后 int 变 str）转回 int；非数字原样保留。"""
    if isinstance(value, bool) or not isinstance(value, str):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def sanitize_tasks(checkpoint: Dict[str, Any]) -> List[Dict[str, Any]]:
    """恢复可供引擎直接执行的任务定义（状态重置为 pending 交给引擎）。

    JSON 往返后任务 id 可能变数字字符串、dependencies 仍为 int——统一归
    一化为 int，保证依赖引用与引擎 by_id 键一致。
    """
    tasks: List[Dict[str, Any]] = []
    for raw in checkpoint.get("tasks", []):
        task = _sanitize_task(raw)
        task["id"] = _coerce_int(task.get("id"))
        task["dependencies"] = [_coerce_int(d)
                                for d in (task.get("dependencies") or [])]
        task["status"] = "pending"
        tasks.append(task)
    return tasks
