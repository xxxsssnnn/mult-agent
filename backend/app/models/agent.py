import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, JSON, ARRAY
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.core.database import Base


class Agent(Base):
    __tablename__ = "agents"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    type = Column(String(50), nullable=False)
    description = Column(Text)
    config = Column(JSON)
    capabilities = Column(ARRAY(String))
    status = Column(String(20), default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    tasks = relationship("Task", back_populates="agent")
