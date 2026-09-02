"""记忆向量索引（基于 ChromaDB）

为记忆条目提供语义检索能力，与关键词/质量混合打分互补。

设计要点:
- 懒加载: 首次使用时才初始化 Chroma 客户端（无 chroma 环境自动降级，
  仅记录告警日志，不阻断记忆主流程）
- 一致性: 向量索引与"有效记忆"保持一致 —— 写入/强化时 upsert，
  删除/归档时 remove
- 用户隔离: 元数据携带 user_id，检索按用户过滤
"""

import threading
from typing import List, Optional, Tuple

import structlog

logger = structlog.get_logger(__name__)

_ANONYMOUS = "anonymous"


def _uid_key(user_id) -> str:
    """用户隔离键：None 使用统一占位符"""
    return str(user_id) if user_id is not None else _ANONYMOUS


class MemoryVectorStore:
    """记忆向量索引管理器"""

    def __init__(self):
        self._manager = None
        self._available: Optional[bool] = None
        self._lock = threading.Lock()

    def _ensure(self):
        """懒加载 Chroma 管理器；失败一次后永久降级"""
        if self._available is not None:
            return self._manager
        with self._lock:
            if self._available is not None:
                return self._manager
            try:
                from app.rag.vector_store import VectorStoreManager

                self._manager = VectorStoreManager(collection_name="memory_entries")
                self._available = True
                logger.info("memory.vector.initialized")
            except Exception as e:  # noqa: BLE001
                self._available = False
                logger.warning("memory.vector.unavailable", error=str(e)[:200])
        return self._manager

    @property
    def available(self) -> bool:
        return self._ensure() is not None

    def index_entries(self, entries) -> int:
        """对记忆条目做向量 upsert（幂等）

        Args:
            entries: MemoryEntry 可迭代对象

        Returns:
            成功索引条数
        """
        manager = self._ensure()
        if not manager or not entries:
            return 0
        docs = [e.content or "" for e in entries]
        ids = [str(e.id) for e in entries]
        metas = [
            {
                "user_id": _uid_key(e.user_id),
                "memory_type": e.memory_type or "unknown",
                "session_id": str(e.session_id or ""),
            }
            for e in entries
        ]
        try:
            embs = manager.embedding_service.embeddings.embed_documents(docs)
            manager.collection.upsert(
                ids=ids,
                documents=docs,
                embeddings=embs,
                metadatas=metas,
            )
            return len(ids)
        except Exception as e:  # noqa: BLE001
            logger.warning("memory.vector.index_failed", error=str(e)[:200])
            return 0

    def remove_entries(self, ids) -> int:
        """按记忆条目 ID 移除向量"""
        manager = self._ensure()
        if not manager or not ids:
            return 0
        str_ids = [str(i) for i in ids]
        try:
            manager.collection.delete(ids=str_ids)
            return len(str_ids)
        except Exception as e:  # noqa: BLE001
            logger.warning("memory.vector.remove_failed", error=str(e)[:200])
            return 0

    def search(
        self,
        query: str,
        top_k: int = 10,
        user_id=None,
    ) -> List[Tuple[str, float]]:
        """语义搜索，返回 [(memory_id, similarity)]，similarity ∈ (0, 1]"""
        manager = self._ensure()
        if not manager or not query:
            return []
        try:
            emb = manager.embedding_service.embeddings.embed_query(query)
            res = manager.collection.query(
                query_embeddings=[emb],
                n_results=top_k,
                where={"user_id": _uid_key(user_id)},
            )
            ids = (res.get("ids") or [[]])[0]
            distances = (res.get("distances") or [[]])[0]
            hits: List[Tuple[str, float]] = []
            for mid, dist in zip(ids, distances):
                sim = 1.0 / (1.0 + float(dist)) if dist is not None else 0.0
                hits.append((mid, sim))
            return hits
        except Exception as e:  # noqa: BLE001
            logger.warning("memory.vector.search_failed", error=str(e)[:200])
            return []


memory_vector_store = MemoryVectorStore()
