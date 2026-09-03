from typing import Dict, Any, TypedDict, List
from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field
from app.agents.coder import CoderAgent
from app.agents.reviewer import ReviewerAgent
from app.workflows.base import BaseWorkflow
from uuid import uuid4
import structlog
import json

logger = structlog.get_logger(__name__)


class CodeIssue(BaseModel):
    """代码问题模型"""
    severity: str = Field(..., description="严重程度: critical, major, minor, suggestion")
    category: str = Field(..., description="问题类别: bug, security, performance, maintainability, readability")
    description: str = Field(..., description="问题描述")
    line_number: int = Field(default=-1, description="问题所在行号，-1表示不适用")
    suggestion: str = Field(..., description="改进建议")


class StructuredReview(BaseModel):
    """结构化审查结果"""
    score: int = Field(..., ge=0, le=100, description="代码质量评分 0-100")
    has_critical_issues: bool = Field(..., description="是否存在严重问题")
    issues: List[CodeIssue] = Field(default_factory=list, description="发现的问题列表")
    suggestions: List[str] = Field(default_factory=list, description="改进建议列表")
    approved: bool = Field(..., description="是否通过审查")
    summary: str = Field(..., description="审查总结")


class CodeReviewState(TypedDict):
    """代码审查工作流状态"""
    requirement: str
    language: str
    generated_code: str
    review_result: str
    approved: bool
    iteration_count: int
    max_iterations: int


class CodeReviewWorkflow(BaseWorkflow):
    """代码生成与审查工作流"""
    
    def __init__(self, coder_agent: CoderAgent, reviewer_agent: ReviewerAgent,
                 max_iterations: int = 3, memory_manager=None):
        super().__init__(
            name="code_review_workflow",
            description="Automated code generation and review workflow",
            memory_manager=memory_manager
        )
        self.coder_agent = coder_agent
        self.reviewer_agent = reviewer_agent
        self.max_iterations = max_iterations
    
    def build_graph(self) -> StateGraph:
        """构建代码审查工作流图"""
        workflow = StateGraph(CodeReviewState)
        
        # 添加节点
        workflow.add_node("generate_code", self.generate_code)
        workflow.add_node("review_code", self.review_code)
        workflow.add_node("refine_code", self.refine_code)
        
        # 设置入口点
        workflow.set_entry_point("generate_code")
        
        # 添加边
        workflow.add_edge("generate_code", "review_code")
        
        # 条件边：根据审查结果决定下一步
        workflow.add_conditional_edges(
            "review_code",
            self.decide_next_step,
            {
                "approve": END,
                "refine": "refine_code",
                "reject": END
            }
        )
        
        workflow.add_edge("refine_code", "review_code")
        
        self.graph = workflow.compile()
        return self.graph
    
    async def generate_code(self, state: CodeReviewState) -> CodeReviewState:
        """生成代码"""
        logger.info("Generating code", requirement=state["requirement"])
        
        result = await self.coder_agent.execute_with_memory({
            "requirement": state["requirement"],
            "language": state["language"],
            "context": f"Iteration {state.get('iteration_count', 0) + 1}"
        })
        
        if result["success"]:
            state["generated_code"] = result.get("code", "")
            logger.info("Code generated successfully")
        else:
            logger.error("Code generation failed", error=result.get("error"))
            state["generated_code"] = ""
        
        return state
    
    async def review_code(self, state: CodeReviewState) -> CodeReviewState:
        """审查代码，使用结构化输出"""
        logger.info("Reviewing code with structured output")
        
        if not state.get("generated_code"):
            state["review_result"] = "No code to review"
            state["approved"] = False
            return state
        
        result = await self.reviewer_agent.execute_with_memory({
            "code": state["generated_code"],
            "language": state["language"],
            "focus_areas": ["quality", "security", "performance"]
        })
        
        if result["success"]:
            # 尝试解析为结构化结果
            try:
                structured_review = await self._parse_structured_review(result.get("review", ""))
                
                state["review_result"] = structured_review.summary
                state["approved"] = structured_review.approved
                
                logger.info(
                    "Structured code review completed",
                    score=structured_review.score,
                    approved=structured_review.approved,
                    issues_count=len(structured_review.issues)
                )
                
                # 将结构化结果保存到状态中（可选）
                state["structured_review"] = structured_review.dict()
                
            except Exception as e:
                logger.warning(f"Failed to parse structured review: {e}, using simple judgment")
                # 降级到简单判断
                state["review_result"] = result.get("review", "")
                review_text = state["review_result"].lower()
                state["approved"] = "critical" not in review_text and "major issue" not in review_text
                
                logger.info("Simple code review completed", approved=state["approved"])
        else:
            state["review_result"] = "Review failed"
            state["approved"] = False
        
        return state
    
    async def _parse_structured_review(self, review_text: str) -> StructuredReview:
        """将LLM的文本输出解析为结构化审查结果"""
        # 如果ReviewerAgent支持结构化输出，直接使用
        # 否则尝试从文本中提取信息
        
        system_prompt = """你是一个代码审查结果解析器。请将审查文本转换为结构化的JSON格式。

要求：
1. 评分标准：
   - 90-100: 优秀，无重大问题
   - 80-89: 良好，有少量改进空间
   - 70-79: 一般，有需要优化的地方
   - 60-69: 较差，存在明显问题
   - <60: 不合格，有严重问题

2. 严重程度定义：
   - critical: 会导致系统崩溃或安全漏洞
   - major: 影响功能或性能的重要问题
   - minor: 小的改进建议
   - suggestion: 可选的优化建议

3. 通过标准：
   - 没有critical级别的问题
   - major级别问题不超过2个
   - 评分 >= 70

请返回JSON格式，包含以下字段：
- score: int (0-100)
- has_critical_issues: bool
- issues: 数组，每个元素包含 severity, category, description, line_number, suggestion
- suggestions: 字符串数组
- approved: bool
- summary: 字符串
"""
        
        prompt = f"""
审查文本：
{review_text}

请将上述审查结果转换为结构化JSON格式。
"""
        
        from langchain_core.messages import SystemMessage, HumanMessage
        from app.core.config import settings
        from langchain_openai import ChatOpenAI
        
        llm = ChatOpenAI(
            model=settings.OPENAI_MODEL,
            temperature=0.3,
            openai_api_key=settings.OPENAI_API_KEY
        )
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=prompt)
        ]
        
        response = await llm.ainvoke(messages)
        
        # 解析JSON
        try:
            content = response.content
            if "```json" in content:
                json_str = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                json_str = content.split("```")[1].split("```")[0].strip()
            else:
                json_str = content
            
            data = json.loads(json_str)
            
            # 构建结构化审查结果
            issues = []
            for issue_data in data.get("issues", []):
                issues.append(CodeIssue(**issue_data))
            
            structured = StructuredReview(
                score=data.get("score", 75),
                has_critical_issues=data.get("has_critical_issues", False),
                issues=issues,
                suggestions=data.get("suggestions", []),
                approved=data.get("approved", True),
                summary=data.get("summary", "Review completed")
            )
            
            return structured
        
        except Exception as e:
            logger.error(f"Failed to parse structured review: {e}")
            raise ValueError(f"Invalid review format: {e}")
    
    async def refine_code(self, state: CodeReviewState) -> CodeReviewState:
        """根据审查意见优化代码"""
        logger.info("Refining code based on review")
        
        state["iteration_count"] = state.get("iteration_count", 0) + 1
        
        if state["iteration_count"] >= self.max_iterations:
            logger.warning("Max iterations reached")
            state["approved"] = False
            return state
        
        # 使用审查结果作为上下文重新生成代码
        context = f"""
Previous review feedback:
{state.get('review_result', '')}

Please improve the code based on this feedback.
"""
        
        result = await self.coder_agent.execute_with_memory({
            "requirement": state["requirement"],
            "language": state["language"],
            "context": context
        })
        
        if result["success"]:
            state["generated_code"] = result.get("code", "")
            logger.info("Code refined successfully")
        
        return state
    
    def decide_next_step(self, state: CodeReviewState) -> str:
        """决定下一步操作"""
        if state.get("approved"):
            return "approve"
        
        if state.get("iteration_count", 0) >= self.max_iterations:
            return "reject"
        
        return "refine"
    
    async def execute(self, initial_state: Dict[str, Any]) -> Dict[str, Any]:
        """执行工作流，带重试机制和进度追踪"""
        retry_count = 0
        last_error = None
        
        while retry_count < self.max_retries:
            try:
                logger.info("Starting code review workflow", attempt=retry_count + 1)
                
                # 会话记忆：构造注入或 initial_state["memory"] 配置
                memory = await self.ensure_memory(initial_state)
                if memory is not None:
                    await self.coder_agent.attach_memory(memory)
                    await self.reviewer_agent.attach_memory(memory)
                
                # 初始化状态
                state = CodeReviewState(
                    requirement=initial_state.get("requirement", ""),
                    language=initial_state.get("language", "python"),
                    generated_code="",
                    review_result="",
                    approved=False,
                    iteration_count=0,
                    max_iterations=self.max_iterations,
                    structured_review=None
                )
                
                # 编译并运行工作流
                if not self.graph:
                    self.build_graph()
                
                # 通知开始
                await self.notify_progress("workflow_start", 4, 0)
                
                result = await self.graph.ainvoke(state)
                
                # 通知完成
                await self.notify_progress("workflow_complete", 4, 4)
                
                logger.info(
                    "Workflow completed",
                    approved=result.get("approved"),
                    iterations=result.get("iteration_count")
                )
                
                metadata = {
                    "attempt": retry_count + 1,
                    "has_structured_review": result.get("structured_review") is not None
                }
                memory_meta = self.memory_info()
                if memory_meta:
                    metadata["memory"] = memory_meta
                
                return {
                    "success": True,
                    "code": result.get("generated_code", ""),
                    "review": result.get("review_result", ""),
                    "structured_review": result.get("structured_review"),
                    "approved": result.get("approved", False),
                    "iterations": result.get("iteration_count", 0),
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
                    import asyncio
                    await asyncio.sleep(self.retry_delay * retry_count)
                else:
                    logger.error("Max retries reached, workflow failed")
        
        # 所有重试都失败
        return {
            "success": False,
            "error": str(last_error),
            "max_retries_reached": True,
            "attempts": retry_count
        }
