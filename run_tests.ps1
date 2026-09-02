# 记忆系统一键回归测试
# 用法: powershell -ExecutionPolicy Bypass -File run_tests.ps1
# 任一测试套件失败时整体退出码非 0，可用于 CI。
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location "$root\backend"

$env:PYTHONPATH = "$root\backend"
$env:PYTHONIOENCODING = "utf-8"

$py = Join-Path $root "backend\venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "未找到 venv: $py，请先运行 install_dependencies.ps1"
    exit 1
}

$tests = @(
    "tests\test_memory_phase2.py",
    "tests\test_memory_improvement.py",
    "tests\test_memory_retrieval_quality.py",
    "tests\test_memory_short_term_restore.py",
    "tests\test_memory_summary_mock.py",
    "tests\test_memory_extractor_heuristic.py",
    "tests\test_memory_concurrency.py",
    "tests\test_e2e_memory.py",
    "tests\test_migrations.py",
    "tests\test_memory_decay_expiry.py",
    "tests\test_celery_registration.py"
)

$failed = @()
foreach ($t in $tests) {
    Write-Host ""
    Write-Host "=== $t ==="
    & $py $t
    if ($LASTEXITCODE -ne 0) {
        $failed += $t
        Write-Host "FAILED (exit=$LASTEXITCODE): $t"
    }
}

Write-Host ""
if ($failed.Count -gt 0) {
    Write-Host "FAILED suites: $($failed -join ', ')"
    exit 1
}
Write-Host "ALL TEST SUITES PASSED"
exit 0
