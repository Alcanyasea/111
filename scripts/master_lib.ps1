# master_lib.ps1 — 基础设施层：进程锁 / 日志 / 配置读取 / 路径变量 /
# 模拟器启停 / 数据清理。由 master.ps1 dot-source 引入，函数与变量直接
# 在主脚本作用域生效（与原来写在同一文件里完全等价）。

# ---- master.lock 句柄独占锁 — 防计划任务双触发并发双跑（v4.2 重构）：----
# 排他性由「打开锁文件并持有 FileShare.Read 独占句柄到进程退出」保证，OS 兜底：
# 持有者活着 = 其他 master 再用 ReadWrite 打开必失败（IOException）；持有者死了 =
# OS 自动关闭其句柄，残留锁文件可被下一轮 OpenOrCreate 直接接管覆写，无需判活。
# 旧版 CreateNew+读内容判活方案的两个洞由此消除：
# 1) A 创建锁后、写入内容前的瞬间，B 读到空内容会把 A 的活锁误判成陈旧锁删掉
#    （双跑）——新方案完全不读内容判活，不存在该窗口；
# 2) 断电残留锁的 PID 被复用给别的 pwsh 窗口时，需要启动时间/命令行层层兜底——
#    新方案里死进程句柄必然被 OS 回收，PID 复用与判活彻底无关。
# FileShare.Read 允许 GUI/导出脚本（runner.lock_details、export_operbox、cleanup）
# 在挂机运行中读取锁内容做诊断显示，不破坏现有读方；但写打开仍互斥。
# 锁内容仍写 "PID|进程启动时间(UTC Ticks)"（ASCII，v4.1 格式不变），只作诊断，
# 不参与本脚本的判活。
$lockFile = "D:\1\scripts\master.lock"
$script:lockFs = $null
$myStartTicks = (Get-Process -Id $PID).StartTime.ToUniversalTime().Ticks
try {
    $script:lockFs = [System.IO.File]::Open($lockFile,
        [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::Read)
} catch {
    # 打不开 = 有活实例持有句柄（死进程的句柄会被 OS 回收，不会阻塞）；其余
    # 异常（权限等）同样保守放弃——宁可少跑一轮，不冒双跑风险
    $lockContent = ""
    try { $lockContent = [System.IO.File]::ReadAllText($lockFile).Trim() } catch { }
    Write-Host "Another instance is already running (or lock unavailable: $lockContent). Exiting."
    try {
        Add-Content -Path "D:\1\scripts\master_log.txt" -Value ("[{0}] Another instance is already running (lock: {1}), exit." -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $lockContent) -ErrorAction SilentlyContinue
    } catch { }
    exit 0
}
# 接管陈旧锁时文件可能比新内容长（旧 PID|ticks 更长）：先截断再写，防残留
# 字节拼进 ticks 字段被 GUI 读出错值。写入后不关句柄：独占持有到进程退出
$script:lockFs.SetLength(0)
$lockBytes = [System.Text.Encoding]::ASCII.GetBytes("$PID|$myStartTicks")
$script:lockFs.Write($lockBytes, 0, $lockBytes.Length)
$script:lockFs.Flush()

function Release-Lock {
    # 收尾统一出口：先关句柄再删文件（句柄未关时 Remove-Item 会因占用失败）；
    # 崩溃/被杀场景句柄由 OS 回收，残留锁文件由下一轮 OpenOrCreate 直接接管
    try { if ($script:lockFs) { $script:lockFs.Close() } } catch { }
    $script:lockFs = $null
    Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
}

# ---- 默认路径（config.json 可覆盖；见下方配置读取段）----
$adb = "D:\软件\MuMu模拟器\MuMuPlayer\nx_main\adb.exe"
$device = "127.0.0.1:16384"
$cli = "D:\软件\MuMu模拟器\MuMuPlayer\nx_main\mumu-cli.exe"
# 官服 MAA 目录默认值：探测 D:\软件\MAA 下 MAA-v*-win-x64（须含 MAA.exe）取版本号
# 最新。MAA 版本更新是原地升级、目录名不变（gui maa_update 同口径），目录名里的
# 版本号只在首次安装时真实，因此不钉死具体版本；config.json 的 paths.maa_official
# 永远优先（见下方配置读取段），这段探测只补「无配置文件」时的默认值
$maaOfficialDir = "D:\软件\MAA\MAA-v6.11.1-win-x64"
$probeVer = $null
foreach ($probeDir in (Get-ChildItem "D:\软件\MAA" -Directory -ErrorAction SilentlyContinue)) {
    if ($probeDir.Name -match '^MAA-v(\d+(?:\.\d+)*)-win-x64$' -and
        (Test-Path (Join-Path $probeDir.FullName "MAA.exe"))) {
        $v = [version]$Matches[1]
        if ($null -eq $probeVer -or $v -gt $probeVer) {
            $probeVer = $v
            $maaOfficialDir = $probeDir.FullName
        }
    }
}
$maaOfficial = Join-Path $maaOfficialDir "MAA.exe"
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
. (Join-Path $PSScriptRoot "config_lib.ps1")
$config = Read-AppConfigJson "D:\1\config.json"
# 插件/通知统一经 --config 传入的配置路径：必须显式定义——未定义的变量求值
# 为 $null，pwsh 7 会把参数数组里的 $null 元素直接丢弃，子进程 argparse 收到
# 「--config --account」报 expected one argument（exit 2），farm 全线误判自检失败
$configPath = "D:\1\config.json"
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
        # 键名沿用 maa_min（GUI「运行设置」同键），语义为"无进展判超时分钟数"
        $maaStallTimeoutSec = [int]$config.timeouts.maa_min * 60
    }
    if ($null -ne $config.timeouts -and $null -ne $config.timeouts.launch_wait_sec) {
        $mumuLaunchTimeoutSec = [int]$config.timeouts.launch_wait_sec
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

function Stop-StaleMaa([string]$maaDir) {
    # 启动前清理：只杀「本 MAA 目录」下的残留实例（上次崩溃/异常遗留）。按进程
    # 可执行文件路径前缀匹配，不按名字全局杀——用户手动开的别的 MAA（人工
    # 排障/查看）不能误杀；同目录的手动 MAA 与脚本会写同一份配置，必须清
    Get-Process -Name "MAA" -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            $p = $_.Path
            if ($p -and $p.StartsWith($maaDir, [System.StringComparison]::OrdinalIgnoreCase)) {
                Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
            }
        } catch { }   # Path 读不到（权限/刚好退出）的实例不碰
    }
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
