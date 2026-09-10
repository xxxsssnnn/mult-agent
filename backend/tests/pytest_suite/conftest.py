"""pytest 套件的环境与路径准备。

说明：
- 脚本式回归套件（backend/tests/test_*.py）由 run_tests.ps1 以独立进程逐个执行；
- 本目录是标准 pytest 套件，供 CI 采集覆盖率，二者互不干扰（不在同一发现路径）。
"""
import os
import sys
from pathlib import Path

# 让 `import app.*` 可用（backend 目录加入 sys.path）
BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# 必须早于任何 app 模块导入：固定测试环境，避免误连外部依赖 / 生产配置
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DEBUG", "False")
os.environ.setdefault("SECRET_KEY", "pytest-secret-key-0123456789abcdef-0123456789")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_pytest_smoke.db")
