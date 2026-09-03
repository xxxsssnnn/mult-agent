"""两阶段重排器（Enterprise RAG Phase 3）

第一段（召回）由检索器完成：hybrid/similarity 等按放大后的候选数召回；
本模块是第二段：用 LLM 对每个候选做点级（pointwise）相关性打分（0.0~1.0），
按分数降序重排并截断到最终 top-k。

设计取舍：
- 零新依赖：复用已装配的 OpenAI 聊天模型（ChatOpenAI），无 cross-encoder 重量级模型下载
- 单次批量打分（一次 LLM 调用完成全部候选），避免 N 次串行调用
- 打分结果解析失败 / 未配置 LLM / 未启用时一律降级返回原序，保证检索链路永不中断
"""

import logging
import re
from typing import List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain.schema import Document

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是文档检索相关性重排器。

给定用户问题与若干候选文档片段，请为每个片段独立判断它与用户问题的相关性。
评分依据：片段本身是否直接回答、或包含回答用户问题所需的关键信息。
不要因片段之间内容重复而互相扣分；不要脑补片段之外的信息。"""


_FLOAT_LINE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)")


def parse_score_list(text: str, expected: int) -> Optional[List[float]]:
    """从 LLM 输出解析逐行分数（第 i 行对应第 i 个候选）。

    严格解析：去掉空行后必须恰好 `expected` 行、且每行都必须是纯数字，
    否则返回 None（调用方降级为原序）。
    不采用"全文抓数字"是因为候选正文里也可能出现数字，抓取会错位——
    宁可安全降级，也不静默错序。
    """
    if not text:
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) != expected:
        return None
    scores = []
    for line in lines:
        if not _FLOAT_LINE.fullmatch(line):
            return None
        scores.append(max(0.0, min(1.0, float(line))))
    return scores


class LLMReranker:
    """LLM 点级重排器（批量打分，稳定排序，失败降级原序）"""

    def __init__(
        self,
        llm=None,
        *,
        enabled: bool = True,
        max_doc_chars: int = 600,
    ):
        self.llm = llm
        self.enabled = enabled
        self.max_doc_chars = max_doc_chars
        # 最近一次打分的分数列表（与重排后输出一一对应；失败/未启用为 None）
        self.last_scores: Optional[List[float]] = None

    @property
    def active(self) -> bool:
        """是否真正参与链路：开关打开且已装配 LLM"""
        return bool(self.enabled and self.llm is not None)

    def _slice(self, documents: List[Document], k: Optional[int]) -> List[Document]:
        return documents[:k] if k is not None else documents

    async def rerank(
        self,
        query: str,
        documents: List[Document],
        k: Optional[int] = None,
    ) -> List[Document]:
        """对候选做点级打分并按分数重排，返回前 k（未启用时原序返回）。

        Returns:
            重排后的文档列表（长度 <= k；候选不足则全部返回）
        """
        self.last_scores = None
        n = len(documents)
        if n == 0 or not self.active:
            return self._slice(documents, k)

        candidates = []
        for i, doc in enumerate(documents, 1):
            text = (doc.page_content or "").strip()
            if len(text) > self.max_doc_chars:
                text = text[: self.max_doc_chars] + "..."
            candidates.append(f"{i}. {text}")

        prompt = f"""用户问题：{query}

候选文档片段（每个一行）：
{chr(10).join(candidates)}

请为每个候选片段输出一个 0.0 到 1.0 之间的相关性分数。
每行一个数字，第 i 行对应上面第 i 个候选片段，顺序必须一致。
只输出数字，不要输出任何解释、编号或标点符号。"""

        try:
            response = await self.llm.ainvoke(
                [
                    SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(content=prompt),
                ]
            )
            raw = response.content if hasattr(response, "content") else str(response)
        except Exception as e:  # noqa: BLE001 - 重排失败绝不能中断检索链路
            logger.warning(
                "LLM rerank failed, falling back to original order: %s", str(e)
            )
            return self._slice(documents, k)

        scores = parse_score_list(raw, n)
        if scores is None:
            logger.info(
                "LLM rerank scores unparseable, falling back to original order "
                "(candidates=%d, raw_head=%r)",
                n,
                str(raw)[:80],
            )
            return self._slice(documents, k)

        # 稳定排序：分数降序，同分保持原顺序；截断后 last_scores 与输出一一对应
        ranked = sorted(
            ((score, i, doc) for i, (score, doc) in enumerate(zip(scores, documents))),
            key=lambda item: (-item[0], item[1]),
        )
        limit = k if k is not None else n
        chosen = ranked[:limit]
        self.last_scores = [item[0] for item in chosen]
        return [item[2] for item in chosen]
