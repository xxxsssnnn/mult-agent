"""认证闭环回归测试（独立运行：python tests/test_auth_closure.py）

走进程内真实 ASGI 全链路（含启动迁移 0001→0007），验证：
- /auth/me 返回当前用户；未携带/伪造 token 一律 401
- token 分型：refresh token 不能冒充 Bearer access（否则形同长命后门）
- /auth/refresh：签发新一对；旧 token 重用触发整族吊销（重用检测）
- /auth/logout：吊销本人 refresh（幂等；他人 token 不生效）
- /auth/logout-all：吊销该用户全部会话
"""
import os
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

# 独立临时库，避免触碰本地 multi_agent.db
TEST_DB = "./_test_auth_closure.db"
if os.path.exists(TEST_DB):
    os.remove(TEST_DB)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB}"

import asyncio  # noqa: E402
import httpx  # noqa: E402
from app.main import app  # noqa: E402

PASSED = []
FAILED = []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(
        f"  [{'PASS' if ok else 'FAIL'}] {name}"
        + (f" | {detail}" if not ok and detail else "")
    )


async def _login(client, username, password):
    r = await client.post(
        "/api/v1/auth/login",
        data={"username": username, "password": password},
    )
    if r.status_code != 200:
        return None
    return r.json()


async def run():
    suffix = uuid.uuid4().hex[:8]
    alice_name, alice_pwd = f"alice_{suffix}", "Passw0rd!alice"
    bob_name, bob_pwd = f"bob_{suffix}", "Passw0rd!bob"

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # ---------- 注册 ----------
            r = await client.post("/api/v1/auth/register", json={
                "username": alice_name, "email": f"{alice_name}@test.com", "password": alice_pwd,
            })
            check("注册 alice", r.status_code == 201, f"status={r.status_code}")
            r = await client.post("/api/v1/auth/register", json={
                "username": bob_name, "email": f"{bob_name}@test.com", "password": bob_pwd,
            })
            check("注册 bob", r.status_code == 201, f"status={r.status_code}")
            r = await client.post("/api/v1/auth/register", json={
                "username": alice_name, "email": f"dup_{suffix}@test.com", "password": "x",
            })
            check("重复用户名 400", r.status_code == 400, f"status={r.status_code}")
            r = await client.post("/api/v1/auth/register", json={
                "username": f"dupemail_{suffix}", "email": f"{alice_name}@test.com", "password": "x",
            })
            check("重复邮箱 400", r.status_code == 400, f"status={r.status_code}")

            # ---------- 登录 + /me ----------
            r = await client.post("/api/v1/auth/login", data={
                "username": alice_name, "password": "wrong-pass",
            })
            check("错误密码 401", r.status_code == 401, f"status={r.status_code}")

            rA1 = await _login(client, alice_name, alice_pwd)
            check("alice 登录成功", rA1 is not None)
            rB1 = await _login(client, bob_name, bob_pwd)
            check("bob 登录成功", rB1 is not None)
            if rA1 is None or rB1 is None:
                return
            access_a, refresh_a1 = rA1["access_token"], rA1["refresh_token"]
            access_b = rB1["access_token"]

            r = await client.get("/api/v1/auth/me")
            check("无 token 访问 /me 401", r.status_code == 401, f"status={r.status_code}")
            hA = {"Authorization": "Bearer " + access_a}
            r = await client.get("/api/v1/auth/me", headers=hA)
            check(
                "/me 返回当前用户",
                r.status_code == 200 and r.json().get("username") == alice_name,
                f"status={r.status_code}",
            )

            # ---------- token 分型：refresh 不能当 access 用 ----------
            hRefresh = {"Authorization": "Bearer " + refresh_a1}
            r = await client.get("/api/v1/auth/me", headers=hRefresh)
            check("refresh token 冒充 Bearer → 401", r.status_code == 401, f"status={r.status_code}")

            # ---------- 刷新 + 轮换 + 重用检测 ----------
            r = await client.post("/api/v1/auth/refresh", json={"refresh_token": access_a})
            check("access token 冒充 refresh → 401", r.status_code == 401, f"status={r.status_code}")
            r = await client.post("/api/v1/auth/refresh", json={"refresh_token": "garbage-token"})
            check("伪造 refresh → 401", r.status_code == 401, f"status={r.status_code}")

            r = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_a1})
            check("合法 refresh → 200 新一对", r.status_code == 200, f"status={r.status_code}")
            if r.status_code == 200:
                refresh_a2 = r.json()["refresh_token"]
                access_a2 = r.json()["access_token"]
            else:
                refresh_a2 = access_a2 = None

            r = await client.get(
                "/api/v1/auth/me", headers={"Authorization": "Bearer " + str(access_a2)}
            )
            check("轮换后的 access 可用", r.status_code == 200, f"status={r.status_code}")

            r = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_a1})
            check("重用已轮换的旧 refresh → 401", r.status_code == 401, f"status={r.status_code}")
            r = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_a2})
            check("重用检测整族吊销（新 refresh 也 401）", r.status_code == 401, f"status={r.status_code}")

            # ---------- logout：他人 token 不生效 ----------
            rA3 = await _login(client, alice_name, alice_pwd)
            refresh_a3 = rA3["refresh_token"]
            access_a3 = rA3["access_token"]
            hB = {"Authorization": "Bearer " + access_b}
            r = await client.post(
                "/api/v1/auth/logout",
                json={"refresh_token": refresh_a3}, headers=hB,
            )
            check("bob 用 alice 的 refresh logout → 200（幂等）", r.status_code == 200, f"status={r.status_code}")
            r = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_a3})
            check("alice 会话未被 bob 误杀", r.status_code == 200, f"status={r.status_code}")
            if r.status_code == 200:
                refresh_a4 = r.json()["refresh_token"]
            else:
                refresh_a4 = None

            # ---------- logout：本人注销 ----------
            r = await client.post(
                "/api/v1/auth/logout",
                json={"refresh_token": refresh_a4},
                headers={"Authorization": "Bearer " + access_a3},
            )
            check("本人 logout → 200", r.status_code == 200, f"status={r.status_code}")
            r = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_a4})
            check("logout 后 refresh → 401", r.status_code == 401, f"status={r.status_code}")

            # ---------- logout-all ----------
            rA5 = await _login(client, alice_name, alice_pwd)
            rA6 = await _login(client, alice_name, alice_pwd)
            refresh_a5 = rA5["refresh_token"]
            refresh_a6 = rA6["refresh_token"]
            r = await client.post(
                "/api/v1/auth/logout-all",
                headers={"Authorization": "Bearer " + rA6["access_token"]},
            )
            check("logout-all → 200", r.status_code == 200, f"status={r.status_code}")
            r = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_a5})
            check("logout-all 后会话1 refresh → 401", r.status_code == 401, f"status={r.status_code}")
            r = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_a6})
            check("logout-all 后会话2 refresh → 401", r.status_code == 401, f"status={r.status_code}")


def test_auth_closure():
    asyncio.run(run())


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    sys.exit(1 if FAILED else 0)
