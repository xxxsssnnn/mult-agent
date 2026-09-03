from abc import ABC, abstractmethod
from copy import deepcopy
from typing import Any, Dict, List, Optional
from uuid import UUID
import structlog

logger = structlog.get_logger(__name__)


class BaseAgent(ABC):
    """Agent基类，所有具体Agent都需要继承此类"""
    
    #: execute_with_memory 从任务输入提取"用户消息"的键（按优先级）
    _MEMORY_INPUT_KEYS: List[str] = ["user_input", "requirement", "question", "query"]
    #: execute_with_memory 从结果提取"助手消息"的键（按优先级）
    _MEMORY_OUTPUT_KEYS: List[str] = [
        "explanation", "review", "summary", "output", "answer",
    ]
    #: 注入 prompt 的记忆上下文最大长度（超出截断，控制 token 成本）
    MEMORY_CONTEXT_MAX_CHARS: int = 4000
    #: 以代码作为用户消息/助手消息时的截断长度
    _MEMORY_CODE_MAX_CHARS: int = 2000
    
    def __init__(self, agent_id: UUID, name: str, config: Optional[Dict[str, Any]] = None):
        self.agent_id = agent_id
        self.name = name
        self.config = config or {}
        self.is_initialized = False
        self.memory_manager = None  # 记忆管理器
    
    @abstractmethod
    async def initialize(self) -> bool:
        """初始化Agent"""
        pass
    
    @abstractmethod
    async def execute(self, task_input: Dict[str, Any]) -> Dict[str, Any]:
        """执行任务"""
        pass
    
    @abstractmethod
    def get_capabilities(self) -> List[str]:
        """获取Agent能力列表"""
        pass
    
    async def health_check(self) -> Dict[str, Any]:
        """健康检查"""
        return {
            "agent_id": str(self.agent_id),
            "name": self.name,
            "status": "healthy" if self.is_initialized else "uninitialized",
            "is_initialized": self.is_initialized,
            "has_memory": self.memory_manager is not None
        }
    
    async def set_memory(self, session_id: str, user_id: Optional[str] = None, db_session=None):
        """
        为Agent设置记忆管理器（按会话新建）

        Args:
            session_id: 会话ID
            user_id: 用户ID（可选）
            db_session: 数据库会话（可选）
        """
        from app.memory import MemoryManager
        
        manager = MemoryManager(
            session_id=session_id,
            user_id=user_id,
            db_session=db_session
        )
        await manager.initialize()
        await self.attach_memory(manager)
        return manager
    
    async def attach_memory(self, memory_manager):
        """
        挂载已构造的记忆管理器（供工作流在多 Agent 间共享同一会话记忆）
        
        与 set_memory 的区别：不重新初始化、复用外部传入的 manager，
        因此多个 Agent 可以指向同一个 MemoryManager，共享会话上下文。
        """
        self.memory_manager = memory_manager
        logger.info(
            "Memory attached to agent",
            agent_name=self.name,
            session_id=getattr(memory_manager, "session_id", None),
        )
    
    # ------------------------------------------------------------------ #
    # 记忆消息提取适配
    # ------------------------------------------------------------------ #
    @classmethod
    def extract_user_message(cls, task_input: Dict[str, Any]) -> Optional[str]:
        """从任务输入中提取应写入记忆的"用户消息"文本"""
        for key in cls._MEMORY_INPUT_KEYS:
            value = task_input.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        # 无自然语言输入、仅有待处理代码时（如 code review），加前缀避免歧义
        code = task_input.get("code")
        if isinstance(code, str) and code.strip():
            return "请审查以下代码：\n" + code[: cls._MEMORY_CODE_MAX_CHARS]
        return None
    
    @classmethod
    def extract_assistant_message(cls, result: Dict[str, Any]) -> Optional[str]:
        """从执行结果中提取应写入记忆的"助手消息"文本"""
        for key in cls._MEMORY_OUTPUT_KEYS:
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        # 兜底：产物本身（如生成的代码），截断保存
        code = result.get("code")
        if isinstance(code, str) and code.strip():
            return code[: cls._MEMORY_CODE_MAX_CHARS]
        return None
    
    @staticmethod
    def truncate(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "\n…（内容过长已截断）"
    
    # ------------------------------------------------------------------ #
    # 带记忆执行
    # ------------------------------------------------------------------ #
    async def execute_with_memory(self, task_input: Dict[str, Any]) -> Dict[str, Any]:
        """
        带记忆执行任务：检索记忆上下文注入任务输入，并把本轮对话写入记忆。

        - 使用 task_input 的深拷贝，不污染调用方 dict
        - 用户消息自动适配各 Agent 输入键（requirement/question/code…）
        - 助手消息自动适配各 Agent 输出键（output/code/review…）
        - 未挂载记忆时行为等价于直接 execute()
        
        Returns:
            执行结果（与 execute 相同结构）
        """
        work_input: Dict[str, Any] = deepcopy(task_input)
        
        if self.memory_manager is not None:
            context = await self.memory_manager.get_context()
            if context:
                work_input["memory_context"] = self.truncate(
                    context, self.MEMORY_CONTEXT_MAX_CHARS
                )
            
            user_text = self.extract_user_message(task_input)
            if user_text:
                await self.memory_manager.add_message("user", user_text)
        
        result = await self.execute(work_input)
        
        if self.memory_manager is not None:
            assistant_text = self.extract_assistant_message(result)
            if assistant_text:
                await self.memory_manager.add_message(
                    "assistant", assistant_text
                )
        
        return result
    
    def __repr__(self):
        return f"{self.__class__.__name__}(id={self.agent_id}, name={self.name})"
