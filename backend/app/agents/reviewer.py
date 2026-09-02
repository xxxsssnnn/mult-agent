from typing import Dict, Any, List, Optional
from uuid import UUID
from app.agents.base import BaseAgent
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from app.core.config import settings
import structlog

logger = structlog.get_logger(__name__)


class ReviewerAgent(BaseAgent):
    """代码审查Agent，负责代码质量检查和安全审计"""
    
    def __init__(self, agent_id: UUID, name: str = "Reviewer", config: Optional[Dict[str, Any]] = None):
        super().__init__(agent_id, name, config)
        self.llm = None
        self.system_prompt = """你是一个资深的代码审查专家，专注于：
1. 代码质量和可读性
2. 安全性漏洞检测
3. 性能优化建议
4. 最佳实践遵循
5. 潜在bug识别

请提供具体、可操作的改进建议。"""
    
    async def initialize(self) -> bool:
        """初始化LLM"""
        try:
            if not settings.OPENAI_API_KEY:
                logger.warning("OPENAI_API_KEY not set, using mock mode")
                self.is_initialized = True
                return True
            
            self.llm = ChatOpenAI(
                model=settings.OPENAI_MODEL,
                temperature=0.3,  # 更低温度以获得更一致的审查结果
                openai_api_key=settings.OPENAI_API_KEY
            )
            self.is_initialized = True
            logger.info("ReviewerAgent initialized successfully")
            return True
        except Exception as e:
            logger.error("Failed to initialize ReviewerAgent", error=str(e))
            return False
    
    async def execute(self, task_input: Dict[str, Any]) -> Dict[str, Any]:
        """执行代码审查任务"""
        try:
            if not self.is_initialized:
                await self.initialize()
            
            code = task_input.get("code", "")
            language = task_input.get("language", "python")
            focus_areas = task_input.get("focus_areas", ["quality", "security", "performance"])
            
            if not self.llm:
                return {
                    "success": True,
                    "issues": [],
                    "suggestions": ["Mock review - LLM not configured"],
                    "score": 85
                }
            
            prompt = f"""
语言: {language}
关注领域: {', '.join(focus_areas)}

代码:
```{language}
{code}
```

请审查以上代码，并提供：
1. 发现的问题（按严重程度分类）
2. 改进建议
3. 代码质量评分（0-100）
"""
            
            messages = [
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=prompt)
            ]
            
            response = await self.llm.ainvoke(messages)
            
            return {
                "success": True,
                "review": response.content,
                "language": language,
                "focus_areas": focus_areas
            }
        
        except Exception as e:
            logger.error("ReviewerAgent execution failed", error=str(e))
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_capabilities(self) -> List[str]:
        return ["code_review", "security_audit", "performance_analysis", "best_practices"]
