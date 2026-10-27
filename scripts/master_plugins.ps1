# master_plugins.ps1 — 子进程调度层：插件调用 / pwsh 子脚本拉起（心跳看门狗）/
# 槽位回拉。由 master.ps1 dot-source 引入（依赖 master_lib.ps1 已先加载的
# $venvPython / $scriptDir / $adb / $device / Log 等变量与函数）。

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
    # 插件输出统一 UTF-8：控制台代码页为 cp1252 等编不了中文的环境下，插件
    # print 中文会 UnicodeEncodeError 直接崩（CI 实测）。只对这一次子进程调用
    # 做作用域内 UTF-8（子进程编码与父进程解码两端同时切），不影响 adb 等
    # 其它命令的解码
    $oldConsoleEnc = [Console]::OutputEncoding
    $env:PYTHONIOENCODING = "utf-8"
    try {
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
        $out = & $venvPython $pyPath @argList 2>&1
    } finally {
        [Console]::OutputEncoding = $oldConsoleEnc
        Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue
    }
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
# 后台子进程用 pwsh：安装包内置便携版优先，其次系统安装，最后裸名兜底
$pwshExe = Join-Path $root "gui\runtime\pwsh\pwsh.exe"
if (-not (Test-Path $pwshExe)) { $pwshExe = "C:\Program Files\PowerShell\7\pwsh.exe" }
if (-not (Test-Path $pwshExe)) { $pwshExe = "pwsh" }

# 读取子进程重定向文件。子脚本的输出编码取决于启动环境：bat 里 chcp 65001
# 启动是 UTF-8，GUI/计划任务继承 GBK 控制台。先按严格 UTF-8 解码，失败回退
# GBK，两种情况中文都能还原（此前统一按 UTF-8 读，GBK 时日志全是 U+FFFD）
function Read-ChildText([string]$Path) {
    if (-not (Test-Path $Path)) { return @() }
    # 子脚本退出后句柄可能仍被孙进程持有（adb server 等守护进程会继承重定向
    # 句柄），ReadAllBytes 的共享模式此时撞 sharing violation：切换日志丢失、
    # tmp 残留（2026-10-27 实测）。显式以「允许他人读写删」共享模式打开并短
    # 重试；彻底失败只告警返回空，绝不抛异常打断主流程的退出码处理。
    $bytes = $null
    for ($attempt = 1; $attempt -le 4; $attempt++) {
        try {
            $fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete)
            try {
                $len = [int]$fs.Length
                $bytes = [byte[]]::new($len)
                $read = 0
                while ($read -lt $len) {
                    $n = $fs.Read($bytes, $read, $len - $read)
                    if ($n -le 0) { break }
                    $read += $n
                }
            } finally {
                $fs.Dispose()
            }
            break
        } catch {
            if ($attempt -eq 4) {
                Log ("  [WARN] 子进程输出读取失败（文件仍被占用，已重试 3 次）: " + (Split-Path $Path -Leaf))
                return @()
            }
            Start-Sleep -Milliseconds 800
        }
    }
    if ($null -eq $bytes -or $bytes.Length -eq 0) { return @() }
    try {
        $text = [System.Text.UTF8Encoding]::new($false, $true).GetString($bytes)
    } catch {
        $text = [System.Text.Encoding]::GetEncoding(936).GetString($bytes)
    }
    $text = $text.TrimStart([char]0xFEFF)
    return ($text -split "`r?`n")
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
    # with the console pipeline (2>&1 on child powershell mangles output)
    $tmpOut = "$scriptDir\switch_output.tmp"
    $tmpErr = "$scriptDir\switch_error.tmp"
    # 清掉上一轮残留，避免读到旧输出（异常退出时可能没删干净）
    Remove-Item $tmpOut, $tmpErr -Force -ErrorAction SilentlyContinue
    # pwsh 7 在解释器不存在时抛 InvalidOperationException（不是返回 $null），
    # 必须 try/catch，否则异常直接冒泡、主流程没有明确错误
    $proc = $null
    try {
        $proc = Start-Process -FilePath $pwshExe `
            -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$sp`" $extraArgs" `
            -NoNewWindow -PassThru `
            -RedirectStandardOutput $tmpOut `
            -RedirectStandardError $tmpErr `
            -ErrorAction SilentlyContinue
    } catch {
        $proc = $null
    }
    if (-not $proc) {
        Log ("  [ERROR] 无法启动子脚本（解释器：" + $pwshExe + "）")
        Remove-Item $tmpOut, $tmpErr -Force -ErrorAction SilentlyContinue
        return 127
    }
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
        Remove-Item $tmpOut, $tmpErr -Force -ErrorAction SilentlyContinue
        Start-Sleep 1
        return 124
    }
    # 无参 WaitForExit 刷新退出状态：保证 ExitCode 可读
    $proc.WaitForExit() | Out-Null
    # stdout/stderr 都按 Read-ChildText 的 UTF-8→GBK 回退解码，读完即删
    foreach ($l in (Read-ChildText $tmpOut)) {
        $trimmed = $l.Trim()
        if ($trimmed.Length -gt 0) { Log "  $trimmed" }
    }
    foreach ($l in (Read-ChildText $tmpErr)) {
        $trimmed = $l.Trim()
        if ($trimmed.Length -gt 0) { Log "  [stderr] $trimmed" }
    }
    Remove-Item $tmpOut, $tmpErr -Force -ErrorAction SilentlyContinue
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
