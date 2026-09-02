"""端到端测试：对话 → 记忆沉淀 → 跨会话记忆复用（独立运行：python tests/test_e2e_memory.py）

走进程内真实 ASGI 全链路（认证、路由、依赖注入、DB、Chroma 降级、内联 consolidation），
使用独立临时 SQLite 库，不污染本地 multi_agent.db。
"""
import os
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

# 独立临时库，避免触碰本地 multi_agent.db
TEST_DB = "./_test_e2e.db"
for f in ("_test_e2e.db",):
    if os.path.exists(f):
        os.remove(f)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB}"

import asyncio  # noqa: E402
import httpx  # noqa: E402
from app.main import app  # noqa: E402

PASSED = []
FAILED = []


def check(name, ok, detail=""):
    if ok:
        PASSED.append(name)
    else:
        FAILED.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" | {detail}" if not ok else ""))


MSGS = [
    "你好，我叫李雷，是一名产品经理。",
    "我主要负责公司内部的 SaaS 产品设计。",
    "我每周三会参加产品评审会，和大家讨论需求。",
    "最近我在学习弹吉他，买了把民谣吉他。",
    "我的咖啡偏好是美式，每天早晨都要喝一杯。",
    "我养了一只叫旺财的柯基犬。",
]


async def run():
    suffix = uuid.uuid4().hex[:8]
    username = f"e2e_{suffix}"
    password = "Passw0rd!"

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. 注册 + 登录
            r = await client.post("/api/v1/auth/register", json={
                "username": username, "email": f"{username}@test.com", "password": password,
            })
            check("注册成功", r.status_code == 201, f"status={r.status_code}")
            r = await client.post("/api/v1/auth/login", data={
                "username": username, "password": password,
            })
            check("登录成功", r.status_code == 200, f"status={r.status_code}")
            token = r.json()["access_token"]
            headers = {"Authorization": "Bearer " + token}

            # 2. 会话1：6 条消息
            r = await client.post("/api/v1/memory/session", headers=headers, json={})
            check("创建会话1", r.status_code == 200, f"status={r.status_code}")
            sid1 = r.json()["session_id"]
            for i, m in enumerate(MSGS):
                r = await client.post(
                    f"/api/v1/memory/{sid1}/message",
                    headers=headers, json={"role": "user", "content": m},
                )
                check(f"会话1 第{i + 1}条消息", r.status_code == 200, f"status={r.status_code}")

            # 3. 跨会话检索：咖啡偏好应被提取为结构化记忆
            r = await client.get("/api/v1/memory/entries", params={"query": "咖啡"}, headers=headers)
            check("记忆检索接口正常", r.status_code == 200, f"status={r.status_code}")
            data = r.json()
            entries = data.get("entries") or []
            types = {e.get("memory_type") for e in entries}
            check("检索到沉淀记忆", len(entries) >= 1, f"count={len(entries)}")
            check(
                "含结构化记忆（preference/event）",
                bool({"preference", "event"} & types),
                f"types={types}",
            )
            pref = next((e for e in entries if e.get("memory_type") == "preference"), None)
            check("咖啡偏好记忆被提取", bool(pref and "咖啡" in pref.get("content", "")), f"pref={pref}")

            # 4. 会话2：跨会话记忆注入上下文
            r = await client.post("/api/v1/memory/session", headers=headers, json={})
            check("创建会话2", r.status_code == 200, f"status={r.status_code}")
            sid2 = r.json()["session_id"]
            r = await client.post(
                f"/api/v1/memory/{sid2}/message",
                headers=headers, json={"role": "user", "content": "帮我推荐一款咖啡豆"},
            )
            check("会话2 发消息", r.status_code == 200, f"status={r.status_code}")
            context = r.json().get("context_preview", "")
            check(
                "会话2 上下文注入跨会话记忆",
                "Relevant Memories" in context and "咖啡" in context,
                f"ctx={context[:160]}",
            )


if __name__ == "__main__":
    asyncio.run(run())
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for f in ("_test_e2e.db",):
        if os.path.exists(f):
            os.remove(f)
    sys.exit(1 if FAILED else 0)
