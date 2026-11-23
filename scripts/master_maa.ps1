# MAA 生命周期。加载本文件只定义函数，不读配置、不启动进程。
# 每次启动前建立日志游标；完成信号只负责唤醒，最终结果仍由日志校验。

function Convert-MaaChainName($chain) {
    switch ($chain) {
        'StartUp' { '开始唤醒' }
        'Recruit' { '公开招募' }
        'Infrast' { '基建换班' }
        'Fight' { '理智作战' }
        'Mall' { '信用收支' }
        'Award' { '领取奖励' }
        'Roguelike' { '集成战略' }
        'Reclamation' { '生息演算' }
        default { $chain }
    }
}

function New-MaaLogState([string]$MaaDir) {
    $path = Join-Path $MaaDir 'debug\asst.log'
    $pos = 0L
    if (Test-Path -LiteralPath $path) {
        # 元数据不会打开数据流；记录启动前的长度，旧错误不属于本次运行。
        $pos = (Get-Item -LiteralPath $path -ErrorAction Stop).Length
    }
    $updatePath = Join-Path $MaaDir 'debug\pending-update-applier.log'
    $updatePos = 0L
    if (Test-Path -LiteralPath $updatePath) {
        # 更新日志也只认本轮启动后的新增内容，旧的重开记录不能证明进程归属。
        $updatePos = (Get-Item -LiteralPath $updatePath -ErrorAction Stop).Length
    }
    return @{
        Path = $path; Position = $pos; ObservedLength = $pos; Carry = ''
        UpdatePath = $updatePath; UpdatePosition = $updatePos
        Errors = [Collections.Generic.List[string]]::new()
        Timer = [Diagnostics.Stopwatch]::StartNew(); LastActivity = 0.0
        SawProgress = $false; ReadOK = $false; ReadFailures = 0
    }
}

function Read-MaaLogProgress($State) {
    $fs = $null
    $State.ReadOK = $false
    try {
        $fs = [IO.File]::Open($State.Path, [IO.FileMode]::Open, [IO.FileAccess]::Read,
            [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)
        $length = $fs.Length
        if ($length -lt $State.Position) {
            $State.Position = 0L
            $State.Carry = ''
        }
        [void]$fs.Seek($State.Position, [IO.SeekOrigin]::Begin)
        # 固定大小缓冲，防日志暴增一次分配数百 MB；按实际读取字节推进游标。
        $buffer = [byte[]]::new(65536)
        while ($State.Position -lt $length) {
            $count = $fs.Read($buffer, 0, [int][math]::Min($buffer.Length, $length - $State.Position))
            if ($count -le 0) { break }
            $State.Position += $count
            $chunk = $State.Carry + [Text.Encoding]::UTF8.GetString($buffer, 0, $count)
            $newProgress = @([regex]::Matches($chunk, 'append_callback\s*\|\s*(?:SubTask|TaskChain)') |
                Where-Object { $_.Index + $_.Length -gt $State.Carry.Length })
            if ($newProgress.Count -gt 0) {
                $State.LastActivity = $State.Timer.Elapsed.TotalSeconds
                $State.SawProgress = $true
            }
            foreach ($match in [regex]::Matches($chunk, 'TaskChainError\s*\{\s*"taskchain"\s*:\s*"([^"]+)"')) {
                $chain = $match.Groups[1].Value
                $State.SawProgress = $true
                if (-not $State.Errors.Contains($chain)) {
                    $State.Errors.Add($chain)
                    Log ('  [WARN] MAA 任务链报错: ' + (Convert-MaaChainName $chain))
                }
            }
            $State.Carry = $chunk.Substring([math]::Max(0, $chunk.Length - 4096))
        }
        $State.ObservedLength = $length
        $State.ReadOK = ($State.Position -ge $length)
        $State.ReadFailures = 0
    } catch {
        $State.ReadFailures++
        # 文件增长仅作临时心跳。绝不能推进 Position，否则恢复可读后会漏掉错误。
        try {
            $length = (Get-Item -LiteralPath $State.Path -ErrorAction Stop).Length
            if ($length -gt $State.ObservedLength) {
                $State.LastActivity = $State.Timer.Elapsed.TotalSeconds
            }
            $State.ObservedLength = $length
        } catch { }
        if ($State.ReadFailures -eq 3) { Log '  [WARN] MAA 日志暂不可读，保留游标等待重读' }
    } finally {
        if ($fs) { $fs.Dispose() }
    }
}

function Wait-MAADone($t, $maaDir, $maaProc, $State) {
    if ($null -eq $State) { $State = New-MaaLogState $maaDir }
    $script:LastMaaChainErrors = $State.Errors
    Log ("Waiting for MAA tasks... ({0} 秒无战斗/任务进展判超时)" -f $t)
    $watcher = $null
    $source = 'MaaDoneSignal-' + [guid]::NewGuid().ToString('N')
    $deadCount = 0
    $completionAt = $null
    $deadAt = $null
    $restartCount = 0
    $restartGrace = if ($null -ne $script:MaaRestartGraceSec) { [int]$script:MaaRestartGraceSec } else { 30 }
    $script:LastMaaActiveProcess = $maaProc
    try {
        try {
            $watcher = [IO.FileSystemWatcher]::new((Split-Path -Parent $signalFile), (Split-Path -Leaf $signalFile))
            $null = Register-ObjectEvent -InputObject $watcher -EventName Created -SourceIdentifier $source
            $watcher.EnableRaisingEvents = $true
        } catch {
            if ($watcher) { $watcher.Dispose(); $watcher = $null }
        }
        while ($true) {
            # 先检查信号再读取日志：信号出现之前的尾部错误必须在返回成功前消费。
            $done = Test-Path -LiteralPath $signalFile
            Read-MaaLogProgress $State
            # 完成信号没有进程身份；即使已有信号，也必须先排除外来重开。
            if ($null -eq $maaProc -or $maaProc.HasExited) { $deadCount++ } else { $deadCount = 0 }
            if ($deadCount -gt 0) {
                if ($null -eq $deadAt) { $deadAt = $State.Timer.Elapsed.TotalSeconds }
                $replacement = Find-MaaRestart $maaDir $maaProc $State
                if ($replacement.Conflict) { return 'interrupted' }
                if ($replacement.Process) {
                    # 只有尚未执行任务时才能接续更新启动；保留同一次日志游标。
                    if ($State.SawProgress -or -not $State.ReadOK -or $restartCount -ge 1) {
                        $script:LastMaaActiveProcess = $replacement.Process
                        return 'interrupted'
                    }
                    $maaProc = $replacement.Process
                    $script:LastMaaActiveProcess = $maaProc
                    $restartCount++
                    $deadCount = 0; $deadAt = $null
                    $State.LastActivity = $State.Timer.Elapsed.TotalSeconds
                    if ($replacement.Updater) {
                        Log 'MAA 自动更新已重开，接续更新后的进程，等待任务结果'
                    } else {
                        Log 'MAA 启动阶段已重启，接续本轮派生进程，等待任务结果'
                    }
                }
            } else { $deadAt = $null }
            if ($done) {
                if ($State.ReadOK) {
                    Log ('MAA finished! {0} min' -f [math]::Round($State.Timer.Elapsed.TotalMinutes, 1))
                    if ($State.Errors.Count -gt 0) { return 'chain-error' }
                    return 'ok'
                }
                if ($null -eq $completionAt) { $completionAt = $State.Timer.Elapsed.TotalSeconds }
                if ($State.Timer.Elapsed.TotalSeconds - $completionAt -ge 30) {
                    if ($State.Errors.Count -gt 0) { return 'chain-error' }
                    Log 'ERROR: MAA 已发完成信号，但日志不可读，无法确认任务结果'
                    return 'log-error'
                }
            } else {
                if ($deadCount -ge 2 -and ($State.SawProgress -or
                    $State.Timer.Elapsed.TotalSeconds - $deadAt -ge $restartGrace)) {
                    # 日志未知时不自动重启：不能证明尚未执行过任务。
                    if ($State.Errors.Count -gt 0) { return 'chain-error' }
                    if ($State.SawProgress -or -not $State.ReadOK) { return 'interrupted' }
                    return 'dead-early'
                }
                if ($deadCount -eq 0 -and $State.Timer.Elapsed.TotalSeconds - $State.LastActivity -ge $t) {
                    if ($State.Errors.Count -gt 0) { return 'chain-error' }
                    if (-not $State.ReadOK) { return 'interrupted' }
                    return 'stall'
                }
            }
            if ($watcher) {
                [void](Wait-Event -SourceIdentifier $source -Timeout 2)
                Get-Event -SourceIdentifier $source -ErrorAction SilentlyContinue | Remove-Event -ErrorAction SilentlyContinue
            } else { Start-Sleep -Seconds 2 }
        }
    } finally {
        Unregister-Event -SourceIdentifier $source -ErrorAction SilentlyContinue
        Get-Event -SourceIdentifier $source -ErrorAction SilentlyContinue | Remove-Event -ErrorAction SilentlyContinue
        if ($watcher) { $watcher.Dispose() }
    }
}

function Test-MaaUpdaterRestart($State, $Previous) {
    # 外部更新器会在重开后退出，不能只靠当前进程树确认父子关系。
    # 用本轮游标之后的日志把「原 MAA -> 更新器 -> 重开」串起来；读不到证据时保持保守。
    if ($null -eq $State -or $null -eq $Previous -or -not $Previous.Id -or
        $null -eq $State.UpdatePath -or $State.UpdatePosition -lt 0 -or
        -not (Test-Path -LiteralPath $State.UpdatePath -PathType Leaf)) {
        return $false
    }
    $fs = $null
    $reader = $null
    $text = ''
    try {
        $fs = [IO.File]::Open($State.UpdatePath, [IO.FileMode]::Open, [IO.FileAccess]::Read,
            [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)
        $start = [long]$State.UpdatePosition
        if ($fs.Length -lt $start) { $start = 0L }
        if ($fs.Length -eq $start) { return $false }
        [void]$fs.Seek($start, [IO.SeekOrigin]::Begin)
        $reader = [IO.StreamReader]::new($fs, [Text.Encoding]::UTF8)
        $text = $reader.ReadToEnd()
    } catch {
        return $false
    } finally {
        if ($reader) { $reader.Dispose() } elseif ($fs) { $fs.Dispose() }
    }
    $parent = [regex]::Match($text, '(?m)^\[[^\r\n\]]+\]\s+Parent PID:\s*' +
        [regex]::Escape([string]$Previous.Id) + '\s*,')
    if (-not $parent.Success) { return $false }
    $afterParent = $text.Substring($parent.Index + $parent.Length)
    # Relaunching 在创建进程前写入，覆盖“新进程已可见、成功日志尚未落盘”的短暂窗口。
    return [regex]::IsMatch($afterParent,
        '(?m)^\[[^\r\n\]]+\]\s+(?:Relaunching MAA:|Relaunch succeeded\.)')
}

function Find-MaaRestart($MaaDir, $Previous, $State) {
    # 路径相同不够：手动重开不属于本轮。接受原进程的直接子进程，或有本轮
    # 更新日志佐证的外部更新器重开；启动时间和安装目录仍必须同时匹配。
    $found = $null
    $fromUpdater = $false
    try {
        $target = [IO.Path]::GetFullPath((Join-Path $MaaDir 'MAA.exe'))
        foreach ($item in @(Get-CimInstance Win32_Process -Filter "Name = 'MAA.exe'" -ErrorAction Stop)) {
            if (-not $item.ExecutablePath) { return @{Process=$null; Conflict=$true} }
            if (-not [IO.Path]::GetFullPath($item.ExecutablePath).Equals($target, [StringComparison]::OrdinalIgnoreCase)) { continue }
            $directRestart = $null -ne $Previous -and $item.ParentProcessId -eq $Previous.Id
            $updaterRestart = $false
            if ($null -ne $Previous -and -not $directRestart) {
                $updaterRestart = Test-MaaUpdaterRestart $State $Previous
            }
            if ((-not $directRestart -and -not $updaterRestart) -or $found) {
                return @{Process=$null; Conflict=$true}
            }
            $candidate = Get-Process -Id $item.ProcessId -ErrorAction Stop
            if ($candidate.HasExited -or -not $Previous.StartTime -or $candidate.StartTime -lt $Previous.StartTime -or
                -not [IO.Path]::GetFullPath($candidate.Path).Equals($target, [StringComparison]::OrdinalIgnoreCase)) {
                return @{Process=$null; Conflict=$true}
            }
            $found = $candidate
            $fromUpdater = $updaterRestart
        }
        return @{Process=$found; Conflict=$false; Updater=$fromUpdater}
    } catch {
        return @{Process=$null; Conflict=$true}
    }
}

function Run-MAA($exe, $dir, $label) {
    Log ('=== Run MAA [' + $label + '] ===')
    $script:LastMaaStatus = 'launch-error'
    $script:LastMaaChainErrors = [Collections.Generic.List[string]]::new()
    $script:LastMaaCleanupWarning = ''
    $maaProc = $null
    $state = $null
    try {
        Stop-StaleMaa $dir
        for ($attempt = 0; $attempt -lt 2; $attempt++) {
            $maaProc = $null
            $script:LastMaaActiveProcess = $null
            $state = $null
            try {
                if (Test-Path -LiteralPath $signalFile) { Remove-Item -LiteralPath $signalFile -Force -ErrorAction Stop }
                $state = New-MaaLogState $dir
                Log 'Launching MAA...'
                $maaProc = Start-Process $exe -WorkingDirectory $dir -PassThru -ErrorAction Stop
                if ($null -eq $maaProc) { throw 'MAA 未返回进程句柄' }
                # 不再固定睡 5 秒；启动瞬间的进展、错误和完成都在本次游标范围内。
                $script:LastMaaStatus = Wait-MAADone $maaStallTimeoutSec $dir $maaProc $state
            } finally {
                foreach ($owned in @($maaProc, $script:LastMaaActiveProcess) | Where-Object { $_ } | Sort-Object Id -Unique) {
                    try {
                        if (-not $owned.HasExited) { Stop-Process -Id $owned.Id -Force -ErrorAction Stop }
                        if ($owned -is [Diagnostics.Process] -and -not $owned.WaitForExit(15000)) { throw 'MAA 退出等待超时' }
                    } catch {
                        $script:LastMaaCleanupWarning = 'MAA 退出清理异常：' + $_.Exception.Message
                        Log ('WARN: ' + $script:LastMaaCleanupWarning)
                    } finally {
                        if ($owned -is [Diagnostics.Process]) { $owned.Dispose() }
                    }
                }
                # 启动器退出后可能出现无法确认归属的同目录 MAA（例如自动更新重开）。
                # Wait-MAADone 会保守返回 interrupted，但它不一定在已登记的句柄里；
                # 不能只清 $maaProc，否则残留进程会阻碍配置恢复并串到下一账号。
                # Stop-StaleMaa 严格按安装目录匹配，不会误杀其他目录的人工 MAA。
                try {
                    Stop-StaleMaa $dir
                } catch {
                    $detail = 'MAA 同目录残留清理异常：' + $_.Exception.Message
                    $script:LastMaaCleanupWarning = (@($script:LastMaaCleanupWarning, $detail) |
                        Where-Object { $_ }) -join '；'
                    Log ('WARN: ' + $detail)
                }
            }
            if ($script:LastMaaStatus -ne 'dead-early' -or $attempt -eq 1) { break }
            Log 'WARN: MAA 未执行任务即退出，3 秒后重试一次启动'
            Start-Sleep -Seconds 3
        }
    } catch {
        $script:LastMaaStatus = if ($state -and $state.Errors.Count -gt 0) { 'chain-error' } elseif ($maaProc) { 'interrupted' } else { 'launch-error' }
        Log ('ERROR: MAA 启动或监测异常: ' + $_.Exception.Message)
    }
    $ok = ($script:LastMaaStatus -eq 'ok')
    if ($ok) { Log ('MAA [' + $label + '] completed successfully') }
    else { Log ('ERROR: MAA [' + $label + '] FAILED: ' + $script:LastMaaStatus) }
    return $ok
}
