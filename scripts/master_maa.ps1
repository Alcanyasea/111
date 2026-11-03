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
    return @{
        Path = $path; Position = $pos; ObservedLength = $pos; Carry = ''
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
            if ($done) {
                if ($State.ReadOK) {
                    Log ('MAA finished! {0} min' -f [math]::Round($State.Timer.Elapsed.TotalMinutes, 1))
                    if ($State.Errors.Count -gt 0) { return 'chain-error' }
                    return 'ok'
                }
                if ($null -eq $completionAt) { $completionAt = $State.Timer.Elapsed.TotalSeconds }
                if ($State.Timer.Elapsed.TotalSeconds - $completionAt -ge 30) {
                    Log 'ERROR: MAA 已发完成信号，但日志不可读，无法确认任务结果'
                    return 'log-error'
                }
            } else {
                if ($null -eq $maaProc -or $maaProc.HasExited) { $deadCount++ } else { $deadCount = 0 }
                if ($deadCount -ge 2) {
                    # 日志未知时不自动重启：不能证明尚未执行过任务。
                    if ($State.SawProgress -or -not $State.ReadOK) { return 'dead' }
                    return 'dead-early'
                }
                if ($State.Timer.Elapsed.TotalSeconds - $State.LastActivity -ge $t) { return 'stall' }
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

function Run-MAA($exe, $dir, $label) {
    Log ('=== Run MAA [' + $label + '] ===')
    $script:LastMaaStatus = 'launch-error'
    $script:LastMaaChainErrors = [Collections.Generic.List[string]]::new()
    try {
        Stop-StaleMaa $dir
        for ($attempt = 0; $attempt -lt 2; $attempt++) {
            $maaProc = $null
            try {
                if (Test-Path -LiteralPath $signalFile) { Remove-Item -LiteralPath $signalFile -Force -ErrorAction Stop }
                $state = New-MaaLogState $dir
                Log 'Launching MAA...'
                $maaProc = Start-Process $exe -WorkingDirectory $dir -PassThru -ErrorAction Stop
                if ($null -eq $maaProc) { throw 'MAA 未返回进程句柄' }
                # 不再固定睡 5 秒；启动瞬间的进展、错误和完成都在本次游标范围内。
                $script:LastMaaStatus = Wait-MAADone $maaStallTimeoutSec $dir $maaProc $state
            } finally {
                if ($maaProc) {
                    if (-not $maaProc.HasExited) { Stop-Process -Id $maaProc.Id -Force -ErrorAction SilentlyContinue }
                    if ($maaProc -is [Diagnostics.Process]) { $maaProc.Dispose() }
                }
            }
            if ($script:LastMaaStatus -ne 'dead-early' -or $attempt -eq 1) { break }
            Log 'WARN: MAA 未执行任务即退出，3 秒后重试一次启动'
            Start-Sleep -Seconds 3
        }
    } catch {
        $script:LastMaaStatus = 'launch-error'
        Log ('ERROR: MAA 启动或监测异常: ' + $_.Exception.Message)
    }
    $ok = ($script:LastMaaStatus -eq 'ok')
    if ($ok) { Log ('MAA [' + $label + '] completed successfully') }
    else { Log ('ERROR: MAA [' + $label + '] FAILED: ' + $script:LastMaaStatus) }
    return $ok
}
