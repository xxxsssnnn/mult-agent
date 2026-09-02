"""记忆条目模型 - 企业级长期记忆核心表

memory_type 枚举约定:
- fact        事实记忆（如"项目部署在阿里云 ECS"）
- preference  偏好记忆（如"用户偏好 pytest"）
- event       事件记忆（如"2024-05-01 完成了用户注册模块"）
- summary     会话摘要
- procedural  程序性记忆（如"部署流程是先构建再滚动更新"）
"""
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Text, DateTime, Float, Integer, JSON, ForeignKey, Index, Uuid,
)
from sqlalchemy.orm import relationship
from app.core.database import Base


class MemoryEntry(Base):
    __tablename__ = "memory_entries"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # 可空: 兼容匿名/系统级记忆
    user_id = Column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)
    # 来源会话, 可空表示跨会话记忆
    session_id = Column(String(100), nullable=True, index=True)
    # 命名空间: user / team / session
    namespace = Column(String(20), nullable=False, default="user")
    # 记忆类型: fact / preference / event / summary / procedural
    memory_type = Column(String(20), nullable=False, index=True)
    # 记忆内容(已脱敏)
    content = Column(Text, nullable=False)
    # 主体实体, 用于冲突检测与覆盖, 如 "test_framework"
    entity = Column(String(200), nullable=True)
    # 记忆强度 0~1, 用于衰减与遗忘
    strength = Column(Float, default=0.5)
    # 置信度 0~1, 由提取阶段给出
    confidence = Column(Float, default=0.5)
    # 命中次数(检索时 +1)
    access_count = Column(Integer, default=0)
    last_accessed_at = Column(DateTime, nullable=True)
    # 溯源: 产生该记忆的消息 ID 列表, 支持审计
    source_message_ids = Column(JSON, nullable=True)
    # 合规保留策略: 过期时间, 到期后由定时任务归档
    expires_at = Column(DateTime, nullable=True, index=True)
    # 软删除(被遗忘权)
    archived_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 复合索引: 同一用户按强度/时间取高权重记忆
    __table_args__ = (
        Index(
            "ix_memory_user_strength_updated",
            "user_id", "strength", "updated_at",
        ),
        # 检索主查询 user_id + archived_at IS NULL + ORDER BY strength/updated_at，
        # 旧复合索引无法过滤归档行；此索引覆盖活跃条目检索路径
        Index(
            "ix_memory_user_archived_strength_updated",
            "user_id", "archived_at", "strength", "updated_at",
        ),
    )
