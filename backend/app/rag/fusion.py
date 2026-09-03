"""检索结果融合工具（Enterprise RAG Phase 2）"""

from typing import Dict, List, Tuple


def reciprocal_rank_fusion(
    ranked_id_lists: List[List[str]],
    rrf_k: int = 60,
) -> List[Tuple[str, float]]:
    """Reciprocal Rank Fusion：对多路（按相关性排序的）chunk_id 列表做融合。

    Args:
        ranked_id_lists: 多路检索结果，每路是按相关性从高到低排列的 id 列表。
        rrf_k: RRF 常数（越大越平滑，经典取值 60）。

    Returns:
        按融合分从高到低排列的 [(chunk_id, score), ...]。
    """
    scores: Dict[str, float] = {}
    for ranked in ranked_id_lists:
        for rank, chunk_id in enumerate(ranked):
            if not chunk_id:
                continue
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (rrf_k + rank + 1)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)
