from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from datetime import datetime
from uuid import UUID


class MessageBase(BaseModel):
    content: str
    role: str


class MessageCreate(MessageBase):
    tool_calls: Optional[List[Dict[str, Any]]] = None


class MessageResponse(MessageBase):
    id: UUID
    conversation_id: UUID
    tool_calls: Optional[List[Dict[str, Any]]] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


class ConversationBase(BaseModel):
    title: Optional[str] = None


class ConversationCreate(ConversationBase):
    pass


class ConversationResponse(ConversationBase):
    id: UUID
    session_id: str
    user_id: Optional[UUID] = None
    metadata_: Optional[Dict[str, Any]] = None
    created_at: datetime
    messages: List[MessageResponse] = []
    
    class Config:
        from_attributes = True
