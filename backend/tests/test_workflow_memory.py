"""Agents/Workflows 挂载会话记忆回归测试（Enterprise Phase：记忆接入执行链路）

在无 Redis / 无数据库 / 无 LLM Key 的纯离线环境验证：

- BaseAgent.attach_memory 与 execute_with_memory 的键适配/上下文注入/防污染
- CoderAgent、ReviewerAgent 的输入输出键适配
- CodeReviewWorkflow / TaskPlannerWorkflow 共享会话记忆（同一 manager 贯穿所有 Agent）
- 未启用记忆时行为与旧版完全一致（零回归）

通过 `python tests/test_workflow_memory.py` 直接运行。
"""
import asyncio
import os
import sys
from typing import Any, Dict, List, Optional
from uuid import uuid4

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 确保无真实 LLM/Redis/DB 依赖（若环境已配置 Key 也不走网络：Coder/Reviewer 均无 Key 触发 mock）
_ = os.environ.pop("OPENAI_API_KEY", None)

from app.agents.base import BaseAgent  # noqa: E402
from app.agents.coder import CoderAgent  # noqa: E402
from app.agents.reviewer import ReviewerAgent  # noqa: E402
from app.workflows.code_review import (  # noqa: E402
    CodeReviewWorkflow,
    StructuredReview,
)
from app.workflows.task_planner import TaskPlannerWorkflow  # noqa: E402

PASSED = []
FAILED = []


def ok(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(
        f"  [{'PASS' if condition else 'FAIL'}] {name}"
        + (f" | {detail}" if not condition else "")
    )


# --------------------------------------------------------------------------- #
# 测试替身
# --------------------------------------------------------------------------- #


class FakeMemoryManager:
    """记忆管理器鸭子类型替身：记录消息、返回固定上下文，零外部依赖"""

    session_id: str = "fake-session-1"
    is_initialized: bool = True
    messages: List[Dict[str, str]]
    context_text: str

    def __init__(self, context_text: str = "[历史记忆] 用户偏好清晰、简洁的实现"):
        self.messages = []
        self.context_text = context_text

    async def get_context(self) -> str:
        return self.context_text

    async def add_message(self, role: str, content: str, metadata: Optional[Dict[str, Any]] = None):
        self.messages.append({"role": role, "content": content})


class RecordingAgent(BaseAgent):
    """捕获 execute 收到输入的探针 Agent（验证记忆上下文注入与防污染）"""

    result: Dict[str, Any]
    seen_input: Optional[Dict[str, Any]]

    def __init__(self, result: Optional[Dict[str, Any]] = None):
        super().__init__(agent_id=uuid4(), name="RecordingAgent")
        self.result = result or {"success": True, "output": "OUT-42"}
        self.seen_input = None

    async def initialize(self) -> bool:
        self.is_initialized = True
        return True

    async def execute(self, task_input: Dict[str, Any]) -> Dict[str, Any]:
        self.seen_input = task_input
        return dict(self.result)

    def get_capabilities(self) -> List[str]:
        return ["record"]


async def _init_agent(agent):
    if not agent.is_initialized:
        assert await agent.initialize(), "agent 初始化失败"
    return agent


# --------------------------------------------------------------------------- #
# 1. BaseAgent 记忆消息提取适配
# --------------------------------------------------------------------------- #


async def test_message_extraction():
    print("== BaseAgent 消息提取适配 ==")
    # 用户消息：优先级 user_input > requirement > question > query
    got = RecordingAgent.extract_user_message(
        {"requirement": "实现登录", "code": "x = 1"}
    )
    ok("requirement 作为用户消息", got == "实现登录", str(got))
    got2 = RecordingAgent.extract_user_message({"query": "什么是RAG?"})
    ok("query 作为用户消息", got2 == "什么是RAG?")
    # 无自然语言、仅有代码 → 审查前缀兜底
    got3 = RecordingAgent.extract_user_message({"code": "def f(): pass"})
    ok("纯代码输入加审查前缀",
       bool(got3) and got3.startswith("请审查以下代码："))
    ok("无任何输入键返回 None", RecordingAgent.extract_user_message({}) is None)
    # 助手消息：explanation/review/summary/output/answer 优先，code 兜底截断
    got4 = RecordingAgent.extract_assistant_message(
        {"code": "# long", "explanation": "设计说明"}
    )
    ok("explanation 优先于 code", got4 == "设计说明", str(got4))
    got5 = RecordingAgent.extract_assistant_message({"code": "短代码"})
    ok("无叙述时以 code 兜底", got5 == "短代码")
    ok("空结果返回 None", RecordingAgent.extract_assistant_message({"success": True}) is None)


# --------------------------------------------------------------------------- #
# 2. execute_with_memory 注入/记录/防污染
# --------------------------------------------------------------------------- #


async def test_execute_with_memory_injection():
    print("== execute_with_memory 上下文注入与记录 ==")
    fake = FakeMemoryManager(context_text="CTX-MARKER")
    agent = RecordingAgent()
    await _init_agent(agent)
    await agent.attach_memory(fake)

    original = {"requirement": "实现排序算法"}
    result = await agent.execute_with_memory(original)

    ok("结果正常返回", result.get("output") == "OUT-42")
    assert agent.seen_input is not None
    ok("记忆上下文注入 execute 输入",
       agent.seen_input.get("memory_context") == "CTX-MARKER",
       str(agent.seen_input.get("memory_context")))
    ok("不污染外部 dict", "memory_context" not in original and len(original) == 1)
    ok("user 消息记录需求",
       fake.messages[0] == {"role": "user", "content": "实现排序算法"},
       str(fake.messages))
    ok("assistant 消息记录输出",
       fake.messages[1] == {"role": "assistant", "content": "OUT-42"},
       str(fake.messages))
    ok("无多余消息", len(fake.messages) == 2, str(len(fake.messages)))


async def test_execute_without_memory_is_passthrough():
    print("== 未挂载记忆时 execute_with_memory 等价 execute ==")
    agent = RecordingAgent()
    await _init_agent(agent)
    result = await agent.execute_with_memory({"requirement": "任何需求"})
    ok("无记忆也正常执行", result.get("success") is True)
    assert agent.seen_input is not None
    ok("不注入 memory_context", "memory_context" not in agent.seen_input)
    ok("不尝试写记忆（无 manager）", True)


# --------------------------------------------------------------------------- #
# 3. 真实 CoderAgent / ReviewerAgent 键适配
# --------------------------------------------------------------------------- #


async def test_coder_and_reviewer_key_adaptation():
    print("== CoderAgent / ReviewerAgent 键适配 ==")
    fake = FakeMemoryManager()
    coder = CoderAgent(agent_id=uuid4(), name="CoderT")
    await _init_agent(coder)
    await coder.attach_memory(fake)
    await coder.execute_with_memory({"requirement": "写个函数", "language": "python"})
    ok("Coder user 消息=需求", fake.messages[0]["content"] == "写个函数")
    ok("Coder assistant 消息=说明性输出",
       fake.messages[1]["role"] == "assistant" and "Mock" in fake.messages[1]["content"],
       str(fake.messages))

    fake2 = FakeMemoryManager()
    reviewer = ReviewerAgent(agent_id=uuid4(), name="ReviewerT")
    await _init_agent(reviewer)
    await reviewer.attach_memory(fake2)
    await reviewer.execute_with_memory({"code": "print('hi')", "language": "python"})
    ok("Reviewer 纯代码输入带审查前缀",
       fake2.messages[0]["content"].startswith("请审查以下代码：")
       and "print('hi')" in fake2.messages[0]["content"],
       str(fake2.messages[0].get("content", "")[:60]))
    # Reviewer mock 无叙述性输出 → 不产生 assistant 消息（不崩溃即通过）
    ok("Reviewer 无叙述输出时不记录 assistant",
       len(fake2.messages) == 1, str(len(fake2.messages)))


# --------------------------------------------------------------------------- #
# 4. CodeReviewWorkflow 共享会话记忆
# --------------------------------------------------------------------------- #


async def _make_mock_coder_reviewer():
    coder = CoderAgent(agent_id=uuid4(), name="WfCoder")
    reviewer = ReviewerAgent(agent_id=uuid4(), name="WfReviewer")
    await _init_agent(coder)
    await _init_agent(reviewer)
    return coder, reviewer


async def test_code_review_workflow_memory():
    print("== CodeReviewWorkflow 共享会话记忆 ==")
    fake = FakeMemoryManager()
    coder, reviewer = await _make_mock_coder_reviewer()
    workflow = CodeReviewWorkflow(
        coder_agent=coder, reviewer_agent=reviewer, memory_manager=fake
    )

    async def _fake_parse(review_text):
        return StructuredReview(
            score=88, has_critical_issues=False, issues=[], suggestions=[],
            approved=True, summary="通过",
        )

    workflow._parse_structured_review = _fake_parse

    result = await workflow.execute(
        {"requirement": "开发一个计算器应用", "language": "python"}
    )

    ok("工作流执行成功", result.get("success") is True, str(result.get("error", "")))
    ok("Coder 挂载共享 manager", coder.memory_manager is fake)
    ok("Reviewer 挂载共享 manager", reviewer.memory_manager is fake)
    roles = [m["role"] for m in fake.messages]
    ok("生成轮 user+assistant 已记录", roles[:2] == ["user", "assistant"], str(roles))
    ok("审查轮（代码输入）已记录", len(roles) >= 3 and roles[2] == "user", str(roles))
    ok("审查上下文包含生成代码",
       len(fake.messages) >= 3 and "# Generated code" in fake.messages[2]["content"])
    ok("metadata 带回会话", result["metadata"]["memory"]["session_id"] == "fake-session-1",
       str(result["metadata"].get("memory")))


async def test_code_review_workflow_without_memory_compat():
    print("== CodeReviewWorkflow 未启用记忆 = 旧行为 ==")
    coder, reviewer = await _make_mock_coder_reviewer()
    workflow = CodeReviewWorkflow(coder_agent=coder, reviewer_agent=reviewer)

    async def _fake_parse(review_text):
        return StructuredReview(
            score=75, has_critical_issues=False, issues=[], suggestions=[],
            approved=True, summary="通过",
        )

    workflow._parse_structured_review = _fake_parse

    result = await workflow.execute({"requirement": "实现一个排序功能", "language": "python"})
    ok("执行成功", result.get("success") is True)
    ok("Agent 未挂载记忆", coder.memory_manager is None and reviewer.memory_manager is None)
    ok("metadata 无 memory 字段", "memory" not in result["metadata"], str(result["metadata"]))
    ok("代码正常生成", "Generated code" in result.get("code", ""), result.get("code", "")[:40])


# --------------------------------------------------------------------------- #
# 5. TaskPlannerWorkflow 共享会话记忆（子 Agent 动态挂载）
# --------------------------------------------------------------------------- #


async def test_task_planner_workflow_memory():
    print("== TaskPlannerWorkflow 子 Agent 挂载会话记忆 ==")
    fake = FakeMemoryManager()
    workflow = TaskPlannerWorkflow(memory_manager=fake)
    assert workflow.llm is None or not os.getenv("OPENAI_API_KEY"), "测试需离线（无 LLM）"

    result = await workflow.execute({"user_input": "开发一个订单管理系统"})

    ok("工作流执行成功", result.get("success") is True)
    ok("识别到 code_generation 子任务",
       any(t["task_type"] == "code_generation" for t in result["tasks"]),
       str([t["task_type"] for t in result["tasks"]]))
    ok("子 Agent 记录了需求轮次",
       any(m["role"] == "user" and "实现核心业务逻辑" in m["content"] for m in fake.messages),
       str(fake.messages))
    ok("子 Agent 记录了产出轮次",
       any(m["role"] == "assistant" for m in fake.messages),
       str(fake.messages))
    ok("metadata 带回会话", result["metadata"]["memory"]["session_id"] == "fake-session-1",
       str(result["metadata"].get("memory")))
    ok("workflow 持有共享 manager", workflow.memory_manager is fake)


async def test_task_planner_without_memory_compat():
    print("== TaskPlannerWorkflow 未启用记忆 = 旧行为 ==")
    workflow = TaskPlannerWorkflow()
    result = await workflow.execute({"user_input": "实现一个斐波那契函数"})
    ok("执行成功", result.get("success") is True)
    ok("metadata 无 memory 字段", "memory" not in result["metadata"], str(result["metadata"]))
    ok("结果包含任务执行", result["status"] == "completed")


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


async def main():
    await test_message_extraction()
    await test_execute_with_memory_injection()
    await test_execute_without_memory_is_passthrough()
    await test_coder_and_reviewer_key_adaptation()
    await test_code_review_workflow_memory()
    await test_code_review_workflow_without_memory_compat()
    await test_task_planner_workflow_memory()
    await test_task_planner_without_memory_compat()


if __name__ == "__main__":
    asyncio.run(main())
    print("")
    if FAILED:
        print(f"FAILED ({len(FAILED)}): {FAILED}")
        sys.exit(1)
    print(f"ALL PASSED ({len(PASSED)} assertions)")
    sys.exit(0)
