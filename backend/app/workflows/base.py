from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Callable
from langgraph.graph import StateGraph, END
import structlog
from datetime import datetime

logger = structlog.get_logger(__name__)


class WorkflowState(Dict[str, Any]):
    """工作流状态基类"""
    pass


class WorkflowProgress:
    """工作流进度信息"""
    def __init__(self, workflow_name: str, current_step: str, total_steps: int, completed_steps: int):
        self.workflow_name = workflow_name
        self.current_step = current_step
        self.total_steps = total_steps
        self.completed_steps = completed_steps
        self.timestamp = datetime.utcnow()
        self.percentage = (completed_steps / total_steps * 100) if total_steps > 0 else 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "workflow_name": self.workflow_name,
            "current_step": self.current_step,
            "total_steps": self.total_steps,
            "completed_steps": self.completed_steps,
            "percentage": round(self.percentage, 2),
            "timestamp": self.timestamp.isoformat()
        }


class BaseWorkflow(ABC):
    """工作流基类"""
    
    def __init__(self, name: str, description: str = "", memory_manager=None):
        self.name = name
        self.description = description
        self.graph = None
        self.progress_callback: Optional[Callable] = None
        self.max_retries = 3
        self.retry_delay = 1  # seconds
        #: 工作流级共享会话记忆管理器（可构造注入复用，或由 execute 的 memory 配置创建）
        self.memory_manager = memory_manager
    
    def set_progress_callback(self, callback: Callable):
        """设置进度回调函数"""
        self.progress_callback = callback
    
    async def notify_progress(self, current_step: str, total_steps: int, completed_steps: int):
        """通知进度更新"""
        progress = WorkflowProgress(self.name, current_step, total_steps, completed_steps)
        
        if self.progress_callback:
            try:
                await self.progress_callback(progress.to_dict())
            except Exception as e:
                logger.warning(f"Progress callback failed: {e}")
        
        logger.info(
            "Workflow progress",
            workflow=self.name,
            step=current_step,
            progress=f"{completed_steps}/{total_steps}",
            percentage=f"{progress.percentage:.1f}%"
        )
    
    @abstractmethod
    def build_graph(self) -> StateGraph:
        """构建工作流图"""
        pass
    
    @abstractmethod
    async def execute(self, initial_state: Dict[str, Any]) -> Dict[str, Any]:
        """执行工作流"""
        pass
    
    def get_workflow_info(self) -> Dict[str, Any]:
        """获取工作流信息"""
        return {
            "name": self.name,
            "description": self.description,
            "type": self.__class__.__name__
        }
    
    # ------------------------------------------------------------------ #
    # 会话记忆支持（workflow 级共享，贯穿其所有 Agent）
    # ------------------------------------------------------------------ #
    async def ensure_memory(self, initial_state: Dict[str, Any]):
        """
        确保存在共享会话记忆管理器并返回。
        
        优先级：构造注入的 memory_manager > initial_state["memory"] 配置。
        initial_state["memory"] 格式：
            {"session_id": str, "user_id": optional, "db_session": optional}
        未配置时返回 None（该工作流执行不产生记忆，行为与旧版一致）。
        """
        if self.memory_manager is not None:
            return self.memory_manager

        cfg = initial_state.get("memory") or {}
        if not isinstance(cfg, dict) or not cfg.get("session_id"):
            return None

        from app.memory import MemoryManager

        manager = MemoryManager(
            session_id=str(cfg["session_id"]),
            user_id=cfg.get("user_id"),
            db_session=cfg.get("db_session"),
        )
        await manager.initialize()
        self.memory_manager = manager
        return manager
    
    def memory_info(self) -> Optional[Dict[str, Any]]:
        """工作流会话记忆元信息（供结果回传，便于前端延续同一会话）"""
        if self.memory_manager is None:
            return None
        return {
            "enabled": True,
            "session_id": getattr(self.memory_manager, "session_id", None),
        }
