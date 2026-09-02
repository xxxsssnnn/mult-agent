Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Multi-Agent Platform - Auto Installer" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "[1/3] Checking Python..." -ForegroundColor Yellow
try {
    python --version 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $ver = python --version
        Write-Host "  Python already installed: $ver" -ForegroundColor Green
    } else {
        Write-Host "  Python not found" -ForegroundColor Red
        Write-Host "  Please install from: https://www.python.org/downloads/" -ForegroundColor Yellow
    }
} catch {
    Write-Host "  Python not found" -ForegroundColor Red
    Write-Host "  Please install from: https://www.python.org/downloads/" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[2/3] Checking Node.js..." -ForegroundColor Yellow
try {
    node --version 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $ver = node --version
        Write-Host "  Node.js already installed: $ver" -ForegroundColor Green
    } else {
        Write-Host "  Node.js not found" -ForegroundColor Red
        Write-Host "  Please install from: https://nodejs.org/" -ForegroundColor Yellow
    }
} catch {
    Write-Host "  Node.js not found" -ForegroundColor Red
    Write-Host "  Please install from: https://nodejs.org/" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[3/3] Checking Docker..." -ForegroundColor Yellow
try {
    docker --version 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $ver = docker --version
        Write-Host "  Docker already installed: $ver" -ForegroundColor Green
    } else {
        Write-Host "  Docker not found (optional)" -ForegroundColor Gray
        Write-Host "  Optional: https://www.docker.com/products/docker-desktop/" -ForegroundColor Gray
    }
} catch {
    Write-Host "  Docker not found (optional)" -ForegroundColor Gray
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Next Steps" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "After installing required software:" -ForegroundColor White
Write-Host ""
Write-Host "1. Close and reopen this terminal" -ForegroundColor Yellow
Write-Host ""
Write-Host "2. Setup backend:" -ForegroundColor White
Write-Host "   cd D:\multi-agent\backend" -ForegroundColor Gray
Write-Host "   python -m venv venv" -ForegroundColor Gray
Write-Host "   .\venv\Scripts\Activate.ps1" -ForegroundColor Gray
Write-Host "   pip install -r requirements.txt" -ForegroundColor Gray
Write-Host ""
Write-Host "3. Setup frontend (new terminal):" -ForegroundColor White
Write-Host "   cd D:\multi-agent\frontend" -ForegroundColor Gray
Write-Host "   npm install" -ForegroundColor Gray
Write-Host ""
Write-Host "See SETUP_GUIDE.md for detailed instructions." -ForegroundColor Gray
Write-Host ""
