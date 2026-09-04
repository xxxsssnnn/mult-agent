"""Workflow DAG 执行引擎回归测试（离线，无 LLM/DB/Redis）

验证执行引擎语义：
- id/依赖校验、循环依赖检测
- 依赖就绪调度：子任务在其直接依赖结束后才启动，context 仅含成功依赖
- 有界并发：同时运行的任务数不超过 max_concurrency
- 子任务超时（asyncio.wait_for）与失败重试（attempt 递增、重试耗尽终态）
- skip_on_failure：显式开启后，依赖失败的下游被跳过；默认不跳过
- priority 降序就绪

通过 `python tests/test_workflow_execution.py` 直接运行。
"""
import asyncio
import os
import sys
import time
from typing import Any, Dict, List

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 确保离线
_ = os.environ.pop("OPENAI_API_KEY", None)

from app.workflows.execution import (  # noqa: E402
    CyclicDependencyError,
    ExecutionOptions,
    detect_cycle,
    execute_dag,
    validate_tasks,
)

PASSED = []
FAILED = []


def ok(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(
        f"  [{'PASS' if condition else 'FAIL'}] {name}"
        + (f" | {detail}" if not condition else "")
    )


# --------------------------------------------------------------------------- #
# 测试替身
# --------------------------------------------------------------------------- #


async def run_task_ok(task: Dict[str, Any], ctx: Dict[int, Dict[str, Any]], attempt: int) -> Dict[str, Any]:
    """直接成功的 run_task 替身：输出 = f"out-{id}" """
    return {
        "task_id": task["id"],
        "status": "completed",
        "output": f"out-{task['id']}",
        "task_type": task.get("task_type", "general"),
    }


def make_tasks(specs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按 [(id, deps, priority, ...)] 生成任务 dict 列表"""
    tasks = []
    for spec in specs:
        tid = spec[0]
        deps = spec[1] if len(spec) > 1 else []
        priority = spec[2] if len(spec) > 2 else 1
        task_type = spec[3] if len(spec) > 3 else "general"
        tasks.append({
            "id": tid,
            "dependencies": list(deps),
            "priority": priority,
            "task_type": task_type,
            "status": "pending",
        })
    return tasks


# --------------------------------------------------------------------------- #
# 1. 校验与环检测
# --------------------------------------------------------------------------- #


def test_validate_tasks():
    print("== 校验与环检测 ==")
    try:
        validate_tasks([
            {"id": 1, "dependencies": []},
            {"id": 1, "dependencies": []},
        ])
        ok("重复 id 抛 ValueError", False, "未抛异常")
    except ValueError:
        ok("重复 id 抛 ValueError", True)

    try:
        validate_tasks([{"id": 1, "dependencies": [99]}])
        ok("依赖不存在抛 ValueError", False, "未抛异常")
    except ValueError:
        ok("依赖不存在抛 ValueError", True)

    # 合法输入不抛
    validate_tasks(make_tasks([(1, []), (2, [1])]))
    ok("合法 DAG 不抛异常", True)


def test_detect_cycle():
    print("== 环检测 ==")
    acyclic = make_tasks([(1, []), (2, [1]), (3, [1, 2])])
    ok("无环返回 None", detect_cycle(acyclic) is None)

    cyclic = make_tasks([(1, [2]), (2, [1])])
    cyc = detect_cycle(cyclic)
    ok("自引用环被检出", cyc is not None and len(cyc) > 0, str(cyc))

    self_loop = make_tasks([(1, [1])])
    ok("直接自环被检出", detect_cycle(self_loop) is not None)

    longer_cycle = make_tasks([(1, [3]), (2, [1]), (3, [2])])
    ok("三节点环被检出", detect_cycle(longer_cycle) is not None)


# --------------------------------------------------------------------------- #
# 2. 顺序执行（concurrency=1）
# --------------------------------------------------------------------------- #


async def test_sequential_execution():
    print("== 顺序执行（concurrency=1）==")
    calls = []

    async def run_rec(task, ctx, attempt):
        calls.append(task["id"])
        return await run_task_ok(task, ctx, attempt)

    tasks = make_tasks([(1, []), (2, []), (3, [])])
    report = await execute_dag(tasks, run_rec, ExecutionOptions(max_concurrency=1))

    ok("按 id 顺序启动", calls == [1, 2, 3], str(calls))
    ok("启动顺序记录", report["order"] == [1, 2, 3], str(report["order"]))
    ok("全部完成",
       all(report["results"][tid]["status"] == "completed" for tid in (1, 2, 3)),
       str(report["results"]))
    ok("每任务仅尝试一次", report["attempts"] == {1: 1, 2: 1, 3: 1}, str(report["attempts"]))


# --------------------------------------------------------------------------- #
# 3. 依赖就绪调度 + context 精确传递 + priority
# --------------------------------------------------------------------------- #


async def test_dependency_ordering():
    print("== 依赖就绪调度 ==")
    events: List[str] = []
    results_cache: Dict[int, Dict[str, Any]] = {}

    async def run_rec(task, ctx, attempt):
        tid = task["id"]
        events.append(f"start-{tid}")
        # 记录本任务启动时可见的依赖输出（下游的 ctx 应已含依赖结果）
        results_cache[tid] = {
            "ctx_keys": sorted(ctx.keys()),
            "ctx_out": ctx.get(1, {}).get("output") if 1 in ctx else None,
        }
        events.append(f"end-{tid}")
        return await run_task_ok(task, ctx, attempt)

    tasks = make_tasks([(1, []), (2, [1])])
    await execute_dag(tasks, run_rec, ExecutionOptions(max_concurrency=5))

    ok("依赖任务先完成", events.index("end-1") < events.index("start-2"),
       str(events))
    ok("下游在依赖输出就绪后才启动", results_cache[2]["ctx_keys"] == [1]
       and results_cache[2]["ctx_out"] == "out-1", str(results_cache[2]))
    ok("上游任务不感知下游", results_cache[1]["ctx_keys"] == [],
       str(results_cache[1]))


async def test_context_only_completed_deps():
    print("== context 仅含成功依赖（失败依赖不阻塞下游）==")
    calls: List[int] = []
    seen_ctx: Dict[int, List[int]] = {}

    async def run_rec(task, ctx, attempt):
        tid = task["id"]
        calls.append(tid)
        seen_ctx[tid] = sorted(ctx.keys())
        if tid == 2:
            return {"task_id": 2, "status": "failed", "output": "", "error": "boom"}
        return await run_task_ok(task, ctx, attempt)

    # 3 依赖 1 与 2；1 成功、2 失败 → 默认不跳过，3 仍执行
    tasks = make_tasks([(1, []), (2, []), (3, [1, 2])])
    report = await execute_dag(tasks, run_rec, ExecutionOptions(max_concurrency=5))

    ok("失败任务未阻断下游（默认语义）", 3 in calls, str(calls))
    ok("3 的 ctx 仅含成功依赖 1", seen_ctx.get(3) == [1], str(seen_ctx))
    ok("1 完成且 2 终态失败",
       report["results"][1]["status"] == "completed"
       and report["results"][2]["status"] == "failed",
       str(report["results"]))


async def test_priority_order():
    print("== priority 降序就绪 ==")
    calls: List[int] = []

    async def run_rec(task, ctx, attempt):
        calls.append(task["id"])
        return await run_task_ok(task, ctx, attempt)

    tasks = make_tasks([(1, [], 1), (2, [], 5), (3, [], 3), (4, [], 5)])
    report = await execute_dag(tasks, run_rec, ExecutionOptions(max_concurrency=5))

    order = report["order"]
    ok("高 priority 任务先启动", order.index(2) < order.index(1)
       and order.index(4) < order.index(1), f"order={order}")
    ok("中 priority 紧随其后", order.index(3) < order.index(1), f"order={order}")


# --------------------------------------------------------------------------- #
# 4. 有界并发窗口
# --------------------------------------------------------------------------- #


async def test_concurrency_window():
    print("== 有界并发 ==")
    counter = {"active": 0, "peak": 0}
    tasks_done: List[int] = []

    async def run_barrier(task, ctx, attempt):
        counter["active"] += 1
        counter["peak"] = max(counter["peak"], counter["active"])
        await asyncio.sleep(0.05)
        counter["active"] -= 1
        tasks_done.append(task["id"])
        return await run_task_ok(task, ctx, attempt)

    tasks = make_tasks([(1, []), (2, []), (3, [])])
    await execute_dag(tasks, run_barrier, ExecutionOptions(max_concurrency=2))

    ok("并发峰值不超过 2", counter["peak"] <= 2, f"peak={counter['peak']}")
    ok("并发窗口被充分利用（峰值达到 2）", counter["peak"] == 2, f"peak={counter['peak']}")
    ok("全部任务完成", sorted(tasks_done) == [1, 2, 3], str(tasks_done))


async def test_concurrency_replenish():
    print("== 并发补位（4 任务 / 窗口 2）==")
    counter = {"active": 0, "peak": 0, "finished": 0}

    async def run_barrier(task, ctx, attempt):
        counter["active"] += 1
        counter["peak"] = max(counter["peak"], counter["active"])
        await asyncio.sleep(0.03)
        counter["active"] -= 1
        counter["finished"] += 1
        return await run_task_ok(task, ctx, attempt)

    tasks = make_tasks([(1, []), (2, []), (3, []), (4, [])])
    await execute_dag(tasks, run_barrier, ExecutionOptions(max_concurrency=2))

    ok("并发峰值不超过 2", counter["peak"] <= 2, f"peak={counter['peak']}")
    ok("四个任务全部完成", counter["finished"] == 4, str(counter))


# --------------------------------------------------------------------------- #
# 5. 子任务超时与重试
# --------------------------------------------------------------------------- #


async def test_timeout_marks_failed():
    print("== 子任务超时 ==")

    async def run_slow(task, ctx, attempt):
        await asyncio.sleep(0.1)  # 超过超时阈值
        return await run_task_ok(task, ctx, attempt)

    tasks = make_tasks([(1, [])])
    report = await execute_dag(
        tasks, run_slow, ExecutionOptions(task_timeout_seconds=0.02)
    )

    res = report["results"][1]
    ok("超时终态为 failed", res["status"] == "failed", str(res))
    ok("错误信息含 timeout", "timeout" in res.get("error", "").lower(),
       res.get("error", ""))
    # 默认 max_retries=1：首次超时 + 1 次重试也超时 → 共尝试 2 次
    ok("超时后按策略重试耗尽", report["attempts"][1] == 2, str(report["attempts"]))


async def test_retry_then_success():
    print("== 重试后成功 ==")
    calls: List[int] = []

    async def run_flaky(task, ctx, attempt):
        calls.append(attempt)
        if attempt == 1:
            return {"task_id": task["id"], "status": "failed",
                    "output": "", "error": "first try boom"}
        return await run_task_ok(task, ctx, attempt)

    tasks = make_tasks([(1, [])])
    report = await execute_dag(
        tasks, run_flaky, ExecutionOptions(task_max_retries=1)
    )

    ok("第二次尝试成功", report["results"][1]["status"] == "completed",
       str(report["results"]))
    ok("恰好尝试两次", report["attempts"][1] == 2, str(report["attempts"]))
    ok("run_task 收到递增 attempt", calls == [1, 2], str(calls))


async def test_retry_exhausted():
    print("== 重试耗尽 ==")
    calls: List[int] = []

    async def run_always_fail(task, ctx, attempt):
        calls.append(attempt)
        return {"task_id": task["id"], "status": "failed",
                "output": "", "error": "always boom"}

    tasks = make_tasks([(1, [])])
    report = await execute_dag(
        tasks, run_always_fail, ExecutionOptions(task_max_retries=2)
    )

    ok("终态为 failed", report["results"][1]["status"] == "failed",
       str(report["results"]))
    ok("总尝试 = max_retries + 1 = 3", report["attempts"][1] == 3,
       str(report["attempts"]))
    ok("共调用 3 次", len(calls) == 3, str(calls))


async def test_retry_disabled():
    print("== 关闭重试（max_retries=0）==")

    async def run_always_fail(task, ctx, attempt):
        return {"task_id": task["id"], "status": "failed",
                "output": "", "error": "boom"}

    tasks = make_tasks([(1, [])])
    report = await execute_dag(
        tasks, run_always_fail, ExecutionOptions(task_max_retries=0)
    )

    ok("失败即终态", report["results"][1]["status"] == "failed",
       str(report["results"]))
    ok("仅尝试一次", report["attempts"][1] == 1, str(report["attempts"]))


# --------------------------------------------------------------------------- #
# 6. skip_on_failure（可选跳过；默认不跳过）
# --------------------------------------------------------------------------- #


async def test_skip_on_failure():
    print("== skip_on_failure=True：失败依赖的下游被跳过 ==")
    calls: List[int] = []

    async def run_rec(task, ctx, attempt):
        calls.append(task["id"])
        if task["id"] == 1:
            return {"task_id": 1, "status": "failed",
                    "output": "", "error": "boom"}
        return await run_task_ok(task, ctx, attempt)

    tasks = make_tasks([(1, []), (2, [1]), (3, [2]), (4, [])])
    report = await execute_dag(
        tasks, run_rec, ExecutionOptions(max_concurrency=5, skip_on_failure=True)
    )

    ok("独立任务 4 不受影响", report["results"][4]["status"] == "completed",
       str(report["results"]))
    ok("失败任务 1 终态 failed", report["results"][1]["status"] == "failed",
       str(report["results"]))
    ok("下游 2 被标记 skipped", report["results"][2]["status"] == "skipped",
       str(report["results"]))
    ok("skipped 传递到 3", report["results"][3]["status"] == "skipped",
       str(report["results"]))
    # 任务 1 失败后会按 max_retries=1 重试一次，因此 calls 含 [1, 4, 1]；
    # 关键断言：被跳过的 2/3 从未进入 run_task
    ok("跳过任务未执行 run_task", 2 not in calls and 3 not in calls, str(calls))
    ok("失败任务自身按策略重试过一次", calls.count(1) == 2, str(calls))


async def test_default_no_skip():
    print("== 默认（skip_on_failure=False）：失败依赖不阻断下游 ==")
    calls: List[int] = []

    async def run_rec(task, ctx, attempt):
        calls.append(task["id"])
        if task["id"] == 1:
            return {"task_id": 1, "status": "failed",
                    "output": "", "error": "boom"}
        return await run_task_ok(task, ctx, attempt)

    tasks = make_tasks([(1, []), (2, [1])])
    report = await execute_dag(tasks, run_rec, ExecutionOptions(max_concurrency=5))

    ok("下游 2 仍执行", 2 in calls, str(calls))
    ok("下游 2 正常完成", report["results"][2]["status"] == "completed",
       str(report["results"]))
    ok("没有 skipped 标记", all(
        r["status"] != "skipped" for r in report["results"].values()),
       str(report["results"]))


# --------------------------------------------------------------------------- #
# 7. TaskPlannerWorkflow 集成：DAG 路径下对外契约不变
# --------------------------------------------------------------------------- #


async def test_workflow_integration_contract():
    print("== TaskPlannerWorkflow 集成契约（DAG 引擎路径）==")
    from app.workflows.task_planner import TaskPlannerWorkflow

    wf = TaskPlannerWorkflow()
    res = await wf.execute({"user_input": "实现一个订单管理系统（含数据库）"})

    ok("契约: success", res.get("success") is True, str(res.get("error")))
    tasks = res.get("tasks", [])
    results = res.get("results", [])
    ok("契约: tasks 非空", len(tasks) > 0, str(tasks))
    ok("契约: tasks/results 同序对齐",
       len(tasks) == len(results)
       and all(r.get("task_id") == t["id"] for t, r in zip(tasks, results)),
       str([(t["id"], r.get("task_id"), r.get("status")) for t, r in zip(tasks, results)]))
    ok("契约: 任务状态已由引擎更新（无 pending 残留）",
       all(t.get("status") in ("completed", "failed") for t in tasks),
       str([(t["id"], t.get("status")) for t in tasks]))
    ok("契约: metadata.recap 存在", "recap" in res.get("metadata", {}),
       str(list(res.get("metadata", {}).keys())))
    ok("契约: code_generation 离线 mock 成功",
       any(r.get("status") == "completed"
           and r.get("task_type") == "code_generation" for r in results),
       str([(r.get("task_type"), r.get("status")) for r in results]))


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


async def main():
    await test_workflow_integration_contract()
    test_validate_tasks()
    test_detect_cycle()
    await test_sequential_execution()
    await test_dependency_ordering()
    await test_context_only_completed_deps()
    await test_priority_order()
    await test_concurrency_window()
    await test_concurrency_replenish()
    await test_timeout_marks_failed()
    await test_retry_then_success()
    await test_retry_exhausted()
    await test_retry_disabled()
    await test_skip_on_failure()
    await test_default_no_skip()


if __name__ == "__main__":
    asyncio.run(main())
    print("")
    if FAILED:
        print(f"FAILED ({len(FAILED)}): {FAILED}")
        sys.exit(1)
    print(f"ALL PASSED ({len(PASSED)} assertions)")
    sys.exit(0)
