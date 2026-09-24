# MAA Auto Farm v4 — Dual MAA + 槽位切号（非点击）
# 2026-09-22 v4.3 拆分重构：本文件只保留主流程（MAA 等待/运行 + 账号管线 +
# 班次判定 + 收尾），基础设施/插件调度/留痕通知拆到三个库文件：
#   master_lib.ps1      锁 + 日志 + 配置 + 模拟器启停 + 数据清理
#   master_plugins.ps1  Invoke-Plugin + Run-Switch（心跳看门狗）+ Refresh-SlotData
#   master_history.ps1  Save-RunHistory + Send-RunNotify
# dot-source 顺序：lib → plugins（依赖 lib 变量）→ history（依赖 Invoke-Plugin）
# -NoShutdown: GUI 手动运行传入，跳过结束后的自动关机（无论成败）；计划任务不传，行为不变
# -SkipMAA: 测试切号流程用——跳过 MAA、结束时保留模拟器运行供检查
# -InfrastCollect: 基建收菜模式——逐个已启用账号只收制造站/贸易站产物（生产房间
#   skip：不换干员、不用无人机；宿舍自动换休：留空+autofill，疲惫干员进驻、
#   满心情换出，不抽走在岗干员），停用理智/招募/信用/领奖任务，不碰班次计划；
#   使用独立的「收菜」配置方案（切 Current 指针，结束切回 Default），
#   farm 用的 Default 方案全程不被触碰，无需备份/恢复。
#   定时收菜（计划任务 MAA_基建收菜，GUI「仪表盘 → 收菜计划」维护）到点带
#   -InfrastCollect 调本脚本：跑全部已启用账号（不做班次账号筛选）；结束后
#   是否关机按「收菜计划」每项的关机开关判定（任务动作无法按触发器带参数，
#   由本脚本按启动时刻匹配最近一个已到的启用收菜时间点，见 MAIN 的收菜段；
#   无配置不关机）。GUI 手动收菜带 -NoShutdown，永不关机
# -SwitchTo <slot>: 只切到该槽位账号并完成登录校验即停——不跑 MAA、模拟器保持
#   运行、不关机不推送（GUI「账号管理 → 切换到此账号」，切完直接手动游戏）
# -SwitchTo <slot> -NoLoginCheck: 快速启动——只推入槽位登录数据（token）并启动
#   游戏，跳过更新等待与登录校验（两者都在 login_check 内）即停；token 失效时
#   游戏会停在登录界面，需手动输账号密码或重新捕获（GUI「账号管理 → 卡片快速启动」）
# 每轮结束把结果写入 scripts\run_history\run_<时间戳>.json（保留 60 天），
# GUI「运行历史」页读取；config.notify.enabled 时按账号逐步骤检查（切号 → 登录
# 校验 → 任务开关自检 → MAA）：某个步骤失败的账号单独推送一条，标题含账号名与
# 失败步骤；成功推送由 config.notify.on_success 控制（默认关闭）。
# 失败重试：有账号失败时（部分失败或全部失败），整轮跑完后对失败号再完整跑一遍；
# 重试的号无论成败都推送（成功标「重试成功」、仍失败标「重试后仍失败」）；
# 手动切号（人在电脑前）不走自动重试
# 2026-09 性能与成功率优化：模拟器启动接 config 启动等待并轮询开机完成（不再固定
#   睡 15 秒）、45 秒连不上自动重拉实例；MAA 启动即崩溃（零任务进展）自动重试一次；
#   完成信号 FileSystemWatcher + Wait-Event 事件驱动（文件创建瞬间醒来，
#   旧 4 秒轮询平均 2 秒）；各阶段衔接 sleep 3→1 秒
param([switch]$NoShutdown, [switch]$SkipMAA, [switch]$InfrastCollect,
    [string]$SwitchTo = "", [switch]$NoLoginCheck)
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

# ---- 加载库文件（拆分重构 v4.3）：顺序固定，后一层依赖前一层定义的变量/函数 ----
. (Join-Path $PSScriptRoot "master_lib.ps1")
. (Join-Path $PSScriptRoot "master_plugins.ps1")
. (Join-Path $PSScriptRoot "master_history.ps1")

# ---- MAA 完成等待：FileSystemWatcher 事件驱动 + 2 秒兜底轮询（v4.3）----
# 完成信号由 signal_done.bat（MAA 任务结束后回调）创建 maa_done.signal 文件。
# 心跳 = MAA 的 asst.log 持续出现 SubTask 事件（进战斗、战斗中 PRTS 轮询、
# 结算等，正常战斗下每几秒一条）；超过 $t 秒没有任何心跳才判超时。
# 存活判定用 Run-MAA -PassThru 拿到的进程对象（本脚本拉起的那个实例），
# 不按进程名全局扫描——用户手动开的无关 MAA 不影响 dead/dead-early 判定。
# 返回状态字符串：ok / stall（无进展超时）/ dead（进程退出且已执行过任务）/
#   dead-early（进程启动即退出、一次任务都没跑过，由 Run-MAA 自动重试一次）。
# 任务链报错统计：MAA 跑完全部任务链会照常发完成信号，「正常退出」≠ 全部成功
# ——个别任务链失败（TaskChainError）必须从 asst.log 单独识别（2026-09-23
# 官服漏刷事故：MAA 资源过期导致全链报错，MAA 照常退出，调度层却全绿报成功，
# 理智/日常一个没刷）。报错链名记入 $script:LastMaaChainErrors，Run-MAA 据此
# 把「有报错的完成」判为失败（chain-error），走重试 + 失败通知；健康轮历史上
# 零 TaskChainError，不会误伤。
# v4.3 变更：完成信号检测由固定 4 秒轮询 Test-Path 改为 FileSystemWatcher +
#   Wait-Event 事件驱动——signal 文件创建瞬间事件入队，Wait-Event 提前醒来
#   （毫秒级）立即检测到文件，平均发现延迟从 ~2 秒降至接近零。watcher 初始化
#   失败时自然回退 2 秒轮询（Test-Path 兜底不变，不会误判）。其余检查（进程
#   退出/日志心跳/超时）仍按 2 秒间隔轮询（比旧版 4 秒更 responsive）。
function Convert-MaaChainName($chain) {
    # MAA 任务链名 → 中文名（日志/失败原因/通知可读性）；未知链名原样返回
    switch ($chain) {
        "StartUp"     { "开始唤醒" }
        "Recruit"     { "公开招募" }
        "Infrast"     { "基建换班" }
        "Fight"       { "理智作战" }
        "Mall"        { "信用收支" }
        "Award"       { "领取奖励" }
        "Roguelike"   { "集成战略" }
        "Reclamation" { "生息演算" }
        default       { $chain }
    }
}

function Wait-MAADone($t, $maaDir, $maaProc) {
    Log ("Waiting for MAA tasks... ({0} 秒无战斗/任务进展判超时)" -f $t)
    if (Test-Path $signalFile) { Remove-Item $signalFile -Force }
    $logPath = Join-Path $maaDir "debug\asst.log"
    $logPos = -1
    $carry = ""   # 上一块日志尾部（跨读块边界的心跳匹配用，见下方读取段）
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
    # 本次 MAA 启动窗口内报错的任务链（按链名去重；carry 拼接会重扫同一段字节）。
    # 有报错的「正常完成」由 Run-MAA 判为 chain-error 失败，见顶部注释
    $script:LastMaaChainErrors = New-Object System.Collections.Generic.List[string]
    # ---- FileSystemWatcher 完成信号（v4.3）----
    # 不带 -Action 订阅 + 主循环 Wait-Event 等待：文件创建瞬间事件入队，
    # Wait-Event 立即提前醒来（实测毫秒级），比等满轮询间隔快。
    # 不能用「-Action 里 Set ManualResetEvent + WaitOne」的写法——事件 Action
    # 在管道线程被 .NET 阻塞调用（WaitOne）期间不会执行，WaitOne 永远等满
    # 超时（实测 5/5 轮等满 3 秒），事件驱动形同虚设；Wait-Event 由引擎在
    # 等待中处理事件队列，才能真正提前返回
    $fswSignal = $null
    $fswSrc = "MaaDoneSignal"
    try {
        $fswSignal = New-Object System.IO.FileSystemWatcher
        $fswSignal.Path = $scriptDir
        $fswSignal.Filter = "maa_done.signal"
        $fswSignal.EnableRaisingEvents = $true
        $null = Register-ObjectEvent -InputObject $fswSignal `
            -EventName "Created" -SourceIdentifier $fswSrc
    } catch {
        if ($fswSignal) { try { $fswSignal.Dispose() } catch {} }
        $fswSignal = $null   # watcher 初始化失败 → 回退纯轮询（不影响正确性）
    }
    try {
    while ($true) {
        if (Test-Path $signalFile) {
            Log ("MAA finished! {0} min" -f [math]::Round($sw.Elapsed.TotalMinutes, 1))
            if ($script:LastMaaChainErrors.Count -gt 0) {
                Log ("  [WARN] MAA 正常退出，但 {0} 个任务链报错: {1}" -f $script:LastMaaChainErrors.Count,
                    (($script:LastMaaChainErrors | ForEach-Object { Convert-MaaChainName $_ }) -join "、"))
            }
            Start-Sleep 2
            return "ok"
        }
        # MAA 进程意外退出（启动即崩溃/被手动关闭）→ 不傻等心跳超时，快速判失败；
        # 连续 2 轮 HasExited 才判，避开进程刚拉起的瞬间。是否执行过任务决定
        # Run-MAA 是否自动重试（已有进展的退出重试可能重复执行任务，不重试）
        if ($null -eq $maaProc -or $maaProc.HasExited) {
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
                $readFail = 0   # 恢复可读即清零连击（旧版只在匹配心跳时清零，安静期偶发失败会跨成功读取累积）
                if ($len -lt $logPos) { $logPos = 0; $carry = "" }   # asst.log 被轮转/重建
                if ($len -gt $logPos) {
                    $fs.Seek($logPos, 'Begin') | Out-Null
                    $n = $len - $logPos
                    $buf = New-Object byte[] $n
                    # Read 不保证一次读满（短读合法）：循环读满，且只按实际读到
                    # 的字节推进 logPos，余下字节下轮接着读
                    $off = 0
                    while ($off -lt $n) {
                        $r = $fs.Read($buf, $off, $n - $off)
                        if ($r -le 0) { break }
                        $off += $r
                    }
                    $logPos += $off
                    # 心跳串可能正好被读块边界切开（2 秒一轮按字节续读）：保留
                    # 上块尾部 64 字符拼到本块前面再匹配；UTF-8 多字节字符跨界
                    # 至多解码成替换符，不影响 ASCII 心跳串匹配
                    $chunk = $carry + [System.Text.Encoding]::UTF8.GetString($buf, 0, $off)
                    if ($chunk.Length -gt 64) { $carry = $chunk.Substring($chunk.Length - 64) }
                    else { $carry = $chunk }
                    if ($chunk -match 'append_callback \| SubTask') {
                        $lastAct = Get-Date
                        $sawProgress = $true
                        $readFail = 0
                    }
                    # 任务链报错识别（见顶部注释）：匹配链名并首次出现时实时落日志。
                    # 正则与链名合计约 40 字符，64 字符 carry 保证跨读块边界不漏配
                    foreach ($mc in [regex]::Matches($chunk, 'TaskChainError \{"taskchain":"([^"]+)"')) {
                        $errChain = $mc.Groups[1].Value
                        if (-not $script:LastMaaChainErrors.Contains($errChain)) {
                            $script:LastMaaChainErrors.Add($errChain)
                            Log ("  [WARN] MAA 任务链报错: " + (Convert-MaaChainName $errChain) + "（" + $errChain + "）")
                        }
                    }
                }
                $fs.Dispose()
            } catch {
                if ($fs) { try { $fs.Dispose() } catch {} }
                # 日志读不到（杀软扫描/MAA 独占写等瞬时竞争）：连败 4 次
                # （约 16 秒）就把 lastAct 拨到超时点、当轮立即判 stall——正常
                # 战斗会被瞬时锁误杀。补强为「文件增长判活」：MAA 活着就持续
                # 追加日志，元数据查询（Get-Item）不打开数据流、不受数据锁
                # 影响（已实测）——在长即视为有进展刷新 lastAct；查不到或不在
                # 长则不动 lastAct，由统一的 stall 计时自然兜底（与日志可读时
                # 完全同口径，不会更快也不会更慢）
                $readFail++
                $len2 = -1
                try { $len2 = (Get-Item -LiteralPath $logPath -ErrorAction Stop).Length } catch { }
                if ($len2 -gt 0) {
                    if ($logPos -ge 0 -and $len2 -gt $logPos) {
                        $lastAct = Get-Date   # 在长 = MAA 活着在写，进度照常
                        $logPos = $len2       # 增长字节已计入基线，成功读取后无需重扫
                        $readFail = 0
                    } elseif ($logPos -lt 0) {
                        $logPos = $len2       # 首次建立基线，不给进度
                    }
                }
                if ($readFail -eq 3) {
                    Log "  [WARN] asst.log 连续 3 次读取失败且文件无增长（疑似被独占），按无进展计时中"
                }
            }
        }
        $idleSec = ((Get-Date) - $lastAct).TotalSeconds
        if ($idleSec -ge $t) {
            Log ("ERROR: MAA stall: 已 {0} 分钟没有战斗/任务进展，判定超时" -f [math]::Round($idleSec / 60, 1))
            return "stall"
        }
        # 等完成信号事件（文件创建瞬间 Wait-Event 提前返回）或 2 秒超时；
        # 超时后正常做下一轮进程/日志/超时检查。watcher 不可用时退化为
        # Start-Sleep 2 秒轮询（Test-Path 兜底不变）。醒来后排空事件队列，
        # 防残留事件让下一轮 Wait-Event 假醒（假醒无害，Test-Path 兜底，
        # 但排空更干净）
        if ($fswSignal) {
            [void](Wait-Event -SourceIdentifier $fswSrc -Timeout 2)
            Get-Event -SourceIdentifier $fswSrc -ErrorAction SilentlyContinue |
                Remove-Event -ErrorAction SilentlyContinue
        } else {
            Start-Sleep 2
        }
    }
    } finally {
        # 清理 watcher 与事件订阅（防句柄/队列泄漏；Wait-MAADone 每次调用各建各拆。
        # 先排空再注销：注销后残留队列事件会跟随 SourceIdentifier 一起失效，先排空更稳）
        if ($fswSignal) {
            Get-Event -SourceIdentifier $fswSrc -ErrorAction SilentlyContinue |
                Remove-Event -ErrorAction SilentlyContinue
            Unregister-Event -SourceIdentifier $fswSrc -Force -ErrorAction SilentlyContinue
            try { $fswSignal.Dispose() } catch {}
        }
    }
}

function Run-MAA($exe, $dir, $label) {
    Log ("=== Run MAA [" + $label + "] ===")
    Stop-StaleMaa $dir
    Start-Sleep 2
    if (Test-Path $signalFile) { Remove-Item $signalFile -Force }
    Log "Launching MAA..."
    # -PassThru 拿到本脚本拉起的进程对象：存活判定与收尾只针对它，
    # 用户手动开的无关 MAA 不再被误杀/误判
    $maaProc = Start-Process $exe -WorkingDirectory $dir -PassThru
    Start-Sleep 5
    $status = Wait-MAADone $maaStallTimeoutSec $dir $maaProc
    $script:LastMaaStatus = $status
    if ($status -eq "dead-early") {
        # 启动即崩溃（一次任务都没跑）自动重试一次：偶发崩溃/被占用拦截时自愈。
        # 已有任务进展的退出不重试（重试可能重复执行任务），按失败处理。
        Log "WARN: 3 秒后自动重试一次 MAA 启动"
        Start-Sleep 3
        Log "Launching MAA (retry)..."
        $maaProc = Start-Process $exe -WorkingDirectory $dir -PassThru
        Start-Sleep 5
        $status = Wait-MAADone $maaStallTimeoutSec $dir $maaProc
        $script:LastMaaStatus = $status
    }
    # 收尾只杀自己拉起的实例（按 Id），不按名字全局杀
    if ($maaProc -and -not $maaProc.HasExited) {
        Stop-Process -Id $maaProc.Id -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep 2
    $ok = ($status -eq "ok")
    if ($ok -and $script:LastMaaChainErrors.Count -gt 0) {
        # 有报错的「正常完成」不是成功：资源过期/界面改动会让任务链逐个失败，
        # MAA 照常退出发信号——按失败处理，走整轮重试 + 失败通知
        $ok = $false
        $script:LastMaaStatus = "chain-error"
        Log ("ERROR: MAA [" + $label + "] 完成，但任务链报错: " +
            (($script:LastMaaChainErrors | ForEach-Object { Convert-MaaChainName $_ }) -join "、"))
    } elseif ($ok) { Log ("MAA [" + $label + "] completed successfully") }
    else     { Log ("ERROR: MAA [" + $label + "] FAILED or timed out") }
    return $ok
}

# config.accounts 非数组或为空 → 拒绝运行（防跑错号；旧 3 账号点击流程已废弃）
if (-not $accountList -or $accountList.Count -eq 0) {
    Log "FATAL: config.accounts 缺失或不是数组格式，无法运行"
    Log "FATAL: 请在控制台「账号管理」页配置账号后重试"
    Release-Lock
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
        Release-Lock
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
# -SwitchTo 已明确指定单个账号，不再按班次筛选；收菜模式同样不筛选——定时收菜
# 与手动收菜口径一致，跑全部已启用账号（班次 accounts 只约束挂机跑理智/日常的
# 账号范围）。筛选保留 config.accounts 原顺序。
if (-not $SwitchTo -and -not $InfrastCollect -and $null -ne $pick) {
    $pickAccProp = $pick.PSObject.Properties['accounts']
    if ($null -ne $pickAccProp -and $null -ne $pickAccProp.Value) {
        $shiftIds = @($pickAccProp.Value | ForEach-Object { [string]$_ })
        if ($shiftIds.Count -gt 0) {
            $totalCount = $accountList.Count
            $filtered = @($accountList | Where-Object { $shiftIds -contains [string]$_.id })
            if ($filtered.Count -eq 0) {
                Log ("FATAL: 班次 " + $bsBatch + " 勾选的账号在当前列表中都不存在，跳过本轮")
                Log "FATAL: 请在控制台「仪表盘 → 班次计划 → 账号」重新勾选"
                Release-Lock
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
    Log "=== Mode: InfrastCollect（基建收菜：独立「收菜」方案，生产房间 skip 只收产物、宿舍自动换休） ==="
    # ---- 收菜关机开关：按「收菜计划」判定，与挂机班次的关机开关完全无关 ----
    # 收菜计划任务的动作不带 -NoShutdown（无法按触发器带参数），关机由这里按
    # 启动时刻匹配 config.schedule.collect_times：取最近一个已到的启用时间点的
    # shutdown（00:00-最早时间点之间属昨天最后一个时间点，与挂机班次判定同口径）。
    # 无配置/无启用时间不关机（安全兜底：误关机代价远大于少关一次）；
    # GUI 手动收菜带 -NoShutdown，不会走到下面的关机分支
    $shutdownEnabled = $false
    $collectEntries = @()
    if ($config -and $null -ne $config.schedule -and
        $null -ne $config.schedule.collect_times -and
        $config.schedule.collect_times -is [System.Array]) {
        foreach ($ct in $config.schedule.collect_times) {
            if ($ct -and $ct.time -and $ct.enabled) { $collectEntries += $ct }
        }
    }
    if ($collectEntries.Count -gt 0) {
        $sortedC = @($collectEntries | Sort-Object { $_.time })
        $nowC = Get-Date
        $runMinC = $nowC.Hour * 60 + $nowC.Minute
        $pickC = $null
        foreach ($ct in $sortedC) {
            $minsC = [int]$ct.time.Substring(0, 2) * 60 + [int]$ct.time.Substring(3, 2)
            if ($minsC -le $runMinC) { $pickC = $ct } else { break }
        }
        if ($null -eq $pickC) { $pickC = $sortedC[$sortedC.Count - 1] }
        $shutdownEnabled = [bool]$pickC.shutdown
        Log ("[Schedule] 收菜关机开关：{0}（按收菜计划 {1}）" -f
            $(if ($shutdownEnabled) { "开" } else { "关" }), $pickC.time)
    } else {
        Log "[Schedule] 收菜计划无启用时间点，本次收菜结束不关机"
    }
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
    Release-Lock
    exit 1
}

# ============ 动态账号流程（config.accounts 数组）============
# 每个账号：槽位切号（重启游戏+推入登录数据，非点击）→ MAA。
# 完整管线抽成 Invoke-AccountRun：主循环与「失败重试」（循环后的 RETRY 段）共用，
# 函数只返回结果对象，$results 的追加/替换由调用方负责。
function Invoke-AccountRun($acc, $idx, $IsRetry, [switch]$NoRefresh) {
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
                "stall"       { $failReason = "MAA 无进展超时" }
                "dead"        { $failReason = "MAA 中途退出" }
                "dead-early"  { $failReason = "MAA 启动即崩溃（已自动重试一次）" }
                "chain-error" { $failReason = "MAA 任务链失败: " +
                    (($script:LastMaaChainErrors | ForEach-Object { Convert-MaaChainName $_ }) -join "、") }
                default       { $failReason = "MAA 运行失败" }
            }
        }
    }
    # 把设备上最新的登录数据（弹窗处理标记等）拉回槽位，避免下次切号弹窗重现。
    # -NoRefresh（非最后一个号）：主循环在返回后以后台进程拉起 refresh_slot.ps1，
    # 与下一个号的 slot_switch 并行执行（每号省 ~5-8 秒）；最后一个号无下一号
    # 可流水线，保持同步回拉保证收尾完整。
    if (-not $NoRefresh) { Refresh-SlotData $accServer $accSlot }
    $dur = [math]::Round($sw.Elapsed.TotalMinutes, 1)
    # Step = 失败步骤名（切号/登录校验/任务开关自检/MAA 运行），成功为空串；
    # Reason 保持纯失败说明（不再带「重试后仍失败：」前缀，重试状态看 Retried）
    $maaStep = if ($failReason) { "MAA 运行" } else { "" }
    return [PSCustomObject]@{ Account=$accLabel; OK=$ok; Duration="$dur min"; Minutes=$dur; Reason=$failReason; Step=$maaStep; Retried=[bool]$IsRetry }
}

$total = $accountList.Count
$idx = 0
$bgRefreshProcs = @()   # 后台回拉进程（每号一个；主流程不阻塞等它，收尾时兜底等待）
foreach ($acc in $accountList) {
    $idx++
    # 非最后一个号传 -NoRefresh：MAA 完成后立刻返回、不等同步回拉，
    # 由下方 Start-Process 拉起后台回拉与下一号的 slot_switch 并行
    $isLast = ($idx -eq $total)
    $results += (Invoke-AccountRun $acc $idx $false -NoRefresh:(-not $isLast))
    # 后台回拉（仅完整跑成功了的非最后号；skipped / 失败号不需要——失败号由
    # 重试管线跑成功后的同步回拉兜底）。注意 -WindowStyle 与 -NoNewWindow
    # 分属 Start-Process 的不同参数集，不能同时给（实测报参数集解析失败、
    # 进程起不来）；Hidden 独立隐藏窗口，子脚本日志走 master_log.txt 直写
    $accSlot = if ($null -ne $acc.slot) { [string]$acc.slot } else { "" }
    $lastResult = $results[$idx-1]
    if (-not $isLast -and $lastResult.OK -and $null -ne $lastResult.Minutes -and $accSlot) {
        $accSrv = if ($acc.server) { [string]$acc.server } else { "official" }
        $rp = Start-Process -FilePath $pwshExe `
            -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$scriptDir\refresh_slot.ps1`" -Server $accSrv -Slot $([string]$acc.slot)" `
            -WindowStyle Hidden -PassThru
        if ($null -eq $rp) {
            Log "  [WARN] 后台回拉 $accSlot 启动失败（Start-Process 未返回进程），该号下次切号前请手动检查"
        } else {
            $bgRefreshProcs += $rp
            Log "  [Pipeline] 后台回拉 $accSlot 已启动（与下一号切号并行）"
        }
    }
}
# 收尾兜底：等待残留的后台回拉完成（正常 5-8 秒；最多 30 秒防 ADB 挂死阻塞收尾）
foreach ($bp in $bgRefreshProcs) {
    if ($bp -and -not $bp.HasExited) { [void]$bp.WaitForExit(30000) }
    if ($bp) { try { $bp.Dispose() } catch {} }
}

# ---- 失败重试：有账号失败时（部分失败或全部失败），整轮跑完后对失败号按原顺序
# 完整重试一遍（切号→登录校验→插件→MAA 与首跑同一管线）。全失败常见于系统性
# 问题（游戏维护/更新），重试等于赌维护在两轮之间结束——接受这份耗时换取成功率。
# 重试仍失败的号才计入最终失败——弹窗/通知/关机都按重试后的结果判定；
# 重试成功的号按成功计（运行历史带 retried 标记）。Reason 只存纯失败说明，
# 「重试后仍失败」由 Retried 标记承载，展示（弹窗/历史/推送）时再拼。
# 手动切号（-SwitchTo）人在电脑前，失败可手动再点，不走自动重试。
if (-not $SwitchTo) {
    $attempted = @($results | Where-Object { $null -ne $_.Minutes })
    $failedFirst = @($attempted | Where-Object { -not $_.OK })
    if ($attempted.Count -gt 0 -and $failedFirst.Count -gt 0) {
        Log " "
        Log ("=== [RETRY] {0}/{1} 个账号失败，等待 {2} 秒后重试失败号 ===" -f $failedFirst.Count, $attempted.Count, $retryFailedDelaySec)
        Start-Sleep -Seconds $retryFailedDelaySec
        for ($i = 0; $i -lt $accountList.Count; $i++) {
            # $results 与 $accountList 按序一一对应（每个账号恰好追加一条）
            if ($null -eq $results[$i] -or $results[$i].OK -or $null -eq $results[$i].Minutes) { continue }
            # 记下首跑失败原因：重试翻身的号推送「重试成功」时展示死因
            $prevReason = [string]$results[$i].Reason
            # 重试调用传入 $IsRetry=$true，失败返回自带 Retried 标记
            $results[$i] = Invoke-AccountRun $accountList[$i] ($i + 1) $true
            if ($results[$i].OK -and $prevReason) {
                Add-Member -InputObject $results[$i] -NotePropertyName FirstFailReason `
                    -NotePropertyValue $prevReason
            }
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
    # 两步关法（同 gui/core/adb.py close_emulator）：先关虚拟机，再 main close 关主程序。
    # 直接杀 MuMuNxMain 会被服务拉起，必须用 mumu-cli main close 正常退出。
    & $cli control -v 0 shutdown 2>$null | Out-Null
    Start-Sleep 5
    & $cli main close 2>$null | Out-Null
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

# Release lock and cleanup BEFORE any blocking prompt,
# so the 16:00 run is never blocked by a leftover popup
Release-Lock

# 成功 -> no popup, no confirmation needed
if ($failed -eq 0) {
    Log "ALL ACCOUNTS COMPLETED SUCCESSFULLY"
} else {
    Log ("WARNING: {0} account(s) FAILED!" -f $failed)
}

# 通知推送（config.notify.enabled 时）：按账号逐步骤检查的结果收尾——某个步骤
# 失败的账号各推送一条（标题 = 账号名 + 失败说明，正文带失败步骤/重试标记与本轮
# 汇总）；重试成功的账号也单独推送一条「重试成功」（正文带首跑死因）——重试的号
# 无论成败都通知（2026-09-23 起）。手动切号/快速启动（-SwitchTo）人在电脑前，
# GUI/日志已可见，不推送。
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
    # 重试成功的号单独推送（重试无论成败都通知）：首跑报错的号用户必须知道
    # 最终结局——仍失败的走上面失败推送（带「重试后仍失败」），翻身的走这里
    foreach ($r in ($results | Where-Object { $_.OK -and $_.Retried })) {
        $firstReason = if ($r.PSObject.Properties['FirstFailReason'] -and [string]$r.FirstFailReason) {
            [string]$r.FirstFailReason
        } else { "运行失败" }
        $title = "MAA {0}重试成功：「{1}」" -f $modeLabel, $r.Account
        $push = "首跑失败：$firstReason`n重试已成功，本轮按成功计"
        $push += ("`n{0}班 · 用时 {1} 分钟" -f $bsBatch, $r.Minutes)
        $push += ("`n本轮共 {0} 个账号：成功 {1} · 失败 {2}" -f @($results).Count, $passed, $failed)
        Send-RunNotify $title $push
    }
}

# 成功汇总推送（config.notify.on_success 控制是否启用；v4.3 新增）：
# 全部账号成功时发一条极简汇总（班次 + 每号用时），让用户不开控制台也能
# 确认今天跑过了。skipped（停用）的号不列在推送里（只列实际跑了的）。
# 与失败推送同为「先推送后关机」次序：关机倒计时不能挡住推送发出
if (-not $SwitchTo -and $config -and $null -ne $config.notify -and
    $config.notify.on_success -and $failed -eq 0) {
    $ranAccounts = @($results | Where-Object { $null -ne $_.Minutes })
    if ($ranAccounts.Count -gt 0) {
        $modeLabel = if ($InfrastCollect) { "基建收菜" } else { "挂机" }
        $title = "MAA {0}完成 ✅" -f $modeLabel
        $push = ("{0}班 · {1} 个账号全部成功 · 总用时 {2} 分钟" -f $bsBatch, $ranAccounts.Count, $totalDur)
        foreach ($r in $ranAccounts) {
            $mark = if ($r.Retried) { "（重试成功）" } else { "" }
            $push += ("`n  {0} {1}min{2}" -f $r.Account, $r.Minutes, $mark)
        }
        Send-RunNotify $title $push
    }
}

# 每个时间点的「关机」开关（schedule.times 每项 shutdown）决定本次运行是否关机；
# 成败都关——失败场景通知已在上面发出，通知发完即关机（2026-09 废除「失败不关机」）；
# 关机触发先于失败弹窗：弹窗阻塞等点击，不能挡住 60 秒倒计时。
# GUI 手动运行传 -NoShutdown 跳过；-SwitchTo 切号后模拟器保持运行供手动游戏，绝不关机
if (-not $NoShutdown -and -not $SwitchTo -and $shutdownEnabled) {
    $shutdownWhy = if ($failed -eq 0) { "全部成功" } else { "失败已推送通知" }
    # 收菜模式（定时收菜）到这里说明该收菜计划项开了关机；挂机模式按班次显示
    $shutdownWhat = if ($InfrastCollect) { "定时收菜" } else { "$bsBatch 班" }
    Log ("{0}结束（{1}）- 60秒后自动关机" -f $shutdownWhat, $shutdownWhy)
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
