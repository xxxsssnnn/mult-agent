from fastapi import FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.observability import (
    METRICS_CONTENT_TYPE,
    PrometheusMiddleware,
    collect_readiness,
    render_metrics,
)
from app.api import auth, agents, tasks, workflows, memory, rag
import structlog

# 配置结构化日志
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)

# 创建FastAPI应用
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Enterprise Multi-Agent Collaboration Platform",
    # 生产环境关闭交互式文档与 OpenAPI schema，减少信息暴露面
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)

# 可观测性中间件（先注册，使 CORS 处于更外层，优先处理跨域预检）
app.add_middleware(PrometheusMiddleware)

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(auth.router, prefix=settings.API_V1_PREFIX)
app.include_router(agents.router, prefix=settings.API_V1_PREFIX)
app.include_router(tasks.router, prefix=settings.API_V1_PREFIX)
app.include_router(workflows.router, prefix=settings.API_V1_PREFIX)
app.include_router(memory.router, prefix=settings.API_V1_PREFIX)
app.include_router(rag.router, prefix=settings.API_V1_PREFIX)


@app.get("/")
async def root():
    """根路径"""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running"
    }


@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {
        "status": "healthy",
        "service": settings.APP_NAME
    }


@app.get("/health/live")
async def liveness_probe():
    """存活探针：进程可响应即存活，不检查外部依赖（避免依赖抖动引发误重启）。"""
    return {"status": "alive", "service": settings.APP_NAME}


@app.get("/health/ready")
async def readiness_probe(response: Response):
    """就绪探针：关键依赖不可用时返回 503，供编排 / 负载均衡摘流。"""
    result = await collect_readiness()
    if result["ready"]:
        result["status"] = "ready"
    else:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        result["status"] = "not_ready"
    result["service"] = settings.APP_NAME
    return result


@app.get("/metrics")
async def metrics_endpoint():
    """Prometheus 指标出口（可通过 METRICS_ENABLED 关闭）。"""
    if not settings.METRICS_ENABLED:
        raise HTTPException(status_code=404, detail="metrics disabled")
    return Response(content=render_metrics(), media_type=METRICS_CONTENT_TYPE)


@app.on_event("startup")
async def startup_event():
    """应用启动时执行"""
    # fail fast：生产环境下拒绝默认 JWT 密钥 / 默认数据库口令 / 开启的 DEBUG
    settings.validate()
    logger.info("Application starting up", environment=settings.ENVIRONMENT)
    # 建表兜底（生产环境建议使用 alembic 迁移）
    from app.core.database import init_db
    await init_db()


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时执行"""
    logger.info("Application shutting down")
    # 这里可以清理资源


if __name__ == "__main__":
    import uvicorn

    # B104 精确豁免（容器内需监听所有接口）：外部暴露面由 compose 的端口映射与
    # 网络策略控制（生产仅前端 8080 对外，后端绑定回环），应用侧不做额外收敛。
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",  # nosec B104
        port=8000,
        reload=settings.DEBUG
    )
