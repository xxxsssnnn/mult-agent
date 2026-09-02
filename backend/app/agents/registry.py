from typing import Dict, List, Optional
from uuid import UUID
from app.agents.base import BaseAgent
import structlog

logger = structlog.get_logger(__name__)


class AgentRegistry:
    """Agent注册中心，管理所有可用的Agent"""
    
    def __init__(self):
        self._agents: Dict[str, BaseAgent] = {}
    
    def register(self, agent: BaseAgent) -> None:
        """注册Agent"""
        agent_key = str(agent.agent_id)
        self._agents[agent_key] = agent
        logger.info("Agent registered", agent_id=agent_key, name=agent.name)
    
    def unregister(self, agent_id: UUID) -> bool:
        """注销Agent"""
        agent_key = str(agent_id)
        if agent_key in self._agents:
            del self._agents[agent_key]
            logger.info("Agent unregistered", agent_id=agent_key)
            return True
        return False
    
    def get_agent(self, agent_id: UUID) -> Optional[BaseAgent]:
        """获取指定Agent"""
        agent_key = str(agent_id)
        return self._agents.get(agent_key)
    
    def list_agents(self) -> List[Dict[str, any]]:
        """列出所有已注册的Agent"""
        agents_info = []
        for agent_key, agent in self._agents.items():
            agents_info.append({
                "agent_id": agent_key,
                "name": agent.name,
                "capabilities": agent.get_capabilities(),
                "is_initialized": agent.is_initialized
            })
        return agents_info
    
    def get_agents_by_capability(self, capability: str) -> List[BaseAgent]:
        """根据能力查找Agent"""
        matching_agents = []
        for agent in self._agents.values():
            if capability in agent.get_capabilities():
                matching_agents.append(agent)
        return matching_agents
    
    def clear(self):
        """清空所有Agent"""
        self._agents.clear()
        logger.info("All agents cleared")


# 全局Agent注册表实例
agent_registry = AgentRegistry()
