from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from uuid import UUID
import structlog

logger = structlog.get_logger(__name__)


class BaseAgent(ABC):
    """Agent基类，所有具体Agent都需要继承此类"""
    
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
        为Agent设置记忆管理器
        
        Args:
            session_id: 会话ID
            user_id: 用户ID（可选）
            db_session: 数据库会话（可选）
        """
        from app.memory import MemoryManager
        
        self.memory_manager = MemoryManager(
            session_id=session_id,
            user_id=user_id,
            db_session=db_session
        )
        await self.memory_manager.initialize()
        logger.info("Memory set for agent", agent_name=self.name, session_id=session_id)
    
    async def execute_with_memory(self, task_input: Dict[str, Any]) -> Dict[str, Any]:
        """
        带记忆执行任务
        
        Args:
            task_input: 任务输入
            
        Returns:
            执行结果
        """
        # 如果有记忆，获取上下文并添加到任务输入中
        if self.memory_manager:
            context = await self.memory_manager.get_context()
            task_input["memory_context"] = context
            
            # 记录用户输入到记忆
            if "user_input" in task_input:
                await self.memory_manager.add_message("user", task_input["user_input"])
        
        # 执行任务
        result = await self.execute(task_input)
        
        # 记录助手输出到记忆
        if self.memory_manager and "output" in result:
            await self.memory_manager.add_message("assistant", result["output"])
        
        return result
    
    def __repr__(self):
        return f"{self.__class__.__name__}(id={self.agent_id}, name={self.name})"
