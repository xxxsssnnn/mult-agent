from typing import Dict, Any, List, TypedDict, Optional
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from pydantic import BaseModel, Field
import json
from app.workflows.base import BaseWorkflow
from app.workflows.recap import build_recap, format_recap
from app.workflows.execution import (
    CyclicDependencyError,
    ExecutionOptions,
    execute_dag,
)
from app.workflows.checkpoint import (
    build_checkpoint,
    extract_resume,
    sanitize_tasks,
)
from app.core.config import settings
import structlog

logger = structlog.get_logger(__name__)


class SubTask(BaseModel):
    """子任务模型"""
    id: int = Field(..., description="任务ID")
    title: str = Field(..., description="任务标题")
    description: str = Field(..., description="详细的任务描述")
    task_type: str = Field(default="general", description="任务类型: code_generation, code_review, analysis, documentation, testing")
    priority: int = Field(default=1, ge=1, le=5, description="优先级 1-5，5最高")
    estimated_complexity: str = Field(default="medium", description="预估复杂度: low, medium, high")
    dependencies: List[int] = Field(default_factory=list, description="依赖的任务ID列表")


class TaskPlanState(TypedDict):
    """任务规划状态"""
    user_input: str
    tasks: List[Dict[str, Any]]
    results: List[Dict[str, Any]]
    status: str
    llm: Optional[ChatOpenAI]
    # 运行台账配置（鸭子类型：store 提供 create/save_checkpoint/load_run/
    # finalize；不配置时走旧行为，全离线可用）
    checkpoint: Optional[Dict[str, Any]]
    # 从台账载入的已终态 results/attempts（引擎 resume seed）
    resume: Optional[Dict[str, Any]]


class TaskPlannerWorkflow(BaseWorkflow):
    """任务规划与执行工作流"""
    
    def __init__(self, max_iterations: int = 3, memory_manager=None):
        super().__init__(
            name="task_planner_workflow",
            description="Break down complex tasks and execute them sequentially with intelligent planning",
            memory_manager=memory_manager
        )
        self.max_iterations = max_iterations
        self.llm = None
        
        # 初始化LLM
        if settings.OPENAI_API_KEY:
            try:
                self.llm = ChatOpenAI(
                    model=settings.OPENAI_MODEL,
                    temperature=0.7,
                    openai_api_key=settings.OPENAI_API_KEY
                )
                logger.info("TaskPlanner LLM initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize LLM: {e}")
    
    def build_graph(self) -> StateGraph:
        """构建任务规划工作流图"""
        workflow = StateGraph(TaskPlanState)
        
        # 添加节点（analyze → run_dag → aggregate：任务调度由 DAG 引擎执行）
        workflow.add_node("analyze_task", self.analyze_task)
        workflow.add_node("run_dag", self.run_dag)
        workflow.add_node("aggregate_results", self.aggregate_results)
        
        # 设置入口点
        workflow.set_entry_point("analyze_task")
        
        # 添加边
        workflow.add_edge("analyze_task", "run_dag")
        workflow.add_edge("run_dag", "aggregate_results")
        workflow.add_edge("aggregate_results", END)
        
        self.graph = workflow.compile()
        return self.graph
    
    async def analyze_task(self, state: TaskPlanState) -> TaskPlanState:
        """分析用户输入，使用LLM智能分解为子任务"""
        logger.info("Analyzing task with LLM", input=state["user_input"])
        
        # resume 路径：任务定义已持久化在台账中，跳过重新分解（避免重复 LLM 消耗）
        if state.get("tasks"):
            logger.info("Resume: 复用台账中已分解任务，跳过重新规划",
                        total=len(state["tasks"]))
            state["status"] = "analyzed"
            return state

        user_input = state["user_input"]
        
        # 如果有LLM，使用智能分解
        if self.llm:
            try:
                tasks = await self._intelligent_decompose(user_input)
                logger.info(f"Intelligent decomposition completed: {len(tasks)} subtasks")
            except Exception as e:
                logger.error(f"Intelligent decomposition failed: {e}, falling back to simple decomposition")
                tasks = self._simple_decompose(user_input)
        else:
            # 降级到简单分解
            logger.warning("LLM not available, using simple decomposition")
            tasks = self._simple_decompose(user_input)
        
        state["tasks"] = tasks
        state["results"] = []
        state["status"] = "analyzed"
        
        logger.info(f"Task decomposed into {len(tasks)} subtasks")
        return state
    
    async def _intelligent_decompose(self, user_input: str) -> List[Dict[str, Any]]:
        """使用LLM智能分解任务"""
        system_prompt = """你是一个专业的任务规划专家。你的职责是将复杂的用户需求分解为具体的、可执行的子任务。

分解原则：
1. 每个子任务必须具体明确，有清晰的交付物
2. 任务数量根据复杂度决定（通常3-8个）
3. 识别任务之间的依赖关系
4. 评估每个任务的复杂度和优先级
5. 考虑技术实现的可行性

任务类型包括：
- code_generation: 代码生成
- code_review: 代码审查
- analysis: 需求分析或技术分析
- documentation: 文档编写
- testing: 测试用例编写
- general: 其他通用任务

请返回JSON格式的任务列表，包含以下字段：
- id: 任务编号（从1开始）
- title: 任务标题（简洁明了）
- description: 详细的任务描述（包含具体要求）
- task_type: 任务类型
- priority: 优先级（1-5，5最高）
- estimated_complexity: 预估复杂度（low/medium/high）
- dependencies: 依赖的任务ID列表（如果没有依赖则为空数组）
"""
        
        prompt = f"""
用户需求：{user_input}

请将上述需求分解为具体的子任务。确保每个任务都是可执行的、有明确目标的。

示例输出格式：
```json
[
  {{
    "id": 1,
    "title": "设计数据库schema",
    "description": "创建用户表、订单表等核心数据表结构，定义字段类型和索引",
    "task_type": "analysis",
    "priority": 5,
    "estimated_complexity": "medium",
    "dependencies": []
  }},
  {{
    "id": 2,
    "title": "实现用户认证API",
    "description": "开发注册、登录、Token刷新接口，使用JWT进行身份验证",
    "task_type": "code_generation",
    "priority": 5,
    "estimated_complexity": "high",
    "dependencies": [1]
  }}
]
```

现在请分解任务：
"""
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=prompt)
        ]
        
        response = await self.llm.ainvoke(messages)
        
        # 解析JSON响应
        try:
            # 提取JSON部分
            content = response.content
            if "```json" in content:
                json_str = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                json_str = content.split("```")[1].split("```")[0].strip()
            else:
                json_str = content
            
            tasks_data = json.loads(json_str)
            
            # 转换为字典格式
            tasks = []
            for task_data in tasks_data:
                tasks.append({
                    "id": task_data["id"],
                    "title": task_data["title"],
                    "description": task_data["description"],
                    "task_type": task_data.get("task_type", "general"),
                    "priority": task_data.get("priority", 3),
                    "estimated_complexity": task_data.get("estimated_complexity", "medium"),
                    "dependencies": task_data.get("dependencies", []),
                    "status": "pending"
                })
            
            return tasks
        
        except Exception as e:
            logger.error(f"Failed to parse LLM response: {e}")
            raise ValueError(f"Invalid task decomposition format: {e}")
    
    def _simple_decompose(self, user_input: str) -> List[Dict[str, Any]]:
        """简单的规则分解（降级方案）"""
        # 根据关键词判断任务类型
        lower_input = user_input.lower()
        
        if any(word in lower_input for word in ["系统", "平台", "应用", "app"]):
            # 复杂系统分解
            tasks = [
                {
                    "id": 1,
                    "title": "需求分析与架构设计",
                    "description": f"分析需求：{user_input}，设计系统架构和技术选型",
                    "task_type": "analysis",
                    "priority": 5,
                    "estimated_complexity": "high",
                    "dependencies": [],
                    "status": "pending"
                },
                {
                    "id": 2,
                    "title": "核心功能开发",
                    "description": f"实现核心业务逻辑和功能模块",
                    "task_type": "code_generation",
                    "priority": 5,
                    "estimated_complexity": "high",
                    "dependencies": [1],
                    "status": "pending"
                },
                {
                    "id": 3,
                    "title": "测试与优化",
                    "description": "编写单元测试、集成测试，性能优化",
                    "task_type": "testing",
                    "priority": 3,
                    "estimated_complexity": "medium",
                    "dependencies": [2],
                    "status": "pending"
                }
            ]
        elif any(word in lower_input for word in ["代码", "函数", "算法"]):
            # 代码任务分解
            tasks = [
                {
                    "id": 1,
                    "title": "代码实现",
                    "description": user_input,
                    "task_type": "code_generation",
                    "priority": 5,
                    "estimated_complexity": "medium",
                    "dependencies": [],
                    "status": "pending"
                },
                {
                    "id": 2,
                    "title": "代码审查",
                    "description": "检查代码质量、安全性和性能",
                    "task_type": "code_review",
                    "priority": 4,
                    "estimated_complexity": "low",
                    "dependencies": [1],
                    "status": "pending"
                }
            ]
        else:
            # 通用任务
            tasks = [
                {
                    "id": 1,
                    "title": "任务执行",
                    "description": user_input,
                    "task_type": "general",
                    "priority": 3,
                    "estimated_complexity": "medium",
                    "dependencies": [],
                    "status": "pending"
                }
            ]
        
        return tasks
    
    async def run_dag(self, state: TaskPlanState) -> TaskPlanState:
        """由 DAG 引擎并发执行全部子任务（依赖/超时/重试由引擎负责）。

        台账配置存在时：每子任务终态落一次 checkpoint；结束后标记 completed。
        resume seed 传入引擎：已终态任务直接复用，断点续跑。
        """
        logger.info("Running task DAG", total=len(state["tasks"]))

        ckpt_cfg = state.get("checkpoint") or {}
        store = ckpt_cfg.get("store")
        run_id = ckpt_cfg.get("run_id")

        async def checkpoint_hook(partial) -> None:
            """每子任务终态 → 组装 checkpoint 落台账（失败仅告警）。"""
            try:
                await store.save_checkpoint(run_id, build_checkpoint(
                    run_id=run_id,
                    label=ckpt_cfg.get("label") or "任务规划",
                    objective=ckpt_cfg.get("objective")
                    or state.get("user_input", ""),
                    tasks=state["tasks"],
                    partial=partial,
                ))
            except Exception:
                logger.exception("Checkpoint 落库失败（尽力而为）",
                                 run_id=run_id)

        try:
            report = await execute_dag(
                state["tasks"],
                self._dag_runner(),
                ExecutionOptions(
                    max_concurrency=settings.WORKFLOW_MAX_CONCURRENCY,
                    task_timeout_seconds=settings.WORKFLOW_TASK_TIMEOUT_SECONDS,
                    task_max_retries=settings.WORKFLOW_TASK_MAX_RETRIES,
                ),
                resume=state.get("resume"),
                on_settle=checkpoint_hook if (store and run_id) else None,
            )
        except CyclicDependencyError as e:
            logger.error("Cyclic dependency detected", error=str(e))
            await self._finalize_run(store, run_id, "failed", error=str(e))
            raise
        except ValueError as e:
            logger.error("Invalid task DAG", error=str(e))
            await self._finalize_run(store, run_id, "failed", error=str(e))
            raise

        for t in state["tasks"]:
            res = report["results"].get(t["id"])
            if res:
                # 兜底字段：skip/timeout/异常结果补齐任务类型，保持结果结构完整
                res.setdefault("task_type", t.get("task_type", "general"))
                t["status"] = res.get("status", t.get("status", "pending"))

        # results 与 tasks 同序对齐（归档/复盘依赖 zip(tasks, results)）
        state["results"] = [report["results"][t["id"]] for t in state["tasks"]]
        state["status"] = "executed"
        await self._finalize_run(store, run_id, "completed")
        logger.info("Task DAG finished", total=len(state["tasks"]),
                    attempts=report["attempts"])
        return state

    async def _finalize_run(self, store, run_id: Optional[str],
                            status: str, error: Optional[str] = None) -> None:
        """台账收尾（尽力而为）。"""
        if not (store and run_id):
            return
        try:
            await store.finalize(run_id, status=status, error=error)
        except Exception:
            logger.exception("台账收尾失败（尽力而为）", run_id=run_id,
                             status=status)

    def _dag_runner(self):
        """把单子任务执行适配为引擎的 run_task 签名 (task, ctx_map, attempt)。

        ctx_map 仅含直接依赖且成功的任务结果 → 拼装为文本上下文。
        """
        async def run_task(task: Dict[str, Any],
                           ctx_map: Dict[int, Dict[str, Any]],
                           attempt: int) -> Dict[str, Any]:
            context_text = "\n".join(
                f"Task {dep_id}: {res.get('output', '')}"
                for dep_id, res in sorted(ctx_map.items())
            )
            logger.info("Executing task", task_id=task["id"],
                        title=task.get("title"), type=task.get("task_type"),
                        attempt=attempt)
            try:
                return await self._run_single_task(task, context_text)
            except Exception as e:  # 引擎仍有兜底，这里保证结果字段与旧版一致
                logger.error("Task execution failed", task_id=task["id"], error=str(e))
                return {
                    "task_id": task["id"],
                    "status": "failed",
                    "output": f"执行失败: {str(e)}",
                    "error": str(e),
                    "task_type": task.get("task_type", "general"),
                }
        return run_task

    async def _run_single_task(self, task: Dict[str, Any], context: str) -> Dict[str, Any]:
        """根据任务类型执行不同的逻辑"""
        task_type = task.get("task_type", "general")
        description = task.get("description", "")
        
        if task_type == "code_generation":
            # 代码生成任务 - 使用CoderAgent
            from app.agents.coder import CoderAgent
            from uuid import uuid4
            
            coder = CoderAgent(agent_id=uuid4(), name=f"TaskCoder-{task['id']}")
            await coder.initialize()
            if self.memory_manager is not None:
                await coder.attach_memory(self.memory_manager)
            
            result = await coder.execute_with_memory({
                "requirement": description,
                "language": "python",
                "context": f"Previous work:\n{context}" if context else ""
            })
            
            return {
                "task_id": task["id"],
                "status": "completed" if result.get("success") else "failed",
                "output": result.get("code", ""),
                "explanation": result.get("explanation", ""),
                "task_type": "code_generation"
            }
        
        elif task_type == "code_review":
            # 代码审查任务 - 使用ReviewerAgent
            from app.agents.reviewer import ReviewerAgent
            from uuid import uuid4
            
            # 从上下文中提取需要审查的代码
            code_to_review = context if context else "# No code available for review"
            
            reviewer = ReviewerAgent(agent_id=uuid4(), name=f"TaskReviewer-{task['id']}")
            await reviewer.initialize()
            if self.memory_manager is not None:
                await reviewer.attach_memory(self.memory_manager)
            
            result = await reviewer.execute_with_memory({
                "code": code_to_review,
                "language": "python",
                "focus_areas": ["quality", "security", "performance"]
            })
            
            return {
                "task_id": task["id"],
                "status": "completed" if result.get("success") else "failed",
                "output": result.get("review", ""),
                "task_type": "code_review"
            }
        
        elif task_type == "analysis" or task_type == "documentation":
            # 分析或文档任务 - 直接使用LLM
            if self.llm:
                system_prompt = """你是一个专业的技术分析师和文档编写专家。
你的职责是：
1. 深入分析技术需求和问题
2. 提供清晰、结构化的分析报告
3. 编写易于理解的技术文档
4. 给出可操作的建议"""
                
                prompt = f"""
任务描述：{description}

相关上下文：
{context if context else "无"}

请提供详细的分析和文档。
"""
                
                from langchain_core.messages import SystemMessage, HumanMessage
                messages = [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=prompt)
                ]
                
                response = await self.llm.ainvoke(messages)
                
                return {
                    "task_id": task["id"],
                    "status": "completed",
                    "output": response.content,
                    "task_type": task_type
                }
            else:
                return {
                    "task_id": task["id"],
                    "status": "failed",
                    "output": "LLM not available for analysis/documentation task",
                    "task_type": task_type
                }
        
        elif task_type == "testing":
            # 测试任务 - 生成测试用例
            if self.llm:
                system_prompt = """你是一个专业的测试工程师。你的职责是：
1. 设计全面的测试用例（单元测试、集成测试）
2. 考虑边界情况和异常场景
3. 编写清晰的测试代码
4. 提供测试覆盖率分析"""
                
                prompt = f"""
为以下功能设计测试用例：
{description}

相关上下文：
{context if context else "无"}

请提供：
1. 测试策略说明
2. 具体的测试用例（包括输入、预期输出）
3. Python测试代码示例（使用pytest）
"""
                
                from langchain_core.messages import SystemMessage, HumanMessage
                messages = [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=prompt)
                ]
                
                response = await self.llm.ainvoke(messages)
                
                return {
                    "task_id": task["id"],
                    "status": "completed",
                    "output": response.content,
                    "task_type": "testing"
                }
            else:
                return {
                    "task_id": task["id"],
                    "status": "failed",
                    "output": "LLM not available for testing task",
                    "task_type": "testing"
                }
        
        else:
            # 通用任务 - 使用LLM直接回答
            if self.llm:
                prompt = f"""
任务：{description}

相关上下文：
{context if context else "无"}

请完成上述任务，提供详细的结果。
"""
                
                from langchain_core.messages import HumanMessage
                response = await self.llm.ainvoke([HumanMessage(content=prompt)])
                
                return {
                    "task_id": task["id"],
                    "status": "completed",
                    "output": response.content,
                    "task_type": "general"
                }
            else:
                return {
                    "task_id": task["id"],
                    "status": "failed",
                    "output": "LLM not available for general task",
                    "task_type": "general"
                }
    
    async def aggregate_results(self, state: TaskPlanState) -> TaskPlanState:
        """聚合所有任务结果"""
        logger.info("Aggregating results")
        
        state["status"] = "completed"
        
        summary = {
            "total_tasks": len(state["tasks"]),
            "completed_tasks": len([t for t in state["tasks"] if t["status"] == "completed"]),
            "results": state["results"]
        }
        
        logger.info(
            "All tasks completed",
            summary=summary
        )
        
        return state
    
    async def execute(self, initial_state: Dict[str, Any]) -> Dict[str, Any]:
        """执行工作流，带重试机制和进度追踪"""
        retry_count = 0
        last_error = None
        
        while retry_count < self.max_retries:
            try:
                logger.info("Starting task planner workflow", attempt=retry_count + 1)
                
                # 会话记忆：构造注入或 initial_state["memory"] 配置
                await self.ensure_memory(initial_state)

                # 运行台账：initial_state["checkpoint"] 注入 store + run_id
                ckpt_cfg = initial_state.get("checkpoint") or {}
                store = ckpt_cfg.get("store")
                run_id = ckpt_cfg.get("run_id")

                # 断点恢复：从台账载入 checkpoint（已终态任务/定义复用）
                saved = None
                if store and run_id:
                    saved = await store.load_run(run_id)

                # 初始化状态
                state = TaskPlanState(
                    user_input=saved["objective"] if (saved and saved.get("tasks"))
                    else initial_state.get("user_input", ""),
                    tasks=[],
                    results=[],
                    status="pending",
                    llm=self.llm,
                    checkpoint=ckpt_cfg if ckpt_cfg else None,
                    resume=None,
                )
                if saved and saved.get("tasks"):
                    state["tasks"] = sanitize_tasks(saved)
                    state["resume"] = extract_resume(saved)
                    logger.info("Resume workflow run from ledger",
                                run_id=run_id,
                                total=len(state["tasks"]),
                                seeded=len(state["resume"]["results"]))
                
                # 编译并运行工作流
                if not self.graph:
                    self.build_graph()
                
                # 通知开始
                await self.notify_progress("workflow_start", 3, 0)
                
                result = await self.graph.ainvoke(state)
                
                # 通知完成
                await self.notify_progress("workflow_complete", 3, 3)
                
                logger.info("Workflow completed successfully", status=result["status"])
                
                metadata = {
                    "total_tasks": len(result["tasks"]),
                    "completed_tasks": len([t for t in result["tasks"] if t["status"] == "completed"]),
                    "failed_tasks": len([t for t in result["tasks"] if t["status"] == "failed"]),
                    "attempt": retry_count + 1
                }
                if ckpt_cfg and run_id:
                    metadata["run_id"] = run_id
                memory_meta = self.memory_info()
                if memory_meta:
                    metadata["memory"] = memory_meta

                # 长任务复盘：结构化回传 + 写入会话记忆（供同会话延续）
                recap = build_recap(
                    workflow_name=self.name,
                    objective=state.get("user_input", ""),
                    success=True,
                    attempts=retry_count + 1,
                    summary={
                        "total_tasks": metadata["total_tasks"],
                        "completed_tasks": metadata["completed_tasks"],
                        "failed_tasks": metadata["failed_tasks"],
                    },
                    tasks=[
                        {
                            "id": t.get("id"),
                            "task_type": t.get("task_type"),
                            "status": t.get("status"),
                            "summary": t.get("title", ""),
                        }
                        for t in result["tasks"]
                    ],
                )
                await self.record_recap(format_recap(recap))
                metadata["recap"] = recap
                
                return {
                    "success": True,
                    "tasks": result["tasks"],
                    "results": result["results"],
                    "status": result["status"],
                    "metadata": metadata
                }
            
            except Exception as e:
                last_error = e
                retry_count += 1
                logger.error(
                    f"Workflow execution failed (attempt {retry_count}/{self.max_retries})",
                    error=str(e)
                )
                # 台账失败收尾（尽力而为），断点信息仍保留供后续续跑
                _cfg = initial_state.get("checkpoint") or {}
                await self._finalize_run(_cfg.get("store"), _cfg.get("run_id"),
                                         "failed", error=str(e))

                if retry_count < self.max_retries:
                    # 等待后重试
                    import asyncio
                    await asyncio.sleep(self.retry_delay * retry_count)  # 指数退避
                else:
                    logger.error("Max retries reached, workflow failed")
        
        # 所有重试都失败
        recap = build_recap(
            workflow_name=self.name,
            objective=initial_state.get("user_input", ""),
            success=False,
            attempts=retry_count,
            notes=[f"执行失败：{str(last_error)}"],
        )
        await self.record_recap(format_recap(recap))
        return {
            "success": False,
            "error": str(last_error),
            "max_retries_reached": True,
            "attempts": retry_count,
            "metadata": {"recap": recap},
        }
