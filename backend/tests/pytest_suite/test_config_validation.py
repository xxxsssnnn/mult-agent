"""生产环境配置校验（Settings.validate）单元测试。"""
import pytest

from app.core.config import Settings


def _prod_settings(**overrides) -> Settings:
    """构造一份合法的生产配置，再按需覆盖字段。"""
    s = Settings()
    s.ENVIRONMENT = "production"
    s.DEBUG = False
    s.SECRET_KEY = "0" * 40
    s.DATABASE_URL = "postgresql+asyncpg://app:StrongPass123!@db:5432/multi_agent"
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


def test_production_valid_config_passes():
    _prod_settings().validate()


def test_production_rejects_debug_enabled():
    with pytest.raises(RuntimeError, match="DEBUG"):
        _prod_settings(DEBUG=True).validate()


def test_production_rejects_default_secret_key():
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        _prod_settings(SECRET_KEY="your-secret-key-change-in-production").validate()


def test_production_rejects_short_secret_key():
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        _prod_settings(SECRET_KEY="too-short").validate()


def test_production_rejects_default_db_password():
    with pytest.raises(RuntimeError, match="数据库口令"):
        _prod_settings(
            DATABASE_URL="postgresql+asyncpg://postgres:postgres@db:5432/multi_agent"
        ).validate()


def test_development_is_lenient():
    s = Settings()
    s.ENVIRONMENT = "development"
    s.DEBUG = True
    s.SECRET_KEY = "your-secret-key-change-in-production"
    s.validate()  # 开发环境不应因弱默认值抛错
