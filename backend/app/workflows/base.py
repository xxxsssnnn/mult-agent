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
    
    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.graph = None
        self.progress_callback: Optional[Callable] = None
        self.max_retries = 3
        self.retry_delay = 1  # seconds
    
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
