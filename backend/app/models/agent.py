import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, JSON, Uuid
from sqlalchemy.orm import relationship
from app.core.database import Base


class Agent(Base):
    __tablename__ = "agents"
    
    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    type = Column(String(50), nullable=False)
    description = Column(Text)
    config = Column(JSON)
    # 能力列表（JSON 存储，兼容 PostgreSQL/SQLite；无数组 SQL 查询语义）
    capabilities = Column(JSON)
    status = Column(String(20), default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    tasks = relationship("Task", back_populates="agent")
