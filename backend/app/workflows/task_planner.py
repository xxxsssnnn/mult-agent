from typing import Dict, Any, List, TypedDict, Optional
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from pydantic import BaseModel, Field
import json
from app.workflows.base import BaseWorkflow
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
    current_task_index: int
    results: List[Dict[str, Any]]
    status: str
    llm: Optional[ChatOpenAI]


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
        
        # 添加节点
        workflow.add_node("analyze_task", self.analyze_task)
        workflow.add_node("execute_task", self.execute_task)
        workflow.add_node("aggregate_results", self.aggregate_results)
        
        # 设置入口点
        workflow.set_entry_point("analyze_task")
        
        # 添加边
        workflow.add_edge("analyze_task", "execute_task")
        
        # 条件边：检查是否还有任务需要执行
        workflow.add_conditional_edges(
            "execute_task",
            self.check_next_task,
            {
                "continue": "execute_task",
                "complete": "aggregate_results"
            }
        )
        
        workflow.add_edge("aggregate_results", END)
        
        self.graph = workflow.compile()
        return self.graph
    
    async def analyze_task(self, state: TaskPlanState) -> TaskPlanState:
        """分析用户输入，使用LLM智能分解为子任务"""
        logger.info("Analyzing task with LLM", input=state["user_input"])
        
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
        state["current_task_index"] = 0
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
    
    async def execute_task(self, state: TaskPlanState) -> TaskPlanState:
        """执行当前任务，根据任务类型调用对应的Agent"""
        current_index = state["current_task_index"]
        tasks = state["tasks"]
        
        if current_index >= len(tasks):
            return state
        
        current_task = tasks[current_index]
        logger.info("Executing task", task_id=current_task["id"], title=current_task["title"], type=current_task.get("task_type"))
        
        # 根据任务类型选择执行策略
        try:
            result = await self._execute_by_type(current_task, state)
        except Exception as e:
            logger.error(f"Task execution failed: {e}")
            result = {
                "task_id": current_task["id"],
                "status": "failed",
                "output": f"执行失败: {str(e)}",
                "error": str(e)
            }
        
        state["results"].append(result)
        state["current_task_index"] = current_index + 1
        
        # 更新任务状态
        tasks[current_index]["status"] = result.get("status", "completed")
        
        logger.info(
            "Task completed",
            task_id=current_task["id"],
            status=result.get("status"),
            completed=current_index + 1,
            total=len(tasks)
        )
        
        return state
    
    async def _execute_by_type(self, task: Dict[str, Any], state: TaskPlanState) -> Dict[str, Any]:
        """根据任务类型执行不同的逻辑"""
        task_type = task.get("task_type", "general")
        description = task.get("description", "")
        
        # 获取之前任务的结果作为上下文
        previous_results = state.get("results", [])
        context = "\n".join([f"Task {r['task_id']}: {r.get('output', '')}" for r in previous_results[-2:]])
        
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
    
    def check_next_task(self, state: TaskPlanState) -> str:
        """检查是否还有任务需要执行"""
        if state["current_task_index"] < len(state["tasks"]):
            return "continue"
        return "complete"
    
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
                
                # 初始化状态
                state = TaskPlanState(
                    user_input=initial_state.get("user_input", ""),
                    tasks=[],
                    current_task_index=0,
                    results=[],
                    status="pending",
                    llm=self.llm
                )
                
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
                memory_meta = self.memory_info()
                if memory_meta:
                    metadata["memory"] = memory_meta
                
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
                
                if retry_count < self.max_retries:
                    # 等待后重试
                    import asyncio
                    await asyncio.sleep(self.retry_delay * retry_count)  # 指数退避
                else:
                    logger.error("Max retries reached, workflow failed")
        
        # 所有重试都失败
        return {
            "success": False,
            "error": str(last_error),
            "max_retries_reached": True,
            "attempts": retry_count
        }
