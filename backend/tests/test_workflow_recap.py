"""Workflow 长任务复盘（recap）回归测试

验证（无 Redis / 无 DB / 无 LLM Key 离线环境）：

- build_recap / format_recap 纯函数：结构化字段 + 可读文本渲染
- BaseWorkflow.record_recap：仅在启用会话记忆时写入、失败静默降级
- TaskPlannerWorkflow / CodeReviewWorkflow 成功执行后 metadata.recap 自动生成
- 复盘作为 kind=task_recap 的 assistant 消息写入共享会话记忆
- 未启用记忆时 metadata 中仍返回复盘（归档不依赖记忆）

通过 `python tests/test_workflow_recap.py` 直接运行。
"""
import asyncio
import os
import sys
from typing import Any, Dict, List, Optional
from uuid import uuid4

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_ = os.environ.pop("OPENAI_API_KEY", None)

from app.workflows.recap import build_recap, format_recap  # noqa: E402
from app.workflows.base import BaseWorkflow  # noqa: E402
from app.agents.coder import CoderAgent  # noqa: E402
from app.agents.reviewer import ReviewerAgent  # noqa: E402
from app.workflows.code_review import (  # noqa: E402
    CodeReviewWorkflow,
    StructuredReview,
)
from app.workflows.task_planner import TaskPlannerWorkflow  # noqa: E402

PASSED = []
FAILED = []


def ok(name: str, condition: bool, detail: str = ""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f" | {detail}" if not condition else ""))


class FakeMemoryManager:
    """记忆管理器替身：保留 role/content/metadata，供断言复盘写入"""

    session_id: str = "fake-session-r1"
    is_initialized: bool = True
    messages: List[Dict[str, Any]]
    context_text: str

    def __init__(self, context_text: str = "[历史记忆] 用户偏好简洁实现"):
        self.messages = []
        self.context_text = context_text

    async def get_context(self) -> str:
        return self.context_text

    async def add_message(self, role: str, content: str,
                          metadata: Optional[Dict[str, Any]] = None):
        self.messages.append({"role": role, "content": content, "metadata": metadata})


class DummyWorkflow(BaseWorkflow):
    def __init__(self, memory_manager=None):
        super().__init__(name="dummy_workflow", memory_manager=memory_manager)

    def build_graph(self):
        raise NotImplementedError

    async def execute(self, initial_state: Dict[str, Any]) -> Dict[str, Any]:
        return {"success": True}


async def _init(agent):
    if not agent.is_initialized:
        assert await agent.initialize()
    return agent


# --------------------------------------------------------------------------- #
# 1. 纯函数：结构 + 文本渲染
# --------------------------------------------------------------------------- #


async def test_recap_build_and_format():
    print("== build_recap / format_recap ==")
    recap = build_recap(
        "task_planner_workflow",
        objective="开发一个订单管理系统",
        success=True,
        attempts=2,
        summary={"total_tasks": 3, "completed_tasks": 2, "failed_tasks": 1},
        tasks=[
            {"id": 1, "task_type": "analysis", "status": "completed", "summary": "架构设计"},
            {"id": 2, "task_type": "code_generation", "status": "failed", "summary": "核心开发"},
        ],
        notes=["部分子任务执行失败"],
    )
    ok("中文标签映射", recap["label"] == "任务规划执行", recap["label"])
    ok("目标写入", recap["objective"] == "开发一个订单管理系统")
    ok("汇总写入", recap["summary"]["total_tasks"] == 3)
    ok("子任务映射 type/status",
       len(recap["tasks"]) == 2 and recap["tasks"][0]["type"] == "analysis"
       and recap["tasks"][1]["status"] == "failed",
       str(recap["tasks"]))
    ok("子任务摘要取自 title", recap["tasks"][0]["summary"] == "架构设计")

    text = format_recap(recap)
    ok("文本含标签", "任务规划执行" in text, text)
    ok("文本含目标", "开发一个订单管理系统" in text)
    ok("文本含结果", "成功" in text and "尝试次数：2" in text, text)
    ok("文本含任务汇总", "任务 3 个" in text and "失败 1" in text, text)
    ok("文本含任务明细行", "[completed] analysis: 架构设计" in text, text)
    ok("文本含备注", "部分子任务执行失败" in text)

    cr = build_recap(
        "code_review_workflow", objective="实现登录", success=True,
        iterations=2, summary={"approved": True}, notes=["审查通过"],
    )
    ok("CodeReview 标签映射", cr["label"] == "代码生成与审查")
    ok("迭代轮次写入", cr["iterations"] == 2)
    cr_text = format_recap(cr)
    ok("审查通过渲染", "审查通过" in cr_text, cr_text)


# --------------------------------------------------------------------------- #
# 2. record_recap 记忆写入
# --------------------------------------------------------------------------- #


async def test_record_recap():
    print("== BaseWorkflow.record_recap ==")
    wf = DummyWorkflow()
    ok("无记忆管理器不写入", await wf.record_recap("anything") is False)

    fake = FakeMemoryManager()
    wf2 = DummyWorkflow(fake)
    ok("有记忆管理器写入成功", await wf2.record_recap("复盘文本") is True)
    msg = fake.messages[-1]
    ok("以 assistant 写入", msg["role"] == "assistant")
    ok("内容为复盘文本", msg["content"] == "复盘文本")
    ok("带 kind 标记", (msg["metadata"] or {}).get("kind") == "task_recap",
       str(msg.get("metadata")))


# --------------------------------------------------------------------------- #
# 3. TaskPlanner 执行结束自动复盘
# --------------------------------------------------------------------------- #


async def test_task_planner_recap():
    print("== TaskPlannerWorkflow 自动复盘 ==")
    fake = FakeMemoryManager()
    wf = TaskPlannerWorkflow(memory_manager=fake)
    res = await wf.execute({"user_input": "开发一个订单管理系统"})
    ok("执行成功", res.get("success") is True, str(res.get("error", "")))

    recap = (res.get("metadata") or {}).get("recap") or {}
    ok("metadata 含 recap", "recap" in res["metadata"], str(res["metadata"].keys()))
    ok("recap 标记 workflow", recap.get("workflow") == "task_planner_workflow")
    ok("recap 含任务汇总", recap["summary"]["total_tasks"] == 3, str(recap.get("summary")))
    ok("recap 含子任务明细", len(recap.get("tasks") or []) == 3)
    ok("recap 记录成功", recap.get("success") is True)

    recap_msgs = [m for m in fake.messages if (m.get("metadata") or {}).get("kind") == "task_recap"]
    ok("复盘写入共享会话记忆", len(recap_msgs) == 1, str(len(recap_msgs)))
    ok("复盘文本含标签与目标",
       recap_msgs and "复盘" in recap_msgs[0]["content"]
       and "订单管理系统" in recap_msgs[0]["content"])


async def test_task_planner_recap_without_memory():
    print("== TaskPlannerWorkflow 复盘不依赖记忆 ==")
    wf = TaskPlannerWorkflow()
    res = await wf.execute({"user_input": "实现一个斐波那契函数"})
    ok("执行成功", res.get("success") is True)
    ok("仍返回复盘", res["metadata"]["recap"]["success"] is True)
    ok("无 memory 元数据", "memory" not in res["metadata"])
    ok("无记忆时也统计子任务", len(res["metadata"]["recap"]["tasks"]) >= 2)


# --------------------------------------------------------------------------- #
# 4. CodeReview 执行结束自动复盘
# --------------------------------------------------------------------------- #


async def test_code_review_recap():
    print("== CodeReviewWorkflow 自动复盘 ==")
    fake = FakeMemoryManager()
    coder = CoderAgent(agent_id=uuid4(), name="RecapCoder")
    reviewer = ReviewerAgent(agent_id=uuid4(), name="RecapReviewer")
    await _init(coder)
    await _init(reviewer)
    workflow = CodeReviewWorkflow(coder_agent=coder, reviewer_agent=reviewer,
                                  memory_manager=fake)

    async def _fake_parse(review_text: str):
        return StructuredReview(
            score=90, has_critical_issues=False, issues=[], suggestions=[],
            approved=True, summary="ok",
        )

    workflow._parse_structured_review = _fake_parse
    res = await workflow.execute({"requirement": "实现冒泡排序", "language": "python"})
    ok("执行成功且通过审查", res.get("success") is True and res.get("approved") is True)

    recap = res["metadata"]["recap"]
    ok("recap 标记 workflow", recap["workflow"] == "code_review_workflow")
    ok("recap 含目标", recap["objective"] == "实现冒泡排序")
    ok("recap 审查通过", recap["summary"]["approved"] is True,
       str(recap.get("summary")))
    ok("recap 评分写入", recap["summary"]["score"] == 90, str(recap["summary"]))

    recap_msgs = [m for m in fake.messages if (m.get("metadata") or {}).get("kind") == "task_recap"]
    ok("复盘写入共享会话记忆", len(recap_msgs) == 1, str(len(recap_msgs)))
    ok("复盘文本含审查结论", recap_msgs and "审查通过" in recap_msgs[0]["content"])


async def test_code_review_recap_without_memory():
    print("== CodeReviewWorkflow 复盘不依赖记忆 ==")
    coder = CoderAgent(agent_id=uuid4(), name="RecapCoder2")
    reviewer = ReviewerAgent(agent_id=uuid4(), name="RecapReviewer2")
    await _init(coder)
    await _init(reviewer)
    workflow = CodeReviewWorkflow(coder_agent=coder, reviewer_agent=reviewer)

    async def _fake_parse(review_text: str):
        return StructuredReview(
            score=60, has_critical_issues=False, issues=[], suggestions=[],
            approved=True, summary="ok",
        )

    workflow._parse_structured_review = _fake_parse
    res = await workflow.execute({"requirement": "实现一个排序", "language": "python"})
    ok("执行成功", res.get("success") is True)
    ok("仍返回复盘", res["metadata"]["recap"]["workflow"] == "code_review_workflow")
    ok("无 memory 元数据", "memory" not in res["metadata"])


async def main():
    await test_recap_build_and_format()
    await test_record_recap()
    await test_task_planner_recap()
    await test_task_planner_recap_without_memory()
    await test_code_review_recap()
    await test_code_review_recap_without_memory()


if __name__ == "__main__":
    asyncio.run(main())
    print("")
    if FAILED:
        print(f"FAILED ({len(FAILED)}): {FAILED}")
        sys.exit(1)
    print(f"ALL PASSED ({len(PASSED)} assertions)")
    sys.exit(0)
