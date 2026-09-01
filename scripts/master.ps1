# MAA Auto Farm v4 - Dual MAA + 槽位切号（非点击）
# -NoShutdown: GUI 手动运行传入，跳过「成功后关机」；计划任务不传，行为不变
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
$venvPython = "D:\1\gui\.venv\Scripts\python.exe"
$baseSchedulePy = "D:\1\plugins\base_schedule\base_schedule.py"

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
    $eveningShutdown = $false
    if ($null -ne $config.behavior -and $null -ne $config.behavior.evening_shutdown) {
        $eveningShutdown = [bool]$config.behavior.evening_shutdown
    }
} else {
    $closeEmulator = $true; $morningShutdown = $true; $eveningShutdown = $false
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

function Refresh-SlotData($Server, $Slot) {
    # MAA 跑完后把设备上最新的登录数据拉回槽位：游戏在处理首次启动弹窗/公告弹窗后
    # 会往 playerprefs 写入「已处理」标记（配音选择、公告版本号等），且写入有延迟。
    # 不拉回的话，下次切号会推送旧槽位数据，弹窗每次重新出现，甚至卡住 MAA。
    # 失败仅告警，不影响主流程；写入前校验设备 uid 与槽位 uid 一致（防跑错号）。
    if (-not $Slot) { return }
    $slotDir = Join-Path $scriptDir ("accounts\" + $Slot)
    if (-not (Test-Path $slotDir)) { return }
    $pkg = if ($Server -eq "bilibili") { "com.hypergryph.arknights.bilibili" } else { "com.hypergryph.arknights" }
    $ppName = ""
    try {
        $out = (& $adb -s $device shell "ls /data/data/$pkg/shared_prefs/" 2>$null) -join "`n"
        $ppName = ($out -split "`n" | Where-Object { $_ -match '\.v2\.playerprefs\.xml' } | Select-Object -First 1) -replace '\s+',''
    } catch {}
    if (-not $ppName) { Log "  [WARN] Refresh slot: playerprefs not found on device"; return }
    $tmpPp = Join-Path $env:TEMP "ark_refresh_pp.xml"
    & $adb -s $device pull ("/data/data/{0}/shared_prefs/{1}" -f $pkg, $ppName) $tmpPp 2>$null | Out-Null
    if (-not (Test-Path $tmpPp)) { Log "  [WARN] Refresh slot: pull failed for $Slot"; return }
    $devUid = ""
    try {
        $ppc = [System.IO.File]::ReadAllText($tmpPp, [System.Text.Encoding]::UTF8)
        $m = [regex]::Match($ppc, 'name="u8sdk_cached_uid">([0-9]+)')
        if ($m.Success) { $devUid = $m.Groups[1].Value }
    } catch {}
    Remove-Item $tmpPp -Force -ErrorAction SilentlyContinue
    $uidFile = Join-Path $slotDir "uid.txt"
    $expectUid = ""
    if (Test-Path $uidFile) { $expectUid = (Get-Content $uidFile -Raw -ErrorAction SilentlyContinue).Trim() }
    if (-not $devUid -or ($expectUid -and ($devUid -ne $expectUid))) {
        Log ("  [WARN] Refresh slot: uid mismatch (device=" + $devUid + ", slot=" + $expectUid + "), skipped")
        return
    }
    $dstShared = Join-Path $slotDir "shared_prefs"
    $dstFiles = Join-Path $slotDir "files\zx"
    New-Item -ItemType Directory -Force $dstShared, $dstFiles | Out-Null
    & $adb -s $device pull ("/data/data/{0}/shared_prefs/{1}" -f $pkg, $ppName) (Join-Path $dstShared $ppName) 2>$null | Out-Null
    & $adb -s $device pull "/data/data/$pkg/shared_prefs/HypergryphSdkPreferences.xml" (Join-Path $dstShared "HypergryphSdkPreferences.xml") 2>$null | Out-Null
    & $adb -s $device pull "/data/data/$pkg/files/zx/lc.cache" (Join-Path $dstFiles "lc.cache") 2>$null | Out-Null
    Log "  [Refresh] slot '$Slot' data updated from device"
}

# ---- 数据清理（16:00 下午班完整清理；凌晨班只清截图，见 MAIN）----
# 与 GUI「运行设置 → 数据清理」一致：debug 目录 / 残留临时文件 / 测试遗留文件 /
# 旧配置备份 / master_log.txt 超限截断（>1MB 保留尾部 512KB）。
# 在运行开始阶段执行，清的是上一轮的残留，不影响本轮；master.lock 正在使用不删。
function Clear-UnnecessaryData {
    # 1) debug 目录（登录校验截图 + 捕获日志）
    $debugDir = Join-Path $scriptDir "debug"
    if (Test-Path $debugDir) {
        Get-ChildItem $debugDir -File -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
    }
    # 2) 残留临时文件（正常结束会自删，异常中断会残留）
    foreach ($name in @("switch_output.tmp", "master.lock.tmp", "maa_done.signal")) {
        Remove-Item (Join-Path $scriptDir $name) -Force -ErrorAction SilentlyContinue
    }
    # 3) 测试遗留文件（调试时 dump 的登录缓存，含 token）
    foreach ($name in @("_t1.xml", "_t2.xml", "_t3.bin")) {
        Remove-Item (Join-Path $scriptDir $name) -Force -ErrorAction SilentlyContinue
    }
    # 4) 旧配置备份
    Remove-Item "D:\1\config.json.bak" -Force -ErrorAction SilentlyContinue
    # 5) master_log.txt 超限截断（>1MB 保留尾部 512KB 并对齐行首；此刻无活动日志句柄，安全）
    $logPath = Join-Path $scriptDir "master_log.txt"
    if (Test-Path $logPath) {
        $size = (Get-Item $logPath).Length
        if ($size -gt 1048576) {
            try {
                $all = [System.IO.File]::ReadAllBytes($logPath)
                $tail = $all[($all.Length - 524288)..($all.Length - 1)]
                # 对齐行首；注意 PS 5.1 范围索引 $arr[5..-1] 会倒序，必须显式复制
                $nl = [Array]::IndexOf($tail, [byte]10)
                if ($nl -ge 0 -and $nl -lt $tail.Length - 1) {
                    $keep = New-Object byte[] ($tail.Length - $nl - 1)
                    [Array]::Copy($tail, $nl + 1, $keep, 0, $keep.Length)
                    $tail = $keep
                }
                $note = [System.Text.Encoding]::UTF8.GetBytes(
                    ("{0} - [清理] 旧日志已截断（原 {1} KB），仅保留最近部分`n" -f
                     (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), [int]($size / 1024)))
                $out = New-Object byte[] ($note.Length + $tail.Length)
                [Array]::Copy($note, 0, $out, 0, $note.Length)
                [Array]::Copy($tail, 0, $out, $note.Length, $tail.Length)
                [System.IO.File]::WriteAllBytes($logPath, $out)
            } catch {}
        }
    }
    Log "Cleaned up old debug data"
}

function Clear-CacheData {
    # Python 字节码缓存（__pycache__）与生成的基建计划文件：
    # 都是运行前自动重建的临时产物，默认每次运行顺手清掉，避免本地残留。
    # 只清项目代码目录，不碰 .venv（虚拟环境属运行环境）。
    foreach ($root in @("D:\1\gui\core", "D:\1\gui\pages", "D:\1\plugins")) {
        if (Test-Path $root) {
            Get-ChildItem $root -Directory -Recurse -Filter "__pycache__" -ErrorAction SilentlyContinue |
                Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item "D:\1\gui\__pycache__" -Recurse -Force -ErrorAction SilentlyContinue
    $plansDir = "D:\1\plugins\base_schedule\plans"
    if (Test-Path $plansDir) {
        Get-ChildItem $plansDir -File -Filter "*.json" -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
    }
    Log "Cleaned Python caches and generated base schedule plans"
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

# ---- 本次运行属于哪一班（动态）：config.schedule.times 里最近一个已到（或已过）的
# 启用时间点决定批次名与关机选项。凌晨 00:00 - 最早时间点 之间属于昨天最后一班。
# 无配置/全禁用时回退旧逻辑：4点班（04:00-16:00 启动）→ "4点"，否则 "16点"。
$scheduleEntries = @()
if ($config -and $null -ne $config.schedule -and $null -ne $config.schedule.times -and
    $config.schedule.times -is [System.Array]) {
    foreach ($t in $config.schedule.times) {
        if ($t -and $t.time -and $t.enabled) {
            $scheduleEntries += $t
        }
    }
}
# 旧格式兜底：config.json 尚未被 GUI 保存为新格式时，schedule.morning/evening 仍存在
if ($scheduleEntries.Count -eq 0 -and $config -and $null -ne $config.schedule -and
    $null -ne $config.schedule.morning -and $null -ne $config.schedule.evening) {
    foreach ($kv in @(@("morning", "morning_shutdown"), @("evening", "evening_shutdown"))) {
        $item = $config.schedule.($kv[0])
        $t = [string]$item.time
        if ($t -match '^([01]\d|2[0-3]):[0-5]\d$') {
            $scheduleEntries += [pscustomobject]@{
                time    = $t
                enabled = [bool]$item.enabled
                shutdown = [bool]$config.behavior.($kv[1])
            }
        }
    }
}

$bsBatch = "16点"
$shutdownEnabled = $false
if ($scheduleEntries.Count -gt 0) {
    $sorted = @($scheduleEntries | Sort-Object { $_.time })
    $now = Get-Date
    $runMin = $now.Hour * 60 + $now.Minute
    $pick = $null
    foreach ($t in $sorted) {
        $mins = [int]$t.time.Substring(0, 2) * 60 + [int]$t.time.Substring(3, 2)
        if ($mins -le $runMin) { $pick = $t } else { break }
    }
    if ($null -eq $pick) { $pick = $sorted[$sorted.Count - 1] }
    $hh = [int]$pick.time.Substring(0, 2)
    $bsBatch = if ($hh -eq 0) { "24点" } else { "$($hh)点" }
    $shutdownEnabled = [bool]$pick.shutdown
} else {
    $isFourOClockRun = ((Get-Date).Hour -ge 4 -and (Get-Date).Hour -lt 16)
    $bsBatch = if ($isFourOClockRun) { "4点" } else { "16点" }
    $shutdownEnabled = if ($isFourOClockRun) { $morningShutdown } else { $eveningShutdown }
}

# 每次运行前清理本地缓存/生成文件（默认自动，无需配置）
Clear-CacheData

# Clean up debug screenshots from previous run (prevent disk bloat)
# 下午/晚间（Hour >= 12）→ 完整清理；凌晨班保持只清截图
if ((Get-Date).Hour -ge 12) {
    Clear-UnnecessaryData
} else {
    $debugDir = "D:\1\scripts\debug"
    if (Test-Path $debugDir) {
        try {
            Remove-Item "$debugDir\*.png" -Force -ErrorAction SilentlyContinue
            Log "Cleaned up old debug screenshots"
        } catch {}
    }
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
            # ---- 精确基建派驻插件：启动 MAA 前按账号写入自定义计划（未启用则恢复 Rotation）----
            $accId = if ($null -ne $acc.id -and [string]$acc.id) { [string]$acc.id } else { "" }
            if ($accId -and (Test-Path $venvPython) -and (Test-Path $baseSchedulePy)) {
                $bsOut = & $venvPython $baseSchedulePy apply --config $configPath --account $accId --server $accServer --batch $bsBatch 2>&1
                $bsCode = $LASTEXITCODE
                foreach ($bsLine in $bsOut) {
                    if ($bsLine -and [string]$bsLine) { Log ("  [基建插件] " + [string]$bsLine) }
                }
                if ($bsCode -ne 0) {
                    Log "  [WARN] 基建插件执行失败（exit $bsCode），继续按 MAA 原配置运行"
                }
            } else {
                Log "  [WARN] 基建插件不可用（venv python 或脚本缺失），继续按 MAA 原配置运行"
            }
            $maaExe = if ($accServer -eq "bilibili") { $maaBilibili } else { $maaOfficial }
            $maaDir = if ($accServer -eq "bilibili") { $maaBilibiliDir } else { $maaOfficialDir }
            $ok = Run-MAA $maaExe $maaDir $accLabel
        }
        # 把设备上最新的登录数据（弹窗处理标记等）拉回槽位，避免下次切号弹窗重现
        Refresh-SlotData $accServer $accSlot
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

# 每个时间点的「关机」开关（schedule.times 每项 shutdown）决定本次运行是否关机；
# 失败时保留弹窗便于查看，不关机；GUI 手动运行传 -NoShutdown 跳过
if (-not $NoShutdown -and $shutdownEnabled -and $failed -eq 0) {
    Log "$bsBatch班成功 - 60秒后自动关机"
    shutdown /s /t 60
}
