"""可观测性：Prometheus 指标 + 依赖就绪探针。

职责：
- 指标采集：以 ASGI 中间件自动记录 HTTP 请求数 / 耗时 / 在途请求，暴露给 /metrics。
- 就绪探针：检查关键外部依赖（数据库，按需 Redis）连通性，供 /health/ready 判定是否可接流量。

设计要点：
- 标签基数受控：path 标签只取路由模板（如 /api/v1/tasks/{task_id}），未匹配路由归一为常量，
  避免 UUID 类路径把指标标签打爆。
- 依赖判定分级：数据库始终为关键依赖；Redis 仅在显式配置为唯一短期记忆后端时才视为关键
  （auto 模式下 Redis 不可用会降级内存，不应导致整个实例被判未就绪）。
"""
from __future__ import annotations

import time
from typing import Any, Dict

import structlog
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings

logger = structlog.get_logger(__name__)

# 不纳入 HTTP 指标统计的路径（避免 /metrics 自计数）
_METRICS_EXCLUDED_PATHS = {"/metrics"}

REQUEST_COUNT = Counter(
    "http_requests_total",
    "HTTP 请求总数",
    ["method", "path", "status"],
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP 请求处理耗时（秒）",
    ["method", "path"],
)
IN_FLIGHT = Gauge(
    "http_requests_in_flight",
    "当前正在处理的 HTTP 请求数",
)
DEPENDENCY_UP = Gauge(
    "dependency_up",
    "外部依赖可用性（1=可用，0=不可用）",
    ["dependency"],
)


def _resolve_path(scope: Scope) -> str:
    """把请求归一为路由模板，控制指标标签基数。"""
    route = scope.get("route")
    template = getattr(route, "path", None)
    if template:
        return str(template)
    # 未匹配到路由（404 等）：原始路径可能含任意内容，归一到常量
    return "unmatched"


class PrometheusMiddleware:
    """纯 ASGI 中间件：记录请求数、耗时与在途请求，不干扰正常响应流。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not settings.METRICS_ENABLED:
            await self.app(scope, receive, send)
            return

        if scope.get("path") in _METRICS_EXCLUDED_PATHS:
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        start = time.perf_counter()
        status_code = {"value": 500}

        async def _send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_code["value"] = message["status"]
            await send(message)

        IN_FLIGHT.inc()
        try:
            await self.app(scope, receive, _send_wrapper)
        finally:
            IN_FLIGHT.dec()
            path = _resolve_path(scope)
            REQUEST_COUNT.labels(
                method=method, path=path, status=str(status_code["value"])
            ).inc()
            REQUEST_LATENCY.labels(method=method, path=path).observe(
                time.perf_counter() - start
            )


def render_metrics() -> bytes:
    """生成 Prometheus 文本格式指标。"""
    return generate_latest()


METRICS_CONTENT_TYPE = CONTENT_TYPE_LATEST


async def _check_database() -> bool:
    from sqlalchemy import text

    from app.core.database import engine

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.warning("readiness.database.unreachable", exc_info=False)
        return False


async def _check_redis() -> bool:
    from redis.asyncio import Redis

    client = Redis.from_url(
        settings.REDIS_URL,
        socket_connect_timeout=1,
        socket_timeout=2,
    )
    try:
        return bool(await client.ping())
    except Exception:
        logger.warning("readiness.redis.unreachable", exc_info=False)
        return False
    finally:
        try:
            await client.aclose()
        except AttributeError:
            await client.close()
        except Exception:
            logger.warning("readiness.redis.close_failed", exc_info=False)


def _critical_dependencies() -> set:
    """确定关键依赖集合。

    - 数据库：始终关键（业务数据唯一来源）。
    - Redis：仅当短期记忆被显式锁定为 redis 后端时关键；auto 模式下可降级内存。
    """
    critical = {"database"}
    if str(settings.MEMORY_SHORT_TERM_STORE).strip().lower() == "redis":
        critical.add("redis")
    return critical


async def collect_readiness() -> Dict[str, Any]:
    """采集依赖就绪状态，返回 {"ready": bool, "dependencies": {...}}。"""
    db_ok = await _check_database()
    redis_ok = await _check_redis()

    DEPENDENCY_UP.labels(dependency="database").set(1 if db_ok else 0)
    DEPENDENCY_UP.labels(dependency="redis").set(1 if redis_ok else 0)

    deps = {"database": db_ok, "redis": redis_ok}
    critical = _critical_dependencies()
    ready = all(deps[name] for name in critical)
    return {"ready": ready, "dependencies": deps, "critical": sorted(critical)}
