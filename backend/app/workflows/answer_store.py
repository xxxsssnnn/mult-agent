"""Workflow 答案语义检索（执行档案向量索引）

功能：workflow 归档时把执行答案（父任务复盘文本 + 子任务标题/结果）向量化，
落盘到独立 Chroma collection；后续可用自然语言按用户隔离地语义检索
"上次代码审查结论 / 某次任务规划失败原因 / 某个子任务干了什么"。

设计要点：
- 独立持久目录（默认 ./wf_answer_db），与 RAG 的 chroma_db 分开，
  避免同一进程内多个 PersistentClient 指向同一目录产生 sqlite 锁冲突。
- 全部用户共享 collection，按 metadata.user_id 过滤实现租户隔离。
- Embedding 复用 RAG 配置（RAG_EMBEDDING_MODEL_TYPE/NAME）。
- 后端不可用 / embedding 失败一律静默降级：索引返回 0、检索返回 []，不阻断主流程。
- 同步核心方法（Chroma 非线程安全，内部加锁）；异步入口经 asyncio.to_thread 委托，
  供 API 层直接 await。
"""
import asyncio
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.config import Settings as ChromaSettings
import structlog

from app.core.config import settings
from app.rag.embedding_service import EmbeddingService
from app.workflows.recap import format_recap

logger = structlog.get_logger(__name__)

MAX_DOC_CHARS = 6000  # 单条文档索引文本上限（防超大 detail 拖垮 embedding）

# 状态英文值 → 中文展示（检索文本面向自然语言，中文状态更易命中）
_STATUS_LABELS = {
    "completed": "成功",
    "failed": "失败",
    "pending": "待执行",
    "skipped": "跳过",
    "running": "进行中",
    "processing": "进行中",
}


def _fmt_user(user_id: Any) -> str:
    """统一 user_id 为 Chroma metadata 可存储的字符串（UUID 去横线）。"""
    return str(user_id).replace("-", "")


def _status_label(status: Any) -> str:
    return _STATUS_LABELS.get(str(status), str(status))


def _build_where(
    user_id: Any,
    *,
    workflow_label: Optional[str] = None,
    status: Optional[str] = None,
    task_id: Optional[str] = None,
    parent_task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """组装 Chroma where：多条件必须用 $and（单 key 直接相等过滤）。"""
    conditions: List[Dict[str, Any]] = [{"user_id": _fmt_user(user_id)}]
    if workflow_label:
        conditions.append({"workflow_label": workflow_label})
    if status:
        conditions.append({"status": status})
    if task_id:
        conditions.append({"task_id": task_id})
    if parent_task_id:
        conditions.append({"parent_task_id": parent_task_id})
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


def _render_text(value: Any, depth: int = 0) -> str:
    """把执行结果/明细渲染为可读纯文本（str/dict/list/标量）。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        parts = []
        for k, v in value.items():
            if v is None or v == "":
                continue
            rendered = _render_text(v, depth + 1)
            if rendered:
                parts.append(f"{k}：{rendered}" if not str(k).startswith("_") else rendered)
        return "\n".join(parts)
    if isinstance(value, (list, tuple)):
        parts = []
        for i, v in enumerate(value):
            rendered = _render_text(v, depth + 1)
            if rendered:
                parts.append(f"{i + 1}. {rendered}" if depth == 0 else rendered)
        return "\n".join(parts)
    return str(value)


def build_run_documents(
    *,
    user_id: Any,
    task_id: str,
    title: str,
    workflow_label: str,
    objective: str,
    success: bool,
    recap: Optional[Dict[str, Any]],
    detail: Optional[Dict[str, Any]],
    subtasks: Optional[List[Dict[str, Any]]] = None,
    created_at: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """把一次归档执行展开为待向量化的文档列表（1 父 + N 子）。"""
    uid = _fmt_user(user_id)
    ts = created_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    status = "completed" if success else "failed"

    parent_lines = [
        f"【{workflow_label} · 执行档案】",
        f"标题：{title}",
        f"目标：{objective or '（未提供）'}",
        f"结果：{'成功' if success else '失败'}",
    ]
    recap_text = format_recap(recap) if isinstance(recap, dict) else ""
    if recap_text:
        parent_lines.append(recap_text)
    if detail:
        detail_text = _render_text(detail)
        if detail_text:
            parent_lines.append(f"归档详情：{detail_text}")

    docs: List[Dict[str, Any]] = [
        {
            "id": f"u{uid}-wf{task_id}",
            "text": "\n".join(parent_lines)[:MAX_DOC_CHARS],
            "task_id": task_id,
            "parent_task_id": None,
            "workflow_label": workflow_label,
            "title": title,
            "status": status,
            "is_subtask": 0,
            "created_at": ts,
        }
    ]

    for item in subtasks or []:
        sub_task_id = str(item.get("task_id") or f"{task_id}-{int(item.get('seq', 0)):03d}")
        sub_title = (item.get("title") or item.get("type") or "子任务")[:150]
        sub_status = item.get("status") or "pending"
        lines = [
            f"【{workflow_label} · 子任务执行结果】",
            f"子任务：{sub_title}",
            f"状态：{_status_label(sub_status)}",
        ]
        result = _render_text(item.get("detail") or item.get("result") or item.get("summary"))
        if result:
            lines.append(f"执行结果：{result}")
        docs.append(
            {
                "id": f"u{uid}-wf{sub_task_id}",
                "text": "\n".join(lines)[:MAX_DOC_CHARS],
                "task_id": sub_task_id,
                "parent_task_id": task_id,
                "workflow_label": workflow_label,
                "title": sub_title,
                "status": sub_status,
                "is_subtask": 1,
                "created_at": ts,
            }
        )
    return docs


class WorkflowAnswerStore:
    """workflow 执行答案语义索引。同步核心 + 异步入口；失败静默降级。"""

    def __init__(
        self,
        *,
        persist_directory: Optional[str] = None,
        collection_name: Optional[str] = None,
        embedding_service: Optional[EmbeddingService] = None,
        chroma_client: Optional[Any] = None,
    ):
        self.persist_directory = persist_directory or settings.WORKFLOW_ANSWER_PERSIST_DIRECTORY
        self.collection_name = collection_name or settings.WORKFLOW_ANSWER_COLLECTION
        # embedding 延迟初始化（放 _ensure 内，避免 import 期即下载模型/校验 key）
        self._embedding_service = embedding_service
        self._client = chroma_client
        self._collection = None
        self._lock = threading.Lock()
        self._error: Optional[str] = None
        self._available = False
        self._enabled = settings.WORKFLOW_ANSWER_INDEX_ENABLED

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #

    @property
    def available(self) -> bool:
        if not self._enabled:
            return False
        if not self._available:
            self._ensure()
        return self._available

    @property
    def error(self) -> Optional[str]:
        return self._error

    def _ensure(self) -> bool:
        if self._available or not self._enabled:
            return self._available
        with self._lock:
            if self._available:
                return True
            try:
                if self._embedding_service is None:
                    self._embedding_service = EmbeddingService(
                        model_type=settings.RAG_EMBEDDING_MODEL_TYPE,
                        model_name=settings.RAG_EMBEDDING_MODEL_NAME or None,
                    )
                if self._client is None:
                    self._client = chromadb.PersistentClient(
                        path=self.persist_directory,
                        settings=ChromaSettings(anonymized_telemetry=False),
                    )
                self._collection = self._client.get_or_create_collection(
                    name=self.collection_name,
                    metadata={"hnsw:space": "cosine"},
                )
                self._available = True
                self._error = None
                logger.info(
                    "WorkflowAnswerStore ready",
                    persist_directory=self.persist_directory,
                    collection=self.collection_name,
                )
            except Exception as exc:  # noqa: BLE001
                self._available = False
                self._error = str(exc)
                logger.warning(
                    "WorkflowAnswerStore unavailable; answer semantic search disabled",
                    error=str(exc),
                )
            return self._available

    # ------------------------------------------------------------------ #
    # 写入
    # ------------------------------------------------------------------ #

    def index_run(
        self,
        *,
        user_id: Any,
        task_id: str,
        title: str,
        workflow_label: str,
        objective: str = "",
        success: bool = True,
        recap: Optional[Dict[str, Any]] = None,
        detail: Optional[Dict[str, Any]] = None,
        subtasks: Optional[List[Dict[str, Any]]] = None,
        created_at: Optional[str] = None,
    ) -> int:
        """索引一次归档执行（父复盘 + 子任务），返回索引文档数；失败返回 0。"""
        if not self.available or self._collection is None:
            return 0
        docs = build_run_documents(
            user_id=user_id,
            task_id=task_id,
            title=title,
            workflow_label=workflow_label,
            objective=objective,
            success=success,
            recap=recap,
            detail=detail,
            subtasks=subtasks,
            created_at=created_at,
        )
        if not docs:
            return 0
        try:
            embeddings = self._embedding_service.embeddings.embed_documents(  # type: ignore[union-attr]
                [d["text"] for d in docs]
            )
            with self._lock:
                self._collection.upsert(
                    ids=[d["id"] for d in docs],
                    documents=[d["text"] for d in docs],
                    embeddings=embeddings,
                    metadatas=[
                        {
                            "user_id": _fmt_user(user_id),
                            "task_id": d["task_id"],
                            "parent_task_id": d["parent_task_id"] or "",
                            "workflow_label": d["workflow_label"],
                            "title": d["title"],
                            "status": d["status"],
                            "is_subtask": d["is_subtask"],
                            "created_at": d["created_at"],
                        }
                        for d in docs
                    ],
                )
            return len(docs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("WorkflowAnswerStore.index_run failed", task_id=task_id, error=str(exc))
            return 0

    async def index_run_async(self, **kwargs: Any) -> int:
        """异步入口：归档链路调用，避免同步 embedding 阻塞事件循环。"""
        return await asyncio.to_thread(self.index_run, **kwargs)

    def remove_task(self, user_id: Any, task_id: str) -> int:
        """删除某次归档的全部索引（父任务文档 + 子任务文档）。失败返回 0。"""
        if not self.available or self._collection is None:
            return 0
        try:
            # 父文档：task_id 即本归档 id；子文档：parent_task_id 指向本归档 id
            parent = self._collection.get(where=_build_where(user_id, task_id=task_id), include=[])
            children = self._collection.get(
                where=_build_where(user_id, parent_task_id=task_id), include=[]
            )
            ids = list(dict.fromkeys(list(parent.get("ids") or []) + list(children.get("ids") or [])))
            with self._lock:
                if ids:
                    self._collection.delete(ids=ids)
            return len(ids)
        except Exception as exc:  # noqa: BLE001
            logger.warning("WorkflowAnswerStore.remove_task failed", task_id=task_id, error=str(exc))
            return 0

    # ------------------------------------------------------------------ #
    # 检索
    # ------------------------------------------------------------------ #

    def search(
        self,
        *,
        user_id: Any,
        query: str,
        top_k: int = 5,
        workflow_label: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """语义检索当前用户的执行档案；失败 / 不可用返回 []。"""
        if not self.available or self._collection is None:
            return []
        if not query.strip():
            return []
        try:
            qemb = self._embedding_service.embeddings.embed_query(query)  # type: ignore[union-attr]
            with self._lock:
                result = self._collection.query(
                    query_embeddings=[qemb],
                    n_results=max(1, min(int(top_k), 50)),
                    where=_build_where(user_id, workflow_label=workflow_label, status=status),
                    include=["documents", "metadatas", "distances"],
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("WorkflowAnswerStore.search failed", query=query[:80], error=str(exc))
            return []

        entries: List[Dict[str, Any]] = []
        ids_list = result.get("ids") or [[]]
        docs_list = result.get("documents") or [[]]
        metas_list = result.get("metadatas") or [[]]
        dists_list = result.get("distances") or [[]]
        for i in range(len(ids_list[0])):
            meta = metas_list[0][i] or {}
            distance = float(dists_list[0][i])
            entries.append(
                {
                    "task_id": meta.get("task_id", ""),
                    "parent_task_id": meta.get("parent_task_id") or None,
                    "workflow_label": meta.get("workflow_label", ""),
                    "title": meta.get("title", ""),
                    "status": meta.get("status", ""),
                    "is_subtask": bool(meta.get("is_subtask", 0)),
                    "content": docs_list[0][i],
                    "similarity": round(max(0.0, min(1.0, 1.0 - distance)), 4),
                    "created_at": meta.get("created_at"),
                }
            )
        return entries

    async def search_async(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.search, **kwargs)

    # ------------------------------------------------------------------ #
    # 统计（运维 / 测试用）
    # ------------------------------------------------------------------ #

    def count(self, user_id: Optional[Any] = None) -> int:
        if not self.available or self._collection is None:
            return 0
        try:
            where = {"user_id": _fmt_user(user_id)} if user_id is not None else None
            result = self._collection.get(where=where, include=[])
            return len(result.get("ids") or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("WorkflowAnswerStore.count failed", error=str(exc))
            return 0

    def health(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "error": self.error,
            "persist_directory": self.persist_directory,
            "collection": self.collection_name,
            "indexed_entries": self.count(),
        }


# 进程级单例（lazy：首次调用时才真正连接 Chroma / 加载 embedding）
workflow_answer_store = WorkflowAnswerStore()
