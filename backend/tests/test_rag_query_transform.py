"""RAG 查询转换（LLM 多查询扩展，Enterprise Phase 4）回归测试

覆盖：
- 变体解析：编号/项目符号/引号清洗、去重、数量上限
- 转换器降级：无 LLM / 未启用 / 短查询 / LLM 异常 → 原样 [query]
- Agent 端到端：多变体召回 → RRF 融合（覆盖单查询漏检文档）→ 重排精排
- 缓存：命中不触发转换 LLM 调用；管道（转换开/关）缓存键隔离

通过 `python tests/test_rag_query_transform.py` 直接运行。
"""
import asyncio
import os
import re
import sys
import uuid
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("OPENAI_API_KEY", "")

from langchain.schema import Document  # noqa: E402

from app.rag.query_transformer import (  # noqa: E402
    LLMQueryTransformer,
    parse_query_variants,
)

from test_rag_enterprise import FakeVectorStore, make_agent  # noqa: E402

run = asyncio.run

PASSED = []
FAILED = []


def ok(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(
        f"  [{'PASS' if condition else 'FAIL'}] {name}"
        + (f" | {detail}" if not condition else "")
    )


class StubLLM:
    """固定输出 / 可抛异常的 LLM 替身（转换器单测用）"""

    def __init__(self, content="", raise_error=False):
        self._content = content
        self.raise_error = raise_error

    async def ainvoke(self, messages):
        if self.raise_error:
            raise RuntimeError("simulated LLM failure")
        return SimpleNamespace(content=self._content)


class PipelineLLM:
    """Agent 全链路 LLM：按 prompt 区分三种角色。

    - 查询转换：返回固定变体（覆盖检索/部署两个侧面）
    - 重排：升序分数 → 最终顺序反转
    - 答案生成：空串
    """

    def __init__(self):
        self.calls = []

    async def ainvoke(self, messages):
        text = messages[-1].content if messages else ""
        if "改写后的查询变体" in text:
            self.calls.append("transform")
            return SimpleNamespace(
                content="向量检索 召回 排序 要点\n多云 部署 高可用 架构"
            )
        if "候选文档片段" in text:
            self.calls.append("rerank")
            n = sum(1 for ln in text.splitlines() if re.match(r"^\d+\.\s", ln))
            return SimpleNamespace(
                content="\n".join(f"{0.5 + i * 0.03:.2f}" for i in range(n))
            )
        self.calls.append("answer")
        return SimpleNamespace(content="")


QUERY = "企业级 RAG 向量检索 与 数据隐私 合规 的解决方案"
RETRIEVAL_DOC = "关于 向量检索 召回 排序 的 设计 文档"
PRIVACY_DOC = "关于 数据隐私 合规 审计 的 规范 文档"
DEPLOY_DOC = "关于 多云 部署 高可用 的 最佳实践 文档"


# --------------------------------------------------------------------------- #
# 变体解析
# --------------------------------------------------------------------------- #


def test_parse_variants():
    print("== parse_query_variants ==")
    ok("逐行解析", parse_query_variants("A 查询\nB 查询", 5) == ["A 查询", "B 查询"])
    ok("剥掉数字编号", parse_query_variants("1. 第一问\n2、第二问", 5) == ["第一问", "第二问"])
    ok("剥掉项目符号与引号", parse_query_variants("- 项目一\n* 项目二\n“带引号”", 5) == ["项目一", "项目二", "带引号"])
    ok("去重（保序）", parse_query_variants("甲 问\n乙 问\n甲 问", 5) == ["甲 问", "乙 问"])
    ok("空行忽略", parse_query_variants("A 问\n\n\nB 问", 5) == ["A 问", "B 问"])
    ok("空输出", parse_query_variants("", 5) == [])
    ok("数量上限", parse_query_variants("a\nb\nc\nd", 3) == ["a", "b", "c"])


# --------------------------------------------------------------------------- #
# 转换器单元
# --------------------------------------------------------------------------- #


def test_transformer_unit():
    print("== LLMQueryTransformer 单元 ==")
    q = "这是一条足够长的用户查询问题"

    # 未配置 LLM：inactive，原样返回
    t = LLMQueryTransformer(llm=None, enabled=True)
    out = run(t.transform(q))
    ok("无 LLM 时 active=False", t.active is False)
    ok("无 LLM 原样返回", out == [q])

    # 短查询：不转换
    t = LLMQueryTransformer(llm=StubLLM(), enabled=True, min_query_len=8)
    out = run(t.transform("短"))
    ok("短查询不转换", out == ["短"])

    # num_variants=1：无扩展意义，不调用 LLM
    t = LLMQueryTransformer(llm=StubLLM(), enabled=True, num_variants=1)
    out = run(t.transform(q))
    ok("num_variants=1 直接返回原文", out == [q])
    ok("num_variants=1 视为 inactive", t.active is False)

    # enabled=False：即便有 LLM 也不转换
    t = LLMQueryTransformer(llm=StubLLM(), enabled=False)
    out = run(t.transform(q))
    ok("enabled=False 旁路", out == [q])

    # LLM 抛异常 → 降级原文
    t = LLMQueryTransformer(llm=StubLLM(raise_error=True), enabled=True)
    out = run(t.transform(q))
    ok("LLM 异常降级原文", out == [q])
    ok("降级后不记录变体", t.last_variants == [q])

    # 正常扩展：原文 + 生成变体（封顶 num_variants-1）
    t = LLMQueryTransformer(
        llm=StubLLM(content="变体甲 主题一\n变体乙 主题二\n变体丙 主题三"),
        enabled=True,
        num_variants=3,
    )
    out = run(t.transform(q))
    ok("输出含原文且生成数封顶", out == [q, "变体甲 主题一", "变体乙 主题二"], str(out))
    ok("观测字段记录变体", t.last_variants == out)
    ok("观测字段记录原始输出", "变体丙" in (t.last_raw or ""))

    # 生成的变体与原文重复 → 丢弃
    t = LLMQueryTransformer(
        llm=StubLLM(content=q + "\n其他变体"), enabled=True, num_variants=3
    )
    out = run(t.transform(q))
    ok("与原文重复的变体被丢弃", out == [q, "其他变体"], str(out))

    # LLM 空输出 / 纯符号 → 零变体 → 仅原文
    t = LLMQueryTransformer(llm=StubLLM(content=""), enabled=True)
    ok("空输出降级为仅原文", run(t.transform(q)) == [q])
    t = LLMQueryTransformer(llm=StubLLM(content="- \n*\n"), enabled=True)
    ok("纯符号输出降级为仅原文", run(t.transform(q)) == [q])

    # 说明：模型输出“前言废话+无变体”时，废话行会被容忍为一条变体
    # （检索不到任何内容 → 融合阶段自然无贡献，链路不受影响），此处固化该行为
    t = LLMQueryTransformer(
        llm=StubLLM(content="好的，以下是改写结果"), enabled=True, num_variants=3
    )
    out = run(t.transform(q))
    ok("前言废话被容忍为无害变体（行为固化）", out == [q, "好的，以下是改写结果"], str(out))


# --------------------------------------------------------------------------- #
# Agent 端到端
# --------------------------------------------------------------------------- #


def _seed(store, user):
    for i, content in enumerate(
        (RETRIEVAL_DOC, PRIVACY_DOC, DEPLOY_DOC)
    ):
        run(store.add_chunks(user, f"doc{i}", [Document(page_content=content)], {}))


def test_agent_multiquery_recall_plus_rerank():
    print("== Agent：多变体召回补漏 + RRF 融合 + 重排 ==")
    store = FakeVectorStore()
    agent, _ = make_agent(store=store)
    agent.search_type = "similarity"
    agent.configure_components(llm=PipelineLLM())
    llm = agent.llm
    user = uuid.uuid4()
    _seed(store, user)

    result = run(agent.execute({"query": QUERY, "k": 3}, user_id=user))
    meta = result["transformation"]
    contents = [d["content"] for d in result["retrieved_documents"]]

    ok("转换启用", meta["enabled"] is True, str(meta))
    ok("变体为原文+2 个改写", meta["variants"] == [QUERY, "向量检索 召回 排序 要点", "多云 部署 高可用 架构"], str(meta))
    ok("转换阶段发起一次 LLM 调用", llm.calls.count("transform") == 1, str(llm.calls))
    ok("单查询漏检的多云文档被变体召回", DEPLOY_DOC in contents, str(contents))
    ok("融合后送入重排的精排结果", contents == [DEPLOY_DOC, PRIVACY_DOC, RETRIEVAL_DOC], str(contents))
    ok("重排段元信息正确", result["rerank"]["enabled"] is True and result["rerank"]["candidates"] == 3, str(result["rerank"]))

    # 缓存命中 → 不再触发任何 LLM 阶段
    before = len(llm.calls)
    again = run(agent.execute({"query": QUERY, "k": 3}, user_id=user))
    ok("重排结果被缓存", again["cache"]["hit"] is True)
    ok("缓存命中不再调用 LLM", len(llm.calls) == before, str(llm.calls))
    ok("缓存命中带转换元信息", again["transformation"]["enabled"] is True and again["transformation"]["variant_count"] == 3)

    # 关闭转换 → 单查询召回：多云文档消失（证明它来自扩展变体而非重排）
    agent.transform_enabled = False
    agent.transformer.enabled = False
    transform_calls_before = llm.calls.count("transform")
    plain = run(agent.execute({"query": QUERY, "k": 3}, user_id=user))
    plain_contents = [d["content"] for d in plain["retrieved_documents"]]
    ok("关闭转换后缓存键隔离（miss）", plain["cache"]["hit"] is False)
    ok("单查询不再召回多云文档", DEPLOY_DOC not in plain_contents, str(plain_contents))
    ok("关闭转换后不再发起转换 LLM 调用", llm.calls.count("transform") == transform_calls_before, str(llm.calls))
    ok("单查询（重排后）命中隐私与检索两文档", plain_contents == [RETRIEVAL_DOC, PRIVACY_DOC], str(plain_contents))


def test_agent_no_llm_keeps_legacy_single_query():
    print("== Agent：无 LLM 时查询转换与重排全部旁路 ==")
    store = FakeVectorStore()
    agent, _ = make_agent(store=store)
    agent.search_type = "similarity"
    user = uuid.uuid4()
    _seed(store, user)

    result = run(agent.execute({"query": QUERY, "k": 3}, user_id=user))
    ok("无 LLM 转换不启用", result["transformation"]["enabled"] is False)
    ok("无 LLM 时变体仅原文", result["transformation"]["variants"] == [QUERY])
    ok("无 LLM 时重排不启用", result["rerank"]["enabled"] is False)
    ok("单查询直接召回", result["num_retrieved"] == 2, str([d["content"] for d in result["retrieved_documents"]]))
    ok("仍可正常产出降级答案", bool(result["answer"]))


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


if __name__ == "__main__":
    test_parse_variants()
    test_transformer_unit()
    test_agent_multiquery_recall_plus_rerank()
    test_agent_no_llm_keeps_legacy_single_query()

    print("")
    if FAILED:
        print(f"FAILED ({len(FAILED)}): {FAILED}")
        sys.exit(1)
    print(f"ALL PASSED ({len(PASSED)} assertions)")
    sys.exit(0)
