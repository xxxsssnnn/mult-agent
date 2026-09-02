# 一键安装环境脚本
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Multi-Agent Platform - One-Click Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 设置TLS 1.2
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

Write-Host "[Step 1/2] Downloading Python 3.11..." -ForegroundColor Yellow
$pythonUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
$pythonInstaller = "$env:TEMP\python-3.11.9.exe"

try {
    if (Test-Path $pythonInstaller) {
        Write-Host "  Python installer already exists" -ForegroundColor Gray
    } else {
        Write-Host "  Downloading from $pythonUrl" -ForegroundColor Gray
        Invoke-WebRequest -Uri $pythonUrl -OutFile $pythonInstaller -UseBasicParsing
        Write-Host "  Download completed!" -ForegroundColor Green
    }
} catch {
    Write-Host "  Download failed: $_" -ForegroundColor Red
    Write-Host "  Please manually download from: https://www.python.org/downloads/" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "[Step 2/2] Downloading Node.js LTS..." -ForegroundColor Yellow
$nodeUrl = "https://nodejs.org/dist/v20.11.0/node-v20.11.0-x64.msi"
$nodeInstaller = "$env:TEMP\node-v20.11.0-x64.msi"

try {
    if (Test-Path $nodeInstaller) {
        Write-Host "  Node.js installer already exists" -ForegroundColor Gray
    } else {
        Write-Host "  Downloading from $nodeUrl" -ForegroundColor Gray
        Invoke-WebRequest -Uri $nodeUrl -OutFile $nodeInstaller -UseBasicParsing
        Write-Host "  Download completed!" -ForegroundColor Green
    }
} catch {
    Write-Host "  Download failed: $_" -ForegroundColor Red
    Write-Host "  Please manually download from: https://nodejs.org/" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Downloads Complete!" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor White
Write-Host ""
Write-Host "1. Install Python:" -ForegroundColor Yellow
Write-Host "   Double-click: $pythonInstaller" -ForegroundColor Gray
Write-Host "   IMPORTANT: Check 'Add Python to PATH' during installation!" -ForegroundColor Red
Write-Host ""
Write-Host "2. Install Node.js:" -ForegroundColor Yellow
Write-Host "   Double-click: $nodeInstaller" -ForegroundColor Gray
Write-Host "   Use default settings" -ForegroundColor Gray
Write-Host ""
Write-Host "3. After installation, close this terminal and open a new one" -ForegroundColor Yellow
Write-Host ""
Write-Host "4. Run the setup script:" -ForegroundColor Yellow
Write-Host "   cd D:\multi-agent\backend" -ForegroundColor Gray
Write-Host "   python -m venv venv" -ForegroundColor Gray
Write-Host "   .\venv\Scripts\Activate.ps1" -ForegroundColor Gray
Write-Host "   pip install -r requirements.txt" -ForegroundColor Gray
Write-Host ""
Write-Host "Opening download folder..." -ForegroundColor Cyan
explorer $env:TEMP

Write-Host ""
Write-Host "Press any key to open the folder and exit..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
