# MAA Auto Farm v4 - Dual MAA + 槽位切号（非点击）
# -NoShutdown: GUI 手动运行传入，跳过「早班成功后关机」；计划任务不传，行为不变
# -SkipMAA: 测试切号流程用——跳过 MAA、结束时保留模拟器运行供检查
param([switch]$NoShutdown, [switch]$SkipMAA)
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

# PID-based file lock — prevents race condition from dual scheduled-task launch
# More reliable than Global\mutex which requires admin and can fail across sessions
$lockFile = "D:\1\scripts\master.lock"

# Check if another instance is already running
if (Test-Path $lockFile) {
    try {
        $oldPid = [int](Get-Content $lockFile -Raw -ErrorAction Stop)
        $oldProc = Get-Process -Id $oldPid -ErrorAction Stop
        if ($oldProc.ProcessName -match "^(powershell|pwsh)$") {
            # Old master.ps1 still running — abort
            Write-Host "Another instance is already running (PID $oldPid). Exiting."
            exit 0
        } else {
            # Lock file exists but PID is not powershell — stale lock, clean up
            Write-Host "Stale lock found (PID $oldPid is $($oldProc.ProcessName)), cleaning up."
            Remove-Item $lockFile -Force
        }
    } catch {
        # PID not found or file corrupt — stale lock, clean up
        Write-Host "Stale lock found (process dead), cleaning up."
        Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
    }
}

# Write current PID to lock file (atomic via temp+move to avoid partial writes)
$pidFileTemp = "$lockFile.tmp"
"$pid" | Out-File $pidFileTemp -Encoding ascii -NoNewline
Move-Item $pidFileTemp $lockFile -Force


$adb = "D:\软件\MuMu模拟器\MuMuPlayer\nx_main\adb.exe"
$device = "127.0.0.1:16384"
$cli = "D:\软件\MuMu模拟器\MuMuPlayer\nx_main\mumu-cli.exe"
$maaOfficial = "D:\软件\MAA\MAA-v6.11.1-win-x64\MAA.exe"
$maaOfficialDir = "D:\软件\MAA\MAA-v6.11.1-win-x64"
$maaBilibili = "D:\软件\MAA（b）\MAA.exe"
$maaBilibiliDir = "D:\软件\MAA（b）"
$signalFile = "D:\1\scripts\maa_done.signal"
$logFile = "D:\1\scripts\master_log.txt"
$scriptDir = "D:\1\scripts"
$maaTimeout = 1800

# ---- 读取 GUI 配置（D:\1\config.json），字段缺失时回退上面的硬编码默认 ----
# config.json 由「MAA 挂机控制台」GUI 生成；文件不存在时流程与旧版完全一致。
# 注意 PS 5.1 的 Get-Content -Raw 会按 ANSI 解码导致中文路径乱码，必须显式 UTF-8
$config = $null
$configPath = "D:\1\config.json"
if (Test-Path $configPath) {
    try {
        $raw = [System.IO.File]::ReadAllText($configPath, [System.Text.Encoding]::UTF8)
        $config = $raw | ConvertFrom-Json
    } catch { $config = $null }
}
if ($config) {
    $p = $config.paths
    if ($null -ne $p -and $p.adb)          { $adb = [string]$p.adb }
    if ($null -ne $p -and $p.device)       { $device = [string]$p.device }
    if ($null -ne $p -and $p.cli)          { $cli = [string]$p.cli }
    if ($null -ne $p -and $p.maa_official) {
        $maaOfficial = [string]$p.maa_official
        $maaOfficialDir = Split-Path -Parent $maaOfficial
    }
    if ($null -ne $p -and $p.maa_bilibili) {
        $maaBilibili = [string]$p.maa_bilibili
        $maaBilibiliDir = Split-Path -Parent $maaBilibili
    }
    if ($null -ne $config.timeouts -and $null -ne $config.timeouts.maa_min) {
        $maaTimeout = [int]$config.timeouts.maa_min * 60
    }
    $closeEmulator = $true
    if ($null -ne $config.behavior -and $null -ne $config.behavior.close_emulator) {
        $closeEmulator = [bool]$config.behavior.close_emulator
    }
    $morningShutdown = $true
    if ($null -ne $config.behavior -and $null -ne $config.behavior.morning_shutdown) {
        $morningShutdown = [bool]$config.behavior.morning_shutdown
    }
} else {
    $closeEmulator = $true; $morningShutdown = $true
}

# ---- 账号列表：config.accounts 必须为数组（由控制台「账号管理」页维护）----
# 非数组/缺失时拒绝运行（防跑错号）；旧点击切号流程已废弃
$accountList = $null
if ($config -and $null -ne $config.accounts -and $config.accounts -is [System.Array]) {
    $accountList = @($config.accounts)
}

function Log($msg) {
    $time = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$time - $msg"
    Write-Host $line
    try { Add-Content $logFile $line -Encoding UTF8 -ErrorAction SilentlyContinue } catch {}
}

function Start-MuMu {
    Log "=== Starting MuMu ==="
    # Kill any stale ADB server first (common after MuMu reinstall)
    & $adb kill-server 2>$null | Out-Null
    Start-Sleep 1
    $r = & $adb connect $device 2>&1
    if ($r -match "connected|already") { Log "MuMu OK"; return $true }
    Log "Launching instance..."
    & $cli control -v 0 launch 2>$null | Out-Null
    Start-Sleep 5
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt 120) {
        $r = & $adb connect $device 2>&1
        if ($r -match "connected|already") { Log "ADB connected"; Start-Sleep 15; return $true }
        Start-Sleep 3
    }
    Log "ERROR: MuMu timeout"; return $false
}

function Wait-MAADone($t) {
    Log "Waiting for MAA tasks..."
    if (Test-Path $signalFile) { Remove-Item $signalFile -Force }
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $t) {
        if (Test-Path $signalFile) {
            Log "MAA finished! " + [math]::Round($sw.Elapsed.TotalMinutes,1).ToString() + " min"
            Start-Sleep 3
            return $true
        }
        Start-Sleep 10
    }
    Log "ERROR: MAA timeout"; return $false
}

function Run-MAA($exe, $dir, $label) {
    Log "=== Run MAA [" + $label + "] ==="
    Get-Process -Name "MAA" -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep 2
    if (Test-Path $signalFile) { Remove-Item $signalFile -Force }
    Log "Launching MAA..."
    Start-Process $exe -WorkingDirectory $dir
    Start-Sleep 5
    $ok = Wait-MAADone $maaTimeout
    Get-Process -Name "MAA" -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep 2
    if ($ok) { Log "MAA [" + $label + "] completed successfully" }
    else     { Log "ERROR: MAA [" + $label + "] FAILED or timed out" }
    return $ok
}

function Run-Switch($s) {
    # $s 形如 "slot_switch.ps1 -Server official -Slot official_2"；返回子脚本退出码
    $parts = $s -split ' '
    $sp = Join-Path $scriptDir $parts[0]
    $extraArgs = ""
    if ($parts.Count -gt 1) { $extraArgs = ($parts[1..($parts.Count-1)] -join ' ') }
    Log "Running: $s"
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    # Redirect child process stdout to temp file to avoid encoding issues
    # with PowerShell 5.1 pipeline (2>&1 on child powershell mangles output)
    $tmpOut = "$scriptDir\switch_output.tmp"
    $proc = Start-Process -FilePath powershell `
        -ArgumentList "-ExecutionPolicy Bypass -File `"$sp`" $extraArgs" `
        -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $tmpOut
    if (Test-Path $tmpOut) {
        # 子脚本 stdout 实测为 UTF-8（含中文），Default(GBK) 读取会乱码
        foreach ($l in (Get-Content $tmpOut -Encoding UTF8)) {
            $trimmed = $l.Trim()
            if ($trimmed.Length -gt 0) { Log "  $trimmed" }
        }
        Remove-Item $tmpOut -Force -ErrorAction SilentlyContinue
    }
    $elapsed = [math]::Round($sw.Elapsed.TotalSeconds, 1)
    $code = $proc.ExitCode
    Log ("  [Switch script finished in " + $elapsed + "s, exit=" + $code + "]")
    Start-Sleep 3
    return $code
}

# config.accounts 非数组或为空 → 拒绝运行（防跑错号；旧 3 账号点击流程已废弃）
if (-not $accountList -or $accountList.Count -eq 0) {
    Log "FATAL: config.accounts 缺失或不是数组格式，无法运行"
    Log "FATAL: 请在控制台「账号管理」页配置账号后重试"
    Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
    exit 1
}

# MAIN
Log " "
Log "========================================"
Log "MAA Auto Farm"
Log "========================================"

# Clean up debug screenshots from previous run (prevent disk bloat)
$debugDir = "D:\1\scripts\debug"
if (Test-Path $debugDir) {
    try {
        Remove-Item "$debugDir\*.png" -Force -ErrorAction SilentlyContinue
        Log "Cleaned up old debug screenshots"
    } catch {}
}

if (-not (Start-MuMu)) { Log "FATAL: MuMu failed to start"; Remove-Item $lockFile -Force -ErrorAction SilentlyContinue; exit 1 }

$results = @()
$totalSw = [System.Diagnostics.Stopwatch]::StartNew()

# ============ 动态账号流程（config.accounts 数组）============
# 每个账号：槽位切号（重启游戏+推入登录数据，非点击）→ MAA
$total = $accountList.Count
    $idx = 0
    foreach ($acc in $accountList) {
        $idx++
        $accLabel = if ($null -ne $acc.label -and [string]$acc.label) { [string]$acc.label } else { "Account $idx" }
        $accServer = if ($null -ne $acc.server -and [string]$acc.server) { [string]$acc.server } else { "official" }
        $accEnabled = $true
        if ($null -ne $acc.enabled) { $accEnabled = [bool]$acc.enabled }
        $accSlot = if ($null -ne $acc.slot) { [string]$acc.slot } else { "" }

        Log " "; Log "********** [$idx/$total] $accLabel **********"
        $sw = [System.Diagnostics.Stopwatch]::StartNew()

        if (-not $accEnabled) {
            Log "  [SKIP] disabled in config.json"
            $results += [PSCustomObject]@{ Account=$accLabel; OK=$true; Duration="skip" }
            continue
        }

        # ---- 切换账号：重启游戏 + 推入槽位数据（非点击）----
        $switchOk = $true
        $slotPath = if ($accSlot) { Join-Path $scriptDir ("accounts\" + $accSlot) } else { "" }
        if ($accSlot -and (Test-Path $slotPath)) {
            $switchOk = ((Run-Switch ("slot_switch.ps1 -Server {0} -Slot {1}" -f $accServer, $accSlot)) -eq 0)
        } elseif ($accSlot -and -not (Test-Path $slotPath)) {
            if ($accServer -eq "bilibili") {
                # B 服只有一个号，槽位缺失不影响：照常运行（客户端由 MAA 拉起）
                Log "  [WARN] slot '$accSlot' not found, switching client only"
                $switchOk = $true
            } else {
                # 官服槽位缺失：拒绝运行该号（防跑错号），日志提示补捕获
                Log "  [ERROR] slot '$accSlot' not found - refusing to run (防跑错号)"
                Log "  [ERROR] 请在控制台「账号管理」页重新捕获该账号"
                $switchOk = $false
            }
        } else {
            if ($accServer -eq "bilibili") { $switchOk = ((Run-Switch "switch_to_B服.ps1") -eq 0) }
            else {
                Log "  [ERROR] account '$accLabel' has no slot - refusing to run (防跑错号)"
                $switchOk = $false
            }
        }
        if (-not $switchOk) {
            Log "  [ERROR] Account switch failed"
            $results += [PSCustomObject]@{ Account=$accLabel; OK=$false; Duration="0 min" }
            continue
        }

        # ---- 登录校验：屏幕级确认游戏已登录；未登录自动输账号密码（官服）并刷新槽位 ----
        # 文件级 uid 校验通过不代表游戏真在登录态（token 失效时会回到登录界面）
        $loginOk = ((Run-Switch ("login_check.ps1 -Server {0} -Slot {1}" -f $accServer, $accSlot)) -eq 0)
        if (-not $loginOk) {
            Log "  [ERROR] Login check failed - 请在控制台重新捕获该账号"
            $results += [PSCustomObject]@{ Account=$accLabel; OK=$false; Duration="0 min" }
            continue
        }

        # ---- 跑 MAA（按服务器选对应客户端；-SkipMAA 测试模式跳过）----
        if ($SkipMAA) {
            Log "  [TEST] -SkipMAA: 跳过 MAA，仅验证切号"
            $ok = $true
        } else {
            $maaExe = if ($accServer -eq "bilibili") { $maaBilibili } else { $maaOfficial }
            $maaDir = if ($accServer -eq "bilibili") { $maaBilibiliDir } else { $maaOfficialDir }
            $ok = Run-MAA $maaExe $maaDir $accLabel
        }
        $dur = [math]::Round($sw.Elapsed.TotalMinutes, 1)
        $results += [PSCustomObject]@{ Account=$accLabel; OK=$ok; Duration="$dur min" }
    }

# Close emulator (config: behavior.close_emulator=false 时跳过；-SkipMAA 测试模式保留模拟器供检查)
if ($SkipMAA) {
    Log " "
    Log "=== [TEST] -SkipMAA: 模拟器保持运行，便于检查最终登录状态 ==="
} elseif ($closeEmulator) {
    Log " "; Log "=== Closing emulator ==="
    Log "Shutting down MuMu..."
    & $cli control -v 0 shutdown 2>$null | Out-Null
    Start-Sleep 5
    Log "Emulator closed"
} else {
    Log " "
    Log "=== Skipping emulator close (config: close_emulator=false) ==="
}

$totalDur = [math]::Round($totalSw.Elapsed.TotalMinutes, 1)
# @(...) wrapper required - in PS 5.1, .Count on a single PSCustomObject returns $null
$passed = @($results | Where-Object { $_.OK }).Count
$failed = @($results | Where-Object { -not $_.OK }).Count

Log " "
Log "========================================"
Log "SUMMARY"
Log "========================================"
foreach ($r in $results) {
    $status = if ($r.OK) { "OK" } else { "FAIL" }
    Log ("  [{0}] {1} - {2}" -f $status, $r.Account, $r.Duration)
}
Log ("----------------------------------------")
Log ("Total: {0} min | Passed: {1} | Failed: {2}" -f $totalDur, $passed, $failed)
Log "========================================"

# Release PID lock and cleanup BEFORE any blocking prompt,
# so the 16:00 run is never blocked by a leftover popup
Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
# Clean up temp file too (paranoid)
Remove-Item "$lockFile.tmp" -Force -ErrorAction SilentlyContinue

# Success -> no popup, no confirmation needed
if ($failed -eq 0) {
    Log "ALL ACCOUNTS COMPLETED SUCCESSFULLY"
} else {
    # Failure -> prompt only (blocks until acknowledged)
    Log ("WARNING: {0} account(s) FAILED!" -f $failed)
    $wshell = New-Object -ComObject WScript.Shell
    $body = "有 $failed 个账号失败！`n`n"
    foreach ($r in $results) {
        $s = if ($r.OK) { "OK" } else { "FAIL" }
        $body += ("  [{0}] {1} - {2}`n" -f $s, $r.Account, $r.Duration)
    }
    $body += "`nTotal: $totalDur min"
    $null = $wshell.Popup($body, 0, "MAA Auto Farm - 异常", 0x30)
}

# Morning run (4:00) -> shut down PC after successful run, no confirmation needed
# On failure the popup above keeps the PC on so the failure can be reviewed
# Afternoon run (16:00) unaffected
# GUI 手动运行传 -NoShutdown 跳过；config: behavior.morning_shutdown=false 也可关闭
if (-not $NoShutdown -and $morningShutdown -and (Get-Date).Hour -lt 12 -and $failed -eq 0) {
    Log "Morning run finished - shutting down PC in 60s"
    shutdown /s /t 60
}
