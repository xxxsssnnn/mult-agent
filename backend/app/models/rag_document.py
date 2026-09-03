"""RAG 文档记录模型 - 文档级元数据持久化（Enterprise RAG Phase 1）

支撑能力：
- 文档级管理：列表 / 删除 / 审计（谁在何时导入了什么）
- 幂等导入：以 (user_id, checksum) 唯一约束做 sha256 去重，重复上传直接跳过
- 多租户向量库归属：每行记录该文档切块所在的 Chroma collection，便于按文档清理向量
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)

from app.core.database import Base


class RAGDocument(Base):
    __tablename__ = "rag_documents"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # 租户标识：文档归属用户（物理隔离的 collection 按该用户命名）
    user_id = Column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    # 原始文件名（仅用于展示；磁盘路径一律使用随机 id，防路径穿越）
    filename = Column(String(255), nullable=False)
    # 扩展名（小写，含点）：.pdf / .txt / ...
    file_type = Column(String(20), nullable=False)
    # sha256 文件摘要：幂等去重依据
    checksum = Column(String(64), nullable=False)
    # 向量切块归属的 Chroma collection（rag_{user_id_hex}）
    collection_name = Column(String(128), nullable=False)
    # 向量切块数量（0 表示解析失败/无内容）
    chunk_count = Column(Integer, nullable=False, default=0)
    # indexed / failed（failed 时允许同校验和文件重试导入）
    status = Column(String(20), nullable=False, default="indexed")
    # 失败原因（status=failed 时非空）
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    __table_args__ = (
        # 幂等：同一用户导入相同内容的文件只保留一条记录
        UniqueConstraint("user_id", "checksum", name="uq_rag_documents_user_checksum"),
        # 文档级管理查询
        Index("ix_rag_documents_user_created", "user_id", "created_at"),
        # 删除单文档时按 id 查询（已带 user_id 约束防越权）
    )
