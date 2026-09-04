"""Workflow 答案语义检索（执行档案向量索引）回归测试

独立运行：python tests/test_workflow_answer_search.py
覆盖（无 Redis / 无 LLM Key / 无真 embedding 的离线环境）：
- build_run_documents：归档展开为「1 父复盘 + N 子结果」可索引文档
- WorkflowAnswerStore（注入内存 Chroma + 确定性伪 embedding）：
  索引、语义检索、按 user_id 租户隔离、workflow/status 过滤、
  归档删除、重复索引幂等、embedding 失败静默降级
- API 链路：_archive_run 落库成功后自动索引（带 user_id）；
  GET /workflows/answers/search 鉴权、参数透传、后端不可用优雅降级
"""
import base64
import json
import math
import os
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

# 无 OpenAI key，避免 embedding 走远端
_ = os.environ.pop("OPENAI_API_KEY", None)

# 独立临时 SQLite，不触碰本地 multi_agent.db
TEST_DB = "./_test_wf_answers.db"
if os.path.exists(TEST_DB):
    os.remove(TEST_DB)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB}"

import asyncio  # noqa: E402
import zlib  # noqa: E402

import chromadb  # noqa: E402
import httpx  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from app.core.database import AsyncSessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.task import Task  # noqa: E402
from app.models.user import User  # noqa: E402
from app.workflows.answer_store import WorkflowAnswerStore, build_run_documents  # noqa: E402
from app.workflows.recap import build_recap  # noqa: E402
import app.api.workflows as workflows_api  # noqa: E402

PASSED = []
FAILED = []


def ok(name: str, condition: bool, detail: str = ""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f" | {detail}" if not condition else ""))


# --------------------------------------------------------------------------- #
# 伪 embedding（确定性字符袋，余弦可区分）——让 Chroma 检索可离线验证
# --------------------------------------------------------------------------- #
DIM = 256


class FakeEmbeddings:
    def _vec(self, text: str):
        v = [0.0] * DIM
        for ch in str(text):
            v[zlib.crc32(ch.encode("utf-8")) % DIM] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


class FakeEmbeddingService:
    def __init__(self, fail: bool = False):
        self.embeddings = None if fail else FakeEmbeddings()


# --------------------------------------------------------------------------- #
# 单元测试：Store（内存 Chroma）
# --------------------------------------------------------------------------- #
async def test_store_index_search_and_isolation():
    print("== WorkflowAnswerStore 索引 / 检索 / 租户隔离 ==")
    store = WorkflowAnswerStore(
        persist_directory=":memory:",
        collection_name="wf_answers_test",
        embedding_service=FakeEmbeddingService(),
        chroma_client=chromadb.EphemeralClient(),
    )
    ok("store 可用", store.available, store.error or "")

    user_a = str(uuid.uuid4())
    user_b = str(uuid.uuid4())
    recap = build_recap(
        "task_planner_workflow",
        objective="开发一个订单管理系统",
        success=True,
        summary={"total_tasks": 2, "completed_tasks": 2, "failed_tasks": 0},
        tasks=[
            {"id": 1, "task_type": "analysis", "status": "completed", "summary": "数据库设计"},
            {"id": 2, "task_type": "code_generation", "status": "completed", "summary": "核心接口开发"},
        ],
    )
    n = store.index_run(
        user_id=user_a,
        task_id="wf-index-1",
        title="[任务规划] 开发一个订单管理系统",
        workflow_label="任务规划",
        objective="开发一个订单管理系统",
        success=True,
        recap=recap,
        detail={"status": "success", "total_tasks": 2},
        subtasks=[
            {"seq": 0, "type": "analysis", "title": "数据库设计",
             "status": "completed", "detail": {"summary": "设计 orders/customer 表结构"}},
            {"seq": 1, "type": "code_generation", "title": "核心接口开发",
             "status": "completed", "detail": {"result": "实现订单创建接口"}},
        ],
    )
    ok("父 + 2 子共 3 条索引", n == 3, f"indexed={n}")
    ok("A 库内条数 3", store.count(user_a) == 3, f"count={store.count(user_a)}")
    ok("B 库内条数 0（隔离）", store.count(user_b) == 0)

    # 语义检索：问题词与子任务内容重叠（订单/数据库设计）
    hits = store.search(user_id=user_a, query="订单系统数据库怎么设计的", top_k=5)
    ok("A 检索到结果", len(hits) >= 1, f"hits={len(hits)}")
    top = hits[0]
    ok("结果含相似度", top["similarity"] > 0, f"sim={top['similarity']}")
    ok("结果带来源信息", top["task_id"] and top["workflow_label"] == "任务规划",
       f"task_id={top['task_id']}")
    ok("命中内容含执行结果", any("orders" in h["content"] or "数据库" in h["content"] for h in hits),
       str([h["title"] for h in hits]))

    # 租户隔离：B 查不到 A 的内容
    ok("B 检索为空（隔离）", store.search(user_id=user_b, query="订单系统数据库怎么设计的", top_k=5) == [])

    # 过滤：错误 label / 状态过滤
    ok("workflow_label 过滤为空",
       store.search(user_id=user_a, query="订单", workflow_label="代码审查", top_k=5) == [])
    ok("workflow_label 过滤命中",
       len(store.search(user_id=user_a, query="订单", workflow_label="任务规划", top_k=5)) >= 1)
    ok("status 过滤命中",
       len(store.search(user_id=user_a, query="订单", status="completed", top_k=5)) >= 1)
    ok("status 过滤为空",
       store.search(user_id=user_a, query="订单", status="failed", top_k=5) == [])

    # 重复索引幂等（upsert）
    store.index_run(user_id=user_a, task_id="wf-index-1", title="[任务规划] 开发一个订单管理系统",
                    workflow_label="任务规划", objective="开发一个订单管理系统", success=True)
    ok("重复索引幂等（仍 3 条）", store.count(user_a) == 3, f"count={store.count(user_a)}")

    # 删除该次归档全部索引
    removed = store.remove_task(user_a, "wf-index-1")
    ok("删除归档索引 3 条", removed == 3, f"removed={removed}")
    ok("删除后 A 库为空", store.count(user_a) == 0)


async def test_store_embedding_failure_degrades():
    print("== WorkflowAnswerStore 后端失败静默降级 ==")
    store = WorkflowAnswerStore(
        persist_directory=":memory:",
        collection_name="wf_answers_degrade",
        embedding_service=FakeEmbeddingService(fail=True),
        chroma_client=chromadb.EphemeralClient(),
    )
    # 集合可创建，但 embedding 不可用 → 索引/检索都应安全返回，不抛异常
    n = store.index_run(user_id="u", task_id="wf-x", title="t", workflow_label="任务规划", success=True)
    ok("embedding 失败时索引返回 0 且不抛", n == 0, f"n={n}")
    hits = store.search(user_id="u", query="任意问题", top_k=5)
    ok("embedding 失败时检索返回空且不抛", hits == [], str(hits))


# --------------------------------------------------------------------------- #
# 文档展开纯函数
# --------------------------------------------------------------------------- #
async def test_build_run_documents():
    print("== build_run_documents 展开 ==")
    docs = build_run_documents(
        user_id=uuid.uuid4(),
        task_id="wf-pure-1",
        title="[代码审查] 实现登录",
        workflow_label="代码审查",
        objective="实现登录",
        success=True,
        recap=build_recap("code_review_workflow", objective="实现登录", success=True,
                          summary={"approved": True}),
        detail={"approved": True},
        subtasks=None,
    )
    ok("父任务 1 条", len(docs) == 1, f"len={len(docs)}")
    ok("父文本含目标与结果", "实现登录" in docs[0]["text"] and "成功" in docs[0]["text"])
    ok("元数据标记父任务", docs[0]["is_subtask"] == 0 and docs[0]["parent_task_id"] is None)

    docs2 = build_run_documents(
        user_id=uuid.uuid4(),
        task_id="wf-pure-2",
        title="[任务规划] 重构",
        workflow_label="任务规划",
        objective="重构",
        success=False,
        recap=None,
        detail={"failed": 1},
        subtasks=[
            {"seq": 0, "type": "analysis", "title": "架构梳理", "status": "failed",
             "detail": {"error": "LLM 超时重试耗尽"}},
        ],
    )
    ok("父 + 1 子共 2 条", len(docs2) == 2, f"len={len(docs2)}")
    sub = docs2[1]
    ok("子文本含失败原因", "LLM 超时" in sub["text"] and "失败" in sub["text"])
    ok("子元数据标记正确", sub["is_subtask"] == 1 and sub["parent_task_id"] == "wf-pure-2")


# --------------------------------------------------------------------------- #
# API 链路：归档自动索引 + 语义检索端点
# --------------------------------------------------------------------------- #
class RecordingStore:
    """替身 store：记录索引/检索调用，供断言链路行为"""

    def __init__(self, available: bool = True):
        self.available = available
        self.error = None if available else "test-unavailable"
        self.index_calls = []
        self.search_calls = []

    async def index_run_async(self, **kwargs):
        self.index_calls.append(kwargs)
        return 3

    async def search_async(self, **kwargs):
        self.search_calls.append(kwargs)
        return [
            {
                "task_id": "wf-e2e-1",
                "parent_task_id": None,
                "workflow_label": "任务规划",
                "title": "[任务规划] 开发一个订单管理系统",
                "status": "completed",
                "is_subtask": False,
                "content": "【任务规划 · 执行档案】…",
                "similarity": 0.92,
                "created_at": "2026-09-03T10:00:00+00:00",
            }
        ]


def _decode_payload(token: str) -> dict:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


async def run_api_scenario():
    print("== 归档自动索引 + /answers/search 端点 ==")
    original_store = workflows_api.workflow_answer_store
    recorder = RecordingStore()
    workflows_api.workflow_answer_store = recorder
    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                suffix = uuid.uuid4().hex[:8]
                username = f"wf_{suffix}"
                r = await client.post("/api/v1/auth/register", json={
                    "username": username, "email": f"{username}@t.com", "password": "Passw0rd!",
                })
                ok("注册成功", r.status_code == 201, f"status={r.status_code}")
                r = await client.post("/api/v1/auth/login", data={
                    "username": username, "password": "Passw0rd!",
                })
                ok("登录成功", r.status_code == 200, f"status={r.status_code}")
                token = r.json()["access_token"]
                headers = {"Authorization": "Bearer " + token}
                # 注意：token 的 sub 是 username，真实用户 id 需查库
                async with AsyncSessionLocal() as session:
                    user_row = (
                        await session.execute(select(User).where(User.username == username))
                    ).scalar_one()
                    uid = str(user_row.id)
                ok("token 内 uid 与库中一致",
                   str(_decode_payload(token).get("sub", "")) == username,
                   "sub 应等于 username")

                # 1) 未鉴权 → 401
                r = await client.get("/api/v1/workflows/answers/search", params={"query": "订单"})
                ok("未鉴权 401", r.status_code == 401, f"status={r.status_code}")

                # 2) 缺 query → 422
                r = await client.get("/api/v1/workflows/answers/search", headers=headers)
                ok("缺 query 422", r.status_code == 422, f"status={r.status_code}")

                # 3) 直接调用归档函数：落库 + 触发索引（user_id 透传）
                async with AsyncSessionLocal() as session:
                    parent_id = await workflows_api._archive_run(
                        session,
                        label="任务规划",
                        objective="开发一个订单管理系统",
                        success=True,
                        recap=build_recap(
                            "task_planner_workflow", objective="开发一个订单管理系统", success=True,
                            summary={"total_tasks": 1, "completed_tasks": 1, "failed_tasks": 0},
                        ),
                        detail={"status": "success", "total_tasks": 1},
                        subtasks=[
                            {"seq": 0, "type": "code_generation", "title": "核心接口开发",
                             "status": "completed", "detail": {"result": "实现订单创建接口"}},
                        ],
                        user_id=uid,
                    )
                    ok("归档返回父 task_id", bool(parent_id), str(parent_id))
                    total = (
                        await session.execute(select(func.count()).select_from(Task))
                    ).scalar_one()
                    ok("tasks 表落库 2 条（父+子）", total == 2, f"total={total}")

                ok("索引被调用 1 次", len(recorder.index_calls) == 1,
                   f"calls={len(recorder.index_calls)}")
                call = recorder.index_calls[0]
                ok("索引带 user_id（请求者）", call.get("user_id") == uid, str(call.get("user_id")))
                ok("索引带复盘与子任务",
                   call.get("recap") is not None and len(call.get("subtasks") or []) == 1)
                ok("索引子任务带 task_id", bool((call.get("subtasks") or [{}])[0].get("task_id")))

                # 4) 语义检索端点：参数透传 + 返回结构
                r = await client.get(
                    "/api/v1/workflows/answers/search",
                    params={"query": "上次订单系统任务规划结论", "limit": 3, "workflow_label": "任务规划",
                            "status": "completed"},
                    headers=headers,
                )
                ok("检索端点 200", r.status_code == 200, f"status={r.status_code}")
                body = r.json()
                ok("检索返回结构", body["success"] and body["available"] and body["count"] == 1,
                   json.dumps(body, ensure_ascii=False)[:120])
                ok("检索结果含档案字段",
                   body["results"][0]["task_id"] == "wf-e2e-1"
                   and body["results"][0]["similarity"] == 0.92)
                s_call = recorder.search_calls[-1]
                checks = {
                    "user_id": s_call["user_id"] == uid,
                    "query": s_call["query"].startswith("上次"),
                    "top_k": s_call["top_k"] == 3,
                    "workflow_label": s_call["workflow_label"] == "任务规划",
                    "status": s_call["status"] == "completed",
                }
                ok("检索参数透传",
                   all(checks.values()),
                   f"uid={uid} call={s_call} checks={checks}")

                # 5) 后端不可用 → 优雅降级（available=False，不 5xx）
                workflows_api.workflow_answer_store = RecordingStore(available=False)
                r = await client.get("/api/v1/workflows/answers/search",
                                     params={"query": "订单"}, headers=headers)
                ok("不可用降级 available=False", r.status_code == 200
                   and r.json()["available"] is False and r.json()["count"] == 0,
                   f"status={r.status_code}")
    finally:
        workflows_api.workflow_answer_store = original_store
        # 清理临时库
        if os.path.exists(TEST_DB):
            try:
                os.remove(TEST_DB)
            except OSError:
                pass


async def main():
    await test_build_run_documents()
    await test_store_index_search_and_isolation()
    await test_store_embedding_failure_degrades()
    await run_api_scenario()


if __name__ == "__main__":
    asyncio.run(main())
    print(f"\nPASSED: {len(PASSED)}  FAILED: {len(FAILED)}")
    if FAILED:
        for f in FAILED:
            print(f"  FAILED: {f}")
        sys.exit(1)
    sys.exit(0)
