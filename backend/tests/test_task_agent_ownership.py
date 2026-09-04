"""Agent/Task 租户隔离（所有权）回归测试（独立运行：python tests/test_task_agent_ownership.py）

直接调用 API 层函数（以 current_user 身份驱动，绕过 HTTP 认证层），
验证多用户交叉访问下的隔离语义：
- 非 admin 用户 list/get/update/delete/cancel/execute 只能命中本人资源，
  越权访问一律 404（不泄露资源存在性）
- 创建 Agent/Task 强制写入 user_id 归属
- 创建 Task 引用他人 Agent 一律 404
- 存量无主数据（user_id IS NULL）：普通用户不可见，仅 admin 全量可见
"""
import asyncio
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

_DB_FILE = "_test_task_agent_ownership.db"
if os.path.exists(_DB_FILE):
    os.remove(_DB_FILE)
# 独立临时 SQLite，避免触碰本地 multi_agent.db
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///./{_DB_FILE}"

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import app.models  # noqa: E402,F401  注册全部 ORM 表
from app.core.database import Base  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.agent import Agent  # noqa: E402
from app.models.task import Task  # noqa: E402
from app.schemas.agent import AgentCreate, AgentUpdate  # noqa: E402
from app.schemas.task import TaskCreate, TaskUpdate  # noqa: E402
from app.api.agents import (  # noqa: E402
    create_agent,
    delete_agent,
    execute_agent,
    get_agent,
    list_agents,
    update_agent,
)
from app.api.tasks import (  # noqa: E402
    cancel_task,
    create_task,
    delete_task,
    get_task,
    list_tasks,
    update_task,
)

PASSED = []
FAILED = []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(
        f"  [{'PASS' if ok else 'FAIL'}] {name}"
        + (f" | {detail}" if not ok and detail else "")
    )


def _mkuser(name, role="user"):
    return User(
        username=name,
        email=f"{name}@test.local",
        password_hash="password-placeholder",
        role=role,
    )


async def _expect_404(awaitable, name):
    try:
        await awaitable
        check(name, False, "未抛出 HTTPException")
    except HTTPException as exc:
        check(name, exc.status_code == 404, f"期望 404 实得 {exc.status_code}")


async def _scenario(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as db:
        alice = _mkuser("alice")
        bob = _mkuser("bob")
        admin = _mkuser("root", role="admin")
        db.add_all([alice, bob, admin])
        await db.commit()

        # ----------------- Agent 归属与隔离 -----------------
        agent_a = await create_agent(
            AgentCreate(name="alice-agent", type="general"), db, alice
        )
        await db.commit()
        check("创建 Agent 写入 user_id 归属", agent_a.user_id == alice.id)

        agent_b = await create_agent(
            AgentCreate(name="bob-agent", type="general"), db, bob
        )
        await db.commit()
        check("bob 的 Agent 归属 bob", agent_b.user_id == bob.id)

        lst = await list_agents(db=db, current_user=alice)
        check("alice list 只见自己的 Agent", len(lst) == 1 and lst[0].id == agent_a.id)
        lst = await list_agents(db=db, current_user=bob)
        check("bob list 只见自己的 Agent", len(lst) == 1 and lst[0].id == agent_b.id)

        await _expect_404(get_agent(agent_a.id, db, bob), "bob 读取 alice 的 Agent → 404")
        await _expect_404(
            update_agent(agent_a.id, AgentUpdate(name="hijack"), db, bob),
            "bob 更新 alice 的 Agent → 404",
        )
        await _expect_404(delete_agent(agent_a.id, db, bob), "bob 删除 alice 的 Agent → 404")
        await _expect_404(
            execute_agent(agent_a.id, {"input": "hi"}, db, bob),
            "bob 执行 alice 的 Agent → 404",
        )

        got = await get_agent(agent_a.id, db, alice)
        check("alice 可读取自己的 Agent", got is not None and got.id == agent_a.id)

        # ----------------- Task 归属与隔离 -----------------
        task_a = await create_task(
            TaskCreate(title="alice-task", agent_id=agent_a.id), db, alice
        )
        await db.commit()
        check("创建 Task 写入 user_id 归属", task_a.user_id == alice.id)

        await _expect_404(
            create_task(TaskCreate(title="borrow", agent_id=agent_a.id), db, bob),
            "bob 引用 alice 的 Agent 创建任务 → 404",
        )
        task_free = await create_task(TaskCreate(title="alice-noagent"), db, alice)
        await db.commit()
        check("无 Agent 引用也可创建任务", task_free.user_id == alice.id)

        lst = await list_tasks(db=db, current_user=alice)
        check("alice list 只见自己的任务", len(lst) == 2)
        lst = await list_tasks(db=db, current_user=bob)
        check("bob list 看不到 alice 的任务", len(lst) == 0)

        await _expect_404(get_task(task_a.id, db, bob), "bob 读取 alice 的任务 → 404")
        await _expect_404(
            update_task(task_a.id, TaskUpdate(title="hijack"), db, bob),
            "bob 更新 alice 的任务 → 404",
        )
        await _expect_404(cancel_task(task_a.id, db, bob), "bob 取消 alice 的任务 → 404")
        await _expect_404(delete_task(task_a.id, db, bob), "bob 删除 alice 的任务 → 404")

        got = await get_task(task_a.id, db, alice)
        check("alice 可读取自己的任务", got is not None and got.id == task_a.id)
        upd = await update_task(task_a.id, TaskUpdate(status="completed"), db, alice)
        check("alice 可更新自己的任务", upd.status == "completed")

        # ----------------- 存量无主数据（隔离前遗留） -----------------
        legacy_agent = Agent(name="legacy-agent", type="general", user_id=None)
        legacy_task = Task(
            task_id="legacy-task-0001", title="legacy-task", user_id=None, status="pending"
        )
        db.add_all([legacy_agent, legacy_task])
        await db.commit()

        lst = await list_agents(db=db, current_user=alice)
        check("普通用户不可见无主 Agent", len(lst) == 1)
        lst = await list_tasks(db=db, current_user=bob)
        check("普通用户不可见无主任务", len(lst) == 0)
        await _expect_404(get_task(legacy_task.id, db, bob), "bob 读取无主任务 → 404")
        await _expect_404(get_agent(legacy_agent.id, db, alice), "alice 读取无主 Agent → 404")

        lst = await list_agents(db=db, current_user=admin)
        check("admin 可见全部 Agent（含无主）", len(lst) == 3)
        lst = await list_tasks(db=db, current_user=admin)
        check("admin 可见全部任务（含无主）", len(lst) == 3)
        got = await get_task(legacy_task.id, db, admin)
        check("admin 可读取无主任务", got is not None)

        # ----------------- 属主删除语义 -----------------
        await delete_task(task_free.id, db, alice)
        await db.commit()
        await _expect_404(get_task(task_free.id, db, alice), "属主删除后自己查不到 → 404")

        await delete_agent(agent_b.id, db, bob)
        await db.commit()
        await _expect_404(get_agent(agent_b.id, db, bob), "属主删除后自己查不到 → 404")

        # ----------------- workflow 归档写入属主（隐藏泄漏口） -----------------
        from app.api.workflows import _archive_run  # noqa: E402

        archived_task_id = await _archive_run(
            db,
            label="archived-test",
            objective="验证归档归属",
            success=True,
            recap={"summary": "ok"},
            detail={"detail": "ok"},
            subtasks=[{"type": "step", "seq": 1, "title": "t1", "status": "completed"}],
            user_id=str(alice.id),
        )
        await db.commit()
        parent = (
            await db.execute(select(Task).where(Task.task_id == archived_task_id))
        ).scalar_one_or_none()
        check(
            "workflow 归档父行写入属主",
            archived_task_id is not None and parent is not None and parent.user_id == alice.id,
        )
        sub = (
            await db.execute(
                select(Task).where(Task.task_id == f"{archived_task_id}-001")
            )
        ).scalar_one_or_none()
        check(
            "workflow 归档子行也写入属主",
            sub is not None and sub.user_id == alice.id,
        )


def test_ownership_isolation():
    async def _main():
        engine = create_async_engine(f"sqlite+aiosqlite:///./{_DB_FILE}")
        try:
            await _scenario(engine)
        finally:
            await engine.dispose()

    asyncio.run(_main())


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if os.path.exists(_DB_FILE):
        os.remove(_DB_FILE)
    sys.exit(1 if FAILED else 0)
