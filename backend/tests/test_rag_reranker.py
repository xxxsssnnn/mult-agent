"""RAG 两阶段重排（Enterprise Phase 3）回归测试

覆盖：
- 分数解析：正常 / 多余文字 / 数量不足 / 越界裁剪
- LLM 点级重排：批量打分重排、top-k 截断、稳定排序、分数对齐
- 降级：未配置 LLM / 打分失败 / 输出无法解析 → 原序返回，链路不中断
- Agent 端到端：放大召回 → 重排截断 → 缓存按是否重排隔离

通过 `python tests/test_rag_reranker.py` 直接运行。
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

from app.rag.reranker import LLMReranker, parse_score_list  # noqa: E402

from test_rag_enterprise import (  # noqa: E402
    FakeVectorStore,
    make_agent,
    make_txt,
)
from pathlib import Path  # noqa: E402

run = asyncio.run

PASSED = []
FAILED = []


def ok(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(
        f"  [{'PASS' if condition else 'FAIL'}] {name}"
        + (f" | {detail}" if not condition else "")
    )


class FakeLLM:
    """返回单调递增分数行 → 重排结果应为输入倒序。

    content 缺省时按用户消息中的候选数（"n. ..." 行数）动态生成等量分数行，
    以模拟真实重排打分会随候选数变化。
    """

    def __init__(self, content=None, raise_error=False):
        self._content = content
        self.raise_error = raise_error

    def content(self, messages):
        if self.raise_error:
            raise RuntimeError("simulated LLM failure")
        if self._content is not None:
            return self._content
        human = messages[-1].content if messages else ""
        n = sum(1 for ln in human.splitlines() if re.match(r"^\d+\.\s", ln))
        # 第 i 行分数随 i 增大 → 升序打分 → 重排后顺序反转
        return "\n".join(f"{0.5 + i * 0.03:.2f}" for i in range(n))

    async def ainvoke(self, messages):
        return SimpleNamespace(content=self.content(messages))


def make_docs(n, prefix="候选文档"):
    return [Document(page_content=f"{prefix}{i} 内容主体{i}", metadata={"chunk_id": f"c{i}"}) for i in range(n)]


# --------------------------------------------------------------------------- #
# 分数解析
# --------------------------------------------------------------------------- #


def test_parse_scores():
    print("== parse_score_list（严格逐行解析） ==")
    ok("逐行分数解析", parse_score_list("0.9\n0.1\n0.5", 3) == [0.9, 0.1, 0.5])
    ok("容忍末尾空行", parse_score_list("0.9\n0.1\n\n", 2) == [0.9, 0.1])
    ok("整数行可解析", parse_score_list("1\n0", 2) == [1.0, 0.0])
    ok("越界值裁剪到 [0,1]", parse_score_list("1.7\n-0.2", 2) == [1.0, 0.0])
    ok("混入解释文字返回 None（防正文数字错位）", parse_score_list("候选1: 0.88\n候选2: 0.21", 2) is None)
    ok("数量不足返回 None", parse_score_list("0.9\n0.1", 3) is None)
    ok("空输出返回 None", parse_score_list("", 2) is None)
    ok("无数字返回 None", parse_score_list("很好 很相关", 2) is None)


# --------------------------------------------------------------------------- #
# 重排器单元
# --------------------------------------------------------------------------- #


def test_rerank_unit():
    print("== LLMReranker 单元 ==")
    # 未配置 LLM：不启用，原序返回
    disabled = LLMReranker(llm=None, enabled=True)
    docs = make_docs(4)
    out = run(disabled.rerank("q", docs, k=2))
    ok("无 LLM 时 active=False", disabled.active is False)
    ok("无 LLM 原序截断返回", [d.metadata["chunk_id"] for d in out] == ["c0", "c1"])
    ok("无 LLM 不记分数", disabled.last_scores is None)

    # 启用 + FakeLLM 升序打分 → 输入倒序重排
    reranker = LLMReranker(llm=FakeLLM(), enabled=True)
    docs = make_docs(6)
    out = run(reranker.rerank("查询", docs, k=3))
    ok("升序打分后重排为倒序", [d.metadata["chunk_id"] for d in out] == ["c5", "c4", "c3"])
    ok("重排截断到 k", len(out) == 3)
    ok("分数与输出一一对应且降序", reranker.last_scores == [0.65, 0.62, 0.59], str(reranker.last_scores))

    # 开关关闭：即便有 LLM 也不重排
    off = LLMReranker(llm=FakeLLM(), enabled=False)
    out = run(off.rerank("查询", make_docs(3)))
    ok("enabled=False 时旁路原序", [d.metadata["chunk_id"] for d in out] == ["c0", "c1", "c2"])

    # 候选不足时全量返回（无需截断）
    out = run(reranker.rerank("查询", make_docs(1), k=5))
    ok("候选少于 k 时全量返回", len(out) == 1)

    # LLM 抛异常 → 降级原序
    failing = LLMReranker(llm=FakeLLM(raise_error=True), enabled=True)
    out = run(failing.rerank("查询", make_docs(4), k=2))
    ok("LLM 异常降级原序", [d.metadata["chunk_id"] for d in out] == ["c0", "c1"])
    ok("异常后分数清空", failing.last_scores is None)

    # LLM 输出无法解析 → 降级原序
    garbled = LLMReranker(llm=FakeLLM(content="我认为都很相关，请自行判断"), enabled=True)
    out = run(garbled.rerank("查询", make_docs(4)))
    ok("输出不可解析降级原序", [d.metadata["chunk_id"] for d in out] == ["c0", "c1", "c2", "c3"])

    # 长文本截断进 prompt（不抛错即可，验证 max_doc_chars 生效不炸）
    long_docs = [Document(page_content="长" * 3000, metadata={"chunk_id": "long0"})]
    out = run(reranker.rerank("查询", long_docs))
    ok("超长候选可正常处理", len(out) == 1)


def test_rerank_stable_ties():
    print("== 同分保持原序 ==")
    reranker = LLMReranker(llm=FakeLLM(content="0.5\n0.5\n0.5\n0.5"), enabled=True)
    docs = make_docs(4)
    out = run(reranker.rerank("查询", docs))
    ok("同分时稳定保持原顺序", [d.metadata["chunk_id"] for d in out] == ["c0", "c1", "c2", "c3"])


# --------------------------------------------------------------------------- #
# Agent 端到端
# --------------------------------------------------------------------------- #


def test_agent_two_stage_rerank_flow():
    print("== Agent：放大召回 → 重排截断 ==")
    store = FakeVectorStore()
    agent, _ = make_agent(store=store)
    agent.search_type = "hybrid"
    agent.configure_components(llm=FakeLLM())  # 注入 LLM 后重排自动生效
    user = uuid.uuid4()

    contents = [f"企业级 检索 增强 生成 候选文档{i} 独特内容{i}" for i in range(6)]
    for i, content in enumerate(contents):
        run(store.add_chunks(user, f"doc{i}", [Document(page_content=content)], {}))

    result = run(agent.execute({"query": "企业级 检索", "k": 3}, user_id=user))
    reranked_top = [d["content"] for d in result["retrieved_documents"]]

    ok("重排生效", result["rerank"]["enabled"] is True, str(result["rerank"]))
    ok("第一阶段放大召回 6 个候选", result["rerank"]["candidates"] == 6, str(result["rerank"]))
    ok("最终截断到 k=3", result["num_retrieved"] == 3, str(result["rerank"]))
    ok("重排把原末位提到首位", reranked_top[0] == contents[5], str(reranked_top[0]))
    ok("分数与最终输出一一对应", isinstance(result["rerank"]["scores"], list) and len(result["rerank"]["scores"]) == 3)

    # 同查询再次执行 → 缓存命中，重排结果一致
    again = run(agent.execute({"query": "企业级 检索", "k": 3}, user_id=user))
    ok("重排结果被缓存", again["cache"]["hit"] is True)
    ok("缓存命中返回同一重排顺序", [d["content"] for d in again["retrieved_documents"]] == reranked_top)

    # 知识库经 ingest 新增文档 → 缓存事件失效 → 需重新走全链路
    path6 = make_txt("企业级 检索 增强 生成 候选文档6 独特内容6 新增段落")
    try:
        run(agent.ingest_documents([path6], user_id=user, db=None))
    finally:
        Path(path6).unlink(missing_ok=True)
    redo = run(agent.execute({"query": "企业级 检索", "k": 3}, user_id=user))
    ok("新增文档后重排缓存失效", redo["cache"]["hit"] is False)
    ok("失效后候选重新放大", redo["rerank"]["candidates"] == 7, str(redo["rerank"]))


def test_agent_rerank_cache_key_separated_from_no_rerank():
    print("== 缓存键隔离：是否重排不互相复用 ==")
    store = FakeVectorStore()
    agent, _ = make_agent(store=store)
    agent.search_type = "hybrid"
    user = uuid.uuid4()
    contents = [f"企业级 检索 增强 生成 候选文档{i}" for i in range(5)]
    for i, content in enumerate(contents):
        run(store.add_chunks(user, f"doc{i}", [Document(page_content=content)], {}))

    task = {"query": "企业级 检索", "k": 2}
    # 先关闭重排执行一次（写入 no-rerank 键）
    agent.rerank_enabled = False
    agent.configure_components(llm=None)
    no_rr = run(agent.execute(task, user_id=user))
    ok("关闭重排时直接截断", no_rr["rerank"]["candidates"] == 2, str(no_rr["rerank"]))

    # 打开重排执行：key 不同，必须 miss 而不是误用旧缓存
    agent.rerank_enabled = True
    agent.configure_components(llm=FakeLLM())
    with_rr = run(agent.execute(task, user_id=user))
    ok("重排管道不受未重排缓存影响", with_rr["cache"]["hit"] is False)
    ok("重排键第二次命中", run(agent.execute(task, user_id=user))["cache"]["hit"] is True)

    # 回到未重排管道 → 命中的是自己那份缓存（未被重排键污染）
    agent.rerank_enabled = False
    agent.configure_components(llm=None)
    back = run(agent.execute(task, user_id=user))
    ok("未重排管道命中未重排缓存", back["cache"]["hit"] is True)
    ok("未重排结果与最初一致", [d["content"] for d in back["retrieved_documents"]] == [d["content"] for d in no_rr["retrieved_documents"]])


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


if __name__ == "__main__":
    test_parse_scores()
    test_rerank_unit()
    test_rerank_stable_ties()
    test_agent_two_stage_rerank_flow()
    test_agent_rerank_cache_key_separated_from_no_rerank()

    print("")
    if FAILED:
        print(f"FAILED ({len(FAILED)}): {FAILED}")
        sys.exit(1)
    print(f"ALL PASSED ({len(PASSED)} assertions)")
    sys.exit(0)
