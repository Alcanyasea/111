# master_history.ps1 — 留痕与通知层：运行历史 JSON 写入 / 通知推送。
# 由 master.ps1 dot-source 引入（依赖 master_lib.ps1 的 $runHistoryDir /
# $runStartTs / $runStamp / $runMode / $config / $venvPython / $notifyPy /
# $configPath / Log，以及 master_plugins.ps1 的 Invoke-Plugin；$results /
# $bsBatch / $totalSw 在 MAIN 阶段赋值，函数体调用时才取值，不受影响）。

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
# 失败推送到手机（每个失败账号一条）；成功推送由 master.ps1 MAIN 阶段按
# config.notify.on_success 开关决定是否调用（本函数只管发送，不判断成功/失败策略）。
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
