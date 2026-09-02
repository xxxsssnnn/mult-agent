@echo off
echo ========================================
echo   Multi-Agent Platform - Quick Start
echo ========================================
echo.

REM 检查Docker是否安装
where docker >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Docker is not installed!
    echo Please install Docker Desktop from https://www.docker.com/
    pause
    exit /b 1
)

REM 检查Docker Compose是否安装
where docker-compose >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Docker Compose is not installed!
    pause
    exit /b 1
)

echo [INFO] Starting services with Docker Compose...
echo.

REM 复制环境变量文件（如果不存在）
if not exist backend\.env (
    echo [INFO] Creating .env file from template...
    copy backend\.env.example backend\.env
)

REM 启动Docker服务
docker-compose up -d

if %errorlevel% equ 0 (
    echo.
    echo ========================================
    echo   Services started successfully!
    echo ========================================
    echo.
    echo Backend API: http://localhost:8000
    echo API Docs: http://localhost:8000/docs
    echo Frontend: http://localhost:3000
    echo.
    echo To view logs, run: docker-compose logs -f
    echo To stop services, run: docker-compose down
    echo.
) else (
    echo.
    echo [ERROR] Failed to start services!
    echo Check the error messages above.
    echo.
)

pause
