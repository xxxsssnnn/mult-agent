"""记忆提取器 - 从对话消息中提取结构化记忆条目

提取结果字段（与 memory_entries.memory_type 枚举一致）:
- memory_type: fact / preference / procedural
- entity: 主体实体，用于冲突检测与覆盖（如 test_framework）
- content: 简洁、独立的自然语言陈述
- confidence: 0~1

两种模式:
- LLM 模式（配置 OPENAI_API_KEY）: 结构化 JSON 提取，质量最高
- 启发式模式（无 API Key / LLM 调用失败降级）: 关键词规则提取，保证管线可用
"""

import json
import re
from typing import List, Dict, Optional

import structlog
from langchain_openai import ChatOpenAI

from app.core.config import settings

logger = structlog.get_logger(__name__)

MEMORY_TYPES = ("fact", "preference", "procedural")

_EXTRACTION_PROMPT = """\
你是对话记忆提取引擎。从下面的对话中提取值得长期记住的信息。

只提取以下三类（用英文标识 memory_type）：
- fact: 客观事实（技术栈、环境配置、项目信息、用户身份等）
- preference: 用户偏好（工具偏好、代码风格、约束条件等）
- procedural: 流程知识（"如何做某事"的步骤或约定）

对每条输出 JSON 对象：
{"memory_type": "fact|preference|procedural", "entity": "该记忆的主体(用于冲突检测, 如 test_framework)", "content": "简洁、独立的陈述句", "confidence": 0到1的数值}

要求：
- 忽略寒暄、一次性指令、无信息量内容
- 同一信息只保留一条
- 无法提取时输出空数组 []

对话：
{conversation}

输出 JSON（不要输出其他文字）：
"""

# 启发式模式：偏好关键词（小写比较）
_HEURISTIC_PREFERENCES = (
    "偏好", "喜欢", "希望", "想要", "最好", "习惯", "倾向",
    "prefer", "like ", "favorite", "preferable",
)
# 启发式模式：事实类标记词（命中即视为客观陈述）
_HEURISTIC_FACT_MARKERS = (
    "项目", "技术栈", "数据库", "后端", "前端", "环境", "部署",
    "版本", "框架", "采用", "使用", "配置", "服务器", "仓库",
    "pytest", "redis", "postgresql", "docker", "python", "fastapi", "celery",
)


class MemoryExtractor:
    """从对话消息中提取结构化记忆条目"""

    def __init__(self):
        self.llm = None
        if settings.OPENAI_API_KEY:
            self.llm = ChatOpenAI(
                model=settings.OPENAI_MODEL or "gpt-3.5-turbo",
                temperature=0,
                openai_api_key=settings.OPENAI_API_KEY,
            )
            logger.info("Memory extractor initialized with LLM")
        else:
            logger.info("Memory extractor in heuristic mode (no OpenAI API key)")

    async def extract(self, messages: List[Dict]) -> List[Dict]:
        """从一批消息中提取记忆条目

        Args:
            messages: [{"role": "user", "content": "..."}, ...]

        Returns:
            条目列表 [{"memory_type", "entity", "content", "confidence"}, ...]
        """
        if not messages:
            return []
        if self.llm is None:
            return self._extract_heuristic(messages)
        try:
            conversation = "\n".join(
                f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages
            )
            response = await self.llm.ainvoke(
                _EXTRACTION_PROMPT.format(conversation=conversation)
            )
            entries = self._parse_response(response.content)
            logger.info(
                "memory.extracted",
                source_messages=len(messages),
                extracted=len(entries),
            )
            return entries
        except Exception as exc:
            logger.warning("memory.extract.llm_failed", error=str(exc))
            return self._extract_heuristic(messages)

    @staticmethod
    def _parse_response(raw: Optional[str]) -> List[Dict]:
        """解析 LLM 输出的 JSON 数组，容错 ```json 包裹与前后缀文本"""
        if not raw:
            return []
        text = raw.strip()
        # 去掉 markdown 代码块包裹
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\[[\s\S]*\]", text)
            if not match:
                return []
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                return []
        if not isinstance(data, list):
            return []
        entries = []
        for item in data:
            if not isinstance(item, dict):
                continue
            memory_type = str(item.get("memory_type", "")).strip().lower()
            if memory_type not in MEMORY_TYPES:
                continue
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            try:
                confidence = float(item.get("confidence", 0.6))
            except (TypeError, ValueError):
                confidence = 0.6
            entity = str(item.get("entity", "")).strip() or None
            entries.append({
                "memory_type": memory_type,
                "entity": entity,
                "content": content[:500],
                "confidence": max(0.0, min(1.0, confidence)),
            })
        return entries

    @staticmethod
    def _extract_heuristic(messages: List[Dict]) -> List[Dict]:
        """无 LLM 时的启发式提取（保证管线在本地/降级环境可用）"""
        entries = []
        for msg in messages:
            if msg.get("role") != "user":
                continue
            content = (msg.get("content") or "").strip()
            if len(content) < 6:
                continue
            lowered = content.lower()
            if any(k in lowered for k in _HEURISTIC_PREFERENCES):
                entries.append({
                    "memory_type": "preference",
                    "entity": MemoryExtractor._guess_entity(content),
                    "content": content[:200],
                    "confidence": 0.6,
                })
            elif any(marker in lowered for marker in _HEURISTIC_FACT_MARKERS):
                entries.append({
                    "memory_type": "fact",
                    "entity": MemoryExtractor._guess_entity(content),
                    "content": content[:200],
                    "confidence": 0.5,
                })
        return entries

    @staticmethod
    def _guess_entity(content: str) -> Optional[str]:
        """启发式猜测记忆主体：优先匹配已知主体词，否则取首个词"""
        lowered = content.lower()
        for token in (
            "test_framework", "pytest", "数据库", "后端", "前端", "部署",
            "环境", "项目", "框架", "技术栈", "redis", "postgresql",
            "docker", "python", "fastapi", "celery",
        ):
            if token in lowered:
                return token
        parts = re.split(r"[\s,，。.!！:：;；]+", content.strip())
        for part in parts:
            if len(part) >= 2:
                return part[:20]
        return None
