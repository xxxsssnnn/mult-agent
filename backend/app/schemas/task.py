from typing import Optional, Dict, Any
from pydantic import BaseModel
from datetime import datetime
from uuid import UUID


class TaskBase(BaseModel):
    title: str
    description: Optional[str] = None
    priority: int = 0


class TaskCreate(TaskBase):
    agent_id: Optional[UUID] = None
    input_data: Optional[Dict[str, Any]] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    output_data: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None


class TaskResponse(TaskBase):
    id: UUID
    task_id: str
    agent_id: Optional[UUID] = None
    status: str
    input_data: Optional[Dict[str, Any]] = None
    output_data: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    retry_count: int
    max_retries: int
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    
    class Config:
        from_attributes = True
