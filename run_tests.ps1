# 统一 CI 测试门禁：绿色放行 / 红色拦截
# 用法: powershell -ExecutionPolicy Bypass -File run_tests.ps1
#       powershell -ExecutionPolicy Bypass -File run_tests.ps1 -Suite test_migrations.py
#       powershell -ExecutionPolicy Bypass -File run_tests.ps1 -TimeoutSeconds 600
# 语义:
#   - 后端: 自动发现 backend/tests/test_*.py 全部套件（防"清单漂移"漏跑新套件），
#     逐个独立进程运行；唯一排除项见 $ExcludedSuites（需外部服务 / 真实 LLM Key 的冒烟脚本）
#   - 稳定性: 每个套件都有独立超时（默认 300s）。超时判定为红灯，并**终止整个进程树**，
#     同时把该套件的 stdout/stderr 落盘到 backend/tests/.gate_logs/ 供事后定位。
#     这堵住了"某个套件挂着不退出 → 门禁永久卡死"的漏洞。
#   - 前端: frontend/node_modules 存在时执行 tsc --noEmit 类型检查 + npm run lint；
#     缺失则明确 SKIP（不误伤纯后端环境）
#   - 任一失败/超时 -> 末尾红灯汇总并 exit 1（红灯，禁止提交/合入）
#   - 全部通过 -> 绿灯 exit 0
#   - 单套件模式同样遵循红灯语义，便于本地调试；-Suite 可显式运行被排除的冒烟脚本
param(
    [string]$Suite = "",
    [int]$TimeoutSeconds = 300
)
# 统一 UTF-8 输出，避免中文汇总在重定向/不同控制台代码页下乱码
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# 注意：PS 5.1 下 native stderr（如 alembic INFO）会变成 ErrorRecord，
# $ErrorActionPreference=Stop 会误中止整个门禁 —— 必须保持 Continue。
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$isWindowsShell = ($IsWindows -or ($PSVersionTable.PSEdition -eq "Desktop"))

# ---- Python 解释器探测（跨平台：venv 优先，回退 PATH 上的 python/python3）----
function Resolve-Python {
    $candidates = @(
        (Join-Path $root "backend\venv\Scripts\python.exe"),
        (Join-Path $root "backend/venv/bin/python"),
        (Join-Path $root ".venv\Scripts\python.exe"),
        (Join-Path $root ".venv/bin/python")
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    foreach ($name in @("python", "python3")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    return $null
}
$venvPy = Resolve-Python
if (-not $venvPy) {
    Write-Host "未找到 Python 解释器（backend\venv 或 PATH），请先运行 install_dependencies.ps1" -ForegroundColor Red
    exit 1
}

$env:PYTHONPATH = Join-Path $root "backend"
$env:PYTHONIOENCODING = "utf-8"

# 离入门禁排除清单：仅可手动 -Suite 运行（注释标明依赖）
$ExcludedSuites = @(
    "test_workflow_v2.py"   # 端到端冒烟脚本，依赖 OPENAI_API_KEY 真实调用
)

# 每个套件/关卡的日志目录（超时或失败后保留，便于定位）
$logDir = Join-Path $root "backend\tests\.gate_logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# ---- 进程树终止：超时后连子进程一起清理，避免遗留孤儿进程 ----
function Stop-ProcessTree {
    param([System.Diagnostics.Process]$Proc)
    if ($null -eq $Proc) { return }
    try {
        if ($isWindowsShell) {
            & taskkill /PID $Proc.Id /T /F *> $null
        } else {
            & pkill -TERM -P $Proc.Id 2>$null
            Start-Sleep -Milliseconds 300
            & pkill -KILL -P $Proc.Id 2>$null
        }
    } catch { }
    try { if (-not $Proc.HasExited) { $Proc.Kill() } } catch { }
}

# ---- 带超时执行一个关卡 ----
#   -Exe/-ArgList: 直接以可执行文件启动（后端 Python）
#   -Command:      经系统 shell 执行（Windows cmd / Unix sh），用于 npm / .cmd 脚本
# 返回 {ok, code, timedOut, seconds, log, errLog}
function Invoke-Gate {
    param(
        [string]$Name,
        [string]$Exe = "",
        [string[]]$ArgList = @(),
        [string]$Command = "",
        [string]$WorkDir = $root
    )
    $safe = ($Name -replace '[^\w\.-]', '_')
    $log = Join-Path $logDir "$safe.log"
    $errLog = Join-Path $logDir "$safe.err.log"
    $sw = [System.Diagnostics.Stopwatch]::StartNew()

    # 用 .NET Process API 而非 Start-Process：
    #   - Start-Process -PassThru 在 PS 5.1 下取不到 ExitCode（得到 $null -> 误判红灯）
    #   - 需要可控超时 + 进程树终止 + 稳定的 stdout/stderr 重定向
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    if ($Command) {
        # 经 shell：.cmd / npm 等脚本无法被 .NET Process 直接执行
        if ($isWindowsShell) {
            $psi.FileName = "cmd.exe"
            $psi.Arguments = "/d /s /c `"$Command`""
        } else {
            $psi.FileName = "/bin/sh"
            $psi.Arguments = "-c `"$Command`""
        }
    } else {
        $psi.FileName = $Exe
        $psi.Arguments = (($ArgList | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' ')
    }
    $psi.WorkingDirectory = $WorkDir
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    $started = $false
    try { $started = $proc.Start() } catch { $started = $false }
    if (-not $started) {
        $sw.Stop()
        return [pscustomobject]@{
            name = $Name; ok = $false; code = 127; timedOut = $false
            seconds = [math]::Round($sw.Elapsed.TotalSeconds, 1); log = $log; errLog = $errLog
        }
    }
    # 异步读流，避免子进程输出塞满缓冲区导致死锁
    $outTask = $proc.StandardOutput.ReadToEndAsync()
    $errTask = $proc.StandardError.ReadToEndAsync()
    $exited = $proc.WaitForExit($TimeoutSeconds * 1000)
    $timedOut = -not $exited
    if ($timedOut) { Stop-ProcessTree $proc }
    $code = if ($timedOut) { 124 } else { $proc.ExitCode }
    # 有界等待流收尾，防止被 kill 的子进程留下未关闭管道导致本函数自身挂起
    $outText = if ($outTask.Wait(8000)) { $outTask.Result } else { "[gate] stdout capture timed out" }
    $errText = if ($errTask.Wait(8000)) { $errTask.Result } else { "[gate] stderr capture timed out" }
    [System.IO.File]::WriteAllText($log, $outText, (New-Object System.Text.UTF8Encoding($false)))
    [System.IO.File]::WriteAllText($errLog, $errText, (New-Object System.Text.UTF8Encoding($false)))
    $sw.Stop()
    return [pscustomobject]@{
        name     = $Name
        ok       = ($code -eq 0)
        code     = $code
        timedOut = $timedOut
        seconds  = [math]::Round($sw.Elapsed.TotalSeconds, 1)
        log      = $log
        errLog   = $errLog
    }
}

function Get-PassSummary {
    param([string]$LogPath)
    if (-not (Test-Path $LogPath)) { return "" }
    return (@(Get-Content -Encoding UTF8 $LogPath | Where-Object { $_ -and $_.Trim() } | Select-Object -Last 1) -join "")
}

function Show-GateFailure {
    param($Gate)
    if ($Gate.timedOut) {
        Write-Host ("        超时: 关卡运行超过 {0}s，已终止进程树（红灯）" -f $TimeoutSeconds) -ForegroundColor Red
    }
    foreach ($p in @($Gate.log, $Gate.errLog)) {
        if (Test-Path $p) { Get-Content -Encoding UTF8 $p | ForEach-Object { Write-Host $_ } }
    }
    Write-Host ("        exit=$($Gate.code)  logs=$($Gate.log)") -ForegroundColor Red
}

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
    Write-Host "== 后端测试套件: 自动发现 $($files.Count) 个 (排除: $($ExcludedSuites -join ', '); 单套件超时 ${TimeoutSeconds}s) =="
}

foreach ($f in $files) {
    # 输出先落持久日志再回放：既规避 PS 5.1 下 2>&1 把 stderr 变 ErrorRecord 的问题，
    # 又在超时/失败时保留现场（日志不删除）
    $gate = Invoke-Gate -Name ("backend/" + $f.BaseName) -Exe $venvPy -ArgList @($f.FullName)
    $flag = if ($gate.ok) { "PASS" } else { "FAIL" }
    Write-Host ("[{0}] {1}  ({2}s)" -f $flag, $f.Name, $gate.seconds) -ForegroundColor $(if ($gate.ok) { "Green" } else { "Red" })
    if ($gate.ok) {
        # 通过：仅回放套件自身末尾的断言汇总行，避免全量刷屏
        Write-Host "        $(Get-PassSummary $gate.log)" -ForegroundColor DarkGray
    } else {
        # 失败/超时：完整回放输出并保留日志路径，便于定位
        Show-GateFailure $gate
    }
    $results += [pscustomobject]@{
        name = $f.Name; ok = $gate.ok; seconds = $gate.seconds
        summary = $(if ($gate.ok) { Get-PassSummary $gate.log } else { $(if ($gate.timedOut) { "TIMEOUT" } else { "FAILED" }) })
    }
}

# ---- 前端类型 + lint 门禁（依赖已安装时才参与判定）----
Write-Host "== 前端类型/lint 门禁 =="
$npmRoot = Join-Path $root "frontend"
if ($Suite) {
    Write-Host "  [SKIP] 单套件调试模式，跳过前端门禁" -ForegroundColor Yellow
} elseif (Test-Path (Join-Path $npmRoot "node_modules")) {
    # 经 shell 执行：.cmd / npm 无法被 .NET Process 直接启动
    if ($isWindowsShell) {
        $tscCmd = "node_modules\.bin\tsc.cmd --noEmit"
    } else {
        $tscCmd = "node_modules/.bin/tsc --noEmit"
    }
    $frontendGates = @(
        @{ Name = "frontend (tsc --noEmit)"; Command = $tscCmd; WorkDir = $npmRoot },
        @{ Name = "frontend (eslint)";       Command = "npm run lint --silent"; WorkDir = $npmRoot }
    )
    foreach ($fg in $frontendGates) {
        $gate = Invoke-Gate -Name $fg.Name -Command $fg.Command -WorkDir $fg.WorkDir
        $flag = if ($gate.ok) { "PASS" } else { "FAIL" }
        Write-Host ("[{0}] {1}  ({2}s)" -f $flag, $fg.Name, $gate.seconds) -ForegroundColor $(if ($gate.ok) { "Green" } else { "Red" })
        if (-not $gate.ok) { Show-GateFailure $gate }
        $results += [pscustomobject]@{
            name = $fg.Name; ok = $gate.ok; seconds = $gate.seconds
            summary = $(if ($gate.ok) { "passed" } else { $(if ($gate.timedOut) { "TIMEOUT" } else { "FAILED" }) })
        }
    }
} else {
    Write-Host "  [SKIP] frontend\node_modules 缺失（未安装前端依赖），跳过前端门禁" -ForegroundColor Yellow
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
