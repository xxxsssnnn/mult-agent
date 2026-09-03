"""RAG 评估器 - RAGAS 集成（Enterprise RAG Phase 5，可选依赖）

对 RAG 链路产出（问题/答案/上下文/参考答案）计算 RAGAS 指标：

    faithfulness        忠实度   答案的断言被检索上下文支撑的比例        (LLM)
    answer_relevancy    答案相关性 答案与问题的相关程度（含向量打分）     (LLM+Embedding)
    context_precision   上下文精确率 检索结果中"相关片段"是否排在前面      (LLM)
    context_recall      上下文召回率 参考答案的要点有多少被检索上下文覆盖   (Embedding)

设计要点：
- ragas 为**可选依赖**：本模块顶部零依赖，仅在真正 evaluate 时惰性导入；
  未安装时抛出带安装指引的 RAGEvaluationError，不拖垮平台主链路
- 兼容 ragas 两代 API：
  - legacy（0.1.x，HF datasets.Dataset + metrics.base.set_llm/set_embeddings）
  - v2（0.2.x，EvaluationDataset/SingleTurnSample，evaluate 参数传 llm/embeddings）
  通过能力探测自动选择，无需用户改代码
- 产出结构化 report：指标均值 + 逐问题明细（浮点 NaN 归一为 None）
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ragas 未安装 / 版本不支持时的安装指引
_INSTALL_HINT = (
    "RAGAS is an optional evaluation dependency and is not installed. "
    "Install it with: pip install -r backend/requirements-eval.txt "
    "(or: pip install \"ragas>=0.1.10\"). "
    "ragas also needs an LLM (and embeddings for some metrics); it reads "
    "OPENAI_API_KEY from the environment by default."
)

METRIC_DESCRIPTIONS: Dict[str, str] = {
    "faithfulness": "忠实度：答案断言被检索上下文支撑的比例",
    "answer_relevancy": "答案相关性：答案与问题的相关程度",
    "context_precision": "上下文精确率：相关片段是否排在检索结果前列",
    "context_recall": "上下文召回率：参考答案要点被检索上下文覆盖的比例",
}
DEFAULT_METRICS: List[str] = list(METRIC_DESCRIPTIONS.keys())


class RAGEvaluationError(RuntimeError):
    """评估配置或执行错误（如 ragas 未安装、样本非法）"""


def normalize_samples(samples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把评估样本归一化为统一行结构，缺字段给出明确报错。

    每个样本须含：question / answer / contexts / ground_truth
    容错别名：ground_truths；contexts 与 ground_truth 允许传入单个字符串。
    """
    if not samples:
        raise RAGEvaluationError("samples must not be empty")
    if not isinstance(samples, list):
        raise RAGEvaluationError("samples must be a list of dicts")

    rows: List[Dict[str, Any]] = []
    for idx, raw in enumerate(samples):
        if not isinstance(raw, dict):
            raise RAGEvaluationError(f"sample[{idx}] must be a dict")

        question = str(raw.get("question") or "").strip()
        if not question:
            raise RAGEvaluationError(
                f"sample[{idx}] is missing a non-empty 'question'"
            )
        answer = raw.get("answer")
        if answer is None:
            raise RAGEvaluationError(f"sample[{idx}] is missing 'answer'")

        contexts = raw.get("contexts")
        if contexts is None:
            raise RAGEvaluationError(
                f"sample[{idx}] is missing 'contexts' (retrieved document texts)"
            )
        if isinstance(contexts, str):
            contexts = [contexts]
        contexts = [str(c) for c in contexts if str(c).strip()]

        ground_truth = raw.get("ground_truth", raw.get("ground_truths"))
        if ground_truth is None:
            ground_truth = []
        elif isinstance(ground_truth, str):
            ground_truth = [ground_truth]
        ground_truth = [str(g) for g in ground_truth if str(g).strip()]

        rows.append(
            {
                "question": question,
                "answer": str(answer),
                "contexts": contexts,
                "ground_truth": ground_truth,
            }
        )
    return rows


def validate_metrics(names: List[str]) -> List[str]:
    """校验指标名，返回去重后的顺序列表；未知指标报错并列出支持项"""
    if not names:
        raise RAGEvaluationError("metrics must not be empty")
    known = set(METRIC_DESCRIPTIONS)
    unknown = [n for n in names if n not in known]
    if unknown:
        raise RAGEvaluationError(
            "unsupported metrics: {unknown}; supported: {supported}".format(
                unknown=", ".join(unknown), supported=", ".join(sorted(known))
            )
        )
    seen: List[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return seen


def _is_nan(value: Any) -> bool:
    try:
        return value != value  # NaN != NaN
    except Exception:  # noqa: BLE001
        return True


def _as_float_or_none(value: Any) -> Optional[float]:
    try:
        fv = float(value)
    except (TypeError, ValueError):
        return None
    return None if _is_nan(fv) else fv


def build_report(
    rows: List[Dict[str, Any]], metric_names: List[str], frame: Any
) -> Dict[str, Any]:
    """把 ragas 结果帧聚合为平台统一 report 结构。

    frame 仅需鸭子类型：to_dict(orient="records") 返回逐行 dict。
    真实 ragas 的 pandas DataFrame 与测试替身均满足该接口。
    """
    try:
        records = frame.to_dict(orient="records")
    except Exception as e:  # noqa: BLE001
        raise RAGEvaluationError(
            f"failed to read RAGAS result frame: {e}"
        ) from e

    if len(records) != len(rows):
        logger.warning(
            "RAGAS result rows (%d) != input samples (%d)",
            len(records),
            len(rows),
        )

    per_question: List[Dict[str, Any]] = []
    for i, row in enumerate(rows):
        rec = records[i] if i < len(records) else {}
        item: Dict[str, Any] = {"question": row["question"]}
        for name in metric_names:
            item[name] = _as_float_or_none(rec.get(name))
        per_question.append(item)

    summary: Dict[str, float] = {}
    for name in metric_names:
        values = [item[name] for item in per_question if item[name] is not None]
        summary[name] = round(sum(values) / len(values), 4) if values else None

    return {
        "metrics": metric_names,
        "samples": len(rows),
        "summary": summary,
        "per_question": per_question,
    }


# --------------------------------------------------------------------------- #
# ragas 双代 API 适配
# --------------------------------------------------------------------------- #


def _wrap_langchain_llm(llm: Any) -> Any:
    """尽力把 langchain 聊天模型包成 ragas LLM；失败时原样透传交由 ragas 校验"""
    if llm is None:
        return None
    try:
        from ragas.llms import LangchainLLMWrapper

        return LangchainLLMWrapper(llm)
    except Exception:  # noqa: BLE001 - ragas 版本差异，透传
        return llm


def _metric_objects(names: List[str], ragas_metrics: Any) -> List[Any]:
    objects = []
    for name in names:
        obj = getattr(ragas_metrics, name, None)
        if obj is None:
            raise RAGEvaluationError(
                f"RAGAS version does not expose metric '{name}'"
            )
        objects.append(obj)
    return objects


def _detect_backend() -> str:
    """探测 ragas 版本形态：'legacy'（0.1.x）/ 'v2'（0.2.x）"""
    try:
        import ragas  # noqa: F401
    except ImportError as e:
        raise RAGEvaluationError(_INSTALL_HINT) from e

    try:
        from ragas.metrics.base import set_llm  # noqa: F401

        return "legacy"
    except ImportError:
        pass

    if hasattr(ragas, "EvaluationDataset") and hasattr(ragas, "SingleTurnSample"):
        return "v2"

    raise RAGEvaluationError(
        "Unsupported RAGAS version. Install a supported one with: "
        "pip install -r backend/requirements-eval.txt"
    )


class RAGEvaluator:
    """RAGAS 指标评估器（惰性加载 ragas，离线环境仅配置不报错）"""

    def __init__(
        self,
        metrics: Optional[List[str]] = None,
        llm: Any = None,
        embeddings: Any = None,
    ):
        self.metric_names = validate_metrics(metrics or DEFAULT_METRICS)
        self.llm = llm
        self.embeddings = embeddings

    # -- 0.1.x：HF Dataset + 全局 set_llm/set_embeddings ---------------------- #
    def _evaluate_legacy(
        self, rows: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        try:
            import ragas
            import datasets
        except ImportError as e:  # noqa: BLE001
            raise RAGEvaluationError(_INSTALL_HINT) from e
        from ragas.metrics.base import set_embeddings, set_llm

        dataset = datasets.Dataset.from_list(rows)
        if self.llm is not None:
            set_llm(_wrap_langchain_llm(self.llm))
        if self.embeddings is not None:
            set_embeddings(self.embeddings)

        result = ragas.evaluate(
            dataset,
            metrics=_metric_objects(self.metric_names, ragas.metrics),
        )
        return build_report(rows, self.metric_names, result.to_pandas())

    # -- 0.2.x：EvaluationDataset / SingleTurnSample -------------------------- #
    def _evaluate_v2(self, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        try:
            import ragas
        except ImportError as e:  # noqa: BLE001
            raise RAGEvaluationError(_INSTALL_HINT) from e
        from ragas import EvaluationDataset, SingleTurnSample

        samples = [
            SingleTurnSample(
                user_input=row["question"],
                response=row["answer"],
                retrieved_contexts=row["contexts"],
                reference=row["ground_truth"],
            )
            for row in rows
        ]
        kwargs: Dict[str, Any] = {}
        if self.llm is not None:
            kwargs["llm"] = _wrap_langchain_llm(self.llm)
        if self.embeddings is not None:
            kwargs["embeddings"] = self.embeddings

        result = ragas.evaluate(
            EvaluationDataset(samples=samples),
            metrics=_metric_objects(self.metric_names, ragas.metrics),
            **kwargs,
        )
        return build_report(rows, self.metric_names, result.to_pandas())

    # -- 对外入口 ------------------------------------------------------------- #
    def evaluate(self, samples: List[Dict[str, Any]]) -> Dict[str, Any]:
        """对样本执行 RAGAS 评估，返回平台统一 report 结构。

        样本字段：question / answer / contexts(检索到的全文列表) / ground_truth
        """
        rows = normalize_samples(samples)
        backend = _detect_backend()
        logger.info("Running RAGAS evaluation", samples=len(rows), backend=backend, metrics=self.metric_names)
        if backend == "legacy":
            report = self._evaluate_legacy(rows)
        else:
            report = self._evaluate_v2(rows)

        report["backend"] = backend
        report["metrics_descriptions"] = {
            name: METRIC_DESCRIPTIONS[name] for name in self.metric_names
        }
        return report
