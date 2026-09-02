import asyncio
import re
from pathlib import Path
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


BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"


def _alembic_config() -> "Config":
    """构建与工作目录无关的 Alembic 配置"""
    from alembic.config import Config

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return cfg


def run_alembic_upgrade() -> str:
    """同步执行 alembic upgrade head，返回离线 SQL（在线模式为空串）"""
    from alembic import command

    cfg = _alembic_config()
    cfg.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
    return command.upgrade(cfg, "head") or ""


def stamp_alembic_head() -> None:
    """将当前库标记为最新迁移版本（create_all 兜底后的版本对齐）"""
    from alembic import command

    cfg = _alembic_config()
    cfg.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
    command.stamp(cfg, "head")


async def init_db() -> None:
    """启动建表：优先 Alembic 迁移；失败回退 create_all（幂等）并 stamp 版本。

    - 生产（PostgreSQL）：启动时自动应用迁移，含版本管理，无需手动执行命令。
    - 已有 create_all 引导的环境：无 alembic_version 记录，首次迁移会因表已存在
      失败，回退 create_all 后 stamp head，使后续迁移可直接升级。
    - SQLite 本地：PostgreSQL UUID 方言无法建表，保持捕获异常不阻断启动。
    """
    import structlog

    logger = structlog.get_logger(__name__)

    # 1. 优先 Alembic 迁移（生产标准路径）
    try:
        await asyncio.to_thread(run_alembic_upgrade)
        logger.info("database.migration.applied")
        return
    except Exception:
        logger.warning(
            "database.migration.failed",
            hint="回退 create_all 兜底；SQLite 本地不支持 PostgreSQL UUID 建表",
        )

    # 2. 兜底：创建缺失表（幂等，不修改已有表），并对齐迁移版本
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        try:
            await asyncio.to_thread(stamp_alembic_head)
        except Exception:
            logger.warning("database.migration.stamp_failed")
        logger.info("database.init_db.completed")
    except Exception:
        logger.exception(
            "database.init_db.failed",
            hint="若为 SQLite + PostgreSQL UUID 方言兼容问题，请使用 PostgreSQL 或执行 alembic 迁移",
        )
