"""RAG 语义缓存（Enterprise RAG Phase 2）

按用户作用域缓存 查询→答案，核心价值是省去重复的 LLM 生成与向量检索成本。

设计原则：
- 每用户独立缓存桶（租户隔离：命中/失效都以 user_id 为界）
- 失效策略双保险：知识库变更事件失效（写入/删除/清空时调用 invalidate_user）
  + 可选 TTL 兜底（防止事件失效遗漏导致陈旧答案）
- 进程内 LRU，容量受限（每用户上限），确定性裁剪
- enabled=False 时完全旁路（零开销）

为内存实现；后端接口可后续扩展 Redis/Memcached。
"""
import hashlib
import time
from collections import OrderedDict
from copy import deepcopy
from typing import Any, Dict, Optional, Tuple


class SemanticCache:
    def __init__(
        self,
        enabled: bool = True,
        ttl_seconds: int = 3600,
        max_entries_per_user: int = 200,
    ):
        self.enabled = enabled
        self.ttl_seconds = ttl_seconds
        self.max_entries_per_user = max_entries_per_user
        # {user_id(str): OrderedDict[key -> (timestamp, payload)]}
        self._buckets: Dict[str, "OrderedDict[str, Tuple[float, Any]]"] = {}
        self.hits = 0
        self.misses = 0

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #

    def clear(self) -> None:
        self._buckets.clear()
        self.hits = 0
        self.misses = 0

    def stats(self) -> Dict[str, int]:
        total = self.hits + self.misses
        return {
            "enabled": int(self.enabled),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 4) if total else 0.0,
            "users": len(self._buckets),
            "entries": sum(len(b) for b in self._buckets.values()),
        }

    # ------------------------------------------------------------------ #
    # Key
    # ------------------------------------------------------------------ #

    @staticmethod
    def make_key(user_id, query: str, k: int, search_type: str) -> str:
        raw = f"{user_id}|{search_type}|{k}|{query.strip()}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------ #
    # 读写
    # ------------------------------------------------------------------ #

    def get(self, user_id, key: str) -> Optional[Any]:
        if not self.enabled:
            return None
        bucket = self._bucket(user_id)
        item = bucket.get(key)
        if item is None:
            self.misses += 1
            return None
        timestamp, payload = item
        if self.ttl_seconds > 0 and (time.monotonic() - timestamp) > self.ttl_seconds:
            del bucket[key]
            self.misses += 1
            return None
        bucket.move_to_end(key)  # LRU 更新
        self.hits += 1
        return deepcopy(payload)

    def put(self, user_id, key: str, payload: Any) -> None:
        if not self.enabled:
            return
        bucket = self._bucket(user_id)
        if key in bucket:
            del bucket[key]
        bucket[key] = (time.monotonic(), payload)
        while len(bucket) > self.max_entries_per_user:
            bucket.popitem(last=False)

    def invalidate_user(self, user_id) -> None:
        """知识库变更（写入/删除/清空）后使该用户全部缓存失效。"""
        if not self.enabled:
            return
        self._buckets.pop(str(user_id), None)

    def _bucket(self, user_id) -> "OrderedDict[str, Tuple[float, Any]]":
        key = str(user_id)
        if key not in self._buckets:
            self._buckets[key] = OrderedDict()
        return self._buckets[key]
