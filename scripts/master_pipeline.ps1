# 单账号管线：每个阶段显式返回成功/失败；首跑与重试复用同一路径。
# 加载仅定义函数，设备操作由 master_plugins.ps1 提供。

# ---- 失败现场归档（诊断用，失败不阻断主流程）----
# debug 目录在每轮开头由 Clear-UnnecessaryData 清空（凌晨班至少清 *.png），失败截图
# 和捕获日志活不到下一次运行——事后只能看到 master_log.txt 里的一行错误文本。
# 失败账号在生成结果时把「该槽位相关」的截图/日志复制到 run_history\<runStamp>\<槽位>\，
# 跟运行历史一起保留 60 天（目录由 master_history.ps1 的 Save-RunHistory 清理）。
# 每个失败号最多带 3 个文件（正常只有当前帧 + fail_ 帧），防止 debug 目录堆了几十张
# 时把归档撑大；同一轮重试再次失败会先清空目标目录，不让同号多份累积。
# 放在本文件而非留痕层：pipeline 是唯一的调用方，测试也只需加载管线即可覆盖。
function Save-FailureEvidence($Slot, $Server, $Step) {
    try {
        # 没有运行戳/历史目录时宁可放弃归档，也不往 run_history 根目录散落文件
        if (-not $runStamp -or -not $runHistoryDir) { return }
        $debugDir = Join-Path $scriptDir "debug"
        if (-not (Test-Path -LiteralPath $debugDir)) { return }
        $key = if ($Slot) { [string]$Slot } elseif ($Server) { [string]$Server } else { '' }
        if (-not $key) { return }
        $maxFiles = 3
        $files = @(Get-ChildItem -LiteralPath $debugDir -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object {
                $_.FullName.IndexOf($key, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
                $_.Extension -in @('.png', '.log', '.txt', '.xml')
            } |
            Sort-Object LastWriteTime -Descending | Select-Object -First $maxFiles)
        if ($files.Count -eq 0) { return }
        # 槽位名允许空格/引号（见 slot_switch 的 -Slot 约定），落盘前清洗非法路径字符
        $safe = ($key -replace '[\\/:*?"<>|]', '_').Trim()
        if (-not $safe) { $safe = 'unknown' }
        $dest = Join-Path (Join-Path $runHistoryDir $runStamp) $safe
        if (Test-Path -LiteralPath $dest) {
            Remove-Item $dest -Recurse -Force -ErrorAction SilentlyContinue
        }
        New-Item -ItemType Directory -Force -Path $dest | Out-Null
        foreach ($f in $files) {
            $relative = $f.FullName.Substring($debugDir.Length).TrimStart('\', '/')
            $target = Join-Path $dest $relative
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
            Copy-Item -LiteralPath $f.FullName -Destination $target -Force -ErrorAction SilentlyContinue
        }
        $stepNote = if ($Step) { "$Step " } else { '' }
        Log ("  [证据] {0}失败现场已归档：run_history\{1}\{2}\（{3} 个文件）" -f
            $stepNote, $runStamp, $safe, $files.Count)
    } catch {
        Log ("  [WARN] 失败现场归档失败：" + $_.Exception.Message)
    }
}

function New-AccountResult($Account, $Timer, [bool]$OK, [string]$Step = '', [string]$Reason = '', [bool]$Retried = $false) {
    $minutes = [math]::Round($Timer.Elapsed.TotalMinutes, 3)
    if (-not $OK) {
        # 失败现场归档：debug 目录下一轮开头就清空，不在这里留一份就永远丢了。
        # 槽位/服务器由 Invoke-AccountRun 开头写入（失败分支有 6 处，不逐个传参）
        Save-FailureEvidence $script:EvidenceSlot $script:EvidenceServer $Step
    }
    return [pscustomobject]@{
        Account = $Account; OK = $OK; Duration = "$minutes min"; Minutes = $minutes
        Reason = $Reason; Step = $Step; Retried = $Retried
    }
}

function Invoke-AccountRun($acc, $idx, $IsRetry) {
    $label = if ([string]$acc.label) { [string]$acc.label } else { "Account $idx" }
    $server = if ([string]$acc.server) { [string]$acc.server } else { 'official' }
    $slot = [string]$acc.slot
    # 供 New-AccountResult 归档失败现场（本函数内全部失败分支共用）
    $script:EvidenceSlot = $slot
    $script:EvidenceServer = $server
    $retryMark = if ($IsRetry) { '（重试）' } else { '' }
    Log ' '
    Log ('********** [{0}/{1}] {2}{3} **********' -f $idx, $total, $label, $retryMark)
    $timer = [Diagnostics.Stopwatch]::StartNew()
    if ($null -ne $acc.enabled -and -not $acc.enabled -and -not $SwitchTo) {
        Log '  [SKIP] disabled in config.json'
        return [pscustomobject]@{ Account=$label; OK=$true; Duration='skip'; Minutes=$null; Reason=''; Step=''; Retried=$false }
    }
    $step = '切号'
    try {
        if ($slot -and (Test-Path -LiteralPath (Join-Path $scriptDir "accounts\$slot"))) {
            $code = Run-Switch 'slot_switch.ps1' @('-Server', $server, '-Slot', $slot)
        } elseif ($server -eq 'bilibili') {
            Log '  [WARN] B服无槽位，切换客户端后仍需登录校验'
            $code = Run-Switch 'switch_to_B服.ps1'
        } else {
            Log '  [ERROR] 官服槽位缺失，请重新捕获该账号'
            $code = 1
        }
        if ($code -ne 0) { return New-AccountResult $label $timer $false $step '切号失败' $IsRetry }

        if ($SwitchTo -and $NoLoginCheck) {
            Log '  [FAST] 快速启动：已跳过更新等待与登录校验，模拟器保持运行'
            return New-AccountResult $label $timer $true
        }
        $step = '登录校验'
        if ($server -eq 'bilibili') {
            $code = Run-Switch 'login_check_bilibili.ps1' @('-Slot', $slot)
        } else {
            $code = Run-Switch 'login_check.ps1' @('-Server', $server, '-Slot', $slot)
        }
        if ($code -ne 0) { return New-AccountResult $label $timer $false $step '登录校验失败' $IsRetry }
        if ($SwitchTo) {
            $refreshOk = [bool](Refresh-SlotData $server $slot)
            if (-not $refreshOk) {
                return New-AccountResult $label $timer $false '槽位回拉' '槽位回拉失败' $IsRetry
            }
            Log ('  [OK] 已切换到「' + $label + '」，模拟器保持运行（不跑日常）')
            return New-AccountResult $label $timer $true
        }
        if (-not $SkipMAA) {
            $step = '任务配置'
            if (-not [string]$acc.id) {
                return New-AccountResult $label $timer $false $step '账号缺少 id，无法安全应用任务配置' $IsRetry
            }
            $pluginArgs = @('apply', '--config', $configPath, '--account', [string]$acc.id, '--server', $server)
            # 已请求的配置必须成功写入；不允许失败后继承前一账号/另一模式的配置。
            $stages = @()
            if ($InfrastCollect) {
                $stages += @{Path=$infrastCollectPy; Name='基建收菜'; Args=$pluginArgs}
            } else {
                $stages += @{Path=$farmGuardPy; Name='任务开关自检'; Args=$pluginArgs}
                $plan = $acc.PSObject.Properties['second_fight_plan']
                $hasPlan = ($null -ne $plan -and $null -ne $plan.Value -and @($plan.Value).Count -gt 0)
                if ($hasPlan -or [string]$acc.second_fight_stage) {
                    $stages += @{Path=$fightStagePy; Name='理智关卡'; Args=$pluginArgs}
                }
                # 关闭插件时的恢复配置也必须成功，防上一账号的精确排班残留。
                $stages += @{Path=$baseSchedulePy; Name='基建插件'; Args=($pluginArgs + @('--batch', $bsBatch))}
                $stages += @{Path=$fiammettaPy; Name='菲亚梅塔'; Args=$pluginArgs}
            }
            foreach ($stage in $stages) {
                $step = $stage.Name
                $script:LastPluginError = ''
                if (-not (Invoke-Plugin $stage.Path $step $step $stage.Args '本账号停止运行')) {
                    $reason = "$step`失败"
                    if ($script:LastPluginError) { $reason += '（' + $script:LastPluginError + '）' }
                    return New-AccountResult $label $timer $false $step $reason $IsRetry
                }
            }
            $step = 'MAA 运行'
            $exe = if ($server -eq 'bilibili') { $maaBilibili } else { $maaOfficial }
            $dir = if ($server -eq 'bilibili') { $maaBilibiliDir } else { $maaOfficialDir }
            $ok = Run-MAA $exe $dir $label
            $reason = ''
            if (-not $ok) {
                $reason = switch ($script:LastMaaStatus) {
                    'stall' { 'MAA 无进展超时' }
                    'dead' { 'MAA 中途退出' }
                    'dead-early' { 'MAA 启动即崩溃（已自动重试一次）' }
                    'launch-error' { 'MAA 启动或监测失败' }
                    'log-error' { 'MAA 完成后日志不可读，无法确认结果' }
                    'chain-error' { 'MAA 任务链失败: ' + (($script:LastMaaChainErrors | ForEach-Object { Convert-MaaChainName $_ }) -join '、') }
                    default { 'MAA 运行失败' }
                }
            }
        } else {
            Log '  [TEST] -SkipMAA: 跳过 MAA，仅验证切号'
            $ok = $true
            $reason = ''
        }
        # 必须等设备快照回拉完成才允许下一号改写设备；不再并行争用同一模拟器。
        $refreshOk = [bool](Refresh-SlotData $server $slot)
        if (-not $refreshOk -and $ok) {
            $ok = $false
            $reason = '槽位回拉失败'
            $failedStep = '槽位回拉'
        } else {
            $failedStep = if ($ok) { '' } else { 'MAA 运行' }
        }
        return New-AccountResult $label $timer $ok $failedStep $reason $IsRetry
    } catch {
        Log ('  [ERROR] ' + $step + '异常: ' + $_.Exception.Message)
        return New-AccountResult $label $timer $false $step ($step + '异常：' + $_.Exception.Message) $IsRetry
    }
}
