import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, JSON, Uuid
from app.core.database import Base


class WorkflowRun(Base):
    """Workflow 运行台账：run 级实体 + 增量 checkpoint（断点恢复/运行查询）。

    checkpoint 为 JSON 快照（见 app/workflows/checkpoint.py），记录每次
    子任务终态后的完整任务定义/results/attempts。
    """

    __tablename__ = "workflow_runs"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(String(64), nullable=False, index=True)
    label = Column(String(50), nullable=False, default="workflow")
    objective = Column(Text, nullable=False, default="")
    status = Column(String(20), nullable=False, default="running")
    checkpoint = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
