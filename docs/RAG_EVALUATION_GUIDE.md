# RAG 评估指南（RAGAS）

本指南说明如何对本仓库的两阶段 RAG 链路（召回 → 重排 → 生成）做离线质量评估，
量化「查询转换/重排」等改造带来的收益。评估基于 **RAGAS**（Retrieval-Augmented
Generation Assessment）指标，属**可选能力**：不安装 ragas 不影响平台主链路。

## 1. 指标与含义

| 指标 | 中文 | 衡量 | 需要 |
| --- | --- | --- | --- |
| `faithfulness` | 忠实度 | 答案的断言有多少被检索到的上下文支撑（防幻觉） | LLM |
| `answer_relevancy` | 答案相关性 | 答案与问题的相关程度 | LLM + Embedding |
| `context_precision` | 上下文精确率 | 检索结果中“相关片段”是否排在前面 | LLM |
| `context_recall` | 上下文召回率 | 参考答案的要点被检索上下文覆盖了多少 | Embedding |

含义速记：

- **context_recall 低** → 检索漏了 → 调大 `k`/开启查询转换/检查切块粒度
- **context_precision 低** → 相关片段排太后 → 开启重排（RRF 后 LLM 精排）
- **faithfulness 低** → 答案编造上下文之外内容 → 收紧生成 prompt/提高检索质量
- **answer_relevancy 低** → 答案跑题或空泛

## 2. 安装（可选依赖）

```powershell
pip install -r backend\requirements-eval.txt
```

- 版本：`ragas>=0.1.10`。评估器自动适配 **0.1.x**（legacy）与 **0.2.x**（v2）两代 API。
- 若与主依赖（pydantic 2.x）冲突，建议在独立 venv 安装后，用
  `PYTHONPATH=<repo>\backend` 运行 runner。
- 还需配置 LLM（RAGAS 默认读 `OPENAI_API_KEY`，与平台共用即可）；`answer_relevancy`
  与 `context_recall` 需要 Embedding（默认 OpenAI Embedding，同样读该 Key）。

## 3. 运行评估

```powershell
$env:OPENAI_API_KEY = "sk-..."
python backend\examples\rag_eval_runner.py backend\examples\rag_eval_dataset.example.json `
    --output rag_eval_report.json `
    --k 3 --search-type hybrid
```

输出示例（均值摘要 + 逐问题明细写入 JSON）：

```
  ✓ faithfulness           0.95
  ✓ answer_relevancy       0.90
  ✓ context_precision      0.85
  △ context_recall         0.60
报告已写入: rag_eval_report.json
```

### 3.1 数据集格式

自包含 JSON：`corpus`（导入隔离租户）+ `questions`（带参考答案）：

```json
{
  "corpus": [
    { "filename": "policy.txt", "content": "知识库正文……" }
  ],
  "questions": [
    { "question": "年假多少天？", "ground_truth": ["每年 15 天带薪年假"] }
  ]
}
```

> 提示：生产评估建议每个问题提供 2~3 条参考答案要点，`context_recall` 才能反映要点覆盖。

### 3.2 A/B 对比管道改造

runner 支持开关重排/查询转换做对照实验，量化每次改造的收益：

```powershell
# 关闭全部 LLM 增强（基线）
python backend\examples\rag_eval_runner.py dataset.json --output base.json --no-rerank --no-transform
# 开启全部增强
python backend\examples\rag_eval_runner.py dataset.json --output enhanced.json
```

对比两份报告对应指标即可。

### 3.3 自定义语料库

想评估**已导入现有知识库**而非数据集自带 corpus 时：复用同款 corpus/questions
结构，把 `questions[].ground_truth` 换成人工标注的要点即可（runner 始终以数据集内
corpus 重建隔离租户，保证可复现）。

## 4. 评估公平性说明（实现细节）

- **contexts 取模型实际看到的全文**：平台 API 返回的 `retrieved_documents` 为 200 字符
  预览，仅供展示；评估时通过 `execute(include_full_documents=True)` 取得全文，
  保证 `faithfulness/context_recall` 不因截断失真。
- **自动关闭语义缓存**：评估必须测实时链路（召回/重排/转换的全部变更），
  不能命中历史答案缓存。
- 每问独立跑完整链路，报告含 `transformation/rerank` 元信息可回溯配置。

## 5. 常见问题

### Q1: 报错 “RAGAS ... not installed”

```text
RAGAS is an optional evaluation dependency and is not installed. Install it with:
pip install -r backend/requirements-eval.txt
```

按提示安装后重试；平台其它功能不受影响。

### Q2: 指标出现 null / NaN

该问题通常缺“有效值”可算：

- 答案为空（未配置 LLM 走了降级预览）→ `faithfulness/answer_relevancy` 无值
- 未配置 Embedding → `context_recall` 无值
- 单问指标缺失不影响其余问题均值。

### Q3: 成本很高怎么办？

- 每个指标都有 LLM/Embedding 开销：先用 `--metrics context_recall,faithfulness`
  冒烟，再跑全量
- 问题数建议 20~50 条起步；答案只生成一次（报告含 answer 可复查）

### Q4: 我要换其它评分模型？

评估器支持注入：`RAGEvaluator(metrics=..., llm=<ragas 兼容 LLM>, embeddings=...)`。
runner 默认交给 ragas（读环境变量 OpenAI Key）。在代码里自定义时，legacy 代 API 需把
langchain 模型包成 `ragas.llms.LangchainLLMWrapper`（evaluator 会自动尝试包一层），
0.2.x 代则直接传 `evaluate(llm=..., embeddings=...)`。

## 6. 相关文件

- `backend/app/rag/evaluator.py` — RAGAS 适配与报告聚合（可选依赖，惰性加载）
- `backend/examples/rag_eval_runner.py` — 端到端评估 runner
- `backend/examples/rag_eval_dataset.example.json` — 示例数据集
- `backend/requirements-eval.txt` — 可选依赖清单
- `backend/tests/test_rag_eval.py` — 编排逻辑回归（假 ragas，离线可跑）
