# 统一 CI 测试门禁：绿色放行 / 红色拦截
# 用法: powershell -ExecutionPolicy Bypass -File run_tests.ps1
#       powershell -ExecutionPolicy Bypass -File run_tests.ps1 -Suite test_migrations.py
# 语义:
#   - 后端: 自动发现 backend/tests/test_*.py 全部套件（防"清单漂移"漏跑新套件），
#     逐个独立进程运行；唯一排除项见 $ExcludedSuites（需外部服务 / 真实 LLM Key 的冒烟脚本）
#   - 前端: frontend/node_modules 存在时执行 tsc --noEmit 类型门禁；缺失则明确 SKIP（不误伤纯后端环境）
#   - 任一失败 -> 末尾红灯汇总并 exit 1（红灯，禁止提交/合入）
#   - 全部通过 -> 绿灯 exit 0
#   - 单套件模式同样遵循红灯语义，便于本地调试；-Suite 可显式运行被排除的冒烟脚本
param(
    [string]$Suite = ""
)
# 统一 UTF-8 输出，避免中文汇总在重定向/不同控制台代码页下乱码
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# 注意：PS 5.1 下 native stderr（如 alembic INFO）会变成 ErrorRecord，
# $ErrorActionPreference=Stop 会误中止整个门禁 —— 必须保持 Continue。
# 套件/文件发现的关键操作均已显式 -ErrorAction Stop。
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPy = Join-Path $root "backend\venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "未找到 venv: $venvPy，请先运行 install_dependencies.ps1" -ForegroundColor Red
    exit 1
}

$env:PYTHONPATH = Join-Path $root "backend"
$env:PYTHONIOENCODING = "utf-8"

# 离入门禁排除清单：仅可手动 -Suite 运行（注释标明依赖）
$ExcludedSuites = @(
    "test_workflow_v2.py"   # 端到端冒烟脚本，依赖 OPENAI_API_KEY 真实调用
)

$testsDir = Join-Path $root "backend\tests"
if ($Suite) {
    $files = @(Get-Item (Join-Path $testsDir $Suite) -ErrorAction Stop)
} else {
    $files = @(
        Get-ChildItem $testsDir -Filter "test_*.py" |
            Sort-Object Name |
            Where-Object { $_.Name -notin $ExcludedSuites }
    )
}

$results = @()   # 每项: name / ok / seconds / summary
$total = [System.Diagnostics.Stopwatch]::StartNew()

if ($Suite) {
    Write-Host "== 后端测试套件: 单套件 $($files[0].Name) =="
} else {
    Write-Host "== 后端测试套件: 自动发现 $($files.Count) 个 (排除: $($ExcludedSuites -join ', ')) =="
}

foreach ($f in $files) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    # 输出先落临时日志再回放：规避 PS 5.1 下 2>&1 把 stderr 变 ErrorRecord 导致
    # $ErrorActionPreference=Stop 时误中断管线的问题；exit code 仍可靠取自 $LASTEXITCODE
    $tmpLog = Join-Path $env:TEMP ("gate_" + $f.BaseName + "_" + [guid]::NewGuid().ToString("N") + ".log")
    & $venvPy $f.FullName *> $tmpLog
    $sw.Stop()
    $code = $LASTEXITCODE
    $lines = @(Get-Content $tmpLog)
    Remove-Item $tmpLog -Force -ErrorAction SilentlyContinue

    $secs = [math]::Round($sw.Elapsed.TotalSeconds, 1)
    $summaryLine = ($lines | Select-Object -Last 1) -join ""
    $ok = ($code -eq 0)
    $flag = if ($ok) { "PASS" } else { "FAIL" }
    Write-Host ("[{0}] {1}  ({2}s)" -f $flag, $f.Name, $secs) -ForegroundColor $(if ($ok) { "Green" } else { "Red" })
    if ($ok) {
        # 通过：仅回放套件自身末尾的断言汇总行，避免全量刷屏
        Write-Host "        $summaryLine" -ForegroundColor DarkGray
    } else {
        # 失败：完整回放输出便于定位
        $lines | ForEach-Object { Write-Host $_ }
        Write-Host ("        exit=$code") -ForegroundColor Red
    }
    $results += [pscustomobject]@{ name = $f.Name; ok = $ok; seconds = $secs; summary = $summaryLine }
}

# ---- 前端类型门禁（依赖已安装时才参与判定）----
Write-Host "== 前端类型门禁 =="
$npmRoot = Join-Path $root "frontend"
if (Test-Path (Join-Path $npmRoot "node_modules")) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $tmpLog = Join-Path $env:TEMP ("gate_frontend_" + [guid]::NewGuid().ToString("N") + ".log")
    Push-Location $npmRoot
    try {
        & npx.cmd tsc --noEmit *> $tmpLog
        $frontendOk = ($LASTEXITCODE -eq 0)
    } finally {
        Pop-Location
    }
    $sw.Stop()
    $lines = @(Get-Content $tmpLog)
    Remove-Item $tmpLog -Force -ErrorAction SilentlyContinue
    if (-not $frontendOk) {
        $lines | Select-Object -Last 10 | ForEach-Object { Write-Host $_ }
    }
    $results += [pscustomobject]@{
        name = "frontend (tsc --noEmit)"
        ok = $frontendOk
        seconds = [math]::Round($sw.Elapsed.TotalSeconds, 1)
        summary = $(if ($frontendOk) { "type check passed" } else { "type check FAILED" })
    }
} else {
    Write-Host "  [SKIP] frontend\node_modules 缺失（未安装前端依赖），跳过类型门禁" -ForegroundColor Yellow
}

# ---- 汇总与红灯/绿灯 ----
$total.Stop()
$failed = @($results | Where-Object { -not $_.ok })
Write-Host ""
Write-Host ("== 门禁汇总: {0}/{1} 通过 (总耗时 {2}s) ==" -f ($results.Count - $failed.Count), $results.Count, [math]::Round($total.Elapsed.TotalSeconds, 1))
foreach ($r in $results) {
    $mark = if ($r.ok) { "PASS" } else { "FAIL" }
    Write-Host ("  [{0}] {1} ({2}s) | {3}" -f $mark, $r.name, $r.seconds, $r.summary) -ForegroundColor $(if ($r.ok) { "Green" } else { "Red" })
}
if ($failed.Count -gt 0) {
    Write-Host "红灯: 以下未通过，禁止提交/合入 -> $($failed.name -join ', ')" -ForegroundColor Red
} else {
    Write-Host "绿灯: 全部通过，可提交/合入" -ForegroundColor Green
}

# 机器可读 UTF-8 结果文件（供 CI/外层回读，规避控制台代码页与重定向转码问题）
$summaryLines = @()
$summaryLines += ("== 门禁汇总: {0}/{1} 通过 (总耗时 {2}s) ==" -f ($results.Count - $failed.Count), $results.Count, [math]::Round($total.Elapsed.TotalSeconds, 1))
foreach ($r in $results) {
    $mark = if ($r.ok) { "PASS" } else { "FAIL" }
    $summaryLines += ("  [{0}] {1} ({2}s) | {3}" -f $mark, $r.name, $r.seconds, $r.summary)
}
if ($failed.Count -gt 0) {
    $summaryLines += "红灯: 以下未通过，禁止提交/合入 -> $($failed.name -join ', ')"
} else {
    $summaryLines += "绿灯: 全部通过，可提交/合入"
}
$summaryLines | Out-File -FilePath (Join-Path $root "_gate_result.txt") -Encoding utf8

if ($failed.Count -gt 0) {
    exit 1
}
exit 0
