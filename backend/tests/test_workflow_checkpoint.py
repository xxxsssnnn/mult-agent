"""Workflow 运行台账 + checkpoint 断点恢复回归测试（离线优先）

覆盖（对应 docs/superpowers/plans/2026-09-04-workflow-run-ledger-resume.md）：
- Task1：引擎 on_settle 终态回调（快照格式/失败免疫/重试只触发一次）
- Task2：引擎 resume 断点恢复（seed 复用、失败保持、非法校验）
- Task3：checkpoint 纯函数 build/extract（JSON round-trip 幂等）
- Task4：SQLRunLedger 台账（临时 sqlite + create_all）
- Task5：TaskPlannerWorkflow 接入（FakeStore 内存实现）

通过 `python tests/test_workflow_checkpoint.py` 直接运行。
"""
import asyncio
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 确保离线
_ = os.environ.pop("OPENAI_API_KEY", None)

from app.workflows.execution import (  # noqa: E402
    ExecutionOptions,
    execute_dag,
)

PASSED = []
FAILED = []


def ok(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(
        f"  [{'PASS' if condition else 'FAIL'}] {name}"
        + (f" | {detail}" if not condition else "")
    )


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
# Task1: 引擎 on_settle 终态回调
# --------------------------------------------------------------------------- #


async def test_settle_hook_per_terminal():
    print("== on_settle：每个终态触发一次，快照递增 ==")
    snapshots: List[Dict[str, Any]] = []

    async def hook(snap):
        snapshots.append(snap)

    tasks = make_tasks([(1, []), (2, [1]), (3, [2])])  # 依赖链保证终态顺序确定
    report = await execute_dag(
        tasks, run_task_ok,
        ExecutionOptions(max_concurrency=5), on_settle=hook,
    )

    ok("3 个任务各触发一次", len(snapshots) == 3, str(len(snapshots)))
    ok("results 键集合严格递增",
       [sorted(s["results"]) for s in snapshots] == [[1], [1, 2], [1, 2, 3]],
       str([[sorted(s["results"])] for s in snapshots]))
    ok("末次快照 results 与最终 report 一致",
       snapshots[-1]["results"] == report["results"], str(report))
    ok("快照 attempts 正确（含重试累计）",
       snapshots[-1]["attempts"] == {1: 1, 2: 1, 3: 1}, str(snapshots[-1]["attempts"]))
    ok("快照含 order/running/pending 视图",
       all(k in snapshots[0] for k in ("order", "running", "pending")))
    ok("快照内 running 与 results 互斥",
       all(not (set(s["running"]) & set(s["results"])) for s in snapshots),
       str([(s["running"], sorted(s["results"])) for s in snapshots]))


async def test_settle_hook_failure_ignored():
    print("== on_settle：回调异常不阻断调度 ==")

    async def hook(snap):
        raise RuntimeError("persist boom")

    tasks = make_tasks([(1, []), (2, [])])
    report = await execute_dag(
        tasks, run_task_ok, ExecutionOptions(max_concurrency=2), on_settle=hook,
    )
    ok("引擎照常完成",
       all(r["status"] == "completed" for r in report["results"].values()),
       str(report))


async def test_settle_only_on_terminal_retry():
    print("== on_settle：失败重试仅终态触发一次 ==")
    snapshots: List[Dict[str, Any]] = []

    async def hook(snap):
        snapshots.append(snap)

    async def run_flaky(task, ctx, attempt):
        if attempt == 1:
            return {"task_id": task["id"], "status": "failed",
                    "output": "", "error": "first boom"}
        return await run_task_ok(task, ctx, attempt)

    tasks = make_tasks([(1, [])])
    await execute_dag(
        tasks, run_flaky, ExecutionOptions(task_max_retries=1), on_settle=hook,
    )
    ok("首次失败（重试放回）不触发 settle，终态才触发一次",
       len(snapshots) == 1, str(len(snapshots)))
    ok("终态为 completed", snapshots[0]["results"][1]["status"] == "completed",
       str(snapshots[0]["results"]))
    ok("attempts 反映两次尝试", snapshots[0]["attempts"] == {1: 2},
       str(snapshots[0]["attempts"]))


# --------------------------------------------------------------------------- #
# Task2: 引擎 resume 断点恢复语义
# --------------------------------------------------------------------------- #


async def test_resume_reuses_completed():
    print("== resume：已终态直接复用，只跑未终态 ==")
    calls: List[tuple] = []

    async def run_rec(task, ctx, attempt):
        calls.append((task["id"], sorted(ctx.keys()), attempt))
        return await run_task_ok(task, ctx, attempt)

    seed = {
        "results": {
            1: {"task_id": 1, "status": "completed", "output": "out-1"},
            2: {"task_id": 2, "status": "completed", "output": "out-2"},
        },
        "attempts": {1: 3, 2: 1},
    }
    tasks = make_tasks([(1, []), (2, [1]), (3, [1, 2])])
    report = await execute_dag(
        tasks, run_rec, ExecutionOptions(max_concurrency=5), resume=seed,
    )

    ok("仅执行未终态任务 3", [c[0] for c in calls] == [3], str(calls))
    ok("3 的 ctx 复用 seed 依赖输出",
       calls[0][1] == [1, 2], str(calls))
    ok("seed 结果原样保留（引用一致）",
       report["results"][1] == seed["results"][1]
       and report["results"][2] == seed["results"][2], str(report["results"]))
    ok("seed attempts 保留在统计中", report["attempts"][1] == 3,
       str(report["attempts"]))
    ok("3 正常完成", report["results"][3]["status"] == "completed",
       str(report["results"][3]))
    ok("order 不含 seed 任务", report["order"] == [3], str(report["order"]))


async def test_resume_keeps_failed_and_skipped():
    print("== resume：failed/skipped 保持不重放 ==")
    calls: List[int] = []

    async def run_rec(task, ctx, attempt):
        calls.append(task["id"])
        return await run_task_ok(task, ctx, attempt)

    seed = {
        "results": {
            1: {"task_id": 1, "status": "failed", "output": "", "error": "x"},
            2: {"task_id": 2, "status": "skipped", "output": "", "error": "dep"},
        },
        "attempts": {1: 2},
    }
    tasks = make_tasks([(1, []), (2, [1])])
    report = await execute_dag(
        tasks, run_rec,
        ExecutionOptions(max_concurrency=5, skip_on_failure=True),
        resume=seed,
    )
    ok("无未终态任务 → 零执行", calls == [], str(calls))
    ok("failed 保持 failed", report["results"][1]["status"] == "failed",
       str(report["results"]))
    ok("skipped 保持 skipped", report["results"][2]["status"] == "skipped",
       str(report["results"]))
    ok("attempts 记录历史（skipped 为 0）",
       report["attempts"] == {1: 2, 2: 0}, str(report["attempts"]))


async def test_resume_replays_unfinished():
    print("== resume：崩溃时未终态任务重放 ==")
    calls: List[tuple] = []

    async def run_rec(task, ctx, attempt):
        calls.append((task["id"], sorted(ctx.keys())))
        return await run_task_ok(task, ctx, attempt)

    seed = {
        "results": {
            1: {"task_id": 1, "status": "completed", "output": "out-1"},
        },
        "attempts": {1: 1},
    }
    tasks = make_tasks([(1, []), (2, [1]), (3, [2])])
    report = await execute_dag(
        tasks, run_rec, ExecutionOptions(max_concurrency=5), resume=seed,
    )
    ok("只重放未终态 2、3（按依赖序）",
       [c[0] for c in calls] == [2, 3], str(calls))
    ok("重放任务 ctx 依赖 seed 完成结果",
       calls[0][1] == [1], str(calls))
    ok("重放后全部终态齐", {tid: r["status"] for tid, r in report["results"].items()}
       == {1: "completed", 2: "completed", 3: "completed"}, str(report["results"]))
    ok("重放任务 attempt 从 1 重计", report["attempts"] == {1: 1, 2: 1, 3: 1},
       str(report["attempts"]))


async def test_resume_invalid_seed_rejected():
    print("== resume：非法 seed 校验 ==")
    tasks = make_tasks([(1, []), (2, [1])])

    async def try_invalid(seed):
        try:
            await execute_dag(tasks, run_task_ok,
                              ExecutionOptions(max_concurrency=2),
                              resume=seed)
            return None
        except ValueError as e:
            return str(e)

    e1 = await try_invalid({"results": {99: {"task_id": 99, "status": "completed"}},
                            "attempts": {}})
    ok("引用不存在任务 id → ValueError", e1 is not None and "99" in e1, str(e1))

    e2 = await try_invalid({"results": {1: {"task_id": 1, "status": "running"}},
                            "attempts": {}})
    ok("非终态 status → ValueError", e2 is not None, str(e2))


# --------------------------------------------------------------------------- #
# Task3: checkpoint 纯函数 build / extract / sanitize
# --------------------------------------------------------------------------- #

import json  # noqa: E402
from datetime import datetime  # noqa: E402

from app.workflows.checkpoint import (  # noqa: E402
    CHECKPOINT_VERSION,
    build_checkpoint,
    extract_resume,
    sanitize_tasks,
)
from app.workflows.execution import validate_tasks  # noqa: E402


def _raw_tasks():
    return [
        {
            "id": 1, "title": "需求分析", "description": "分析需求",
            "task_type": "analysis", "priority": 5, "dependencies": [],
            "status": "pending",
            "_transient": lambda: 1,           # 函数 → 不可序列化，应被剔除
            "obj": object(),                   # 对象 → 应被剔除
            "extra": "保留的原生字段",          # 标量 → 应保留
        },
        {
            "id": 2, "title": "编码", "description": "实现功能",
            "task_type": "code_generation", "priority": 1,
            "dependencies": [1], "status": "completed",
        },
    ]


def _partial():
    return {
        "results": {
            1: {"task_id": 1, "status": "completed",
                "output": "out-1", "task_type": "analysis"},
            2: {"task_id": 2, "status": "completed",
                "output": "out-2", "task_type": "code_generation"},
        },
        "attempts": {1: 1, 2: 3},
        "order": [1, 2],
        "running": [],
        "pending": [],
    }


def test_checkpoint_build_roundtrip():
    print("== checkpoint：build + JSON round-trip ==")
    cp = build_checkpoint(run_id="wfrun-abc123", label="任务规划",
                          objective="做一个订单系统", tasks=_raw_tasks(),
                          partial=_partial())
    try:
        cp2 = json.loads(json.dumps(cp))
        ok("整体可 JSON 序列化", True)
    except Exception as e:
        ok("整体可 JSON 序列化", False, str(e))
        return

    ok("往返后元数据一致",
       cp2["version"] == cp["version"] == CHECKPOINT_VERSION
       and cp2["run_id"] == cp["run_id"] == "wfrun-abc123"
       and cp2["label"] == cp["label"] == "任务规划"
       and cp2["objective"] == cp["objective"] == "做一个订单系统",
       str(cp2))
    ok("往返后任务定义无损", cp2["tasks"] == cp["tasks"], str(cp2["tasks"]))
    ok("往返后 attempts 键变 str（引擎 resume 会归一化）",
       all(isinstance(k, str) for k in cp2["attempts"]),
       str(cp2["attempts"].keys()))
    ok("含 saved_at 时间戳", datetime.fromisoformat(cp["saved_at"]).year >= 2026,
       cp.get("saved_at", ""))
    ok("status 为 running（执行中快照）", cp.get("status") == "running", str(cp))

    tasks = cp["tasks"]
    ok("瞬态字段（函数/对象）被剔除",
       all("_transient" not in t and "obj" not in t for t in tasks), str(tasks))
    ok("标量扩展字段保留", "extra" in tasks[0] and tasks[0]["extra"] == "保留的原生字段",
       str(tasks))
    ok("任务结构字段完整",
       all(set(("id", "dependencies", "status")) <= set(t) for t in tasks),
       str(tasks))


async def test_roundtrip_checkpoint_resume_consumable():
    print("== checkpoint：JSON 往返后仍可被引擎 resume 消费 ==")
    cp = build_checkpoint(
        run_id="wfrun-abc", label="任务规划", objective="做一个订单系统",
        tasks=_raw_tasks()[:2],
        partial={"results": {
            1: {"task_id": 1, "status": "completed", "output": "out-1"},
        }, "attempts": {1: 1}},
    )
    cp2 = json.loads(json.dumps(cp))  # 模拟 DB JSON 列往返
    seed = extract_resume(cp2)

    calls: List[int] = []

    async def run_rec(task, ctx, attempt):
        calls.append(task["id"])
        return await run_task_ok(task, ctx, attempt)

    tasks = sanitize_tasks(cp2)
    report = await execute_dag(tasks, run_rec,
                               ExecutionOptions(max_concurrency=2), resume=seed)
    ok("归一化后仅重放未终态任务 2", calls == [2], str(calls))
    ok("seed 结果复用（输出原样）",
       report["results"][1]["output"] == "out-1", str(report["results"]))
    ok("重放任务完成", report["results"][2]["status"] == "completed",
       str(report["results"]))


def test_extract_and_sanitize():
    print("== checkpoint：extract_resume / sanitize_tasks ==")
    cp = build_checkpoint(run_id="wfrun-abc123", label="任务规划",
                          objective="做一个订单系统", tasks=_raw_tasks(),
                          partial=_partial())

    seed = extract_resume(cp)
    ok("extract 出 engine resume 所需结构",
       set(seed) == {"results", "attempts"}, str(seed))
    ok("results 完整保留", seed["results"] == cp["results"], str(seed))
    ok("attempts 完整保留", seed["attempts"] == {1: 1, 2: 3}, str(seed))

    tasks = sanitize_tasks(cp)
    ok("id/依赖与 checkpoint 一致",
       sorted(t["id"] for t in tasks) == [1, 2]
       and tasks[1]["dependencies"] == [1], str(tasks))
    ok("任务状态重置为 pending（交给引擎续跑）",
       all(t["status"] == "pending" for t in tasks), str(tasks))
    ok("sanitize 结果可直接喂给引擎",
       validate_tasks(tasks) is None)
    ok("sanitize 不可序列化字段同样被剔除",
       all("_transient" not in t for t in tasks), str(tasks))


# --------------------------------------------------------------------------- #
# Task4: SQLRunLedger 台账（独立内存 sqlite）
# --------------------------------------------------------------------------- #

from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool  # noqa: E402

import app.models  # noqa: E402,F401  注册全部表元数据
from app.core.database import Base  # noqa: E402
from app.workflows.ledger import SQLRunLedger  # noqa: E402

_ledger_engine = None
_ledger_factory = None


async def _ledger_db() -> AsyncSession:
    global _ledger_engine, _ledger_factory
    if _ledger_engine is None:
        _ledger_engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        async with _ledger_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        _ledger_factory = async_sessionmaker(
            _ledger_engine, class_=AsyncSession, expire_on_commit=False
        )
    return _ledger_factory()


def _mini_checkpoint(run_id: str, n_tasks: int = 1) -> dict:
    return {
        "version": 1,
        "run_id": run_id,
        "label": "任务规划",
        "objective": "做一个订单系统",
        "status": "running",
        "tasks": [{"id": i, "status": "pending"} for i in range(1, n_tasks + 1)],
        "results": {},
        "attempts": {},
        "saved_at": "2026-09-04T10:00:00",
    }


async def test_ledger_lifecycle():
    print("== SQLRunLedger：create/save/load/finalize 生命周期 ==")
    db = await _ledger_db()
    async with db:
        ledger = SQLRunLedger(db, "user-1")
        ok("尚无台账时 load_run → None", await ledger.load_run("wfrun-a") is None)
        ok("尚无台账时 get → None", await ledger.get("wfrun-a") is None)
        ok("新建用户列表为空", await ledger.list() == [])

        await ledger.create("wfrun-a", label="任务规划", objective="做一个订单系统")
        cp = _mini_checkpoint("wfrun-a")
        await ledger.save_checkpoint("wfrun-a", cp)
        loaded = await ledger.load_run("wfrun-a")
        ok("save 后可 load 出 checkpoint", loaded is not None
           and loaded["run_id"] == "wfrun-a" and loaded["tasks"] == cp["tasks"],
           str(loaded))
        ok("list 可见该 run", len(await ledger.list()) == 1
           and (await ledger.list())[0]["run_id"] == "wfrun-a",
           str(await ledger.list()))

        await ledger.finalize("wfrun-a", status="completed")
        meta = await ledger.get("wfrun-a")
        ok("finalize 落状态/时间", meta is not None
           and meta["status"] == "completed" and meta["completed_at"],
           str(meta))
        ok("get 附完整 checkpoint", meta["checkpoint"]["run_id"] == "wfrun-a",
           str(meta))
        ok("load_run 仍返回 checkpoint（可续跑已完成 run）",
           await ledger.load_run("wfrun-a") is not None)


async def test_ledger_ownership_scope():
    print("== SQLRunLedger：用户隔离 ==")
    db = await _ledger_db()
    async with db:
        await SQLRunLedger(db, "user-a").create(
            "wfrun-x", label="任务规划", objective="o")
        ledger_b = SQLRunLedger(db, "user-b")
        ok("他用户 load_run → None", await ledger_b.load_run("wfrun-x") is None)
        ok("他用户 get → None", await ledger_b.get("wfrun-x") is None)
        ok("他用户 list 为空", await ledger_b.list() == [])


async def test_ledger_save_auto_creates_row():
    print("== SQLRunLedger：save_checkpoint 自动补建台账行 ==")
    db = await _ledger_db()
    async with db:
        ledger = SQLRunLedger(db, "user-1")
        await ledger.save_checkpoint("wfrun-z", _mini_checkpoint("wfrun-z"))
        ok("无 create 直接 save 也能落库",
           await ledger.load_run("wfrun-z") is not None)
        ok("补建行元数据来自 checkpoint",
           (await ledger.list())[0]["label"] == "任务规划")


async def test_ledger_list_filter():
    print("== SQLRunLedger：list 排序与过滤 ==")
    db = await _ledger_db()
    async with db:
        ledger = SQLRunLedger(db, "user-1")
        for rid in ("wfrun-1", "wfrun-2", "wfrun-3"):
            await ledger.create(rid, label="任务规划", objective="o")
        await ledger.finalize("wfrun-2", status="completed")
        await ledger.save_checkpoint("wfrun-3", _mini_checkpoint("wfrun-3"))

        all_runs = await ledger.list()
        ok("默认列出全部 3 条", len(all_runs) == 3, str(all_runs))
        ok("limit 生效", len(await ledger.list(limit=1)) == 1)
        completed = await ledger.list(status="completed")
        ok("status 过滤", len(completed) == 1
           and completed[0]["run_id"] == "wfrun-2", str(completed))
        running = await ledger.list(status="running")
        ok("running 过滤剩 2 条（wfrun-1 无 checkpoint 但 running）",
           len(running) == 2, str(running))


# --------------------------------------------------------------------------- #
# Task5: TaskPlannerWorkflow 接入（FakeStore 台账，全离线）
# --------------------------------------------------------------------------- #

import json as _json  # noqa: E402

from app.workflows.task_planner import TaskPlannerWorkflow  # noqa: E402


class FakeStore:
    """内存台账：每次 save 都做 JSON round-trip，模拟 DB JSON 列往返。"""

    def __init__(self, user_id="user-1", preload: dict = None):
        self.user_id = user_id
        self.rows: Dict[str, dict] = {}
        if preload:
            self.rows[preload["run_id"]] = {
                "run_id": preload["run_id"],
                "label": preload.get("label", "任务规划"),
                "objective": preload.get("objective", ""),
                "status": "running",
                "checkpoint": _json.loads(_json.dumps(preload)),
            }
        self.saves: List[dict] = []
        self.finalized: List[tuple] = []

    async def create(self, run_id, *, label, objective):
        self.rows.setdefault(run_id, {
            "run_id": run_id, "label": label, "objective": objective,
            "status": "running", "checkpoint": None,
        })

    async def save_checkpoint(self, run_id, checkpoint):
        row = self.rows.setdefault(run_id, {
            "run_id": run_id,
            "label": checkpoint.get("label", "workflow"),
            "objective": checkpoint.get("objective", ""),
            "status": "running", "checkpoint": None,
        })
        row["checkpoint"] = _json.loads(_json.dumps(checkpoint))
        row["status"] = checkpoint.get("status", "running")
        self.saves.append(row["checkpoint"])

    async def finalize(self, run_id, *, status, error=None):
        row = self.rows.get(run_id)
        if row is None:
            return
        row["status"] = status
        self.finalized.append((run_id, status, error))

    async def load_run(self, run_id):
        row = self.rows.get(run_id)
        return row["checkpoint"] if row and row["checkpoint"] else None

    async def get(self, run_id):
        return self.rows.get(run_id)

    async def list(self, *, limit=20, status=None):
        rows = []
        for row in self.rows.values():
            if status and row["status"] != status:
                continue
            rows.append({"run_id": row["run_id"], "label": row["label"],
                         "objective": row["objective"], "status": row["status"],
                         "task_count": len((row["checkpoint"] or {}).get("tasks") or []),
                         "updated_at": None})
        return rows[:limit]

    @property
    def checkpoint(self):
        return self.saves[-1] if self.saves else None


class CountablePlanner(TaskPlannerWorkflow):
    """离线可跑的任务规划器：强制简单分解 + 计数子任务执行。"""

    def __init__(self):
        super().__init__()
        self.llm = None  # 保证离线 & 走简单分解
        self.task_runs = 0
        self.task_ids: List[int] = []

    async def _run_single_task(self, task, context):
        self.task_runs += 1
        self.task_ids.append(task["id"])
        return {"task_id": task["id"], "status": "completed",
                "output": f"out-{task['id']}",
                "task_type": task.get("task_type", "general")}


def _cfg(run_id: str, store) -> dict:
    return {"run_id": run_id, "store": store, "label": "任务规划",
            "objective": "帮我做一个订单系统"}


async def test_planner_fresh_run_persists():
    print("== TaskPlanner：新 run 增量落库 + 完成收尾 ==")
    wf = CountablePlanner()
    store = FakeStore()
    result = await wf.execute({
        "user_input": "帮我做一个订单系统",
        "checkpoint": _cfg("wfrun-a", store),
    })
    ok("fresh run 成功", result.get("success") is True, str(result))
    ok("metadata 回传 run_id",
       result["metadata"].get("run_id") == "wfrun-a", str(result["metadata"]))
    ok("简单分解跑满 3 个子任务", wf.task_runs == 3 and wf.task_ids == [1, 2, 3],
       str(wf.task_ids))
    ok("每子任务终态各落一次 checkpoint（≥3 次）",
       len(store.saves) >= 3, str(len(store.saves)))
    ok("末次快照含全部终态结果", len(store.checkpoint["results"]) == 3,
       str(store.checkpoint))
    ok("台账收尾为 completed",
       store.finalized and store.finalized[-1][1] == "completed",
       str(store.finalized))


async def test_planner_resume_completed_run():
    print("== TaskPlanner：resume 已全终态 run → 零成本重放 ==")
    wf = CountablePlanner()
    store = FakeStore()
    first = await wf.execute({"user_input": "帮我做一个订单系统",
                              "checkpoint": _cfg("wfrun-a", store)})
    ok("首次运行完成", first.get("success") is True, str(first))

    # 模拟进程重启后的新会话：台账内容来自 DB（JSON 往返已发生）
    store2 = FakeStore(preload=store.rows["wfrun-a"]["checkpoint"])
    wf2 = CountablePlanner()
    second = await wf2.execute({
        "user_input": "（续跑占位）",
        "checkpoint": _cfg("wfrun-a", store2),
    })
    ok("resume 成功", second.get("success") is True, str(second))
    ok("已终态任务零执行（0 次 _run_single_task）", wf2.task_runs == 0,
       str(wf2.task_runs))
    ok("结果与首次一致",
       [r["output"] for r in second["results"]] == ["out-1", "out-2", "out-3"],
       str(second["results"]))
    ok("任务状态全部 completed",
       all(t["status"] == "completed" for t in second["tasks"]),
       str([t["status"] for t in second["tasks"]]))
    ok("metadata 回传同一 run_id",
       second["metadata"].get("run_id") == "wfrun-a", str(second["metadata"]))
    ok("台账再次收尾 completed",
       store2.finalized[-1][1] == "completed", str(store2.finalized))


async def test_planner_resume_half_run():
    print("== TaskPlanner：resume 半程 run → 只续跑未终态任务 ==")
    half_cp = {
        "version": 1, "run_id": "wfrun-b", "label": "任务规划",
        "objective": "帮我做一个订单系统", "status": "running",
        "tasks": [
            {"id": 1, "title": "a", "description": "", "task_type": "analysis",
             "priority": 5, "dependencies": [], "status": "pending"},
            {"id": 2, "title": "b", "description": "", "task_type": "code_generation",
             "priority": 5, "dependencies": [1], "status": "pending"},
            {"id": 3, "title": "c", "description": "", "task_type": "testing",
             "priority": 3, "dependencies": [2], "status": "pending"},
        ],
        "results": {
            "1": {"task_id": 1, "status": "completed", "output": "out-1",
                  "task_type": "analysis"},
            "2": {"task_id": 2, "status": "completed", "output": "out-2",
                  "task_type": "code_generation"},
        },
        "attempts": {"1": 1, "2": 1},
        "saved_at": "2026-09-04T10:00:00",
    }
    store = FakeStore(preload=half_cp)
    wf = CountablePlanner()
    result = await wf.execute({
        "user_input": "（续跑占位）",
        "checkpoint": _cfg("wfrun-b", store),
    })
    ok("resume 半程成功", result.get("success") is True, str(result))
    ok("只执行未终态任务 3", wf.task_runs == 1 and wf.task_ids == [3],
       str(wf.task_ids))
    ok("输出与已终态 seed 一致",
       [r["output"] for r in result["results"]] == ["out-1", "out-2", "out-3"],
       str(result["results"]))
    ok("分析未重跑（任务定义未被改写）",
       [t["id"] for t in result["tasks"]] == [1, 2, 3], str(result["tasks"]))


async def test_planner_no_checkpoint_regression():
    print("== TaskPlanner：无台账配置 → 旧行为零回归 ==")
    wf = CountablePlanner()
    result = await wf.execute({"user_input": "帮我写一个算法实现"})
    ok("无台账 run 正常完成", result.get("success") is True, str(result))
    ok("按关键词分解 2 个任务", len(result["tasks"]) == 2
       and wf.task_runs == 2, str(len(result["tasks"])))
    ok("metadata 不含 run_id", "run_id" not in result["metadata"],
       str(result["metadata"]))


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


async def main():
    await test_settle_hook_per_terminal()
    await test_settle_hook_failure_ignored()
    await test_settle_only_on_terminal_retry()
    await test_resume_reuses_completed()
    await test_resume_keeps_failed_and_skipped()
    await test_resume_replays_unfinished()
    await test_resume_invalid_seed_rejected()
    test_checkpoint_build_roundtrip()
    test_extract_and_sanitize()
    await test_roundtrip_checkpoint_resume_consumable()
    await test_ledger_lifecycle()
    await test_ledger_ownership_scope()
    await test_ledger_save_auto_creates_row()
    await test_ledger_list_filter()
    await test_planner_fresh_run_persists()
    await test_planner_resume_completed_run()
    await test_planner_resume_half_run()
    await test_planner_no_checkpoint_regression()


if __name__ == "__main__":
    asyncio.run(main())
    print("")
    if FAILED:
        print(f"FAILED ({len(FAILED)}): {FAILED}")
        sys.exit(1)
    print(f"ALL PASSED ({len(PASSED)} assertions)")
    sys.exit(0)
