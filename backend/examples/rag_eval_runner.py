"""RAGAS 端到端评估 runner（Enterprise RAG Phase 5，可选依赖）

在自包含数据集上跑完整 RAG 链路并计算 RAGAS 指标：
  corpus    -> 导入隔离租户知识库
  questions -> 逐条走 execute（含重排/查询转换等全部实时链路）

用法（需先安装可选依赖，并配置 OPENAI_API_KEY 以启用答案生成与多数指标）：
  pip install -r backend/requirements-eval.txt
  python backend/examples/rag_eval_runner.py backend/examples/rag_eval_dataset.example.json
      --output rag_eval_report.json
      --k 3 --search-type hybrid --metrics faithfulness,answer_relevancy,context_precision,context_recall
      --no-rerank --no-transform        # A/B：对比不同管道配置

要点：
- contexts 使用模型实际看到的全文（execute 的 include_full_documents），未做截断，
  保证 faithfulness/context_recall 评估公平
- 自动关闭语义缓存：评估测的是实时链路，不能命中历史答案
- 结果报告（均值 + 逐问题明细）写入 --output 指定的 JSON 文件并打印摘要
"""
import argparse
import asyncio
import json
import os
import sys
import tempfile
from uuid import uuid4

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.rag import RAGAgent  # noqa: E402
from app.rag.evaluator import (  # noqa: E402
    DEFAULT_METRICS,
    RAGEvaluator,
    normalize_samples,
)

_TEXT_EXTENSIONS = (".txt", ".md")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="RAGAS end-to-end RAG evaluation")
    parser.add_argument("dataset", help="数据集 JSON（corpus + questions）")
    parser.add_argument("--output", default="rag_eval_report.json", help="报告输出路径")
    parser.add_argument("--k", type=int, default=3, help="检索/生成 top-k")
    parser.add_argument("--search-type", default="hybrid", choices=["hybrid", "similarity", "score", "mmr"])
    parser.add_argument("--metrics", default=",".join(DEFAULT_METRICS), help="逗号分隔的指标名")
    parser.add_argument("--no-rerank", action="store_true", help="关闭两阶段重排（A/B 对比）")
    parser.add_argument("--no-transform", action="store_true", help="关闭查询转换（A/B 对比）")
    return parser.parse_args(argv)


def load_dataset(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data.get("corpus"), list) or not isinstance(data.get("questions"), list):
        raise ValueError("dataset must contain 'corpus' (list) and 'questions' (list)")
    if not data["corpus"] or not data["questions"]:
        raise ValueError("dataset corpus/questions must not be empty")
    return data


async def _ingest_corpus(agent, user_id, corpus, tmp_dir) -> dict:
    file_paths, filenames = [], []
    for idx, doc in enumerate(corpus):
        filename = (doc.get("filename") or f"doc_{idx}.txt").strip()
        if not os.path.splitext(filename)[1].lower() in _TEXT_EXTENSIONS:
            filename = os.path.splitext(filename)[0] + ".txt"
        path = os.path.join(tmp_dir, filename)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(str(doc.get("content", "")))
        file_paths.append(path)
        filenames.append(filename)
    ingest = await agent.ingest_documents(file_paths, user_id=user_id, filenames=filenames)
    total_ok = sum(1 for r in ingest.get("results", []) if r.get("status") != "failed")
    return {"total_ok": total_ok, "ingest": ingest}


async def _run_questions(agent, user_id, questions, k, search_type) -> list:
    samples = []
    for q in questions:
        question = str(q.get("question") or "").strip()
        if not question:
            continue
        gt = q.get("ground_truth", q.get("ground_truths", []))
        result = await agent.execute(
            {
                "query": question,
                "k": k,
                "search_type": search_type,
                "include_full_documents": True,  # 携带模型看到的全文
            },
            user_id=user_id,
        )
        samples.append(
            {
                "question": question,
                "answer": result.get("answer", ""),
                "contexts": result.get("full_documents", []),
                "ground_truth": gt,
            }
        )
        print(
            f"\n[{len(samples)}] Q: {question}\n"
            f"    召回 {result.get('num_retrieved', 0)} 段 | "
            f"重排={result['rerank']['enabled']} 转换={result['transformation']['enabled']}\n"
            f"    A: {str(result.get('answer', ''))[:160]}"
        )
    return samples


async def main(argv=None):
    args = parse_args(argv)
    data = load_dataset(args.dataset)

    if not os.getenv("OPENAI_API_KEY"):
        print("警告：未设置 OPENAI_API_KEY —— 答案将走降级预览，多数 RAGAS 指标需要 LLM 打分。")

    config = {
        "retrieval_k": args.k,
        "search_type": args.search_type,
        "rerank_enabled": not args.no_rerank,
        "transform_enabled": not args.no_transform,
        "persist_directory": "",  # runner 内使用临时目录（见下）
    }
    with tempfile.TemporaryDirectory(prefix="rag_eval_") as tmp_dir:
        config["persist_directory"] = os.path.join(tmp_dir, "chroma")
        agent = RAGAgent(agent_id=uuid4(), name="RAGEvalAgent", config=config)
        if not await agent.initialize():
            raise SystemExit("RAGAgent 初始化失败（检查 embedding 后端配置）")
        agent.semantic_cache = None  # 评估必须测实时链路
        user_id = uuid4()
        print(f"评估租户 user_id: {user_id}\n管道: k={args.k} search={args.search_type} "
              f"rerank={not args.no_rerank} transform={not args.no_transform}")

        summary = await _ingest_corpus(agent, user_id, data["corpus"], tmp_dir)
        print(f"知识库导入完成：成功 {summary['total_ok']}/{len(data['corpus'])} 篇")
        if summary["total_ok"] == 0:
            raise SystemExit("知识库导入全部失败，无法评估")

        samples = await _run_questions(agent, user_id, data["questions"], args.k, args.search_type)
        if not samples:
            raise SystemExit("没有可评估的样本（questions 为空或全部无 question）")
        # 提前校验：字段缺失在打分前给出明确错误
        normalize_samples(samples)

        report = RAGEvaluator(metrics=args.metrics.split(",")).evaluate(samples)
        report["pipeline"] = {
            "k": args.k,
            "search_type": args.search_type,
            "rerank_enabled": not args.no_rerank,
            "transform_enabled": not args.no_transform,
        }
        report["dataset"] = {"file": args.dataset}

        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)

        print("\n" + "=" * 60)
        print("评估报告（均值）：")
        for name, value in report["summary"].items():
            flag = "✓" if (value or 0) >= 0.7 else "△"
            print(f"  {flag} {name:<20} {value}")
        print("=" * 60)
        print(f"报告已写入: {args.output}（含逐问题明细）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
