from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from datetime import datetime
from uuid import UUID


class AgentBase(BaseModel):
    name: str
    type: str
    description: Optional[str] = None
    capabilities: List[str] = []


class AgentCreate(AgentBase):
    config: Optional[Dict[str, Any]] = None


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    description: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    capabilities: Optional[List[str]] = None
    status: Optional[str] = None


class AgentResponse(AgentBase):
    id: UUID
    status: str
    config: Optional[Dict[str, Any]] = None
    created_at: datetime
    
    class Config:
        from_attributes = True
