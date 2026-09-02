from typing import Dict, Any, List, Optional
from uuid import UUID
from app.agents.base import BaseAgent
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from app.core.config import settings
import structlog

logger = structlog.get_logger(__name__)


class CoderAgent(BaseAgent):
    """代码编写Agent，负责生成和优化代码"""
    
    def __init__(self, agent_id: UUID, name: str = "Coder", config: Optional[Dict[str, Any]] = None):
        super().__init__(agent_id, name, config)
        self.llm = None
        self.system_prompt = """你是一个专业的软件工程师，擅长编写高质量、可维护的代码。
你的职责包括：
1. 根据需求编写清晰、规范的代码
2. 遵循最佳实践和设计模式
3. 添加适当的注释和文档
4. 进行代码审查和优化
5. 确保代码的可测试性"""
    
    async def initialize(self) -> bool:
        """初始化LLM"""
        try:
            if not settings.OPENAI_API_KEY:
                logger.warning("OPENAI_API_KEY not set, using mock mode")
                self.is_initialized = True
                return True
            
            self.llm = ChatOpenAI(
                model=settings.OPENAI_MODEL,
                temperature=settings.DEFAULT_TEMPERATURE,
                openai_api_key=settings.OPENAI_API_KEY
            )
            self.is_initialized = True
            logger.info("CoderAgent initialized successfully")
            return True
        except Exception as e:
            logger.error("Failed to initialize CoderAgent", error=str(e))
            return False
    
    async def execute(self, task_input: Dict[str, Any]) -> Dict[str, Any]:
        """执行代码生成任务"""
        try:
            if not self.is_initialized:
                await self.initialize()
            
            requirement = task_input.get("requirement", "")
            language = task_input.get("language", "python")
            context = task_input.get("context", "")
            
            if not self.llm:
                # Mock响应
                return {
                    "success": True,
                    "code": f"# Generated code for: {requirement}\n# Language: {language}\nprint('Hello from CoderAgent')",
                    "language": language,
                    "explanation": "Mock response - LLM not configured"
                }
            
            prompt = f"""
语言: {language}
上下文: {context}

需求: {requirement}

请生成符合要求的代码，并解释关键设计决策。
"""
            
            messages = [
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=prompt)
            ]
            
            response = await self.llm.ainvoke(messages)
            
            return {
                "success": True,
                "code": response.content,
                "language": language,
                "explanation": "Code generated based on requirements"
            }
        
        except Exception as e:
            logger.error("CoderAgent execution failed", error=str(e))
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_capabilities(self) -> List[str]:
        return ["code_generation", "code_review", "refactoring", "debugging"]
