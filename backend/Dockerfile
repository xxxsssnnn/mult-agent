# 生产镜像：Python 3.11（与 requirements.lock 生成环境一致）
FROM python:3.11-slim

WORKDIR /app

# 系统依赖（构建期）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# 优先用锁文件安装，保证可复现（requirements.txt 仅为直接依赖声明）
COPY requirements.lock requirements.txt ./
RUN pip install --no-cache-dir -r requirements.lock

# 复制应用代码
COPY . .

# 非 root 用户运行（最小权限）
RUN groupadd --system --gid 10001 appuser \
    && useradd --system --uid 10001 --gid appuser --create-home appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# 生产默认不带 --reload（开发见 Dockerfile.dev）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
