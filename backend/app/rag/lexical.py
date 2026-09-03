"""词法检索索引（BM25）- Enterprise RAG Phase 2

提供与向量库同步的每用户 BM25 词法索引，支撑混合检索（BM25 + 语义 + RRF）：

- 自实现 Okapi BM25（无外部依赖，避免引入 rank_bm25 等新依赖）
- 分词同时支持中英文：英文单词/数字按词切分，中文按单字切分（中英混合语料最通用做法）
- 数据按 user_id 分区；倒排统计懒构建 + 脏标记增量重建
- 文档新增/删除/清空时同步更新，保证词法层与向量层一致

内存镜像量级为"当前用户的切块文本"，对个人/团队知识库（万级 chunk 内）完全可行。
"""
import math
import re
from collections import Counter, OrderedDict
from typing import Dict, List, Optional

from langchain.schema import Document

# 英文单词/数字串 + CJK 汉字单字（含扩展 A/B 区常用字）
_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")


def tokenize(text: str) -> List[str]:
    """中英文混合分词：单词小写，中文单字；丢弃单字符英文/数字噪音。"""
    tokens = [t.lower() for t in _TOKEN_RE.findall(text or "")]
    return [t for t in tokens if not (len(t) == 1 and t.isascii())]


class LexicalIndex:
    """每用户 BM25 词法索引。

    数据模型（按 user 分区）：
    - docs: {user_id: {chunk_id: _DocEntry}}
    - by_doc: {user_id: {doc_id: {chunk_id}}}  用于按文档整删
    - state: {user_id: {"loaded": bool, "dirty": bool}}
    - stats: {user_id: {"n", "avgdl", "dl", "postings"}}  懒构建缓存
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self._docs: Dict[str, "OrderedDict[str, Dict]"] = {}
        self._by_doc: Dict[str, Dict[str, set]] = {}
        self._state: Dict[str, Dict[str, bool]] = {}
        self._stats: Dict[str, Optional[Dict]] = {}

    # ------------------------------------------------------------------ #
    # 内部结构维护
    # ------------------------------------------------------------------ #

    def _ensure(self, user_id: str) -> None:
        if user_id not in self._docs:
            self._docs[user_id] = OrderedDict()
            self._by_doc[user_id] = {}
            self._state[user_id] = {"loaded": False, "dirty": True}
            self._stats[user_id] = None

    def _mark_dirty(self, user_id: str) -> None:
        if user_id in self._state:
            self._state[user_id]["dirty"] = True
            self._stats[user_id] = None

    # ------------------------------------------------------------------ #
    # 写操作（与向量层同步调用）
    # ------------------------------------------------------------------ #

    def add_document(
        self,
        user_id,
        doc_id,
        chunk_ids: List[str],
        chunks: List[Document],
    ) -> int:
        """新增/覆盖一个文档的全部切块（幂等：同 doc_id 先移除旧数据）。"""
        user = str(user_id)
        self._ensure(user)
        self.remove_document(user, doc_id)
        doc_key = str(doc_id)
        bucket = self._by_doc[user].setdefault(doc_key, set())
        for cid, chunk in zip(chunk_ids, chunks):
            cid = str(cid)
            self._docs[user][cid] = {
                "text": chunk.page_content,
                "doc_id": doc_key,
            }
            bucket.add(cid)
        self._state[user]["loaded"] = True
        self._mark_dirty(user)
        return len(chunk_ids)

    def add_all(self, user_id, chunk_ids: List[str], chunks: List[Document]) -> int:
        """批量写入（无 doc_id 参数时按 metadata['doc_id'] 分组，用于从向量库重建）。"""
        user = str(user_id)
        self._ensure(user)
        added = 0
        for cid, chunk in zip(chunk_ids, chunks):
            cid = str(cid)
            doc_key = str(chunk.metadata.get("doc_id") or "rebuild")
            self._docs[user][cid] = {
                "text": chunk.page_content,
                "doc_id": doc_key,
            }
            self._by_doc[user].setdefault(doc_key, set()).add(cid)
            added += 1
        self._state[user]["loaded"] = True
        self._mark_dirty(user)
        return added

    def remove_document(self, user_id, doc_id) -> int:
        user = str(user_id)
        if user not in self._docs:
            return 0
        removed = 0
        for cid in self._by_doc[user].pop(str(doc_id), set()):
            if cid in self._docs[user]:
                del self._docs[user][cid]
                removed += 1
        if removed:
            self._mark_dirty(user)
        return removed

    def clear_user(self, user_id) -> None:
        user = str(user_id)
        self._docs.pop(user, None)
        self._by_doc.pop(user, None)
        self._state.pop(user, None)
        self._stats.pop(user, None)

    def mark_loaded(self, user_id) -> None:
        user = str(user_id)
        self._ensure(user)
        self._state[user]["loaded"] = True

    # ------------------------------------------------------------------ #
    # 状态查询
    # ------------------------------------------------------------------ #

    def is_loaded(self, user_id) -> bool:
        return bool(self._state.get(str(user_id), {}).get("loaded"))

    def chunk_count(self, user_id) -> int:
        return len(self._docs.get(str(user_id), {}))

    # ------------------------------------------------------------------ #
    # 检索
    # ------------------------------------------------------------------ #

    def search(
        self,
        user_id,
        query: str,
        k: int = 10,
        doc_id_filter: Optional[str] = None,
    ) -> List[Document]:
        """BM25 检索，返回按相关度降序的 Document 列表（metadata 携带 chunk_id/doc_id）。"""
        user = str(user_id)
        docs = self._docs.get(user)
        if not docs:
            return []
        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        self._refresh_stats(user)
        stats = self._stats[user]
        n = stats["n"]
        avgdl = stats["avgdl"] or 1.0
        postings = stats["postings"]
        dl = stats["dl"]

        candidates: Dict[str, float] = {}
        seen_terms = set(query_tokens)
        for term in seen_terms:
            posting = postings.get(term)
            if not posting:
                continue
            # Okapi idf（平滑，避免 df 过高导致的负值）
            idf = math.log(1.0 + (n - len(posting) + 0.5) / (len(posting) + 0.5))
            for cid, tf in posting.items():
                if doc_id_filter is not None and docs[cid]["doc_id"] != str(doc_id_filter):
                    continue
                doc_len = dl.get(cid, 1)
                denom = tf + self.k1 * (1.0 - self.b + self.b * doc_len / avgdl)
                candidates[cid] = candidates.get(cid, 0.0) + idf * (
                    tf * (self.k1 + 1.0)
                ) / denom

        ranked = sorted(candidates, key=lambda cid: candidates[cid], reverse=True)[:k]
        return [self._to_document(user, cid) for cid in ranked]

    def _to_document(self, user: str, cid: str) -> Document:
        entry = self._docs[user][cid]
        return Document(
            page_content=entry["text"],
            metadata={"chunk_id": cid, "doc_id": entry["doc_id"]},
        )

    def _refresh_stats(self, user: str) -> None:
        """懒构建倒排（dirty 才重建）。"""
        if not self._state[user]["dirty"] and self._stats.get(user) is not None:
            return
        postings: Dict[str, Dict[str, int]] = {}
        dl: Dict[str, int] = {}
        n = 0
        total_len = 0
        for cid, entry in self._docs[user].items():
            toks = tokenize(entry["text"])
            n += 1
            doc_len = len(toks) or 1
            dl[cid] = doc_len
            total_len += doc_len
            counts = Counter(toks)
            for term, tf in counts.items():
                postings.setdefault(term, {})[cid] = tf
        self._stats[user] = {
            "n": n,
            "avgdl": (total_len / max(n, 1)),
            "dl": dl,
            "postings": postings,
        }
        self._state[user]["dirty"] = False
