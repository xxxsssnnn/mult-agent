#!/bin/bash

echo "========================================"
echo "  Multi-Agent Platform - Quick Start"
echo "========================================"
echo ""

# 检查Docker是否安装
if ! command -v docker &> /dev/null; then
    echo "[ERROR] Docker is not installed!"
    echo "Please install Docker Desktop from https://www.docker.com/"
    exit 1
fi

# 检查Docker Compose是否安装
if ! command -v docker-compose &> /dev/null; then
    echo "[ERROR] Docker Compose is not installed!"
    exit 1
fi

echo "[INFO] Starting services with Docker Compose..."
echo ""

# 复制环境变量文件（如果不存在）
if [ ! -f backend/.env ]; then
    echo "[INFO] Creating .env file from template..."
    cp backend/.env.example backend/.env
fi

# 启动Docker服务
docker-compose up -d

if [ $? -eq 0 ]; then
    echo ""
    echo "========================================"
    echo "  Services started successfully!"
    echo "========================================"
    echo ""
    echo "Backend API: http://localhost:8000"
    echo "API Docs: http://localhost:8000/docs"
    echo "Frontend: http://localhost:3000"
    echo ""
    echo "To view logs, run: docker-compose logs -f"
    echo "To stop services, run: docker-compose down"
    echo ""
else
    echo ""
    echo "[ERROR] Failed to start services!"
    echo "Check the error messages above."
    echo ""
fi
