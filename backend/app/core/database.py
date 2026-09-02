import re
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from app.core.config import settings

# Check if using SQLite
is_sqlite = bool(re.search(r'sqlite\+', settings.DATABASE_URL))

# Configure engine based on database type
if is_sqlite:
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=settings.DATABASE_ECHO
    )
else:
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=settings.DATABASE_ECHO,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20
    )

AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

Base = declarative_base()


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """启动建表兜底：自动创建缺失的表（幂等）。

    生产环境推荐使用 alembic 迁移（backend/alembic），此处作为兜底保证
    本地开发与测试环境开箱即用。已存在的表不会被修改。
    """
    import structlog
    logger = structlog.get_logger(__name__)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("database.init_db.completed")
    except Exception:
        logger.exception(
            "database.init_db.failed",
            hint="若为 SQLite + PostgreSQL UUID 方言兼容问题，请使用 PostgreSQL 或执行 alembic 迁移",
        )
