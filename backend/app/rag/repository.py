"""RAGDocument 仓储层

集中 RAG 文档记录的数据库访问。RAGAgent 通过该仓储读写文档元数据，
测试可注入内存版仓储以隔离真实数据库。
"""

from typing import Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag_document import RAGDocument


class RAGDocumentRepository:
    """基于 SQLAlchemy AsyncSession 的文档记录仓储"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def find_by_checksum(self, user_id, checksum: str) -> Optional[RAGDocument]:
        """按 (user_id, checksum) 幂等查找（任何状态均返回，由调用方判断）"""
        result = await self.db.execute(
            select(RAGDocument).where(
                RAGDocument.user_id == user_id,
                RAGDocument.checksum == checksum,
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        user_id,
        filename: str,
        file_type: str,
        checksum: str,
        collection_name: str,
        chunk_count: int = 0,
        status: str = "indexed",
        error_message: Optional[str] = None,
    ) -> RAGDocument:
        """创建文档记录并落库"""
        record = RAGDocument(
            user_id=user_id,
            filename=filename,
            file_type=file_type,
            checksum=checksum,
            collection_name=collection_name,
            chunk_count=chunk_count,
            status=status,
            error_message=error_message,
        )
        self.db.add(record)
        await self.db.commit()
        await self.db.refresh(record)
        return record

    async def get_for_user(self, user_id, document_id) -> Optional[RAGDocument]:
        """取文档并校验归属（user_id 约束防越权访问）"""
        result = await self.db.execute(
            select(RAGDocument).where(
                RAGDocument.id == document_id,
                RAGDocument.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_documents(
        self, user_id, offset: int = 0, limit: int = 20
    ) -> Tuple[int, list]:
        """分页列出当前用户的文档，按导入时间倒序"""
        total_result = await self.db.execute(
            select(func.count()).select_from(RAGDocument).where(RAGDocument.user_id == user_id)
        )
        total = total_result.scalar_one()

        rows_result = await self.db.execute(
            select(RAGDocument)
            .where(RAGDocument.user_id == user_id)
            .order_by(RAGDocument.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = list(rows_result.scalars().all())
        return total, rows

    async def delete(self, record: RAGDocument) -> None:
        """删除单条文档记录"""
        await self.db.delete(record)
        await self.db.commit()

    async def delete_all_for_user(self, user_id) -> int:
        """删除当前用户全部文档记录，返回删除条数"""
        rows_result = await self.db.execute(
            select(RAGDocument).where(RAGDocument.user_id == user_id)
        )
        rows = list(rows_result.scalars().all())
        for record in rows:
            await self.db.delete(record)
        await self.db.commit()
        return len(rows)

    async def count_for_user(self, user_id) -> int:
        """统计当前用户文档数"""
        result = await self.db.execute(
            select(func.count()).select_from(RAGDocument).where(RAGDocument.user_id == user_id)
        )
        return result.scalar_one()
