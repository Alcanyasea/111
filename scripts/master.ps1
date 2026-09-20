# MAA Auto Farm v4 - Dual MAA + 槽位切号（非点击）
# -NoShutdown: GUI 手动运行传入，跳过结束后的自动关机（无论成败）；计划任务不传，行为不变
# -SkipMAA: 测试切号流程用——跳过 MAA、结束时保留模拟器运行供检查
# -InfrastCollect: 基建收菜模式——逐个已启用账号只收制造站/贸易站产物（全部房间
#   skip：不换干员、不用无人机），停用理智/招募/信用/领奖任务，不碰班次计划；
#   使用独立的「收菜」配置方案（切 Current 指针，结束切回 Default），
#   farm 用的 Default 方案全程不被触碰，无需备份/恢复
# -SwitchTo <slot>: 只切到该槽位账号并完成登录校验即停——不跑 MAA、模拟器保持
#   运行、不关机不推送（GUI「账号管理 → 切换到此账号」，切完直接手动游戏）
# -SwitchTo <slot> -NoLoginCheck: 快速启动——只推入槽位登录数据（token）并启动
#   游戏，跳过更新等待与登录校验（两者都在 login_check 内）即停；token 失效时
#   游戏会停在登录界面，需手动输账号密码或重新捕获（GUI「账号管理 → 卡片快速启动」）
# 每轮结束把结果写入 scripts\run_history\run_<时间戳>.json（保留 60 天），
# GUI「运行历史」页读取；config.notify.enabled 时按账号逐步骤检查（切号 → 登录
# 校验 → 任务开关自检 → MAA）：某个步骤失败的账号单独推送一条，标题含账号名与
# 失败步骤；成功（含重试成功）不推送。渠道/密钥在 config.notify，GUI
# 「运行设置 → 通知推送」维护，经 plugins\notify 发送
# 失败重试：部分账号失败（有成功有失败）时，整轮跑完后对失败号再完整跑一遍；
# 重试成功按成功计（不推送），重试后仍失败（或全部号都失败——系统性问题不重试）
# 才按失败账号逐个推送
# 2026-09 性能与成功率优化：模拟器启动接 config 启动等待并轮询开机完成（不再固定
#   睡 15 秒）、45 秒连不上自动重拉实例；MAA 启动即崩溃（零任务进展）自动重试一次；
#   完成信号轮询 10→4 秒；各阶段衔接 sleep 3→1 秒
param([switch]$NoShutdown, [switch]$SkipMAA, [switch]$InfrastCollect,
    [string]$SwitchTo = "", [switch]$NoLoginCheck)
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

# PID+StartTime 文件锁 — 防计划任务双触发并发双跑（Global\mutex 需管理员且跨会话
# 不可靠，故用文件锁；v4.1 起在旧版基础上加固两个洞）：
# 1) 抢锁用 [System.IO.File]::Open(CreateNew) 独占创建——文件已存在即失败，
#    「检查→写入」合一为一步原子操作。旧版 Test-Path→Remove→Move 三步之间无
#    互斥，两个计划任务同时启动可同时通过检查、各自写锁后双跑。
# 2) 锁内容为 "PID|进程启动时间(UTC Ticks)"，校验时与 Get-Process StartTime 比对：
#    进程已死 / 进程名不是 pwsh / 启动时间对不上（PID 被系统复用给别的 pwsh 窗口）
#    都按陈旧锁清理后重抢。旧版只看 PID+进程名——断电残留的陈旧锁，其 PID 恰好
#    被用户自己开的 pwsh 窗口复用时，会永久拒绝运行且不清理锁（master.lock 挡死）。
#    旧格式锁（仅 PID 无 |）退回命令行特征判断：命令行含 master.ps1 才算在跑；
#    命令行读不到（跨权限）时保守视为在跑——宁可少跑一轮，不冒双跑风险。
$lockFile = "D:\1\scripts\master.lock"

function Test-LockHeldByLiveMaster([string]$content) {
    # $true = 锁持有者仍是活着的 master 实例（应放弃启动）；$false = 陈旧锁可清理
    $parts = $content -split '\|'
    [int]$lockPid = 0
    if (-not [int]::TryParse($parts[0], [ref]$lockPid) -or $lockPid -le 0) { return $false }
    $proc = Get-Process -Id $lockPid -ErrorAction SilentlyContinue
    if (-not $proc -or $proc.ProcessName -notmatch "^(powershell|pwsh)$") { return $false }
    if ($parts.Count -ge 2 -and $parts[1]) {
        [long]$oldTicks = 0
        if ([long]::TryParse($parts[1], [ref]$oldTicks) -and $oldTicks -gt 0) {
            try {
                return ($proc.StartTime.ToUniversalTime().Ticks -eq $oldTicks)
            } catch { }  # StartTime 读不到（跨权限），退回命令行判断
        }
    }
    try {
        $cl = (Get-CimInstance Win32_Process -Filter "ProcessId=$lockPid" -ErrorAction Stop).CommandLine
        if ($null -ne $cl) { return ($cl -match 'master\.ps1') }
    } catch { }
    return $true
}

$lockAcquired = $false
$myStartTicks = (Get-Process -Id $PID).StartTime.ToUniversalTime().Ticks
for ($lockAttempt = 0; $lockAttempt -lt 2 -and -not $lockAcquired; $lockAttempt++) {
    try {
        $fs = [System.IO.File]::Open($lockFile, [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        $lockBytes = [System.Text.Encoding]::ASCII.GetBytes("$PID|$myStartTicks")
        $fs.Write($lockBytes, 0, $lockBytes.Length)
        $fs.Flush(); $fs.Close()
        $lockAcquired = $true
    } catch [System.IO.IOException] {
        # 文件已存在：校验持有者是否真是活着的 master
        $lockContent = ""
        try { $lockContent = [System.IO.File]::ReadAllText($lockFile).Trim() } catch { }
        if (Test-LockHeldByLiveMaster $lockContent) {
            Write-Host "Another instance is already running (lock: $lockContent). Exiting."
            try {
                Add-Content -Path "D:\1\scripts\master_log.txt" -Value ("[{0}] Another instance is already running (lock: {1}), exit." -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $lockContent) -ErrorAction SilentlyContinue
            } catch { }
            exit 0
        }
        Write-Host "Stale lock found (content: '$lockContent'), cleaning up."
        Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 200
    }
}
if (-not $lockAcquired) {
    # 两轮都没抢到且持有者不像活实例（清理后瞬间被第三方占用等极端竞态）：
    # 保守放弃——宁可不跑这一轮，不冒与别人双跑的风险
    Write-Host "Could not acquire master.lock, exiting."
    exit 0
}


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
# MAA 无进展判超时（秒）：该账号这段时间内没有任何战斗/任务推进才放弃；
# 正常打关（一直有进战斗/结算心跳）不再受单号总时长限制。
$maaStallTimeoutSec = 180
# 失败重试等待（秒）：部分账号失败时，全部号跑完后等待该秒数再对失败号重试一遍；
# 全部号都失败视为系统性问题（模拟器/ADB/网络/游戏维护），不重试直接收尾。
$retryFailedDelaySec = 60
# 模拟器启动等待上限（秒）：config.timeouts.launch_wait_sec 可覆盖（GUI「运行设置→启动等待」）
$mumuLaunchTimeoutSec = 120
# 游戏更新检测：v1.3.2 起并入 login_check.ps1（同一套更新标记等待 + 失败快判 +
# 安装器检测），不再单独跑 game_update_wait.ps1（每号省 ~50 秒探测窗）。
# behavior.wait_game_update / timeouts.game_update_min 保留读取但不再单独使用，
# 更新等待上限由 login_check 的 hardDeadline（2 小时）兜底。
$venvPython = "D:\1\gui\.venv\Scripts\python.exe"
$baseSchedulePy = "D:\1\plugins\base_schedule\base_schedule.py"
$fightStagePy = "D:\1\plugins\fight_stage\fight_stage.py"
$farmGuardPy = "D:\1\plugins\farm_guard\farm_guard.py"
$fiammettaPy = "D:\1\plugins\fiammetta\fiammetta.py"
$infrastCollectPy = "D:\1\plugins\infrast_collect\infrast_collect.py"
$notifyPy = "D:\1\plugins\notify\notify.py"
$runHistoryDir = "D:\1\scripts\run_history"
# 本轮运行历史元数据（Save-RunHistory 使用；MAIN 里按模式改写 $runMode）
$runStartTs = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$runStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$runMode = "farm"

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
        # 键名沿用 maa_min（GUI「运行设置」同键），语义为“无进展判超时分钟数”
        $maaStallTimeoutSec = [int]$config.timeouts.maa_min * 60
    }
    if ($null -ne $config.timeouts -and $null -ne $config.timeouts.launch_wait_sec) {
        $mumuLaunchTimeoutSec = [int]$config.timeouts.launch_wait_sec
    }
    # game_update_min / wait_game_update 曾用于独立的 game_update_wait.ps1，
    # 该脚本已并入 login_check.ps1（见文件头注释），这里不再读取
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
    $relaunched = $false
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $mumuLaunchTimeoutSec) {
        $r = & $adb connect $device 2>&1
        if ($r -match "connected|already") {
            Log ("ADB connected ({0:N0}s)" -f $sw.Elapsed.TotalSeconds)
            # ADB 就绪不等于系统开机完成：轮询 sys.boot_completed，就绪即放行。
            # 旧版固定睡 15 秒，绝大多数时候是白等；60 秒仍未确认则按原样继续
            # （与旧版兜底一致，不因 getprop 异常卡死流程）。
            $bootSw = [System.Diagnostics.Stopwatch]::StartNew()
            while ($bootSw.Elapsed.TotalSeconds -lt 60) {
                $boot = (& $adb -s $device shell "getprop sys.boot_completed" 2>$null | Out-String).Trim()
                if ($boot -match '^1$') {
                    Log ("Boot completed ({0:N0}s)" -f $bootSw.Elapsed.TotalSeconds)
                    return $true
                }
                Start-Sleep 2
            }
            Log "WARN: 60 秒内未确认开机完成，继续按原流程执行"
            return $true
        }
        # 启动命令偶发丢失（CLI 首启窗口期未就绪）：45 秒仍连不上就再拉一次
        if (-not $relaunched -and $sw.Elapsed.TotalSeconds -ge 45) {
            $relaunched = $true
            Log "WARN: 45 秒仍未连上 ADB，重新拉起模拟器实例"
            & $cli control -v 0 launch 2>$null | Out-Null
        }
        Start-Sleep 2
    }
    Log "ERROR: MuMu timeout"; return $false
}

function Wait-MAADone($t, $maaDir) {
    # 完成信号由 signal_done.bat（MAA 任务结束后回调）创建。
    # 心跳 = MAA 的 asst.log 持续出现 SubTask 事件（进战斗、战斗中 PRTS 轮询、
    # 结算等，正常战斗下每几秒一条）；超过 $t 秒没有任何心跳才判超时。
    # 返回状态字符串：ok / stall（无进展超时）/ dead（进程退出且已执行过任务）/
    #   dead-early（进程启动即退出、一次任务都没跑过，由 Run-MAA 自动重试一次）。
    Log ("Waiting for MAA tasks... ({0} 秒无战斗/任务进展判超时)" -f $t)
    if (Test-Path $signalFile) { Remove-Item $signalFile -Force }
    $logPath = Join-Path $maaDir "debug\asst.log"
    $logPos = -1
    if (Test-Path $logPath) {
        try {
            $fs = [System.IO.File]::Open($logPath, 'Open', 'Read', 'ReadWrite')
            $logPos = $fs.Length   # 只统计本次 MAA 启动后新增的日志
            $fs.Dispose()
        } catch { $logPos = -1 }
    }
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $lastAct = Get-Date
    $lastLogTry = Get-Date
    $readFail = 0
    $maaDeadCount = 0
    $sawProgress = $false
    while ($true) {
        if (Test-Path $signalFile) {
            Log ("MAA finished! {0} min" -f [math]::Round($sw.Elapsed.TotalMinutes, 1))
            Start-Sleep 2
            return "ok"
        }
        # MAA 进程意外退出（启动即崩溃/被手动关闭）→ 不傻等心跳超时，快速判失败；
        # 连续 2 轮检测不到才判，避开进程刚拉起的瞬间。是否执行过任务决定
        # Run-MAA 是否自动重试（已有进展的退出重试可能重复执行任务，不重试）
        if (-not (Get-Process -Name "MAA" -ErrorAction SilentlyContinue)) {
            $maaDeadCount++
            if ($maaDeadCount -ge 2) {
                if ($sawProgress) {
                    Log "ERROR: MAA 进程已退出且没有完成信号，判定失败"
                    return "dead"
                }
                Log "ERROR: MAA 启动后未执行任何任务即退出（启动即崩溃）"
                return "dead-early"
            }
        } else {
            $maaDeadCount = 0
        }
        # asst.log 启动瞬间不存在/被占用时周期性重试打开，
        # 否则心跳全程失效，会把正常运行的 MAA 误判为卡死
        if ($logPos -lt 0 -and ((Get-Date) - $lastLogTry).TotalSeconds -ge 30) {
            $lastLogTry = Get-Date
            $fs0 = $null
            try {
                $fs0 = [System.IO.File]::Open($logPath, 'Open', 'Read', 'ReadWrite')
                $logPos = $fs0.Length
                $fs0.Dispose()
            } catch { if ($fs0) { try { $fs0.Dispose() } catch {} } }
        }
        if ($logPos -ge 0) {
            $fs = $null
            try {
                $fs = [System.IO.File]::Open($logPath, 'Open', 'Read', 'ReadWrite')
                $len = $fs.Length
                if ($len -lt $logPos) { $logPos = 0 }   # asst.log 被轮转/重建
                if ($len -gt $logPos) {
                    $fs.Seek($logPos, 'Begin') | Out-Null
                    $n = $len - $logPos
                    $buf = New-Object byte[] $n
                    [void]$fs.Read($buf, 0, $n)
                    $logPos = $len
                    $chunk = [System.Text.Encoding]::UTF8.GetString($buf)
                    if ($chunk -match 'append_callback \| SubTask') {
                        $lastAct = Get-Date
                        $sawProgress = $true
                        $readFail = 0
                    }
                }
                $fs.Dispose()
            } catch {
                if ($fs) { try { $fs.Dispose() } catch {} }
                # MAA 独占日志等短暂不可读：前几次宽容，之后按无进展计
                $readFail++
                if ($readFail -gt 3) { $lastAct = (Get-Date).AddSeconds(-$t) }
            }
        }
        $idleSec = ((Get-Date) - $lastAct).TotalSeconds
        if ($idleSec -ge $t) {
            Log ("ERROR: MAA stall: 已 {0} 分钟没有战斗/任务进展，判定超时" -f [math]::Round($idleSec / 60, 1))
            return "stall"
        }
        # 4 秒轮询：完成信号/进程退出的平均发现延迟约 2 秒（旧 10 秒轮询平均 5 秒）
        Start-Sleep 4
    }
}

function Run-MAA($exe, $dir, $label) {
    Log ("=== Run MAA [" + $label + "] ===")
    Get-Process -Name "MAA" -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep 2
    if (Test-Path $signalFile) { Remove-Item $signalFile -Force }
    Log "Launching MAA..."
    Start-Process $exe -WorkingDirectory $dir
    Start-Sleep 5
    $status = Wait-MAADone $maaStallTimeoutSec $dir
    $script:LastMaaStatus = $status
    if ($status -eq "dead-early") {
        # 启动即崩溃（一次任务都没跑）自动重试一次：偶发崩溃/被占用拦截时自愈。
        # 已有任务进展的退出不重试（重试可能重复执行任务），按失败处理。
        Log "WARN: 3 秒后自动重试一次 MAA 启动"
        Start-Sleep 3
        Log "Launching MAA (retry)..."
        Start-Process $exe -WorkingDirectory $dir
        Start-Sleep 5
        $status = Wait-MAADone $maaStallTimeoutSec $dir
        $script:LastMaaStatus = $status
    }
    Get-Process -Name "MAA" -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep 2
    $ok = ($status -eq "ok")
    if ($ok) { Log ("MAA [" + $label + "] completed successfully") }
    else     { Log ("ERROR: MAA [" + $label + "] FAILED or timed out") }
    return $ok
}

# ---- 插件调用模板：统一「venv/脚本存在检查 → 调用 → 逐行记日志 → 退出码检查」。
# 不可用/执行失败都只告警、不阻断主流程（MAA 按原配置继续跑）。
# $tag 用作每行插件输出的日志前缀；$name 用于告警文本；$missNote 是不可用时的后果说明。
function Invoke-Plugin($pyPath, $tag, $name, $argList, $missNote) {
    # 插件自报的 ERROR 明细（如「任务队列缺少 Fight」）存这里，供失败通知引用
    $script:LastPluginError = ""
    if (-not (Test-Path $venvPython) -or -not (Test-Path $pyPath)) {
        Log ("  [WARN] " + $name + "不可用（venv python 或脚本缺失），" + $missNote)
        return $false
    }
    $out = & $venvPython $pyPath @argList 2>&1
    foreach ($l in $out) {
        $line = [string]$l
        if ($line) {
            if ($line -match '^ERROR\s+(.+)$') { $script:LastPluginError = $Matches[1] }
            Log ("  [" + $tag + "] " + $line)
        }
    }
    if ($LASTEXITCODE -ne 0) {
        Log ("  [WARN] " + $name + "执行失败（exit " + $LASTEXITCODE + "），继续按 MAA 原配置运行")
        return $false
    }
    return $true
}

# ---- 基建收菜不再备份/恢复整份配置（2026-09-17 起）：收菜用独立「收菜」方案，
# farm 用的 Default 全程不被触碰；收菜结束由 infrast_collect.py restore 切回
# Default，即使中断，farm_guard 也会在下次挂机启动前强制切回。

# 子脚本统一用 pwsh 绝对路径拉起：计划任务环境不保证按 PATH 解析裸 pwsh.exe
# （商店版执行别名更解析不到，0x80070002）；本机已换 MSI 版固定在 Program Files。
$pwshExe = "C:\Program Files\PowerShell\7\pwsh.exe"
if (-not (Test-Path $pwshExe)) { $pwshExe = "pwsh" }

function Run-Switch($s) {
    # $s 形如 "slot_switch.ps1 -Server official -Slot official_2"；返回子脚本退出码
    $parts = $s -split ' '
    $sp = Join-Path $scriptDir $parts[0]
    $extraArgs = ""
    if ($parts.Count -gt 1) { $extraArgs = ($parts[1..($parts.Count-1)] -join ' ') }
    Log "Running: $s"
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    # Redirect child process stdout to temp file to avoid encoding issues
    # with the console pipeline (2>&1 on child powershell mangles output)
    $tmpOut = "$scriptDir\switch_output.tmp"
    $proc = Start-Process -FilePath $pwshExe `
        -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$sp`" $extraArgs" `
        -NoNewWindow -PassThru `
        -RedirectStandardOutput $tmpOut
    # 强制 .NET 进程对象持有句柄：PS 5.1 的 -PassThru（不带 -Wait）若不先取 Handle，
    # 结束后 ExitCode 恒为空，登录校验/切号结果会被整体误判
    $null = $proc.Handle
    # 心跳看门狗：login_check 每轮循环刷新 login_heartbeat.tmp（启动子脚本前先删
    # 上一轮残留），判挂死的依据是「距上次心跳 30 秒」而非总时长——游戏更新等待
    # 等多久的合法长耗时都不受限制（用户设定：正常运行不限时，更新等待无限顺延），
    # 真挂死（vision ReadLine / adb 截图卡住）30 秒内被发现。不写心跳的子脚本
    # （切号等快脚本）回退 5 分钟总时长兜底。$hbStaleSec 须大于主循环最坏单轮
    # 耗时（截图+识别+睡眠 ~10-15 秒）的 2 倍。
    $hb = "$scriptDir\login_heartbeat.tmp"
    Remove-Item $hb -Force -ErrorAction SilentlyContinue
    $hbStaleSec = 30
    $hbPollMs = 3000
    $noHbCapSec = 300
    while ($true) {
        if ($proc.WaitForExit($hbPollMs)) { break }
        $hbAge = $null
        if (Test-Path $hb) {
            $hbAge = ((Get-Date) - (Get-Item $hb).LastWriteTime).TotalSeconds
            if ($hbAge -le $hbStaleSec) { continue }
        } elseif ($sw.Elapsed.TotalSeconds -le $noHbCapSec) { continue }
        $why = if ($null -ne $hbAge) { "心跳停止超过 $hbStaleSec 秒" } else { "超过 $noHbCapSec 秒总时长兜底" }
        try {
            $proc.Kill()
            $proc.WaitForExit() | Out-Null   # Kill 是异步的：等句柄释放再清理输出文件
        } catch {}
        Log ("  [ERROR] 子脚本{0}，判定挂死已强制结束" -f $why)
        Log ("  [Switch script finished in {0}s, exit=timeout]" -f
            [math]::Round($sw.Elapsed.TotalSeconds, 1))
        Remove-Item $tmpOut -Force -ErrorAction SilentlyContinue
        Start-Sleep 1
        return 124
    }
    # 无参 WaitForExit 刷新退出状态：保证 ExitCode 可读
    $proc.WaitForExit() | Out-Null
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
    # 1 秒缓冲即可：子脚本结束意味着该阶段已就绪，后续步骤各有自己的就绪判定
    Start-Sleep 1
    return $code
}

# 槽位回拉用共用库的 Ensure-GuideViewed（引导标记清单收敛在 login_device_lib.ps1，
# 与 login_check / capture_account 同源维护，避免多份拷贝漂移——此前本函数漏了
# 这步：MAA 期间游戏新弹过引导时，回拉的槽位仍缺标记，下次切号还会弹）
. (Join-Path $scriptDir "login_device_lib.ps1")

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
    $uidFile = Join-Path $slotDir "uid.txt"
    $expectUid = ""
    if (Test-Path $uidFile) { $expectUid = (Get-Content $uidFile -Raw -ErrorAction SilentlyContinue).Trim() }
    if (-not $devUid -or ($expectUid -and ($devUid -ne $expectUid))) {
        Remove-Item $tmpPp -Force -ErrorAction SilentlyContinue
        Log ("  [WARN] Refresh slot: uid mismatch (device=" + $devUid + ", slot=" + $expectUid + "), skipped")
        return
    }
    $dstShared = Join-Path $slotDir "shared_prefs"
    $dstFiles = Join-Path $slotDir "files\zx"
    New-Item -ItemType Directory -Force $dstShared, $dstFiles | Out-Null
    # uid 校验的就是这份临时文件，直接落盘：省一次 adb pull，且校验与落盘保证为同一份
    Move-Item $tmpPp (Join-Path $dstShared $ppName) -Force
    & $adb -s $device pull "/data/data/$pkg/shared_prefs/HypergryphSdkPreferences.xml" (Join-Path $dstShared "HypergryphSdkPreferences.xml") 2>$null | Out-Null
    & $adb -s $device pull "/data/data/$pkg/files/zx/lc.cache" (Join-Path $dstFiles "lc.cache") 2>$null | Out-Null
    # 引导已看过标记：回拉后补齐清单内缺失项（与 lib 的 Update-SlotData 同口径），
    # 下次切号进对应界面不再弹「首次进入」引导；失败仅告警
    $nGuide = Ensure-GuideViewed (Join-Path $dstShared $ppName)
    if ($nGuide -gt 0) { Log ("  [Refresh] 补写引导已看过标记 {0} 条" -f $nGuide) }
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

# ---- 运行历史留存：每轮结束写一份 JSON 到 scripts\run_history\（GUI「运行历史」页读取）----
# 记录模式/班次/每号结果与失败原因；保留 60 天，且总数超 400 时删最老的
# （切号/收菜多轮时兜底）。写入失败只告警，不影响主流程收尾。
function Save-RunHistory($fatalReason) {
    try {
        New-Item -ItemType Directory -Force $runHistoryDir | Out-Null
        $accs = @()
        foreach ($r in $results) {
            # 历史页的 reason 保持原样可读：重试仍失败的号拼回「重试后仍失败：」前缀
            $reason = [string]$r.Reason
            if (-not $r.OK -and $r.Retried -and $reason) { $reason = "重试后仍失败：" + $reason }
            $accs += [ordered]@{
                name    = [string]$r.Account
                ok      = [bool]$r.OK
                dur_min = $r.Minutes
                skipped = [bool]($null -eq $r.Minutes)
                reason  = $reason
                step    = [string]$r.Step   # 失败步骤名（切号/登录校验/任务开关自检/MAA 运行）
                retried = [bool]$r.Retried  # 该号经过失败重试（成功与否都标）
            }
        }
        $obj = [ordered]@{
            version   = 1
            start     = $runStartTs
            end       = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
            mode      = $runMode
            batch     = $bsBatch
            total_min = [math]::Round($totalSw.Elapsed.TotalMinutes, 1)
            passed    = 0
            failed    = 0
            fatal     = [string]$fatalReason
            accounts  = $accs
        }
        if ($fatalReason) {
            $obj.failed = 1   # 没跑起来（如模拟器启动失败）也留痕，历史页可见
        } else {
            $obj.passed = @($results | Where-Object { $_.OK }).Count
            $obj.failed = @($results | Where-Object { -not $_.OK }).Count
        }
        $json = ConvertTo-Json -InputObject $obj -Depth 5
        # UTF-8 无 BOM（PS 5.1 的 Out-File utf8 带 BOM，GUI 读带 BOM 文件要 utf-8-sig，统一无 BOM）
        [System.IO.File]::WriteAllText(
            (Join-Path $runHistoryDir ("run_" + $runStamp + ".json")), $json,
            (New-Object System.Text.UTF8Encoding($false)))
        $files = @(Get-ChildItem $runHistoryDir -Filter "run_*.json" -File -ErrorAction SilentlyContinue)
        $cutoff = (Get-Date).AddDays(-60)
        $i = 0
        foreach ($f in ($files | Sort-Object Name -Descending)) {
            $i++
            if ($f.LastWriteTime -lt $cutoff -or $i -gt 400) {
                Remove-Item $f.FullName -Force -ErrorAction SilentlyContinue
            }
        }
    } catch {
        Log ("  [WARN] 运行历史写入失败：" + $_.Exception.Message)
    }
}

# ---- 通知推送：经 plugins\notify 把本轮结果发到手机（渠道/密钥读 config.notify）----
# 只推失败：每个失败账号一条（账号 + 失败步骤），成功不推送；
# 手动切号/快速启动（-SwitchTo）人在电脑前，不推送。
# 推送本身失败只记日志，绝不影响收尾（弹窗/关机）。
function Send-RunNotify($title, $body) {
    if ($runMode -eq "switch" -or $runMode -eq "start") { return }
    $n = $null
    if ($config) { $n = $config.notify }
    if (-not $n -or -not $n.enabled) { return }
    if (-not (Test-Path $venvPython) -or -not (Test-Path $notifyPy)) {
        Log "  [WARN] 通知推送不可用（venv python 或插件缺失）"
        return
    }
    [void](Invoke-Plugin $notifyPy "通知" "通知推送" (
        @('send', '--config', $configPath, '--title', $title, '--body', $body)) "通知未发送")
}

# config.accounts 非数组或为空 → 拒绝运行（防跑错号；旧 3 账号点击流程已废弃）
if (-not $accountList -or $accountList.Count -eq 0) {
    Log "FATAL: config.accounts 缺失或不是数组格式，无法运行"
    Log "FATAL: 请在控制台「账号管理」页配置账号后重试"
    Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
    exit 1
}

# ---- -SwitchTo：只切到指定槽位账号、不跑日常（GUI「账号管理 → 切换到此账号」）----
# 槽位不在 config.accounts 时拒绝运行；与 -InfrastCollect 互斥（后者被忽略）。
# 目标账号即使已停用也照切（用户明确指定了要切这个号）。
# -NoLoginCheck：快速启动（GUI 卡片「快速启动」按钮），运行历史记为 start 模式。
if ($SwitchTo) {
    $match = @($accountList | Where-Object { [string]$_.slot -eq $SwitchTo })
    if ($match.Count -eq 0) {
        Log ("FATAL: -SwitchTo 槽位 '" + $SwitchTo + "' 不在 config.accounts 中")
        Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
        exit 1
    }
    $accountList = @($match)
    $InfrastCollect = $false
    $runMode = if ($NoLoginCheck) { "start" } else { "switch" }
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
$pick = $null
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

# ---- 班次账号筛选：schedule.times 每项可配 accounts（账号 id 列表，仪表盘「班次
# 计划 → 账号」按钮勾选）。空/缺失 = 全部账号（旧配置兼容，也含以后新增的号）；
# -SwitchTo 已明确指定单个账号，不再按班次筛选。筛选保留 config.accounts 原顺序。
if (-not $SwitchTo -and $null -ne $pick) {
    $pickAccProp = $pick.PSObject.Properties['accounts']
    if ($null -ne $pickAccProp -and $null -ne $pickAccProp.Value) {
        $shiftIds = @($pickAccProp.Value | ForEach-Object { [string]$_ })
        if ($shiftIds.Count -gt 0) {
            $totalCount = $accountList.Count
            $filtered = @($accountList | Where-Object { $shiftIds -contains [string]$_.id })
            if ($filtered.Count -eq 0) {
                Log ("FATAL: 班次 " + $bsBatch + " 勾选的账号在当前列表中都不存在，跳过本轮")
                Log "FATAL: 请在控制台「仪表盘 → 班次计划 → 账号」重新勾选"
                Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
                exit 1
            }
            $accountList = $filtered
            $names = (@($filtered | ForEach-Object { $_.label }) -join "、")
            Log ("[Schedule] {0}班只跑 {1}/{2} 个账号：{3}" -f $bsBatch, $filtered.Count, $totalCount, $names)
        }
    }
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

if ($InfrastCollect) {
    $runMode = "collect"
    Log "=== Mode: InfrastCollect（基建收菜：独立「收菜」方案，全房间 skip 只收产物不换班） ==="
}

$results = @()
$totalSw = [System.Diagnostics.Stopwatch]::StartNew()

if (-not (Start-MuMu)) {
    Log "FATAL: MuMu failed to start"
    # 没跑起来也留痕 + 推送（无人值守时早上能收到「今天没跑成」的消息）；
    # 通知发出后按班次关机开关收尾（与正常失败路径同一规则），GUI 手动运行不关机
    if (-not $SwitchTo) {
        Save-RunHistory "模拟器启动失败"
        Send-RunNotify "MAA 挂机未运行：模拟器启动失败" ("启动时间：{0}（本轮未执行任何账号）" -f $runStartTs)
        if (-not $NoShutdown -and $shutdownEnabled) {
            Log "模拟器启动失败 - 通知已发送，60秒后自动关机"
            shutdown /s /t 60
        }
    }
    Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
    exit 1
}

# ============ 动态账号流程（config.accounts 数组）============
# 每个账号：槽位切号（重启游戏+推入登录数据，非点击）→ MAA。
# 完整管线抽成 Invoke-AccountRun：主循环与「失败重试」（循环后的 RETRY 段）共用，
# 函数只返回结果对象，$results 的追加/替换由调用方负责。
function Invoke-AccountRun($acc, $idx, $IsRetry) {
    $accLabel = if ($null -ne $acc.label -and [string]$acc.label) { [string]$acc.label } else { "Account $idx" }
    $accServer = if ($null -ne $acc.server -and [string]$acc.server) { [string]$acc.server } else { "official" }
    $accEnabled = $true
    if ($null -ne $acc.enabled) { $accEnabled = [bool]$acc.enabled }
    $accSlot = if ($null -ne $acc.slot) { [string]$acc.slot } else { "" }

    # 横幅格式被 gui/core/logparse.py 的 BANNER_RE 解析（[序号/总数] 必须保持）
    $retryMark = if ($IsRetry) { "（重试）" } else { "" }
    Log " "; Log ("********** [{0}/{1}] {2}{3} **********" -f $idx, $total, $accLabel, $retryMark)
    $sw = [System.Diagnostics.Stopwatch]::StartNew()

    if (-not $accEnabled -and -not $SwitchTo) {
        Log "  [SKIP] disabled in config.json"
        return [PSCustomObject]@{ Account=$accLabel; OK=$true; Duration="skip"; Minutes=$null; Reason=""; Step=""; Retried=$false }
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
        return [PSCustomObject]@{ Account=$accLabel; OK=$false; Duration="0 min"; Minutes=0.0; Reason="切号失败"; Step="切号"; Retried=[bool]$IsRetry }
    }

    # ---- 快速启动（-SwitchTo -NoLoginCheck）：跳过更新等待与登录校验 ----
    # token 已随槽位数据推入，导入完成即可玩；token 失效时游戏会停在登录界面
    # （需手动登录或重新捕获，这正是跳过校验的代价）。也不做 Refresh-SlotData：
    # 数据刚从槽位推入、游戏刚拉起还在加载写文件，此时回拉可能抓到写了一半的
    # 文件覆盖掉槽位里的完好副本。
    if ($SwitchTo -and $NoLoginCheck) {
        Log "  [FAST] 快速启动：已跳过更新等待与登录校验，模拟器保持运行"
    } else {
        # ---- 登录校验：屏幕级确认游戏已登录；未登录自动输账号密码（官服）并刷新槽位 ----
        # 文件级 uid 校验通过不代表游戏真在登录态（token 失效时会回到登录界面）。
        # 游戏更新等待（含失败快判/安装器检测）自 v1.3.2 起由 login_check 内部处理。
        # B服 与官服登录差异大（无标题画面/token 预检不可用/登录界面不可自动登录），
        # 拆为独立的 login_check_bilibili.ps1 单独校验（盲点只在更新进行中禁用，
        # 修复 2026-09-14 更新后无文字画面 240 秒空转超时）。
        if ($accServer -eq "bilibili") {
            $loginOk = ((Run-Switch ("login_check_bilibili.ps1 -Slot {0}" -f $accSlot)) -eq 0)
        } else {
            $loginOk = ((Run-Switch ("login_check.ps1 -Server {0} -Slot {1}" -f $accServer, $accSlot)) -eq 0)
        }
        if (-not $loginOk) {
            Log "  [ERROR] Login check failed - 请在控制台重新捕获该账号"
            return [PSCustomObject]@{ Account=$accLabel; OK=$false; Duration="0 min"; Minutes=0.0; Reason="登录校验失败"; Step="登录校验"; Retried=[bool]$IsRetry }
        }
    }

    # ---- -SwitchTo 模式：切号 + 登录校验完成即停（不跑日常，模拟器保持运行）----
    if ($SwitchTo) {
        # 快速启动不回拉槽位数据（见上方 [FAST] 注释）
        if (-not $NoLoginCheck) { Refresh-SlotData $accServer $accSlot }
        $dur = [math]::Round($sw.Elapsed.TotalMinutes, 1)
        Log ("  [OK] 已切换到「" + $accLabel + "」，模拟器保持运行（不跑日常）")
        return [PSCustomObject]@{ Account=$accLabel; OK=$true; Duration="$dur min"; Minutes=$dur; Reason=""; Step=""; Retried=$false }
    }

    # ---- 跑 MAA（按服务器选对应客户端；-SkipMAA 测试模式跳过）----
    $failReason = ""
    if ($SkipMAA) {
        Log "  [TEST] -SkipMAA: 跳过 MAA，仅验证切号"
        $ok = $true
    } else {
        $accId = if ($null -ne $acc.id -and [string]$acc.id) { [string]$acc.id } else { "" }
        # 插件走同一套调用模板（Invoke-Plugin）；$accId 缺失时全部跳过
        if ($accId) {
            $pluginArgs = @('apply', '--config', $configPath, '--account', $accId, '--server', $accServer)
            if ($InfrastCollect) {
                # ---- 基建收菜：切到独立「收菜」方案（全 skip 不换班），
                # 不碰 Default 方案与班次计划/理智/菲亚梅塔 ----
                [void](Invoke-Plugin $infrastCollectPy "基建收菜" "基建收菜插件" $pluginArgs "按 MAA 原配置运行（可能换班跑全设施）")
            } else {
                # ---- 任务开关自检：收菜配置恢复链一旦断了（备份残留被人工覆盖等），
                # farm 会静默继承「只收菜」配置——MAA 只跑勾着的任务且正常退出，
                # 理智/日常没刷但调度层全绿（2026-09-17 官服漏刷的根因）。
                # 启动 MAA 前强制补开关键任务；自检不过该号按失败处理，走重试+失败通知。
                $guardOk = $false
                if (Test-Path $farmGuardPy) {
                    $guardOk = Invoke-Plugin $farmGuardPy "任务自检" "任务开关自检" $pluginArgs "任务开关未校验"
                } else {
                    Log "  [WARN] 任务自检插件缺失（$farmGuardPy），跳过校验"
                }
                if (-not $guardOk) {
                    Log "  [ERROR] 任务开关自检未通过 - 该号按失败处理（防静默跳过理智/日常）"
                    # 带上插件自报明细（缺哪个任务/方案缺失原因），通知里不用再翻日志
                    $guardReason = "任务开关自检失败"
                    if ($script:LastPluginError) { $guardReason += "（" + $script:LastPluginError + "）" }
                    return [PSCustomObject]@{ Account=$accLabel; OK=$false; Duration="0 min"; Minutes=0.0; Reason=$guardReason; Step="任务开关自检"; Retried=[bool]$IsRetry }
                }
                # ---- 第二理智作战关卡：启动 MAA 前按账号写入第二个 FightTask 的关卡 ----
                $accHasFightPlan = $false
                $planProp = $acc.PSObject.Properties['second_fight_plan']
                if ($null -ne $planProp -and $null -ne $planProp.Value) {
                    $planList = @($planProp.Value)
                    if ($planList.Count -gt 0) {
                        $accHasFightPlan = $true
                    }
                }
                if (-not $accHasFightPlan -and $null -ne $acc.second_fight_stage -and
                    [string]$acc.second_fight_stage) {
                    $accHasFightPlan = $true
                }
                if ($accHasFightPlan) {
                    [void](Invoke-Plugin $fightStagePy "理智关卡" "理智关卡插件" $pluginArgs "第二理智关卡未写入")
                }
                # ---- 精确基建派驻插件：启动 MAA 前按账号写入自定义计划（未启用则恢复 Rotation）----
                [void](Invoke-Plugin $baseSchedulePy "基建插件" "基建插件" ($pluginArgs + @('--batch', $bsBatch)) "继续按 MAA 原配置运行")
                # ---- 菲亚梅塔心情恢复：换班前先恢复目标干员心情 ----
                # 自定义模式（精确基建）由上面生成的计划 JSON 的 Fiammetta 字段生效；
                # 这里写的是常规模式的基建任务参数，两者互斥、都是换班前恢复。
                [void](Invoke-Plugin $fiammettaPy "菲亚梅塔" "菲亚梅塔插件" $pluginArgs "菲亚梅塔设置未写入")
            }
        }
        $maaExe = if ($accServer -eq "bilibili") { $maaBilibili } else { $maaOfficial }
        $maaDir = if ($accServer -eq "bilibili") { $maaBilibiliDir } else { $maaOfficialDir }
        $ok = Run-MAA $maaExe $maaDir $accLabel
        if (-not $ok) {
            # 失败原因跟随心跳判定状态（运行历史/通知里能看到具体死法）
            switch ($script:LastMaaStatus) {
                "stall"      { $failReason = "MAA 无进展超时" }
                "dead"       { $failReason = "MAA 中途退出" }
                "dead-early" { $failReason = "MAA 启动即崩溃（已自动重试一次）" }
                default      { $failReason = "MAA 运行失败" }
            }
        }
    }
    # 把设备上最新的登录数据（弹窗处理标记等）拉回槽位，避免下次切号弹窗重现
    Refresh-SlotData $accServer $accSlot
    $dur = [math]::Round($sw.Elapsed.TotalMinutes, 1)
    # Step = 失败步骤名（切号/登录校验/任务开关自检/MAA 运行），成功为空串；
    # Reason 保持纯失败说明（不再带「重试后仍失败：」前缀，重试状态看 Retried）
    $maaStep = if ($failReason) { "MAA 运行" } else { "" }
    return [PSCustomObject]@{ Account=$accLabel; OK=$ok; Duration="$dur min"; Minutes=$dur; Reason=$failReason; Step=$maaStep; Retried=[bool]$IsRetry }
}

$total = $accountList.Count
$idx = 0
foreach ($acc in $accountList) {
    $idx++
    $results += (Invoke-AccountRun $acc $idx $false)
}

# ---- 失败重试：部分失败（有成功有失败）时，整轮跑完后对失败号按原顺序完整重试
# 一遍（切号→登录校验→插件→MAA 与首跑同一管线）。全部失败视为系统性问题（模拟器/
# ADB/网络/游戏维护），重试大概率也是同样结局，不浪费时间、直接收尾推送。
# 重试仍失败的号才计入最终失败——弹窗/通知/关机都按重试后的结果判定；
# 重试成功的号按成功计（运行历史带 retried 标记）。Reason 只存纯失败说明，
# 「重试后仍失败」由 Retried 标记承载，展示（弹窗/历史/推送）时再拼。
# 手动切号（-SwitchTo）人在电脑前，失败可手动再点，不走自动重试。
if (-not $SwitchTo) {
    $attempted = @($results | Where-Object { $null -ne $_.Minutes })
    $failedFirst = @($attempted | Where-Object { -not $_.OK })
    if ($attempted.Count -gt 0 -and $failedFirst.Count -gt 0 -and $failedFirst.Count -lt $attempted.Count) {
        Log " "
        Log ("=== [RETRY] {0}/{1} 个账号失败，等待 {2} 秒后重试失败号 ===" -f $failedFirst.Count, $attempted.Count, $retryFailedDelaySec)
        Start-Sleep -Seconds $retryFailedDelaySec
        for ($i = 0; $i -lt $accountList.Count; $i++) {
            # $results 与 $accountList 按序一一对应（每个账号恰好追加一条）
            if ($null -eq $results[$i] -or $results[$i].OK -or $null -eq $results[$i].Minutes) { continue }
            # 重试调用传入 $IsRetry=$true，失败返回自带 Retried 标记
            $results[$i] = Invoke-AccountRun $accountList[$i] ($i + 1) $true
        }
        Log ("=== [RETRY] 结束：最终成功 {0}/{1} ===" -f
            (@($results | Where-Object { $_.OK }).Count), $attempted.Count)
    }
}

# Close emulator (config: behavior.close_emulator=false 时跳过；-SkipMAA 测试模式保留模拟器供检查)
if ($InfrastCollect) {
    # 收菜结束：Current 指针切回 Default（MAA 已被 Run-MAA 杀掉，回写不会覆盖）。
    # 中断残留的「收菜」指针由 farm_guard 在下次挂机启动前强制纠正。
    [void](Invoke-Plugin $infrastCollectPy "基建收菜" "基建收菜插件" @('restore', '--config', $configPath) "下次挂机启动时任务自检会强制切回 Default")
    Log "=== [InfrastCollect] MAA 配置已切回 Default ==="
}
if ($SwitchTo) {
    Log " "
    Log "=== [SwitchTo] 切号完成，模拟器保持运行（不跑日常、不关机） ==="
} elseif ($SkipMAA) {
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

# 本轮结果写入运行历史（所有模式都留痕；-SwitchTo 找不到槽位的 FATAL 在上面已单独退出）
Save-RunHistory $null

# Release PID lock and cleanup BEFORE any blocking prompt,
# so the 16:00 run is never blocked by a leftover popup
Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
# Clean up temp file too (paranoid)
Remove-Item "$lockFile.tmp" -Force -ErrorAction SilentlyContinue

# 成功 -> no popup, no confirmation needed
if ($failed -eq 0) {
    Log "ALL ACCOUNTS COMPLETED SUCCESSFULLY"
} else {
    Log ("WARNING: {0} account(s) FAILED!" -f $failed)
}

# 通知推送（config.notify.enabled 时）：按账号逐步骤检查的结果收尾——某个步骤
# 失败的账号各推送一条（标题 = 账号名 + 失败步骤，正文带失败说明/重试标记与本轮
# 汇总）；成功（含重试成功）与停用跳过的账号不推送。手动切号/快速启动（-SwitchTo）
# 人在电脑前，GUI/日志已可见，不推送。
# 顺序上必须先于本机弹窗与关机：失败弹窗会阻塞等点击，无人值守时不能让它挡住推送
if (-not $SwitchTo) {
    $modeLabel = if ($InfrastCollect) { "基建收菜" } else { "挂机" }
    foreach ($r in ($results | Where-Object { -not $_.OK })) {
        # 标题 = 账号 + 失败说明（Reason 本身已含步骤语义，如「切号失败」「MAA 无进展超时」）
        $failText = if ([string]$r.Reason) { [string]$r.Reason } else { "运行失败" }
        $title = "MAA {0}失败：「{1}」{2}" -f $modeLabel, $r.Account, $failText
        # 正文 = 失败步骤名 + 班次/用时/重试标记 + 本轮汇总
        $push = ("失败步骤：{0}" -f $(if ([string]$r.Step) { [string]$r.Step } else { "未知" }))
        $push += ("`n{0}班 · 用时 {1} 分钟" -f $bsBatch, $r.Minutes)
        if ($r.Retried) { $push += " · 重试后仍失败" }
        $push += ("`n本轮共 {0} 个账号：成功 {1} · 失败 {2}" -f @($results).Count, $passed, $failed)
        Send-RunNotify $title $push
    }
}

# 每个时间点的「关机」开关（schedule.times 每项 shutdown）决定本次运行是否关机；
# 成败都关——失败场景通知已在上面发出，通知发完即关机（2026-09 废除「失败不关机」）；
# 关机触发先于失败弹窗：弹窗阻塞等点击，不能挡住 60 秒倒计时。
# GUI 手动运行传 -NoShutdown 跳过；-SwitchTo 切号后模拟器保持运行供手动游戏，绝不关机
if (-not $NoShutdown -and -not $SwitchTo -and $shutdownEnabled) {
    $shutdownWhy = if ($failed -eq 0) { "全部成功" } else { "失败已推送通知" }
    Log ("{0}班结束（{1}）- 60秒后自动关机" -f $bsBatch, $shutdownWhy)
    shutdown /s /t 60
}

# 失败弹窗（本机提示）：放最后——它阻塞等待点击，无人值守时进程随关机结束即可；
# 在场的人读完弹窗可用 shutdown /a 取消倒计时留在机器上排查。
# 非关机班次（shutdown 未开）保持原行为：弹窗等确认，不自动消失
if ($failed -gt 0) {
    $wshell = New-Object -ComObject WScript.Shell
    $body = "有 $failed 个账号失败！`n`n"
    foreach ($r in $results) {
        $s = if ($r.OK) { "OK" } else { "FAIL" }
        $mark = if ($r.OK -and $r.Retried) { "（重试成功）" } else { "" }
        $body += ("  [{0}] {1} - {2}{3}" -f $s, $r.Account, $r.Duration, $mark)
        if (-not $r.OK -and $r.Reason) {
            $retryNote = if ($r.Retried) { "，重试后仍失败" } else { "" }
            $body += "（" + $r.Reason + $retryNote + "）"
        }
        $body += "`n"
    }
    $body += "`nTotal: $totalDur min"
    $null = $wshell.Popup($body, 0, "MAA Auto Farm - 异常", 0x30)
}
