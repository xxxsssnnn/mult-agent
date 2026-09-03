"""查询转换器 - LLM 多查询扩展（Enterprise RAG Phase 4）

检索前把用户问题改写成多个检索变体（多查询扩展，multi-query expansion）：

- 单条查询只能表达一个角度；改写出的每个变体聚焦原问题的一个侧面
  （关键词 / 同义表述 / 需要召回的特定子主题），各自召回后再做 RRF 融合，
  可以覆盖单次检索漏掉的片段
- 输出列表恒以原文开头（变体 0），保证基线召回不劣化
- 未启用 / 未配置 LLM / 查询过短 / LLM 失败 → 一律原样返回 [query]：
  链路不中断、零额外成本、行为与旧版完全一致
"""

import logging
import re
from typing import List, Optional

from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是企业知识库检索的查询改写专家。
你的任务是把用户提问改写成多条更适合文档检索的查询，帮助检索系统从不同角度召回相关片段。"""


def parse_query_variants(text: str, limit: int = 5) -> List[str]:
    """从 LLM 输出解析查询变体行。

    清洗：项目符号 / 数字编号 / 包裹引号 / 空行；按规范化文本去重（保序）。
    Returns:
        清洗后的非空变体列表（最多 limit 条）
    """
    if not text:
        return []
    seen = set()
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # 去掉行首项目符号与编号（- * • · 1. 1、 1) [1] 等）
        line = re.sub(r"^[\s\-*•·‣]+", "", line)
        line = re.sub(r"^\d+[\.\、\)）]\s*", "", line)
        line = re.sub(r"^\[\d+\]\s*", "", line)
        cleaned = line.strip().strip("\"'“”`")
        if not cleaned:
            continue
        norm = " ".join(cleaned.split()).casefold()
        if norm in seen:
            continue
        seen.add(norm)
        out.append(cleaned)
        if len(out) >= limit:
            break
    return out


class LLMQueryTransformer:
    """LLM 多查询扩展：一次调用生成若干检索变体（含原文）。"""

    def __init__(
        self,
        llm=None,
        *,
        enabled: bool = True,
        num_variants: int = 3,
        min_query_len: int = 8,
    ):
        self.llm = llm
        self.enabled = enabled
        self.num_variants = max(1, int(num_variants))
        self.min_query_len = max(0, int(min_query_len))
        # 观测字段：最近一次转换使用的变体 / 原始 LLM 输出
        self.last_variants: Optional[List[str]] = None
        self.last_raw: Optional[str] = None

    @property
    def active(self) -> bool:
        """是否真正参与链路：开关打开 + 已装配 LLM + 需要扩展（>1 个变体）"""
        return bool(self.enabled and self.llm is not None and self.num_variants > 1)

    async def transform(self, query: str) -> List[str]:
        """把查询改写为检索变体列表。

        Returns:
            至少包含原文的变体列表（未启用时即 [query]）
        """
        query = (query or "").strip()
        self.last_variants = [query]
        self.last_raw = None
        if not query or not self.active or len(query) < self.min_query_len:
            return [query]

        extra = self.num_variants - 1
        user_prompt = (
            f"请把下面的用户问题改写成 {extra} 条更适合文档检索的查询变体。\n\n"
            "改写原则：\n"
            "- 每条变体聚焦原问题的一个子主题或角度（关键词 / 同义表述 / 需要召回的具体侧面）\n"
            "- 保留关键实体与领域词，去掉口语化表述\n"
            "- 每条变体是完整、可独立检索的查询\n\n"
            f"用户问题：\n{query}\n\n"
            f"请逐行输出 {extra} 条改写后的查询变体，每行一条，不要编号、不要解释。"
        )
        try:
            response = await self.llm.ainvoke(
                [
                    SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(content=user_prompt),
                ]
            )
            raw = response.content if hasattr(response, "content") else str(response)
        except Exception as e:  # noqa: BLE001 - 转换失败绝不能中断检索链路
            logger.warning(
                "Query transform failed, falling back to original query: %s",
                str(e),
            )
            return [query]

        self.last_raw = str(raw)
        norm_query = " ".join(query.split()).casefold()
        generated = []
        for variant in parse_query_variants(str(raw), limit=extra + 2):
            if " ".join(variant.split()).casefold() == norm_query:
                continue  # 与原文重复的无价值变体
            generated.append(variant)
            if len(generated) >= extra:
                break

        variants = [query] + generated
        self.last_variants = variants
        logger.info(
            "Query transformed (multi-query expansion)",
            original=query,
            variants=variants,
        )
        return variants
