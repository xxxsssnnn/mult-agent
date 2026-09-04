# 计划：Workflow 执行引擎（DAG 依赖解析 + 有界并行 + 子任务超时/重试）

> 状态：**草稿**（待评审）
> 日期：2026-09-03
> 作者：CodeBuddy（配合用户"企业级任务规划与多 Agent 编排"路线图）

## 一、背景与动机

对照业界先进水平（LangGraph checkpoint 持久化、3-5 子 Agent 并行、Anthropic
orchestrator-worker 实践、子任务级超时/重试护栏），本项目 `TaskPlannerWorkflow`
目前的编排是最原始的形态：

- LangGraph 图是 **analyze → execute_task(自循环) → aggregate** 的严格顺序执行，
  `SubTask.dependencies` 字段被 LLM 产出但**从未被解析使用**；
- 无任何并行——互不依赖的子任务也只能排队执行；
- 无子任务级超时/重试：单个子任务挂死会拖死整条链，失败只能靠 workflow 外层
  整体重跑（已完成的子任务被重复执行）；
- 子任务上下文是"最近 2 个结果"这种顺序假设，与 DAG 依赖语义不匹配。

本计划只做**执行引擎**一层（阶段 1 的核心可交付物）。运行台账持久化 /
checkpoint 断点恢复 / human-in-the-loop 等属后续计划，本计划为其留好接口。

## 二、目标与非目标

### 目标
1. 新增纯异步 DAG 执行引擎：解析 `dependencies`、拓扑就绪调度、**有界并发**
   执行、子任务级**超时**与**重试**、循环依赖显式报错。
2. `TaskPlannerWorkflow` 接入引擎：图改为阶段状态机
   `analyze → run_dag → aggregate`，任务调度交给引擎。
3. 外部契约**零破坏**：`execute()` 返回值结构、`tasks/results` 顺序对齐、
   `metadata.recap`、会话记忆挂载、归档 API 均保持不变。
4. 兼容性护栏：默认**不因依赖失败跳过下游**（保住既有离线行为与"尽力继续"
   语义）；提供 `skip_on_failure` 选项供显式开启。

### 非目标（后续计划）
- 运行明细入库 / checkpoint 断点恢复（Plan 2）
- human-in-the-loop 审批点（Plan 3）
- LLM 统一 Client / tool calling / 流式 / 模型分级（Plan 4）
- 动态 replan（依赖智能分解的规划器升级）

## 三、拟议方案

新增独立模块 `backend/app/workflows/execution.py`：

- 不依赖 LLM / LangGraph / DB，可 100% 离线单测；
- 暴露 `ExecutionOptions`（dataclass）与 `execute_dag()`；
- `execute_dag` 内部：id/依赖校验 → 拓扑排序（环 → `CyclicDependencyError`）
  → "就绪集 + 并发窗口"调度循环，每个子任务经
  `asyncio.wait_for(超时)` + 失败计数（重试）包裹；
- 任务完成即把结果写入 `results`，供下游取 context；下游的 context = **仅其
  直接依赖且成功**的结果映射，不再用"最近 N 个"顺序假设。

`TaskPlannerWorkflow` 图改造：

```
原：analyze_task ─► execute_task ─(自循环)→ execute_task ─► aggregate_results
新：analyze_task ─► run_dag(引擎并行) ─► aggregate_results
```

`_execute_by_type(task, state)` 重构为 `_run_single_task(task, context_text, attempt)`，
context 文本由引擎传入（按依赖 map 拼装）；`execute_task` / `check_next_task`
删除；`TaskPlanState` 移除 `current_task_index`。

## 四、技术设计

### 4.1 新模块 `backend/app/workflows/execution.py`

```python
"""Workflow 子任务 DAG 并发执行引擎。

纯异步、零外部依赖（不 import LLM / LangGraph / DB），供离线单元测试。
语义：
- tasks 依 dependencies 形成 DAG；环 → CyclicDependencyError
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
    task_timeout_seconds: float = 120.0 # 单任务超时
    task_max_retries: int = 1           # 失败后最多额外重试次数（总尝试=+1）
    skip_on_failure: bool = False       # 依赖失败是否跳过下游（默认不跳过）
    priority_key: str = "priority"      # 就绪排序字段，值大先跑


# run_task: (task, context_map, attempt) -> result(dict)
#   task        当前子任务 dict（含 id/dependencies/...）
#   context_map  {dep_id: dep_result}，仅含已成功完成的直接依赖
#   attempt      第几次尝试（1 起）
#   result       至少含 task_id/status(completed|failed)/output
RunTaskFn = Callable[[Dict[str, Any], Dict[int, Dict[str, Any]], int], Awaitable[Dict[str, Any]]]


def detect_cycle(tasks: List[Dict[str, Any]]) -> Optional[List[int]]:
    """拓扑检测，返回构成环的任务 id 链；无环返回 None。"""


def validate_tasks(tasks: List[Dict[str, Any]]) -> None:
    """校验 id 唯一、依赖引用的 id 存在；否则抛 ValueError。"""


async def execute_dag(
    tasks: List[Dict[str, Any]],
    run_task: RunTaskFn,
    options: Optional[ExecutionOptions] = None,
) -> Dict[str, Any]:
    """并发执行整个 DAG。

    返回：
      {"results": {task_id: result},   # 每个任务最终一次执行的结果
       "order": [task_id, ...],        # 实际启动顺序
       "attempts": {task_id: n}}       # 每个任务实际尝试次数
    """
```

**调度循环（实现要点）**：

```
validate → 建立 by_id / dep_map / attempts / pending 全集
while pending or running:
    # 1) 填满并发窗口
    while len(running) < max_concurrency and pending:
        ready = [t in pending: 直接依赖均已产生结果
                 and (not skip_on_failure or 无依赖 failed/skipped)]
        if not ready: break
        tid = ready 中 priority 最高者
        ctx  = {d: results[d] for d in dep_map[tid] if results[d].status == "completed"}
        running[tid] = create_task(_guarded_run(tid, ctx))   # 记录启动顺序
    # 2) 若窗口空且仍有 pending（仅 skip 场景）：剩余标 skipped
    if not running:
        pending 全部 → results 记 {"status": "skipped", "error": "dependency_failed"}
        continue
    # 3) 等任意完成
    done = await wait(running.values(), FIRST_COMPLETED)
    for 每个完成:
        res = t.result()          # _guarded_run 保证不抛
        if res.status == "failed" and attempts[tid] <= task_max_retries:
            pending.add(tid)      # 放回就绪，下次重试
        else:
            results[tid] = res    # 终态（completed / failed / skipped）
```

`_guarded_run`：

```python
async def _guarded_run(self, tid, ctx):
    attempts[tid] += 1
    try:
        res = await asyncio.wait_for(run_task(by_id[tid], ctx, attempts[tid]),
                                     timeout=task_timeout_seconds)
        res = dict(res or {})
        res.setdefault("task_id", tid)
        res.setdefault("status", "completed" if res.get("success", True) else "failed")
    except asyncio.TimeoutError:
        res = {"task_id": tid, "status": "failed",
               "output": "", "error": f"task timeout after {task_timeout_seconds}s"}
    except Exception as e:          # run_task 自身异常兜底
        res = {"task_id": tid, "status": "failed", "output": "", "error": str(e)}
    return res
```

### 4.2 config 新增（`backend/app/core/config.py`，接在 L139 workflow 段落后）

```python
    # --- Workflow 执行引擎（DAG 并行 / 子任务护栏） ---
    # 同一时刻最多并发执行的子任务数
    WORKFLOW_MAX_CONCURRENCY: int = int(os.getenv("WORKFLOW_MAX_CONCURRENCY", "2"))
    # 单个子任务执行超时（秒），超时按失败计并可重试
    WORKFLOW_TASK_TIMEOUT_SECONDS: float = float(os.getenv("WORKFLOW_TASK_TIMEOUT_SECONDS", "120"))
    # 子任务失败后的额外重试次数（总尝试 = 该值 + 1）
    WORKFLOW_TASK_MAX_RETRIES: int = int(os.getenv("WORKFLOW_TASK_MAX_RETRIES", "1"))
```

### 4.3 `TaskPlannerWorkflow` 改造（`backend/app/workflows/task_planner.py`）

- `TaskPlanState`：删除 `current_task_index`（L30）。
- `build_graph`（L60-88）：`add_node("run_dag", self.run_dag)`，
  edges：`analyze_task → run_dag → aggregate_results → END`；删除
  `execute_task` 节点与 `check_next_task` 条件边（L76-83）。
- 删除 `execute_task`（L298-335）与 `check_next_task`（L510-514）。
- `_execute_by_type`（L337-509）签名改为 `_run_single_task(task, context_text, attempt)`：
  - 内部把原 `state` 依赖全部换成 `context_text` 参数（原 L343-344 的
    `previous_results[-2:]` 拼接逻辑移到 run_dag 适配层）；
  - code_generation/code_review 分支中 `result.get("output", "")` 内容
    （L344 的 context）用传入的 `context_text`。
- 新增 `run_dag` 节点方法：

```python
async def run_dag(self, state: TaskPlanState) -> TaskPlanState:
    """由 DAG 引擎并行执行全部子任务（依赖/超时/重试由引擎负责）"""
    report = await execute_dag(
        state["tasks"],
        self._dag_runner(),
        ExecutionOptions(
            max_concurrency=settings.WORKFLOW_MAX_CONCURRENCY,
            task_timeout_seconds=settings.WORKFLOW_TASK_TIMEOUT_SECONDS,
            task_max_retries=settings.WORKFLOW_TASK_MAX_RETRIES,
        ),
    )
    for t in state["tasks"]:
        res = report["results"].get(t["id"])
        if res:
            t["status"] = res.get("status", t.get("status", "pending"))
    # results 与 tasks 同序对齐（归档/复盘依赖 zip(tasks, results)）
    state["results"] = [report["results"][t["id"]] for t in state["tasks"]]
    state["status"] = "executed"
    return state
```

- 新增 `_dag_runner` 适配（context 文本按直接依赖拼装，保留 task 内既有的
  agent 创建 / memory attach / LLM 分发逻辑）：

```python
def _dag_runner(self):
    async def run_task(task: Dict[str, Any],
                       ctx: Dict[int, Dict[str, Any]],
                       attempt: int) -> Dict[str, Any]:
        context_text = "\n".join(
            f"Task {d}: {r.get('output', '')}" for d, r in sorted(ctx.items())
        )
        try:
            return await self._run_single_task(task, context_text, attempt)
        except Exception as e:  # 引擎仍有兜底，这里保证字段与旧结构一致
            return {"task_id": task["id"], "status": "failed",
                    "output": f"执行失败: {e}", "error": str(e)}
    return run_task
```

- `execute`（L535-643）：`TaskPlanState(...)` 去掉 `current_task_index=0`（L550），
  其余不动（外层 workflow 级重试仍保留为兜底）。

### 4.4 兼容性决策（评审重点）

| 旧行为 | 新行为 | 影响 |
|---|---|---|
| 顺序执行，依赖字段被忽略 | DAG 就绪调度 + 有界并行 | 独立子任务提速；依赖链仍保序 |
| context = 最近 2 个任务输出 | context = 仅直接依赖且成功的输出 | 更符合 DAG 语义；无依赖任务 context 为空（同旧初始） |
| 单任务失败 → 整体继续，已完成任务不重跑 | 单任务失败按策略重试（默认 1 次额外重试） | 失败自动补救 |
| 无超时 | 默认 120s 超时 | 防挂死 |
| analysis/testing/general 离线（无 LLM Key）必失败但下游照跑 | 默认不跳过 → 行为一致 | 离线测试零回归 |
| 失败依赖的产出不可达 | skip_on_failure 显式开启才跳过 | 默认语义不变 |

### 4.5 影响文件清单

| 文件 | 改动 |
|---|---|
| `backend/app/workflows/execution.py` | **新增**：DAG 引擎 |
| `backend/app/core/config.py` | 追加 3 个环境变量 |
| `backend/app/workflows/task_planner.py` | 图 + 执行层重构 |
| `backend/tests/test_workflow_execution.py` | **新增**：引擎单测 + workflow 集成回归 |
| `run_tests.ps1` | 注册新测试套件 |
| `docs/CHANGELOG.md` | 记录本次改动 |

## 五、任务分解（每个任务产出独立可验证结果）

> 所有测试沿用仓库风格：`python tests/test_workflow_execution.py` 直接运行，
> 断言式 `ok()`，退出码表达成败，纯离线（无 LLM/DB/Redis）。

### Task 1 — `execution.py` 骨架：校验 + 环检测 + 顺序执行（concurrency=1）

- **先写测试** `tests/test_workflow_execution.py`：
  - `test_detect_cycle`：`[{1 dep 2}, {2 dep 1}]` → `detect_cycle` 返回非空；
    `[{1 dep 2}, {2 dep 3}, {3}]` 无环返回 None。
  - `test_validate_duplicate_id` / `test_validate_missing_dep` → `ValueError`。
  - `test_sequential_execution`：`execute_dag(tasks=[{1},{2},{3}],
    concurrency=1)` 下所有任务按 id 顺序执行，`order == [1,2,3]`，
    `results` 全部 completed，`attempts == {1:1,2:1,3:1}`。
- 实现：`validate_tasks` / `detect_cycle` / `execute_dag` 最小可用版
  （串行，超时/重试/skip 未实现亦可先行保证结构）。

### Task 2 — 依赖就绪调度 + context 传递 + priority 排序

- **先写测试**：
  - `test_dependency_ordering`：tasks 1 无依赖、2 依赖 1；用共享事件列表记录
    `run_task` 的 `(tid, ctx_keys)`；断言 2 的 ctx 含 1 的结果、且 1 先于 2 完成。
  - `test_context_only_completed_deps`：3 依赖 1 与 2；2 失败时（默认不跳过）
    3 仍执行且 ctx 只含 1。
  - `test_priority_order`：并发窗口充足时（max_concurrency=5）priority=5 的
    空依赖任务先于 priority=1 启动。
- 实现：就绪集、`_deps_done`、ctx 构造、priority 排序、FIRST_COMPLETED 主循环。

### Task 3 — 有界并发

- **先写测试**：
  - `test_concurrency_window`：3 个无依赖任务，concurrency=2；run_task 用
    `asyncio.Event` 同步，记录同时运行峰值；断言峰值 == 2 且总完成 ≥ 2 个时间片。
  - `test_concurrency_respected_on_replenish`：4 个无依赖任务 concurrency=2，
    峰值 ≤ 2。
- 实现：并发窗口填满/补位逻辑（第 3 节调度循环）。

### Task 4 — 子任务超时与重试

- **先写测试**：
  - `test_timeout_marks_failed`：run_task sleep 0.1s，`task_timeout_seconds=0.02`
    → 终态 failed，error 含 "timeout"，attempts== 重试上限+1。
  - `test_retry_then_success`：run_task 首次失败、第二次成功（按 attempt 分支）
    → 终态 completed，attempts[2] == 2。
  - `test_retry_exhausted`：永远失败 → failed，attempts == task_max_retries + 1，
    run_task 总调用次数 == 该值。
  - `test_retry_disabled`：`task_max_retries=0` → 失败即终态，attempts==1。
- 实现：`_guarded_run`（wait_for 超时 + 异常兜底）+ 调度循环里重试放回 pending。

### Task 5 — skip_on_failure（可选跳过）

- **先写测试**：
  - `test_skip_on_failure`：1 失败、2 依赖 1、`skip_on_failure=True` → 2 为
    skipped 且 run_task 未被调用 2。
  - `test_default_no_skip`：同场景但默认 False → 2 仍执行。
- 实现：ready 判定中的 `_deps_blocked` 分支与"窗口空则剩余标 skipped"收尾。

### Task 6 — config 三项 + 注册测试套件

- 在 `config.py` 追加 4.2 的三个变量；
- `run_tests.ps1` 套件列表追加 `tests/test_workflow_execution.py`；
- 无新测试，靠 Task 7 集成回归兜底。

### Task 7 — `TaskPlannerWorkflow` 接入引擎（图改造 + 执行层重构）

- 按 4.3 改造 `task_planner.py`。
- **集成测试**（`test_workflow_execution.py` 的 workflow 小节）：
  - `test_workflow_contract_preserved`：`TaskPlannerWorkflow().execute(
    {"user_input": "实现一个斐波那契函数"})` 返回 `success/tasks/results/status/
    metadata.recap` 齐全，tasks 状态与 results 一一对应，order 与 id 一致；
  - `test_workflow_dependency_chain_offline`：输入含"系统"走 3 任务链
    （analysis 无 key 失败 → code_generation mock 成功 → testing 依赖失败
    分析输出不足仍执行）——验证默认不跳过且链上后置任务照跑；
  - `test_workflow_memory_attached_via_dag`：`memory_manager=fake` 时子 Agent
    仍被挂载共享记忆（复用 test_workflow_memory 的 FakeMemoryManager）。
- **回归**：`python tests/test_workflow_memory.py`、
  `python tests/test_workflow_recap.py`、`python tests/test_workflow_answer_search.py`
  全绿（这些套件直接踩 `execute()` 返回契约）。

### Task 8 — CHANGELOG + 收尾验证

- `docs/CHANGELOG.md` 顶部按现有格式追加条目；
- `python -m py_compile` 涉及文件 + 全量 `run_tests.ps1`（21+1 套件）全绿。

## 六、验证与评审标准

1. `tests/test_workflow_execution.py`：引擎单测覆盖 环/校验/顺序/依赖/context/
   priority/并发窗口/超时/重试/skip 全部语义；workflow 集成小节覆盖契约回归。
2. 既有 `test_workflow_memory.py` / `test_workflow_recap.py` /
   `test_workflow_answer_search.py` 与全量 22 套件全绿。
3. `execute()` 返回值结构前后完全一致（逐字段比对）。
4. 无真实 LLM/DB 情况下全部可离线运行。

## 七、风险与回滚

- **context 语义变化**：无依赖任务看不到全局结果。缓解：任务通常带依赖链；
  语义与 DAG 一致；plan 文档与 CHANGELOG 记录该变化。
- **并发执行与既有 mock 的隔离**：code_generation 每次创建新 CoderAgent、
  并发 attach 各自 memory manager，互不共享可变状态 → 无竞态。集成测试覆盖。
- **回滚**：改动集中在 task_planner 单个文件 + 新增纯模块，git 可单独 revert。

## 八、后续衔接（不属于本计划）

Plan 2：运行台账持久化（workflow_runs / workflow_run_tasks 实时落库）+
LangGraph checkpointer 断点恢复与失败子任务重放。
Plan 3：human-in-the-loop（审批节点）。Plan 4：LLM 统一 Client /
tool calling / 模型分级与 token 预算。Plan 5：评估集与决策轨迹可观测性。
