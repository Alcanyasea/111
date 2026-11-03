# 子脚本执行边界。结构化参数；不通过字符串分词传递账号槽位。

function ConvertTo-ProcessArgument([AllowEmptyString()][string]$Value) {
    # Windows CommandLineToArgvW/CRT quoting；Start-Process 会把 ArgumentList 合并为字符串。
    $quoted = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $quoted = [regex]::Replace($quoted, '(\\+)$', '$1$1')
    return '"' + $quoted + '"'
}

function Remove-ChildTimestamp([string]$Text) {
    # 仅移除行首时间戳；由 Log 保持 GUI 需要的完整日期格式及主控记录时刻。
    return ($Text.Trim() -replace '^(?:[0-9]{4}-[0-9]{2}-[0-9]{2}\s+)?[0-9]{2}:[0-9]{2}:[0-9]{2}\s+(?:-\s+)?', '')
}

function Run-Switch {
    param([string]$ScriptName, [string[]]$ScriptArgs = @(),
        [int]$HeartbeatTimeoutSec = 30, [int]$NoHeartbeatTimeoutSec = 300,
        [int]$PollMilliseconds = 1000)
    $path = Join-Path $scriptDir $ScriptName
    $token = [guid]::NewGuid().ToString('N')
    $tmpOut = Join-Path ([IO.Path]::GetTempPath()) "maa-child-$token.out"
    $tmpErr = Join-Path ([IO.Path]::GetTempPath()) "maa-child-$token.err"
    $heartbeat = Join-Path $scriptDir 'login_heartbeat.tmp'
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $proc = $null
    $code = 127
    $prefix = if ($ScriptName -eq 'refresh_slot.ps1') { 'Refreshing slot:' } else { 'Running:' }
    Log ("$prefix $ScriptName " + ($ScriptArgs -join ' '))
    try {
        # 子脚本启动后才删除会误删其第一条心跳；必须在启动前清理。
        Remove-Item -LiteralPath $heartbeat -Force -ErrorAction SilentlyContinue
        $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $path) + $ScriptArgs
        $commandLine = ($arguments | ForEach-Object { ConvertTo-ProcessArgument $_ }) -join ' '
        $proc = Start-Process -FilePath $pwshExe -ArgumentList $commandLine `
            -WindowStyle Hidden -PassThru -RedirectStandardOutput $tmpOut `
            -RedirectStandardError $tmpErr -ErrorAction Stop
        if ($null -eq $proc) { throw '未获得子进程句柄' }
        $null = $proc.Handle
        while (-not $proc.WaitForExit($PollMilliseconds)) {
            $item = Get-Item -LiteralPath $heartbeat -ErrorAction SilentlyContinue
            if ($item) {
                if (((Get-Date) - $item.LastWriteTime).TotalSeconds -le $HeartbeatTimeoutSec) { continue }
            } elseif ($timer.Elapsed.TotalSeconds -le $NoHeartbeatTimeoutSec) { continue }
            $code = 124
            Log '  [ERROR] 子脚本心跳或启动等待超时，结束其进程树'
            $proc.Kill($true)
            [void]$proc.WaitForExit(5000)
            break
        }
        if ($code -ne 124) { $code = $proc.ExitCode }
    } catch {
        Log ('  [ERROR] 子脚本执行异常: ' + $_.Exception.Message)
    } finally {
        if ($proc) {
            try {
                if (-not $proc.HasExited) { $proc.Kill($true); [void]$proc.WaitForExit(5000) }
            } catch { }
        }
        # 超时也保留诊断；Read-ChildText 允许孙进程仍持有输出文件句柄。
        foreach ($line in (Read-ChildText $tmpOut)) {
            $text = Remove-ChildTimestamp $line
            if (-not $text) { continue }
            Log ('  ' + $text)
        }
        foreach ($line in (Read-ChildText $tmpErr)) {
            $text = Remove-ChildTimestamp $line
            if ($text) { Log ('  [stderr] ' + $text) }
        }
        Remove-Item -LiteralPath $tmpOut, $tmpErr -Force -ErrorAction SilentlyContinue
        if ($proc) { $proc.Dispose() }
    }
    Log ('  [Switch script finished in {0}s, exit={1}]' -f [math]::Round($timer.Elapsed.TotalSeconds, 1), $code)
    return $code
}

function Refresh-SlotData($Server, $Slot) {
    if (-not $Slot -or -not (Test-Path -LiteralPath (Join-Path $scriptDir "accounts\$Slot"))) { return $true }
    # 唯一快照实现；同步等待，且回拉与下一账号切号不再共享设备写入窗口。
    # 主控会把子进程输出写入 master_log；禁止回拉脚本再写一份，避免重复行。
    $code = Run-Switch 'refresh_slot.ps1' @('-Server', $Server, '-Slot', $Slot, '-NoLogFile')
    if ($code -ne 0) {
        Log ('  [WARN] 槽位回拉未完成（exit ' + $code + '），保留原槽位数据')
        return $false
    }
    return $true
}
