"""RAG 语义缓存（Enterprise RAG Phase 2 → Phase 6 升级）

按用户作用域缓存 查询→答案，核心价值是省去重复的 LLM 生成与向量检索成本。

缓存查询分两级：
1. **精确命中**：查询文本与 key（含管道差异标签）完全一致 → 直接命中，零额外开销；
2. **语义命中**：精确未命中时，将当前查询嵌入后与**同用户、同管道 profile** 的
   缓存条目做余弦比较，相似度 ≥ 阈值即复用答案——改述/近似问法也能命中，
   从而把缓存从"逐字缓存"升级为"语义缓存"。

设计原则：
- 每用户独立缓存桶（租户隔离：命中/失效都以 user_id 为界）
- 语义比较限定同一 pipeline profile（search_type|k|管道标签），不同管道产出
  不同答案，不能互相复用
- 失效策略双保险：知识库变更事件失效（写入/删除/清空时调用 invalidate_user）
  + 可选 TTL 兜底（防止事件失效遗漏导致陈旧答案）
- 进程内 LRU，容量受限（每用户上限），确定性裁剪
- enabled=False 时完全旁路（零开销）；语义层不可用（无嵌入器/嵌入失败）时
  自动退化为纯精确缓存，不影响主流程

为内存实现；后端接口可后续扩展 Redis/Memcached。
"""
import hashlib
import time
from collections import OrderedDict
from copy import deepcopy
from typing import Any, Callable, Dict, Optional, Tuple


def _cosine(a, b):
    """余弦相似度。零向量或长度不一致返回 0。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class SemanticCache:
    def __init__(
        self,
        enabled: bool = True,
        ttl_seconds: int = 3600,
        max_entries_per_user: int = 200,
        semantic_enabled: bool = True,
        semantic_threshold: float = 0.90,
        semantic_min_query_len: int = 6,
    ):
        self.enabled = enabled
        self.ttl_seconds = ttl_seconds
        self.max_entries_per_user = max_entries_per_user
        # 语义命中层开关与参数（无嵌入器/嵌入失败时自动退化为纯精确缓存）
        self.semantic_enabled = semantic_enabled
        self.semantic_threshold = float(semantic_threshold)
        self.semantic_min_query_len = int(semantic_min_query_len)

        # {user_id(str): OrderedDict[key -> {ts, payload, query, profile, embedding}]}
        self._buckets: Dict[str, "OrderedDict[str, Dict[str, Any]]"] = {}
        # 统计：hits = 精确 + 语义命中；misses = 请求级未命中（每请求只记一次）
        self.hits = 0
        self.misses = 0
        self.exact_hits = 0
        self.semantic_hits = 0
        self.semantic_attempts = 0
        self.near_misses = 0

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #

    def clear(self) -> None:
        self._buckets.clear()
        self.hits = 0
        self.misses = 0
        self.exact_hits = 0
        self.semantic_hits = 0
        self.semantic_attempts = 0
        self.near_misses = 0

    def stats(self) -> Dict[str, Any]:
        total = self.hits + self.misses
        return {
            "enabled": int(self.enabled),
            "semantic_enabled": int(self.semantic_enabled),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 4) if total else 0.0,
            "exact_hits": self.exact_hits,
            "semantic_hits": self.semantic_hits,
            "semantic_attempts": self.semantic_attempts,
            "near_misses": self.near_misses,
            "users": len(self._buckets),
            "entries": sum(len(b) for b in self._buckets.values()),
        }

    # ------------------------------------------------------------------ #
    # Key / Profile
    # ------------------------------------------------------------------ #

    @staticmethod
    def make_key(
        user_id,
        query: str,
        k: int,
        search_type: str,
        extra: str = "",
    ) -> str:
        """缓存键。extra 用于区分影响答案内容的管道差异（如是否经重排）。"""
        raw = f"{user_id}|{search_type}|{k}|{extra}|{query.strip()}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def profile(k: int, search_type: str, extra: str = "") -> str:
        """管道指纹：与 make_key 中 user_id 之外的前缀保持一致。

        语义比较只允许发生在同一 profile 内（不同 search_type/k/管道标签
        会产生不同答案，不能互相复用）。
        """
        return f"{search_type}|{k}|{extra}"

    # ------------------------------------------------------------------ #
    # 读写
    # ------------------------------------------------------------------ #

    def _valid(self, entry: Dict[str, Any]) -> bool:
        if self.ttl_seconds <= 0:
            return True
        return (time.monotonic() - entry["ts"]) <= self.ttl_seconds

    def get(self, user_id, key: str) -> Optional[Any]:
        """精确读取（语义层之前的原始契约；仍受 TTL / 统计约束）。"""
        if not self.enabled:
            return None
        bucket = self._bucket(user_id)
        entry = bucket.get(key)
        if entry is None:
            self.misses += 1
            return None
        if not self._valid(entry):
            del bucket[key]
            self.misses += 1
            return None
        bucket.move_to_end(key)  # LRU 更新
        self.hits += 1
        self.exact_hits += 1
        return deepcopy(entry["payload"])

    async def lookup(
        self,
        user_id,
        key: str,
        query: str = "",
        profile: str = "",
        embedder: Optional[Callable] = None,
        threshold: Optional[float] = None,
    ) -> Tuple[Optional[Any], Optional[Dict[str, Any]]]:
        """请求级语义查找：先精确、后同 profile 语义近邻。

        Args:
            embedder: 可选 async callable(text)->List[float]，仅在精确未命中时懒调用，
                      避免对精确命中请求产生任何嵌入开销。
            threshold: 本次查找的相似度阈值（默认用 self.semantic_threshold）。

        Returns:
            (payload, match)：
            - 命中：payload 深拷贝 + match{kind:"exact"|"semantic", key, score, ...}
            - 未命中：(None, match{kind:"miss", reason})。reason 取值：
              disabled / semantic_disabled / no_embedding / query_too_short /
              embed_error / embed_empty / no_candidates / below_threshold。
              match 内部附带本次查询向量 "embedding"，供回填缓存复用，避免二次嵌入。

        命中/未命中每请求只计一次统计（hits/misses），不随扫描过程叠加。
        """
        if not self.enabled:
            return None, {"kind": "miss", "reason": "disabled"}

        bucket = self._bucket(user_id)
        entry = bucket.get(key)
        if entry is not None:
            if not self._valid(entry):
                del bucket[key]
            else:
                bucket.move_to_end(key)  # LRU 更新
                self.hits += 1
                self.exact_hits += 1
                return (
                    deepcopy(entry["payload"]),
                    {"kind": "exact", "key": key, "query": query, "score": 1.0},
                )

        # ---- 精确未命中 → 语义通道（尽力而为，任何不可用都静默退化） ----
        if not self.semantic_enabled:
            self.misses += 1
            return None, {"kind": "miss", "reason": "semantic_disabled"}
        if embedder is None:
            self.misses += 1
            return None, {"kind": "miss", "reason": "no_embedding"}
        if not query or len(query) < self.semantic_min_query_len:
            self.misses += 1
            return None, {"kind": "miss", "reason": "query_too_short"}
        try:
            qvec = await embedder(query)
        except Exception:  # noqa: BLE001 —— 语义层可选，失败不应阻断主流程
            self.misses += 1
            return None, {"kind": "miss", "reason": "embed_error"}
        if not qvec:
            self.misses += 1
            return None, {"kind": "miss", "reason": "embed_empty"}

        self.semantic_attempts += 1
        thr = self.semantic_threshold if threshold is None else float(threshold)
        best_key = None
        best_score = -1.0
        best_payload = None
        best_query = ""
        for cand_key, cand in list(bucket.items()):
            if not self._valid(cand):  # 清扫过期条目
                if bucket.get(cand_key) is cand:
                    del bucket[cand_key]
                continue
            if cand.get("profile") != profile:
                continue
            cand_emb = cand.get("embedding")
            if not cand_emb or len(cand_emb) != len(qvec):
                continue
            sim = _cosine(qvec, cand_emb)
            if sim > best_score:
                best_score = sim
                best_key = cand_key
                best_payload = cand["payload"]
                best_query = cand.get("query") or ""

        if best_key is not None and best_score >= thr:
            bucket.move_to_end(best_key)  # LRU 更新
            self.hits += 1
            self.semantic_hits += 1
            return (
                deepcopy(best_payload),
                {
                    "kind": "semantic",
                    "key": best_key,
                    "query": best_query,
                    "score": round(best_score, 4),
                },
            )
        if best_key is not None:
            self.near_misses += 1
        self.misses += 1
        return (
            None,
            {
                "kind": "miss",
                "reason": "below_threshold" if best_key is not None else "no_candidates",
                "score": round(best_score, 4) if best_key is not None else None,
                "embedding": qvec,  # 供调用方回填缓存复用，避免二次嵌入
            },
        )

    def store(
        self,
        user_id,
        key: str,
        payload: Any,
        query: Optional[str] = None,
        profile: Optional[str] = None,
        embedding: Optional[list] = None,
    ) -> None:
        """写入缓存条目。带 query/embedding 的条目可参与后续语义命中。"""
        if not self.enabled:
            return
        bucket = self._bucket(user_id)
        bucket.pop(key, None)
        bucket[key] = {
            "ts": time.monotonic(),
            "payload": payload,
            "query": query,
            "profile": profile,
            "embedding": embedding,
        }
        while len(bucket) > self.max_entries_per_user:
            bucket.popitem(last=False)

    def put(self, user_id, key: str, payload: Any) -> None:
        """精确缓存写入（向后兼容的原始契约；等价于不带元数据的 store）。"""
        self.store(user_id, key, payload)

    def invalidate_user(self, user_id) -> None:
        """知识库变更（写入/删除/清空）后使该用户全部缓存失效。"""
        if not self.enabled:
            return
        self._buckets.pop(str(user_id), None)

    def _bucket(self, user_id) -> "OrderedDict[str, Dict[str, Any]]":
        key = str(user_id)
        if key not in self._buckets:
            self._buckets[key] = OrderedDict()
        return self._buckets[key]
