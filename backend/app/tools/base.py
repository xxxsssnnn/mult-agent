from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import structlog

logger = structlog.get_logger(__name__)


class BaseTool(ABC):
    """工具基类"""
    
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
    
    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """执行工具"""
        pass
    
    @abstractmethod
    def get_parameters_schema(self) -> Dict[str, Any]:
        """获取参数schema"""
        pass
    
    def validate_input(self, **kwargs) -> bool:
        """验证输入参数"""
        schema = self.get_parameters_schema()
        required_params = schema.get("required", [])
        
        for param in required_params:
            if param not in kwargs:
                logger.warning(f"Missing required parameter: {param}")
                return False
        
        return True
    
    def __repr__(self):
        return f"{self.__class__.__name__}(name={self.name})"
