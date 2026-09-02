import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, Integer, DateTime, JSON, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship, backref
from app.core.database import Base


class Task(Base):
    __tablename__ = "tasks"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(String(100), unique=True, nullable=False, index=True)
    parent_task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id"))
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id"))
    title = Column(String(200), nullable=False)
    description = Column(Text)
    status = Column(String(20), default="pending")
    priority = Column(Integer, default=0)
    input_data = Column(JSON)
    output_data = Column(JSON)
    error_message = Column(Text)
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    agent = relationship("Agent", back_populates="tasks")
    # 自引用关系：parent_task 为多对一方向（remote_side 必须在父端）
    subtasks = relationship(
        "Task",
        backref=backref("parent_task", remote_side="Task.id"),
        foreign_keys="Task.parent_task_id",
    )
