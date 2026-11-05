# ADB 连接发现与自愈。所有连接判断都以 `adb devices` 的精确 transport
# 状态为准，避免 get-state 与 devices 状态短暂不一致时误放行。
#
# 约定：
# - `$adb` 由调用方提供（master_lib / slot_switch / login_check 均在同一作用域定义）。
# - `-Device` 显式传入；缺省时回退调用方作用域的 `$device`（兼容旧调用）。
# - `Ensure-AdbOnline` 成功时返回**确认可用的设备地址**（可能是重新发现的端口），
#   失败返回 $null；调用方必须用返回值覆盖自己的 `$device`。
$script:LastAdbError = ""

function Get-AdbCandidates {
    param([string]$VmConfigPath = "", [string]$CurrentDevice = "", [string[]]$KnownSerials = @())
    $candidates = [System.Collections.Generic.List[string]]::new()
    $add = {
        param([string]$value)
        if ([string]::IsNullOrWhiteSpace($value)) { return }
        $value = $value.Trim()
        if ($value -notmatch '^(?:[^\s:]+|[^\s:]+:\d+)$') { return }
        if (-not $candidates.Contains($value)) { [void]$candidates.Add($value) }
    }
    # 已知地址排最前：正常路径第一次就能命中，不用为扫描付等待。
    & $add $CurrentDevice
    $hasVmPort = $false
    if ($VmConfigPath -and (Test-Path -LiteralPath $VmConfigPath)) {
        try {
            $json = Get-Content -LiteralPath $VmConfigPath -Raw -ErrorAction Stop | ConvertFrom-Json
            $port = $json.vm.nat.port_forward.adb.host_port
            [int]$parsedPort = 0
            if ($null -ne $port -and [int]::TryParse([string]$port, [ref]$parsedPort) -and
                $parsedPort -ge 1 -and $parsedPort -le 65535) {
                & $add ("127.0.0.1:{0}" -f $parsedPort)
                # MuMu 端口被占时通常顺延到下一个（今早 16384 -> 16385）
                if ($parsedPort -lt 65535) { & $add ("127.0.0.1:{0}" -f ($parsedPort + 1)) }
                $hasVmPort = $true
            }
        } catch { }
    }
    # 拿不到实例配置时（非常规安装/文件被删）才退化为小范围端口扫描；
    # 正常情况下这一步不会执行，避免每个关闭端口 ~2 秒的 connect 等待。
    if (-not $hasVmPort) {
        foreach ($port in 16384..16390) { & $add ("127.0.0.1:{0}" -f $port) }
    }
    & $add "emulator-5554"
    # 已经在 adb 设备表里的本机条目（emulator-N / 127.0.0.1:port）直接复用；
    # 只认本机模式，避免把局域网真机当成候选。
    foreach ($serial in @($KnownSerials)) {
        if ([string]$serial -match '^(?:emulator-\d+|127\.0\.0\.1:\d+)$') { & $add ([string]$serial) }
    }
    return @($candidates)
}

function Get-AdbDeviceStates {
    if (-not $adb) { $script:LastAdbError = "未配置 adb 路径"; return @{} }
    $lines = @(& $adb devices 2>&1)
    $code = $LASTEXITCODE
    $states = @{}
    foreach ($line in $lines) {
        foreach ($part in (([string]$line) -split "`r?`n")) {
            $text = $part.Trim()
            if (-not $text -or $text -like "List of devices attached*") { continue }
            # 只认已知 transport 状态；不要求整行只有两列，兼容新版 adb 在
            # 普通 devices 输出里追加 transport_id 的情况。
            if ($text -match '^(\S+)\s+(device|offline|unauthorized|recovery|sideload|bootloader|host|rescue)\b') {
                $states[$Matches[1]] = $Matches[2]
            }
        }
    }
    if ($code -ne 0 -and $states.Count -eq 0) {
        $script:LastAdbError = (($lines -join " ") -replace '\s+', ' ').Trim()
    }
    return $states
}

function Test-AdbOnline {
    param([string]$Device = "")
    if (-not $Device) { $Device = $device }
    if (-not $Device) { $script:LastAdbError = "未配置设备地址"; return $false }
    $states = Get-AdbDeviceStates
    if ($states.ContainsKey($Device) -and $states[$Device] -eq 'device') {
        $script:LastAdbError = ""
        return $true
    }
    $state = if ($states.ContainsKey($Device)) { [string]$states[$Device] } else { "missing" }
    $script:LastAdbError = "${Device}: $state"
    return $false
}

# 设备上是否装了任一目标包。用于排除「连上了别的模拟器/真机」的误判：
# 只要候选设备的 pm path 有输出就算匹配；未指定包名时不做限制。
function Test-AdbGameInstalled {
    param([string]$Serial = "", [string[]]$Packages = @())
    if (-not $Packages -or $Packages.Count -eq 0) { return $true }
    if (-not $adb) { return $false }
    $unknown = $false
    foreach ($pkg in @($Packages | Where-Object { $_ })) {
        for ($i = 1; $i -le 2; $i++) {
            $out = (& $adb -s $Serial shell "pm path $pkg" 2>&1) -join "`n"
            $code = $LASTEXITCODE
            if ($out -match 'package:') { return $true }
            # 只有「命令正常执行且输出为空」才是明确没装；adbd/pm 未就绪时
            # 老版本 adb 仍返回 0，但输出里带 Error/Exception，不能据此判没装。
            if ($code -eq 0 -and -not $out.Trim() -and $out -notmatch 'Error|Exception|Failure|not found') {
                break
            }
            $unknown = $true
            Start-Sleep -Milliseconds 250
        }
    }
    return $unknown
}

# emulator-5554 是 adb 通过模拟器控制台自动登记的别名，同样能作为 -s 的序列号
# 使用；只有拿不到实例配置时才可能落到它上面（正常路径优先用 127.0.0.1:<port>）。
function Find-AdbDevice {
    param(
        [string]$VmConfigPath = "",
        [string]$CurrentDevice = "",
        [string[]]$RequireAnyPackage = @()
    )
    # 每次重新判定：不沿用上一次调用残留的错误（会误触发"不重复拉起"分支）
    $script:LastAdbError = ""
    # 先取一次设备表：已在表里的条目不需要再 connect，省掉每个端口 ~2 秒的等待。
    $states = Get-AdbDeviceStates
    $candidates = Get-AdbCandidates -VmConfigPath $VmConfigPath -CurrentDevice $CurrentDevice -KnownSerials @($states.Keys)
    $tcpCandidates = @($candidates | Where-Object { $_ -match ':\d+$' })
    $aliasCandidates = @($candidates | Where-Object { $_ -notmatch ':\d+$' })
    # 阶段 1：设备表里已经 online 的 TCP 端点，直接校验（模拟器已开机时零 connect）
    foreach ($candidate in $tcpCandidates) {
        if (-not ($states.ContainsKey($candidate) -and $states[$candidate] -eq 'device')) { continue }
        if (-not (Test-AdbGameInstalled -Serial $candidate -Packages $RequireAnyPackage)) {
            $script:LastAdbError = "${candidate}: 在线但未安装目标游戏"
            continue
        }
        $script:LastAdbError = ""
        return $candidate
    }
    # 阶段 2：对还没连上的 TCP 候选逐个 connect
    foreach ($candidate in $tcpCandidates) {
        if ($states.ContainsKey($candidate) -and $states[$candidate] -eq 'device') { continue }
        # 残留的 offline 条目不会被 connect 修复（adb 只回 already connected），先断开
        if ($states.ContainsKey($candidate)) { & $adb disconnect $candidate 2>&1 | Out-Null }
        & $adb connect $candidate 2>&1 | Out-Null
        $states = Get-AdbDeviceStates
        if (-not ($states.ContainsKey($candidate) -and $states[$candidate] -eq 'device')) { continue }
        if (-not (Test-AdbGameInstalled -Serial $candidate -Packages $RequireAnyPackage)) {
            $script:LastAdbError = "${candidate}: 在线但未安装目标游戏"
            continue
        }
        $script:LastAdbError = ""
        return $candidate
    }
    # 阶段 3：最后才用 emulator-N 别名兜底（写进 config/MAA 兼容性差）
    foreach ($candidate in $aliasCandidates) {
        if (-not ($states.ContainsKey($candidate) -and $states[$candidate] -eq 'device')) { continue }
        if (-not (Test-AdbGameInstalled -Serial $candidate -Packages $RequireAnyPackage)) {
            $script:LastAdbError = "${candidate}: 在线但未安装目标游戏"
            continue
        }
        $script:LastAdbError = ""
        return $candidate
    }
    return $null
}

# 只认 MuMu 实例的实时配置：<root>\vms\<实例>\configs\vm_config.json。
# 不能递归全目录找——整个安装目录下还有 data\exportLogs\... 的历史导出副本，
# 里面的端口是旧值（今早 16384/16385 漂移时两者就不一致），命中顺序不可控。
function Get-MuMuVmConfigPath {
    param([string]$ToolPath = "", [string]$Root = "", [int]$PreferPort = 0)
    $roots = @()
    if ($Root) { $roots += $Root }
    if (-not $Root -and $ToolPath) {
        $dir = Split-Path -Parent $ToolPath
        if ($dir) { $roots += (Split-Path -Parent $dir) }
    }
    # 没有任何工具路径可推导时才用安装目录默认值（生产路径一定给 ToolPath，
    # 测试不会因此读到真实模拟器目录）
    if (-not $roots -and -not $Root) { $roots += "D:\软件\MuMu模拟器\MuMuPlayer" }
    $found = @()
    foreach ($base in @($roots | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -Unique)) {
        $vmRoot = Join-Path $base "vms"
        if (-not (Test-Path -LiteralPath $vmRoot)) { continue }
        $files = @(Get-ChildItem -LiteralPath $vmRoot -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name |
            ForEach-Object { Join-Path $_.FullName "configs\vm_config.json" } |
            Where-Object { Test-Path -LiteralPath $_ })
        foreach ($file in $files) {
            try {
                $json = Get-Content -LiteralPath $file -Raw -ErrorAction Stop | ConvertFrom-Json
                $port = $json.vm.nat.port_forward.adb.host_port
                [int]$n = 0
                if ($null -ne $port -and [int]::TryParse([string]$port, [ref]$n) -and $n -gt 0 -and $n -le 65535) {
                    $found += [pscustomobject]@{ Path = $file; Port = $n }
                }
            } catch { }
        }
    }
    if ($found.Count -eq 0) { return $null }
    # 多开时优先选与当前配置端口一致的那个实例，避免把地址同步到别的实例
    if ($PreferPort -gt 0) {
        $hit = @($found | Where-Object { $_.Port -eq $PreferPort })
        if ($hit.Count -gt 0) { return $hit[0].Path }
    }
    return $found[0].Path
}

# 返回确认可用的设备地址；确保过 `adb devices` 的 device 状态。
# -Attempts 是探测次数，最多重连 Attempts-1 次（最后一次失败后直接进入重发现）。
# 给了 -VmConfigPath 时会重新发现端口（MuMu 重启后端口可能漂移）。
function Ensure-AdbOnline {
    param(
        [string]$Device = "",
        [int]$Attempts = 3,
        [int]$RetryDelaySec = 2,
        [string]$VmConfigPath = "",
        [string[]]$RequireAnyPackage = @(),
        [scriptblock]$OnAttempt = $null
    )
    if (-not $Device) { $Device = $device }
    if (-not $Device) { $script:LastAdbError = "未配置设备地址"; return $null }
    $Attempts = [math]::Max(1, $Attempts)
    $killServerUsed = $false
    $packageRetried = $false
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        if ($OnAttempt) { & $OnAttempt | Out-Null }
        if (Test-AdbOnline -Device $Device) {
            # 连上了但没装目标游戏 → 不是我们要的设备（端口被别的模拟器占用时常见）
            if (Test-AdbGameInstalled -Serial $Device -Packages $RequireAnyPackage) { return $Device }
            if (-not $packageRetried) {
                # 冷启动时 PMS 可能还没就绪，给一次重试；这期间不要动 transport
                $packageRetried = $true
                Start-Sleep -Seconds 2
                if (Test-AdbGameInstalled -Serial $Device -Packages $RequireAnyPackage) { return $Device }
            }
            $script:LastAdbError = "${Device}: 在线但未安装目标游戏"
            break
        }
        if ($attempt -ge $Attempts) { break }
        & $adb disconnect $Device 2>&1 | Out-Null
        if ($attempt -gt 1 -and -not $killServerUsed) {
            & $adb kill-server 2>&1 | Out-Null
            $killServerUsed = $true
        }
        & $adb connect $Device 2>&1 | Out-Null
        if ($RetryDelaySec -gt 0) { Start-Sleep -Seconds $RetryDelaySec }
    }
    $lastError = $script:LastAdbError
    if ($VmConfigPath) {
        if ($OnAttempt) { & $OnAttempt | Out-Null }
        $found = Find-AdbDevice -VmConfigPath $VmConfigPath -CurrentDevice $Device -RequireAnyPackage $RequireAnyPackage
        if ($found) {
            $script:LastAdbError = ""
            return $found
        }
        if ($script:LastAdbError) { $lastError = $script:LastAdbError }
    }
    $hint = if ($VmConfigPath) { "；已尝试按实例配置重新发现端口" } else { "" }
    if (-not $lastError) { $lastError = "无可用 device transport" }
    $script:LastAdbError = "$lastError（重连失败$hint）"
    return $null
}

function Wait-AdbBootReady {
    param([string]$Device = "", [int]$Samples = 2, [int]$IntervalSec = 2, [int]$TimeoutSec = 60)
    if (-not $Device) { $Device = $device }
    $Samples = [math]::Max(2, $Samples)
    $deadline = (Get-Date).AddSeconds([math]::Max(1, $TimeoutSec))
    $consecutive = 0
    while ((Get-Date) -lt $deadline) {
        if (Test-AdbOnline -Device $Device) {
            $boot = ((& $adb -s $Device shell "getprop sys.boot_completed" 2>&1) -join "").Trim()
            if ($LASTEXITCODE -eq 0 -and $boot -eq '1') {
                $consecutive++
                if ($consecutive -ge $Samples) { return $true }
            } else { $consecutive = 0 }
        } else { $consecutive = 0 }
        if ($IntervalSec -gt 0) { Start-Sleep -Seconds $IntervalSec }
    }
    return $false
}
