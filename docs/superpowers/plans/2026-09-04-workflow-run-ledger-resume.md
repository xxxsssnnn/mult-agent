# 计划：Workflow 运行台账 + checkpoint 断点恢复（持久化执行）

> 状态：**草稿**（配合路线图阶段 2 推进）
> 日期：2026-09-04
> 作者：CodeBuddy（配合用户"企业级任务规划与多 Agent 编排"路线图）

## 一、背景与动机

阶段 1（执行引擎，2026-09-03 已交付）解决了 DAG 解析、有界并行、子任务级
超时/重试，但**执行过程本身无持久化**：

- `run_dag` 一次性跑完整个 DAG，进程中断 / 异常逃逸（DB 抖动、重启、
  代理超时断连）即全部丢失，重试只能**整轮重跑**（已完成的 code_generation
  LLM 调用被重复消耗）；
- 引擎跑完后才由 API 的 `_archive_run` 一次性归档到 `tasks` 表，无法回答
  "这个 run 现在跑到哪了"；
- 没有任何 run 级实体：执行不可寻址、不可续跑、不可查询。

对照业界（Temporal/DBOS 的 event-sourced 工作流、LangGraph checkpointer
断点恢复、流水线失败重放），阶段 2 交付**持久化执行**：

1. **运行台账**：run 级实体 + 增量 checkpoint（每个子任务终态即落库一次）；
2. **断点恢复**：携带 `run_id` 重发请求时，已终态子任务直接复用其结果，
   未终态子任务继续执行（跳过重复分解/重复 LLM 调用）。

延续阶段 1 的结构纪律：引擎改造保持纯异步、零外部依赖，可 100% 离线测试；
DB/API 为薄接线层；对外 `execute()` 契约零破坏。

## 二、目标与非目标

### 目标
1. 引擎新增**终态回调** `on_settle`：每个子任务进入终态后收到
   当前部分快照（results/attempts/order/running/pending），供外部增量持久化。
2. 引擎新增**恢复语义** `resume`：已终态（completed/failed/skipped）子任务
   直接采用，仅执行台账中无终态记录的子任务（断点续跑；失败子任务 = 崩溃时
   未跑完的任务，重放）。
3. 新增 checkpoint 纯函数（build/extract，JSON round-trip 幂等）。
4. 新增 `workflow_runs` 表（迁移 0005）与 `SQLRunLedger`（AsyncSession 直用）。
5. `TaskPlannerWorkflow` 接入：checkpoint store 鸭子类型注入（默认 None →
   旧行为不变，离线测试不受影响）；`run_dag` 每终态落库一次 checkpoint；
   resume 路径跳过 `analyze_task`、复用已分解任务与已终态结果。
6. API：`POST /workflows/task-planner` 支持 `resume_run_id` 续跑；
   `GET /workflows/task-planner/runs` 列台账（按用户隔离，可查单条状态）。
7. 既有迁移测试 `test_migrations.py` 同步到 0005。

### 非目标（后续计划）
- human-in-the-loop 审批点（Plan 3）
- 后台任务队列 / 真正异步长跑（同步请求模型不变，恢复靠重发请求）
- 多 run 分支 / 版本对比 / 审计报表
- LangGraph 原生 checkpointer（引擎快照已覆盖子任务粒度，LangGraph 只做 3 个大节点）

## 三、拟议方案（语义决策，评审重点）

### 3.1 引擎：终态回调 `on_settle`（纯）

`execute_dag(tasks, run_task, options=None, *, on_settle=None)`。

- `on_settle: Optional[Callable[[Dict[str, Any]], Awaitable[None]]]`；
- 触发时机：每个子任务进入终态（completed / failed 重试耗尽 / skipped）写入
  `results` 之后、调度循环继续之前；
- 快照结构（与最终 report 同构的子集）：
  ```python
  {
    "results": {tid: result},   # 全部已终态任务
    "attempts": {tid: n},       # 已累计尝试次数
    "order": [tid, ...],        # 本 run 启动顺序（resume seed 的不计入）
    "running": [tid, ...],      # 仍执行中
    "pending": [tid, ...],      # 尚未终态且未执行
  }
  ```
- 回调异常**吞掉不阻断调度**（外部观察失败不得破坏核心语义），仅 structlog 告警。
- 注意：同一任务的失败重试（attempt 2 又失败）会先因"重试放回"回到 running/
  pending，**不触发** settle；仅最终终态触发一次。

### 3.2 引擎：恢复语义 `resume`（纯）

```python
async def execute_dag(tasks, run_task, options=None, *,
                      resume: Optional[Dict[str, Any]] = None,
                      on_settle=None) -> Dict[str, Any]
```

`resume` 为先前 save 的 checkpoint 快照（**已终态全集**）：
```python
{"results": {tid: result}, "attempts": {tid: n}}
```

规则（文档写明）：
- `results` 中每个 id 必须存在于 `tasks`，且 status ∈
  {completed, failed, skipped}，否则 `ValueError`；
- 在 `results` 中的任务**直接置入终态，不再执行**（不消耗 LLM）；
  它们原本的 attempts 保留在统计中；
- 不在 `results` 中的任务（崩溃时未终态）正常参与调度，attempt 从 1 重新计
  （崩溃前烧掉的次数不抵后账）；
- 依赖解析照旧：被 seed 任务的 ctx 无需再算；下游 ctx 只取 results 中
  completed 的直接依赖（含 seed 的）。

### 3.3 checkpoint 纯函数（新模块 `workflows/checkpoint.py`）

```python
def build_checkpoint(*, run_id, label, objective,
                     tasks: List[dict], partial: Dict[str, Any]) -> dict
    # -> {"version": 1, "run_id", "label", "objective",
    #     "tasks": [...], "results": {...}, "attempts": {...},
    #     "status": "running", "saved_at": iso}
    # tasks 清洗：仅保留可序列化字段（id/title/description/task_type/
    # priority/dependencies/status），丢弃瞬态（如内存对象）

def extract_resume(checkpoint: dict) -> Dict[str, Any]
    # -> {"results": {...}, "attempts": {...}}（喂给引擎 resume）

def sanitize_tasks(checkpoint: dict) -> List[dict]
    # 恢复可直接进引擎的任务定义（去掉 status 以外的瞬态）
```
JSON round-trip 幂等测试。

### 3.4 台账：`workflow_runs` 表 + 迁移 0005 + `SQLRunLedger`

新表（参考 0004 风格，`sa.Uuid` 在 SQLite 编译 CHAR(32)）：

| 列 | 类型 | 说明 |
|---|---|---|
| id | Uuid pk | 内部主键 |
| run_id | String(64) unique idx | 对外寻址 `wfrun-<hex12>` |
| user_id | String(64) idx | 租户隔离 |
| label | String(50) | 如"任务规划" |
| objective | Text | 原始用户输入 |
| status | String(20) default running | running/completed/failed |
| checkpoint | JSON nullable | 3.3 的 checkpoint dict |
| error_message | Text nullable | finalize 时记录 |
| created_at / updated_at / completed_at | DateTime | 时间轴 |

`app/models/workflow_run.py` + `models/__init__.py` 导出（alembic metadata 可见）。

`SQLRunLedger`（`workflows/ledger.py`，构造注入 AsyncSession，与 API 请求同事务）：

```python
class SQLRunLedger:
    def __init__(self, db: AsyncSession, user_id: str): ...
    async def create(self, run_id, *, label, objective) -> None          # 插 running 行
    async def save_checkpoint(self, run_id, checkpoint: dict) -> None    # upsert checkpoint JSON
    async def load_run(self, run_id) -> Optional[dict]                   # 校验属主，返回 checkpoint dict
    async def get(self, run_id) -> Optional[dict]                        # 行元数据 + checkpoint 摘要（列表/查询用）
    async def list(self, *, limit=20, status=None) -> List[dict]         # 本人 run 按 updated_at 倒序
    async def finalize(self, run_id, *, status, error=None) -> None      # completed/failed + completed_at
```
写入失败只告警不阻断主流程（对齐 `_archive_run` 的"尽力而为"）。

### 3.5 `TaskPlannerWorkflow` 接入

**store 注入走 `initial_state["checkpoint"]` 配置**（对齐 memory 的既有注入风格），
不经构造参数，避免并发 execute 共享实例状态：

```python
# API 层（新 run）
initial_state = {
  "user_input": ...,
  "checkpoint": {"run_id": "wfrun-<hex>", "store": SQLRunLedger(db, uid),
                 "label": "任务规划", "objective": user_input},
}
# API 层（resume run）
#   ledger.load_run(run_id) 校验属主 → 存在才进 execute
```

`execute()` 改造点（行号基于当前文件）：
- `execute`（L541）：构造 state 时若 checkpoint cfg 有 store+run_id，先
  `store.load_run(run_id)`；命中且含 tasks → **resume 路径**：
  `state["tasks"] = sanitize_tasks(saved)`、`state["resume"] = extract_resume(saved)`，
  并从 checkpoint 恢复 objective/user_input（L554 的 user_input 用
  `saved["objective"]`）；
- `analyze_task`（L84）开头：`if state.get("tasks"): return state`（resume
  跳分解；分解分支才重置 results/status）；
- `run_dag`（L291）：`execute_dag(..., resume=state.get("resume"),
  on_settle=_checkpoint_hook)`；hook 闭包捕获 tasks/run_id/store/label/
  objective，调 `build_checkpoint` 后 `store.save_checkpoint`（异常吞掉）；
  结束后（引擎正常返回）`store.finalize(run_id, "completed")`；
- 失败路径：外层 try/except 捕获 `execute_dag` 的 ValueError 分支或图异常时，
  resume 已由 settle hook 持续落库 → **execute 外层重试自动变断点续跑**
  （重跑 run_dag 时先 load → resume seed，已终态不再执行）；
- `metadata["run_id"]` 回传（execute 返回 L610-616 处从 state["checkpoint"] 取）。

TaskPlanState 增加 `checkpoint: Optional[Dict]`、`resume: Optional[Dict]`（L31）。

## 四、影响文件清单

| 文件 | 改动 |
|---|---|
| `backend/app/workflows/execution.py` | 引擎新增 `resume` / `on_settle` |
| `backend/app/workflows/checkpoint.py` | **新增**：checkpoint 纯函数 |
| `backend/app/workflows/ledger.py` | **新增**：SQLRunLedger |
| `backend/app/models/workflow_run.py` + `__init__.py` | **新增** model + 导出 |
| `backend/alembic/versions/0005_add_workflow_runs.py` | **新增**迁移 |
| `backend/tests/test_migrations.py` | 版本断言 0004→0005 + 新表断言 |
| `backend/app/workflows/task_planner.py` | execute/analyze/run_dag 接入 |
| `backend/app/api/workflows.py` | POST resume_run_id + GET runs |
| `backend/tests/test_workflow_checkpoint.py` | **新增**：引擎+checkpoint+ledger+集成 |
| `run_tests.ps1` / `docs/CHANGELOG.md` | 注册 / 记录 |

## 五、任务分解（TDD，每任务独立可验证）

> 沿用仓库风格：`python tests/xxx.py` 直接运行、`ok()` 断言、纯离线。
> 引擎/checkpoint 测试零 DB；ledger 用临时 `sqlite+aiosqlite` 临时文件 +
> `create_all`（沿用 test_workflow_answer_search 的 DATABASE_URL 先行法）；
> TaskPlanner 集成用内存 FakeStore（鸭子类型对齐 3.4 协议）。

### Task 1 — 引擎 `on_settle` 回调（纯）
- **测试先行**（追加 `tests/test_workflow_checkpoint.py`）：
  - 3 任务依赖链，回调收到 3 次；每次快照的 `results` 键集合严格递增；
    最后一次含全部任务与最终 report 一致；`order/running/pending` 语义正确；
  - 回调抛异常 → 引擎照常完成（不传播）；
  - 失败重试任务只在**最终终态**触发一次 settle（首次失败不触发）。
- 实现：调度循环终态写入后 `await _safe_call(on_settle, snapshot)`。

### Task 2 — 引擎 `resume`（纯）
- **测试先行**：
  - `resume` seed 1/2 completed、3 依赖 2 → 只执行 3，ctx 含 1/2 输出；
    最终 `results[1]/[2]` 与 seed 对象一致；`attempts` 保留 seed 值；
  - seed 含 failed → 该任务**不重放**（保持 failed），未 seed 任务照跑；
  - seed 引用不存在任务 id / status 非法 → `ValueError`；
  - seed skipped + skip_on_failure=True → skipped 保持，不重放。
- 实现：初始化时把 resume results 全量并入 results/attempts，pending 初始为
  `[tid for tid not in results]`；校验放 validate 之后。

### Task 3 — checkpoint 纯函数
- **测试先行**：build → json round-trip → extract_resume/sanitize_tasks
  往返幂等；tasks 清洗剔除瞬态字段；saved_at/version 存在。
- 实现 `checkpoint.py`。

### Task 4 — 台账表 + 迁移 0005 + SQLRunLedger
- **测试先行**：
  - 迁移：临时 SQLite 上 `run_alembic_upgrade` 后存在 `workflow_runs` 表、
    alembic_version == "0005"（并入 test_migrations.py 断言更新）；
  - Ledger：create → load 空 → save_checkpoint → load 返回 round-trip 一致；
    属主不符 load → None；finalize 改状态/错误；list 按 updated_at 倒序 + status
    过滤；save 对不存在行自动补建（幂等 upsert）。
- 实现 model + migration + ledger.py。

### Task 5 — TaskPlannerWorkflow 接入（FakeStore 离线集成）
- **测试先行**（FakeStore 内存实现）：
  - 新 run：execute 全链完成，FakeStore 收到 ≥2 次 save_checkpoint 且末次
    快照 results 齐、finalize(completed)；返回 `metadata["run_id"]`；
  - resume 已全终态 run：二次 execute（同 run_id）→ `_run_single_task`
    零调用（断点恢复零成本重放），结果与首次一致，finalize 再次 completed；
  - resume 半程 run：预置 checkpoint（1/2 终态、3 未终态）→ 只执行 3，
    结果齐；analyze 未重跑（FakeStore 中 tasks 与预置一致）；
  - 无 checkpoint 配置 → 行为与旧版一致（回归既有 4 套件）。
- 实现 task_planner.py 接入（3.5）。

### Task 6 — API：resume + run 台账查询
- **测试先行**（httpx ASGI + 临时 DB + auth，沿用 answer_search 场景法）：
  - POST 无 resume_run_id → 返回含 `metadata.run_id`；GET runs 列表含该 run；
  - POST 带 resume_run_id（他人 run）→ 404；不存在 → 404；
  - POST 带 resume_run_id（本人已跑完 run）→ 200 且 tasks/results 复现；
  - GET runs/{run_id}：属主可查、他人 404。
- 实现 api/workflows.py：POST 扩展 + GET `/task-planner/runs`（或
  `/runs`）+ GET `/task-planner/runs/{run_id}`。

### Task 7 — 注册套件 + CHANGELOG
- `run_tests.ps1` 注册 `tests/test_workflow_checkpoint.py`；
- CHANGELOG 顶部条目（沿用格式）。

### Task 8 — 全量回归
- `python -m py_compile` 全部改动文件；
- 既有 test_migrations.py 全绿（0005 断言）＋ 4 个 workflow 套件全绿 +
  `run_tests.ps1` 全量（23 套件）全绿。

## 六、验证与评审标准

1. 引擎 resume/on_settle 单测：seed 复用、重放集、attempt 保留、异常快照免疫。
2. checkpoint round-trip 幂等；台账表迁移后真实可建、属主隔离生效。
3. TaskPlanner 集成：新 run / 全终态 resume / 半程 resume 三态正确，
   无 checkpoint 时旧行为零回归。
4. API：resume 404 边界、跨用户隔离、list 查询。
5. 全程无真实 LLM/DB 依赖即可离线跑（除 API/迁移用临时 SQLite）。
6. 全量 23 套件全绿。

## 七、风险与回滚

- **resume 的一致性边界**：不重放"终态 failed"，避免下游（默认不跳过已跑）
  与其后置结果不一致；崩溃时未终态任务重放，attempt 从 1 重计（护栏口径
  文档化）。回滚：引擎/checkpoint 纯模块单文件 revert；接线改动集中
  task_planner/api 两文件。
- **同步请求模型下"实时台账"有限**：单请求内无法被并发 GET 观测；台账价值在
  中断/失败后的续跑与查询。真异步化属后续计划。
- **checkpoint 体积**：code_generation output 可能较大。SQLite JSON 可承受；
  后续可加裁剪策略（非目标）。
