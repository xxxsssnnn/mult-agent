"""RAG RAGAS 评估器回归测试（Enterprise Phase 5）

ragas 是可选依赖、且评估需要真实 LLM —— 因此本套件**不安装 ragas**，
而是把假 ragas/datasets 模块注入 sys.modules，纯离线验证我方编排逻辑：

- 样本归一化（缺字段/别名/字符串→列表）
- 指标名校验与默认集合
- legacy(0.1.x) / v2(0.2.x) 两代 ragas 适配与结果聚合
- LLM/Embedding 的装配时机（提供与否）
- ragas 未安装 → 带安装指引的明确报错
- NaN/缺失值归一为 None，均值只统计有效值

通过 `python tests/test_rag_eval.py` 直接运行。
"""
import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.rag.evaluator import (  # noqa: E402
    DEFAULT_METRICS,
    METRIC_DESCRIPTIONS,
    RAGEvaluationError,
    RAGEvaluator,
    build_report,
    normalize_samples,
    validate_metrics,
)

PASSED = []
FAILED = []


def ok(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(
        f"  [{'PASS' if condition else 'FAIL'}] {name}"
        + (f" | {detail}" if not condition else "")
    )


def _sample_rows(questions):
    return [
        {
            "question": q,
            "answer": "答案-" + q,
            "contexts": ["片段甲", "片段乙"],
            "ground_truth": ["参考答案甲"],
        }
        for q in questions
    ]


# --------------------------------------------------------------------------- #
# ragas 假模块
# --------------------------------------------------------------------------- #


def _fake_values(n_rows, metric_names):
    """确定性打分：value(i,j) = 1.0 - 0.1*i - 0.05*j（截断到 >=0）"""
    records = []
    for i in range(n_rows):
        row = {}
        for j, name in enumerate(metric_names):
            row[name] = round(max(0.0, 1.0 - 0.1 * i - 0.05 * j), 4)
        records.append(row)
    return records


class FakeFrame:
    def __init__(self, records):
        self._records = records

    def to_dict(self, orient="records"):
        return self._records


def _module(name, **attrs):
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    sys.modules[name] = mod
    return mod


class _Result:
    def __init__(self, records):
        self._records = records

    def to_pandas(self):
        return FakeFrame(self._records)


def _install_legacy_ragas(metric_names):
    """0.1.x 形态：HF Dataset + ragas.metrics.base.set_llm/set_embeddings"""
    captured = {"llm": None, "embeddings": None, "dataset": None, "metrics": None}

    def set_llm(llm):
        captured["llm"] = llm

    def set_embeddings(emb):
        captured["embeddings"] = emb

    def evaluate(dataset, metrics=None):
        captured["dataset"] = dataset
        captured["metrics"] = metrics
        rows = dataset if isinstance(dataset, list) else list(dataset)
        records = _fake_values(len(rows), metric_names)
        return _Result(records)

    metrics = _module("ragas.metrics")
    for name in metric_names:
        setattr(metrics, name, "metric:" + name)
    _module("ragas.metrics.base", set_llm=set_llm, set_embeddings=set_embeddings)
    _module("ragas", metrics=metrics, evaluate=evaluate)
    _module(
        "datasets",
        Dataset=type(
            "Dataset",
            (),
            {"from_list": staticmethod(lambda rows: [dict(r) for r in rows])},
        ),
    )
    return captured


def _install_v2_ragas(metric_names):
    """0.2.x 形态：EvaluationDataset/SingleTurnSample，evaluate(**llm/embeddings)"""
    captured = {"samples": None, "metrics": None, "kwargs": None}

    class SingleTurnSample:
        def __init__(self, user_input=None, response=None, retrieved_contexts=None, reference=None):
            self.user_input = user_input
            self.response = response
            self.retrieved_contexts = retrieved_contexts
            self.reference = reference

    class EvaluationDataset:
        def __init__(self, samples=None):
            self.samples = samples or []

    def evaluate(dataset, metrics=None, **kwargs):
        captured["samples"] = dataset.samples
        captured["metrics"] = metrics
        captured["kwargs"] = kwargs
        records = _fake_values(len(dataset.samples), metric_names)
        return _Result(records)

    metrics = _module("ragas.metrics")
    for name in metric_names:
        setattr(metrics, name, "metric:" + name)
    _module(
        "ragas",
        metrics=metrics,
        evaluate=evaluate,
        SingleTurnSample=SingleTurnSample,
        EvaluationDataset=EvaluationDataset,
    )
    return captured


def _pop_ragas():
    for key in list(sys.modules):
        if key == "ragas" or key.startswith("ragas.") or key == "datasets":
            sys.modules.pop(key, None)


# --------------------------------------------------------------------------- #
# 归一化与指标校验
# --------------------------------------------------------------------------- #


def test_normalization():
    print("== normalize_samples ==")
    ok("合法样本原样保留", normalize_samples(_sample_rows(["q1"]))[0]["contexts"] == ["片段甲", "片段乙"])
    rows = normalize_samples(
        [
            {
                "question": "q",
                "answer": "a",
                "contexts": "单条上下文",
                "ground_truths": "参考答案",  # 别名
            }
        ]
    )
    ok("contexts 字符串→列表", rows[0]["contexts"] == ["单条上下文"])
    ok("ground_truths 别名生效", rows[0]["ground_truth"] == ["参考答案"])
    ok("空列表样本报错", _raises(normalize_samples, []))
    ok("非列表样本报错", _raises(normalize_samples, {"question": "q"}))
    ok("缺 question 报错", _raises(normalize_samples, [{"answer": "a", "contexts": []}]))
    ok("缺 answer 报错", _raises(normalize_samples, [{"question": "q", "contexts": []}]))
    ok("缺 contexts 报错", _raises(normalize_samples, [{"question": "q", "answer": "a"}]))


def _raises(fn, *args):
    try:
        fn(*args)
        return False
    except RAGEvaluationError:
        return True


def test_metrics_validation():
    print("== 指标校验 ==")
    ok("默认指标为四类齐全", DEFAULT_METRICS == ["faithfulness", "answer_relevancy", "context_precision", "context_recall"])
    ok("描述表与默认集合一致", set(METRIC_DESCRIPTIONS) == set(DEFAULT_METRICS))
    try:
        validate_metrics(["faithfulness", "bogus"])
        ok("未知指标报错并带支持列表", False)
    except RAGEvaluationError as e:
        ok("未知指标报错并带支持列表", "supported" in str(e), str(e))
    ok("空指标报错", _raises_metric(validate_metrics, []))
    ok("去重保序", validate_metrics(["context_recall", "faithfulness", "faithfulness"]) == ["context_recall", "faithfulness"])
    ok("合法集合通过", validate_metrics(["faithfulness", "answer_relevancy"]) == ["faithfulness", "answer_relevancy"])


def _raises_metric(fn, names):
    try:
        fn(names)
        return False
    except RAGEvaluationError:
        return True


# --------------------------------------------------------------------------- #
# 报告聚合（无 ragas）
# --------------------------------------------------------------------------- #


def test_build_report():
    print("== build_report ==")
    names = ["faithfulness", "answer_relevancy"]
    rows = _sample_rows(["q1", "q2"])  # 2 行
    frame = FakeFrame(
        [
            {"faithfulness": 0.9, "answer_relevancy": 0.8},
            {"faithfulness": 0.7, "answer_relevancy": None},  # 缺失指标
        ]
    )
    report = build_report(rows, names, frame)
    ok("summary 只对有效值取均值", report["summary"] == {"faithfulness": 0.8, "answer_relevancy": 0.8}, str(report["summary"]))
    ok("缺失值归一为 None", report["per_question"][1]["answer_relevancy"] is None)
    ok("逐行保留 question", report["per_question"][0]["question"] == "q1")
    ok("样本数正确", report["samples"] == 2)
    nan = float("nan")
    report2 = build_report(rows, ["faithfulness"], FakeFrame([{"faithfulness": nan}]))
    ok("NaN 归一为 None 且不进均值", report2["summary"]["faithfulness"] is None and report2["per_question"][0]["faithfulness"] is None)


# --------------------------------------------------------------------------- #
# 端到端（假 ragas）
# --------------------------------------------------------------------------- #


def test_legacy_backend_flow():
    print("== legacy (0.1.x) 适配 ==")
    names = DEFAULT_METRICS
    _pop_ragas()
    captured = _install_legacy_ragas(names)
    rows = _sample_rows(["q1", "q2"])
    ev = RAGEvaluator(metrics=names, llm="chat-llm", embeddings="emb")
    report = ev.evaluate(rows)

    ok("legacy 后端被识别", report["backend"] == "legacy")
    ok("数据集按行传参", len(captured["dataset"]) == 2, str(captured["dataset"]))
    row0 = captured["dataset"][0]
    ok("字段映射正确", set(row0) == {"question", "answer", "contexts", "ground_truth"}, str(row0))
    ok("指标对象按名装配", captured["metrics"] == ["metric:" + n for n in names])
    ok("提供 llm 时调用 set_llm", captured["llm"] == "chat-llm")
    ok("提供 embeddings 时调用 set_embeddings", captured["embeddings"] == "emb")
    # 均值：(1.0+0.9)/2=0.95 / (0.95+0.85)/2=0.9 / (0.9+0.8)/2=0.85 / (0.85+0.75)/2=0.8
    ok("summary 聚合正确", report["summary"] == {"faithfulness": 0.95, "answer_relevancy": 0.9, "context_precision": 0.85, "context_recall": 0.8}, str(report["summary"]))
    ok("逐问题明细完整", len(report["per_question"]) == 2 and report["per_question"][1]["faithfulness"] == 0.9)

    # 不提供 llm/embeddings → 不调用 set_*
    _pop_ragas()
    captured2 = _install_legacy_ragas(names)
    RAGEvaluator(metrics=["faithfulness"]).evaluate(rows)
    ok("未提供 llm 时不调用 set_llm", captured2["llm"] is None)
    ok("未提供 embeddings 时不调用 set_embeddings", captured2["embeddings"] is None)


def test_v2_backend_flow():
    print("== v2 (0.2.x) 适配 ==")
    names = ["faithfulness", "context_recall"]
    _pop_ragas()
    captured = _install_v2_ragas(names)
    rows = _sample_rows(["q1"])
    report = RAGEvaluator(metrics=names, llm="chat-llm", embeddings="emb").evaluate(rows)

    ok("v2 后端被识别", report["backend"] == "v2")
    s0 = captured["samples"][0]
    ok("SingleTurnSample 字段映射", s0.user_input == "q1" and s0.response == "答案-q1", str(s0))
    ok("检索上下文与参考答案带入", s0.retrieved_contexts == ["片段甲", "片段乙"] and s0.reference == ["参考答案甲"])
    ok("llm/embeddings 走 evaluate 参数", captured["kwargs"] == {"llm": "chat-llm", "embeddings": "emb"}, str(captured["kwargs"]))
    ok("指标对象按名装配", captured["metrics"] == ["metric:faithfulness", "metric:context_recall"])
    ok("v2 报告聚合正确", report["summary"]["faithfulness"] == 1.0 and report["summary"]["context_recall"] == 0.95, str(report["summary"]))


def test_missing_ragas_raises_helpful():
    print("== ragas 未安装降级 ==")
    _pop_ragas()
    ev = RAGEvaluator(metrics=["faithfulness"])
    try:
        ev.evaluate(_sample_rows(["q1"]))
        ok("未安装 ragas 时抛错", False)
    except RAGEvaluationError as e:
        msg = str(e)
        ok("未安装 ragas 时抛错", True)
        ok("报错含安装指引", "pip install" in msg and "requirements-eval" in msg, msg)
    ok("未安装时指标配置本身不报错", isinstance(RAGEvaluator(metrics=["faithfulness"]), RAGEvaluator))


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


if __name__ == "__main__":
    test_normalization()
    test_metrics_validation()
    test_build_report()
    test_legacy_backend_flow()
    test_v2_backend_flow()
    test_missing_ragas_raises_helpful()

    print("")
    if FAILED:
        print(f"FAILED ({len(FAILED)}): {FAILED}")
        sys.exit(1)
    print(f"ALL PASSED ({len(PASSED)} assertions)")
    sys.exit(0)
