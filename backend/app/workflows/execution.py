"""Workflow 子任务 DAG 并发执行引擎。

纯异步、零外部依赖（不 import LLM / LangGraph / DB），供离线单元测试。

语义（对应 docs/superpowers/plans/2026-09-03-workflow-execution-engine.md）：
- tasks 依 dependencies 构成 DAG；环 → CyclicDependencyError
- 无依赖或依赖已结束的任务进入就绪集；同批按 priority 降序启动
- 并发窗口 = max_concurrency，任意完成立即补位
- 每个子任务独立计时（task_timeout_seconds），超时按失败计
- 失败后重试至多 task_max_retries 次（总尝试 = task_max_retries + 1）
- 默认失败不阻断下游；skip_on_failure=True 时依赖 failed/skipped 的下游跳过
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional


class CyclicDependencyError(ValueError):
    """子任务依赖构成环，DAG 无法执行。"""


@dataclass
class ExecutionOptions:
    max_concurrency: int = 2            # 有界并发窗口
    task_timeout_seconds: float = 120.0  # 单任务超时
    task_max_retries: int = 1           # 失败后最多额外重试次数（总尝试 = 该值 + 1）
    skip_on_failure: bool = False       # 依赖失败是否跳过下游（默认不跳过）
    priority_key: str = "priority"      # 就绪排序字段，值大先跑


# run_task: (task, context_map, attempt) -> result(dict)
#   task        当前子任务 dict（含 id/dependencies/...）
#   context_map  {dep_id: dep_result}，仅含已成功完成的直接依赖
#   attempt      第几次尝试（1 起）
#   result       至少含 task_id/status(completed|failed)/output
RunTaskFn = Callable[
    [Dict[str, Any], Dict[int, Dict[str, Any]], int],
    Awaitable[Dict[str, Any]],
]


def _dependencies(task: Dict[str, Any]) -> List[int]:
    return list(task.get("dependencies") or [])


def validate_tasks(tasks: List[Dict[str, Any]]) -> None:
    """校验 id 唯一、依赖引用的 id 存在；否则抛 ValueError。"""
    ids = [t["id"] for t in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("子任务 id 必须唯一")
    id_set = set(ids)
    for t in tasks:
        missing = [d for d in _dependencies(t) if d not in id_set]
        if missing:
            raise ValueError(
                f"任务 {t['id']} 依赖不存在的任务: {missing}"
            )


def detect_cycle(tasks: List[Dict[str, Any]]) -> Optional[List[int]]:
    """Kahn 拓扑检测；无环返回 None，有环返回一条环的任务 id 链。"""
    ids = [t["id"] for t in tasks]
    id_set = set(ids)
    deps: Dict[int, List[int]] = {t["id"]: _dependencies(t) for t in tasks}
    indegree = {t["id"]: len(_dependencies(t)) for t in tasks}

    queue = [tid for tid in ids if indegree[tid] == 0]
    order = []
    while queue:
        cur = queue.pop(0)
        order.append(cur)
        for t in tasks:
            if cur in _dependencies(t):
                indegree[t["id"]] -= 1
                if indegree[t["id"]] == 0:
                    queue.append(t["id"])

    remaining = [tid for tid in ids if tid not in order]
    if not remaining:
        return None

    # 剩余集合中任意节点沿依赖行走必回到自身形成环（Kahn 性质）
    remaining_set = set(remaining)
    path: List[int] = []
    seen_pos: Dict[int, int] = {}
    cur = remaining[0]
    while cur not in seen_pos:
        seen_pos[cur] = len(path)
        path.append(cur)
        nxt = [d for d in deps[cur] if d in remaining_set]
        cur = nxt[0] if nxt else cur  # Kahn 性质保证有出边；兜底防死循环
    return path[seen_pos[cur]:] or path


# on_settle: 每任务到达终态后收到一次部分快照（供外部增量持久化）
SettleHook = Callable[[Dict[str, Any]], Awaitable[None]]


async def execute_dag(
    tasks: List[Dict[str, Any]],
    run_task: RunTaskFn,
    options: Optional[ExecutionOptions] = None,
    *,
    resume: Optional[Dict[str, Any]] = None,
    on_settle: Optional[SettleHook] = None,
) -> Dict[str, Any]:
    """执行子任务 DAG。

    参数：
      resume:     先前保存的 checkpoint（{"results": {tid: 终态结果},
                 "attempts": {tid: n}}）。在 results 中的任务直接复用终态
                 结果、不再执行；不在其中的任务（上次未终态）正常调度。
      on_settle:  每个子任务进入终态（completed/failed/skipped）后调用
                 一次，参数为部分快照 dict：{results, attempts, order,
                 running, pending}。回调异常被吞掉，不阻断调度。

    返回：
      {"results": {task_id: result},   # 每个任务最终一次执行的结果
       "order": [task_id, ...],        # 实际启动顺序
       "attempts": {task_id: n}}       # 每个任务实际尝试次数
    """
    opts = options or ExecutionOptions()
    validate_tasks(tasks)
    cycle = detect_cycle(tasks)
    if cycle:
        raise CyclicDependencyError(f"子任务依赖构成环: {cycle}")

    by_id: Dict[int, Dict[str, Any]] = {t["id"]: dict(t) for t in tasks}
    dep_map: Dict[int, List[int]] = {
        tid: list(_dependencies(task)) for tid, task in by_id.items()
    }
    results: Dict[int, Dict[str, Any]] = {}
    attempts: Dict[int, int] = {tid: 0 for tid in by_id}
    order: List[int] = []
    running: Dict[int, asyncio.Task] = {}
    pending: set = set(by_id)

    # resume：校验并直接采用已终态任务（completed/failed/skipped）
    if resume:
        seed_results = resume.get("results") or {}
        seed_attempts = resume.get("attempts") or {}
        valid_statuses = ("completed", "failed", "skipped")

        def _norm_key(k: Any) -> Any:
            """checkpoint 经 JSON 持久化后 int 任务键会变 str，这里归一化。"""
            if k in by_id:
                return k
            try:
                ikey = int(k)
            except (TypeError, ValueError):
                return k
            return ikey if ikey in by_id else k

        seed_results = {_norm_key(k): v for k, v in seed_results.items()}
        seed_attempts = {_norm_key(k): v for k, v in seed_attempts.items()}

        for tid, res in seed_results.items():
            if tid not in by_id:
                raise ValueError(f"resume 引用了不存在的任务: {tid}")
            if res.get("status") not in valid_statuses:
                raise ValueError(
                    f"resume 任务 {tid} 状态非法: {res.get('status')!r}"
                )
        for tid, res in seed_results.items():
            results[tid] = dict(res)
            # 未提供尝试数时按 0 计（skipped/未运行过）；引擎正常跑过的
            # 任务在 checkpoint 中必然带 ≥1 的 attempts
            attempts[tid] = int(seed_attempts.get(tid, 0))
            pending.discard(tid)

    def _snapshot() -> Dict[str, Any]:
        """当前部分状态快照（供 on_settle / 持久化观察方使用）"""
        return {
            "results": dict(results),
            "attempts": dict(attempts),
            "order": list(order),
            "running": list(running),
            "pending": sorted(pending),
        }

    async def settle(tid: int, res: Dict[str, Any]) -> None:
        """写入终态结果并（可选）触发 on_settle；回调异常不阻断调度。"""
        results[tid] = res
        if on_settle:
            try:
                await on_settle(_snapshot())
            except Exception:  # 外部持久化失败不影响核心调度
                pass

    def deps_done(tid: int) -> bool:
        """直接依赖均已产生终态结果（无论成败）"""
        return all(d in results for d in dep_map[tid])

    def deps_blocked(tid: int) -> bool:
        """存在直接依赖为 failed/skipped（仅 skip_on_failure 时生效）"""
        return any(
            results.get(d, {}).get("status") in ("failed", "skipped")
            for d in dep_map[tid]
        )

    def _ctx_of(tid: int) -> Dict[int, Dict[str, Any]]:
        return {
            d: results[d]
            for d in dep_map[tid]
            if results[d].get("status") == "completed"
        }

    async def guarded_run(tid: int, ctx: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
        """单次尝试：超时 + 异常兜底。返回至少含 task_id/status 的结果。"""
        attempts[tid] += 1
        try:
            res = await asyncio.wait_for(
                run_task(by_id[tid], ctx, attempts[tid]),
                timeout=opts.task_timeout_seconds,
            )
            res = dict(res or {})
            res.setdefault("task_id", tid)
            res.setdefault("status", "completed" if res.get("success", True) else "failed")
        except asyncio.TimeoutError:
            res = {
                "task_id": tid,
                "status": "failed",
                "output": "",
                "error": f"task timeout after {opts.task_timeout_seconds}s",
            }
        except Exception as e:  # run_task 自身异常兜底
            res = {"task_id": tid, "status": "failed", "output": "", "error": str(e)}
        return res

    # 就绪集 + 并发窗口 调度循环
    while pending or running:
        # 1) 填满并发窗口
        while len(running) < opts.max_concurrency and pending:
            ready = [
                tid
                for tid in pending
                if deps_done(tid)
                and not (opts.skip_on_failure and deps_blocked(tid))
            ]
            if not ready:
                break
            # priority 降序；同分按 id 升序（确定性）
            ready.sort(key=lambda t: (-by_id[t].get(opts.priority_key, 1), t))
            tid = ready[0]
            pending.discard(tid)
            order.append(tid)
            running[tid] = asyncio.create_task(guarded_run(tid, _ctx_of(tid)))

        # 2) 窗口空但仍有 pending：全部被失败依赖阻塞（仅 skip 模式可达）
        if not running:
            for tid in list(pending):
                skipped = {
                    "task_id": tid,
                    "status": "skipped",
                    "output": "依赖任务失败或缺失，跳过",
                    "error": "dependency_failed",
                }
                pending.discard(tid)
                await settle(tid, skipped)
            continue

        # 3) 等任意任务完成，即时补位 / 处理重试
        done, _ = await asyncio.wait(
            running.values(), return_when=asyncio.FIRST_COMPLETED
        )
        for fut in done:
            tid = next(k for k, v in running.items() if v is fut)
            res = fut.result()
            running.pop(tid)
            if res["status"] == "failed" and attempts[tid] <= opts.task_max_retries:
                pending.add(tid)  # 放回就绪集，下次填窗口时重试
            else:
                await settle(tid, res)

    return {"results": results, "order": order, "attempts": attempts}
