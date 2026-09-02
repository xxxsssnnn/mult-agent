Write-Host "Installing Python and Node.js..." -ForegroundColor Cyan

$pythonInstaller = "$env:TEMP\python-3.11.9.exe"
$nodeInstaller = "$env:TEMP\node-v20.11.0-x64.msi"

if (Test-Path $pythonInstaller) {
    Write-Host "Installing Python..." -ForegroundColor Yellow
    Start-Process -FilePath $pythonInstaller -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_test=0" -Wait
    Write-Host "Python installed!" -ForegroundColor Green
} else {
    Write-Host "Python installer not found" -ForegroundColor Red
}

if (Test-Path $nodeInstaller) {
    Write-Host "Installing Node.js..." -ForegroundColor Yellow
    Start-Process -FilePath "msiexec.exe" -ArgumentList "/i", $nodeInstaller, "/quiet", "/norestart" -Wait
    Write-Host "Node.js installed!" -ForegroundColor Green
} else {
    Write-Host "Node.js installer not found" -ForegroundColor Red
}

Write-Host "Done! Close this terminal and open a new one." -ForegroundColor Cyan
