"""可观测性测试：存活/就绪探针与 Prometheus 指标出口。

用 httpx.ASGITransport 直接调用 ASGI 应用，不触发 startup（不连库建表）。
就绪探针在测试环境下走 sqlite，`SELECT 1` 可通，故预期 ready。
"""
import httpx

from app.main import app


async def _get(path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


async def test_liveness_probe():
    resp = await _get("/health/live")
    assert resp.status_code == 200
    assert resp.json()["status"] == "alive"


async def test_readiness_probe_reports_dependencies():
    resp = await _get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["dependencies"]["database"] is True
    assert "redis" in body["dependencies"]
    assert body["critical"] == ["database"]


async def test_readiness_redis_not_critical_by_default():
    """auto 模式下 Redis 非关键依赖，其不可用不应把实例判为未就绪。"""
    resp = await _get("/health/ready")
    body = resp.json()
    assert "redis" not in body["critical"]


async def test_metrics_endpoint_exposes_http_metrics():
    # 先制造样本：一次就绪探针（含依赖指标）+ 一次存活探针
    await _get("/health/ready")
    await _get("/health/live")

    resp = await _get("/metrics")
    assert resp.status_code == 200
    body = resp.text
    assert "http_requests_total" in body
    assert "http_request_duration_seconds" in body
    assert "http_requests_in_flight" in body
    assert "dependency_up" in body


async def test_metrics_path_label_is_route_template():
    await _get("/health/live")
    resp = await _get("/metrics")
    # 指标 path 标签取路由模板（非原始 URL），避免高基数
    assert 'path="/health/live"' in resp.text


async def test_metrics_endpoint_not_self_counted():
    await _get("/metrics")
    resp = await _get("/metrics")
    # /metrics 自身不计入请求指标
    assert 'path="/metrics"' not in resp.text
