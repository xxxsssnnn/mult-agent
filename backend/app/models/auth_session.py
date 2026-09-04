"""Refresh Token 会话台账：撤销 / 轮换 / 重用检测的基础表。"""
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, String, Uuid

from app.core.database import Base


class AuthSession(Base):
    """一次登录（或一次轮换）对应一行；一条轮换链共用一个 family_id。

    - token_hash：refresh token 的 SHA-256，不落明文（唯一索引关联撤销查找）
    - revoked_reason：logout / logout_all / rotated / reuse_detected
    - replaced_by：轮换后新行的 id，形成审计链
    """

    __tablename__ = "auth_sessions"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    family_id = Column(Uuid(as_uuid=True), nullable=False, index=True)
    token_hash = Column(String(64), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    revoked_reason = Column(String(50), nullable=True)
    replaced_by = Column(Uuid(as_uuid=True), nullable=True)
