"""应用冒烟测试（覆盖 app.main 导入与基础端点）。

用 httpx.ASGITransport 直接调用 ASGI 应用，避免触发 startup（不会连数据库）。
"""
import httpx

from app.main import app


async def _get(path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


async def test_health_endpoint():
    resp = await _get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


async def test_root_endpoint():
    resp = await _get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["name"]
