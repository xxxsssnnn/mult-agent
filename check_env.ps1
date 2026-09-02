# 环境检查脚本
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Multi-Agent Platform - Environment Check" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 检查Docker
Write-Host "[1/5] Checking Docker..." -ForegroundColor Yellow
try {
    $dockerVersion = docker --version 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  ✓ Docker installed: $dockerVersion" -ForegroundColor Green
    } else {
        Write-Host "  ✗ Docker not found" -ForegroundColor Red
        Write-Host "  → Download from: https://www.docker.com/products/docker-desktop/" -ForegroundColor Gray
    }
} catch {
    Write-Host "  ✗ Docker not found" -ForegroundColor Red
}

# 检查Docker Compose
Write-Host ""
Write-Host "[2/5] Checking Docker Compose..." -ForegroundColor Yellow
try {
    $composeVersion = docker compose version 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  ✓ Docker Compose available" -ForegroundColor Green
    } else {
        Write-Host "  ✗ Docker Compose not found" -ForegroundColor Red
    }
} catch {
    Write-Host "  ✗ Docker Compose not found" -ForegroundColor Red
}

# 检查Python
Write-Host ""
Write-Host "[3/5] Checking Python..." -ForegroundColor Yellow
try {
    $pythonVersion = python --version 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  ✓ Python installed: $pythonVersion" -ForegroundColor Green
    } else {
        Write-Host "  ✗ Python not found" -ForegroundColor Red
        Write-Host "  → Download from: https://www.python.org/downloads/" -ForegroundColor Gray
    }
} catch {
    Write-Host "  ✗ Python not found" -ForegroundColor Red
}

# 检查Node.js
Write-Host ""
Write-Host "[4/5] Checking Node.js..." -ForegroundColor Yellow
try {
    $nodeVersion = node --version 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  ✓ Node.js installed: $nodeVersion" -ForegroundColor Green
    } else {
        Write-Host "  ✗ Node.js not found" -ForegroundColor Red
        Write-Host "  → Download from: https://nodejs.org/" -ForegroundColor Gray
    }
} catch {
    Write-Host "  ✗ Node.js not found" -ForegroundColor Red
}

# 检查npm
Write-Host ""
Write-Host "[5/5] Checking npm..." -ForegroundColor Yellow
try {
    $npmVersion = npm --version 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  ✓ npm installed: v$npmVersion" -ForegroundColor Green
    } else {
        Write-Host "  ✗ npm not found" -ForegroundColor Red
    }
} catch {
    Write-Host "  ✗ npm not found" -ForegroundColor Red
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Recommendation" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 给出建议
try {
    docker --version 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ Docker is available. Recommended approach:" -ForegroundColor Green
        Write-Host ""
        Write-Host "  cd D:\multi-agent" -ForegroundColor White
        Write-Host "  docker compose up -d" -ForegroundColor White
        Write-Host ""
        Write-Host "This will start all services automatically." -ForegroundColor Gray
    } else {
        Write-Host "✗ Docker not found. Please install required software:" -ForegroundColor Red
        Write-Host ""
        Write-Host "Option 1 (Recommended): Install Docker Desktop" -ForegroundColor Yellow
        Write-Host "  → Easiest way to run the project" -ForegroundColor Gray
        Write-Host "  → https://www.docker.com/products/docker-desktop/" -ForegroundColor Gray
        Write-Host ""
        Write-Host "Option 2: Install Python + Node.js for local development" -ForegroundColor Yellow
        Write-Host "  → More complex setup" -ForegroundColor Gray
        Write-Host "  → Requires manual dependency installation" -ForegroundColor Gray
    }
} catch {
    Write-Host "Please install Docker Desktop or Python + Node.js" -ForegroundColor Red
}

Write-Host ""
Write-Host "Press any key to exit..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
