"""数据库迁移与方言兼容性测试（独立运行：python tests/test_migrations.py）

覆盖：
- SQLite 本地开发环境下 alembic 迁移可真实建表（UUID -> CHAR(32)）
- init_db 幂等（alembic no-op / create_all 兜底均不报错）
- sa.Uuid 方言编译：PostgreSQL 原生 UUID，SQLite CHAR(32)
- capabilities 列为 JSON 类型（避免 ARRAY 方言不兼容回归）
"""
import os
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

# 使用独立的临时 SQLite 库，避免触碰本地 multi_agent.db
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./_test_migrations.db"

from app.core.database import (  # noqa: E402
    _alembic_config,
    init_db,
    run_alembic_upgrade,
)
import app.models  # noqa: E402,F401
from app.models.agent import Agent  # noqa: E402
from sqlalchemy import Uuid  # noqa: E402
from sqlalchemy.dialects import postgresql, sqlite  # noqa: E402
from sqlalchemy import JSON  # noqa: E402

PASSED = []
FAILED = []


def check(name, ok):
    if ok:
        PASSED.append(name)
    else:
        FAILED.append(name)
    print(("  [PASS] " if ok else "  [FAIL] ") + name)


def _tables():
    conn = sqlite3.connect("_test_migrations.db")
    try:
        return {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()


def _memory_indexes():
    conn = sqlite3.connect("_test_migrations.db")
    try:
        return {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='memory_entries'"
            )
        }
    finally:
        conn.close()


def _alembic_version():
    conn = sqlite3.connect("_test_migrations.db")
    try:
        rows = conn.execute("SELECT version_num FROM alembic_version").fetchall()
        return rows[0][0] if rows else None
    finally:
        conn.close()


def test_sqlite_migration_creates_tables():
    run_alembic_upgrade()
    tables = _tables()
    check("迁移在 SQLite 下建表成功", "memory_entries" in tables)
    check("含全部业务表", {"users", "agents", "tasks", "conversations", "messages"} <= tables)
    idx = _memory_indexes()
    check("memory_entries 单列索引齐全", {"ix_memory_entries_user_id", "ix_memory_entries_session_id", "ix_memory_entries_memory_type", "ix_memory_entries_expires_at"} <= idx)
    check("memory_entries 复合索引存在", "ix_memory_user_strength_updated" in idx)
    check("归档过滤复合索引存在", "ix_memory_user_archived_strength_updated" in idx)
    check("alembic_version 记录到 head", _alembic_version() == "0002")


def test_init_db_idempotent():
    import asyncio
    asyncio.run(init_db())  # 已迁移的库再次 init_db 不应报错
    check("init_db 幂等（迁移 no-op 路径）", True)


def test_uuid_type_dialects():
    check("PG 下 Uuid 编译为原生 UUID", Uuid().compile(dialect=postgresql.dialect()) == "UUID")
    check("SQLite 下 Uuid 编译为 CHAR(32)", Uuid().compile(dialect=sqlite.dialect()) == "CHAR(32)")


def test_capabilities_is_json():
    col_type = type(Agent.__table__.c.capabilities.type).__name__
    check("capabilities 列为 JSON 类型", col_type == "JSON")


def test_alembic_script_location():
    cfg = _alembic_config()
    loc = cfg.get_main_option("script_location")
    check("alembic script_location 为绝对路径且存在", Path(loc).exists() and Path(loc).is_absolute())


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    # 清理临时库
    for f in ("_test_migrations.db",):
        if os.path.exists(f):
            os.remove(f)
    sys.exit(1 if FAILED else 0)
