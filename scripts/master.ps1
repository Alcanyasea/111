# MAA 自动挂机主控：班次选择 → 逐账号执行 → 失败重试 → 历史/通知/关机。
# 兼容现有 GUI、计划任务与 bat 入口；模块加载顺序见下方。
# -NoShutdown：手动操作不关机；定时任务按班次设置决定。
# -SkipMAA：只验证切号/登录，保留模拟器。
# -InfrastCollect：全部启用账号只跑收菜，使用独立方案，结束恢复 Default。
# -SwitchTo <slot>：只切号与登录验证；忽略停用和班次筛选，不跑任务、不关机。
# -SwitchTo <slot> -NoLoginCheck：快速启动，不做更新/登录检查和槽位回拉。
# 账号流程由 master_pipeline.ps1 负责；设备回拉与下一号切换严格串行。
# MAA 生命周期在 master_maa.ps1；历史 JSON 和 GUI 日志协议保持兼容。
param([switch]$NoShutdown, [switch]$SkipMAA, [switch]$InfrastCollect,
    [string]$SwitchTo = "", [switch]$NoLoginCheck)
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

# ---- 加载库文件（拆分重构 v4.3）：顺序固定，后一层依赖前一层定义的变量/函数 ----
. (Join-Path $PSScriptRoot "master_lib.ps1")
. (Join-Path $PSScriptRoot "master_plugins.ps1")
. (Join-Path $PSScriptRoot "master_history.ps1")

. (Join-Path $PSScriptRoot "master_maa.ps1")
. (Join-Path $PSScriptRoot "master_pipeline.ps1")

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
        if (-not $t) { continue }
        $tt = ([string]$t.time).Trim()
        # enabled 缺失视为启用（与 GUI config.py / base_schedule 同口径）
        $isEnabled = if ($null -ne $t.PSObject.Properties['enabled']) { [bool]$t.enabled } else { $true }
        if ($isEnabled -and $tt -match '^([01][0-9]|2[0-3]):[0-5][0-9]$') {
            $t.time = $tt   # 归一化：后续 Substring 取值依赖无前后空格
            $scheduleEntries += $t
        } elseif ($isEnabled) {
            Log ("  [WARN] 忽略非法班次时间：'{0}'（应为 HH:MM）" -f $tt)
        }
    }
}
# 旧格式兜底：config.json 尚未被 GUI 保存为新格式时，schedule.morning/evening 仍存在
if ($scheduleEntries.Count -eq 0 -and $config -and $null -ne $config.schedule -and
    $null -ne $config.schedule.morning -and $null -ne $config.schedule.evening) {
    foreach ($kv in @(@("morning", "morning_shutdown"), @("evening", "evening_shutdown"))) {
        $item = $config.schedule.($kv[0])
        # enabled 缺失视为启用（与 GUI config.py 迁移同口径）；停用项不参与
        # 批次/关机判定（迁移后即为 enabled=false 的 times 项）
        $itemEnabled = if ($null -ne $item.PSObject.Properties['enabled']) { [bool]$item.enabled } else { $true }
        if (-not $itemEnabled) { continue }
        $t = ([string]$item.time).Trim()
        if ($t -match '^([01][0-9]|2[0-3]):[0-5][0-9]$') {
            $scheduleEntries += [pscustomobject]@{
                time    = $t
                enabled = $true
                shutdown = [bool]$config.behavior.($kv[1])
            }
        } else {
            Log ("  [WARN] 忽略非法班次时间：'{0}'（应为 HH:MM）" -f $t)
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
    $mm = [int]$pick.time.Substring(3, 2)
    if ($mm -ne 0) { $bsBatch += ('{0:00}分' -f $mm) }
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
    $debugDir = Join-Path $scriptDir "debug"
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
            if (-not $ct) { continue }
            $ctTime = ([string]$ct.time).Trim()
            # enabled 缺失视为启用（与 GUI config.py 同口径）
            $ctEnabled = if ($null -ne $ct.PSObject.Properties['enabled']) { [bool]$ct.enabled } else { $true }
            if ($ctEnabled -and $ctTime -match '^([01][0-9]|2[0-3]):[0-5][0-9]$') {
                $ct.time = $ctTime
                $collectEntries += $ct
            } elseif ($ctEnabled) {
                Log ("  [WARN] 忽略非法收菜时间：'{0}'（应为 HH:MM）" -f $ctTime)
            }
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

# 每个账号独占设备完成切号、登录、任务和槽位回拉；首跑/重试共用账号管线。
$total = $accountList.Count
$idx = 0
foreach ($acc in $accountList) {
    $idx++
    $results += (Invoke-AccountRun $acc $idx $false)
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
            (@($results | Where-Object { $_.OK -and $null -ne $_.Minutes }).Count),
            $attempted.Count)
    }
}

# Close emulator (config: behavior.close_emulator=false 时跳过；-SkipMAA 测试模式保留模拟器供检查)
if ($InfrastCollect) {
    # 收菜结束：Current 指针切回 Default（MAA 已被 Run-MAA 杀掉，回写不会覆盖）。
    # 中断残留的「收菜」指针由 farm_guard 在下次挂机启动前强制纠正。
    if (Invoke-Plugin $infrastCollectPy "基建收菜" "基建收菜插件" @('restore', '--config', $configPath) "下次挂机启动时任务自检会强制切回 Default") {
        Log "=== [InfrastCollect] MAA 配置已切回 Default ==="
    } else {
        Log "  [WARN] 收菜配置恢复失败，下次挂机将由任务自检恢复 Default"
    }
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
$passed = @($results | Where-Object { $_.OK -and $null -ne $_.Minutes }).Count
$failed = @($results | Where-Object { -not $_.OK }).Count
$skipped = @($results | Where-Object { $null -eq $_.Minutes }).Count
$resultSummary = ("本轮共 {0} 个账号：成功 {1} · 失败 {2}" -f @($results).Count, $passed, $failed)
if ($skipped -gt 0) { $resultSummary += " · 跳过 $skipped" }

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
        $push += "`n$resultSummary"
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
        $push += "`n$resultSummary"
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
        if ($skipped -gt 0) { $push += " · 跳过 $skipped" }
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
