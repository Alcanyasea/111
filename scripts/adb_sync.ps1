# 把「已确认可用的 ADB 地址」同步进项目配置与 MAA 配置。
#
# 设计要点：
# - 地址没变就不写盘（避免每轮重排 60KB 配置，也避开无谓的写失败）。
# - 每次写盘都是「备份 → 写临时文件 → 原子替换」，单个文件要么全旧要么全新。
# - config.json 写不进去 = 子脚本会拿旧地址，视为硬失败（返回 $false）。
# - MAA 配置写不进去只记警告（$script:LastAdbSyncWarning）：ADB 已确认可用，
#   不值得因为 MAA 的 sidecar 文件把整轮挂机 FATAL 掉。
$script:LastAdbSyncWarning = ""

function Write-JsonWithBackup {
    param([string]$Path, $Data, [int]$Attempts = 3)
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $tmp = "$Path.adb.tmp"
    $backup = "$Path.adb.bak"
    for ($i = 1; $i -le [math]::Max(1, $Attempts); $i++) {
        try {
            Copy-Item -LiteralPath $Path -Destination $backup -Force -ErrorAction Stop
            $utf8 = [System.Text.UTF8Encoding]::new($false)
            [System.IO.File]::WriteAllText($tmp, ($Data | ConvertTo-Json -Depth 50), $utf8)
            # 同卷覆盖用 File.Move(overwrite) 更接近原子替换；老运行时回退 Move-Item
            try { [System.IO.File]::Move($tmp, $Path, $true) }
            catch {
                Move-Item -LiteralPath $tmp -Destination $Path -Force -ErrorAction Stop
                # 目标若是目录，Move-Item 会把 tmp 搬进去并"成功"，这里必须识破
                if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "目标不是文件：$Path" }
            }
            return $true
        } catch {
            # 只清理自己写的临时文件；同名目录（异常残留）不删也不提示，
            # 否则非交互运行会卡在 Remove-Item 的确认提示上
            if (Test-Path -LiteralPath $tmp -PathType Leaf) {
                Remove-Item -LiteralPath $tmp -Force -Confirm:$false -ErrorAction SilentlyContinue
            }
            if ($i -lt $Attempts) { Start-Sleep -Milliseconds (120 * $i) }
        }
    }
    return $false
}

# 把一份 MAA gui.new.json 里**所有方案**的地址改成目标值。
# 只改 Current + Default 不够：真实部署里还有「B服 / 官服大号 / 收菜」等方案，
# 端口漂移后其余方案会一直指向死端口，且此后再也不会被判定为「需要同步」。
function Set-MaaAllSchemeAddress {
    param($Data, [string]$Device)
    $configs = $Data.Configurations
    if ($configs -isnot [pscustomobject]) { throw "Configurations 缺失或不是对象" }
    $changed = $false
    foreach ($prop in @($configs.PSObject.Properties)) {
        $conf = $prop.Value
        if ($conf -isnot [pscustomobject]) { continue }
        if ($conf.Gui -isnot [pscustomobject]) {
            $conf | Add-Member -NotePropertyName Gui -NotePropertyValue ([pscustomobject]@{}) -Force
        }
        if ($conf.Gui.ConnectSettings -isnot [pscustomobject]) {
            $conf.Gui | Add-Member -NotePropertyName ConnectSettings -NotePropertyValue ([pscustomobject]@{}) -Force
        }
        if ([string]$conf.Gui.ConnectSettings.Address -ne $Device) { $changed = $true }
        $conf.Gui.ConnectSettings | Add-Member -NotePropertyName Address -NotePropertyValue $Device -Force
    }
    return $changed
}

# 只判断是否需要写盘，不产生副作用。Ok=false 表示 config.json 读不出来，
# 不能当作「无需同步」——必须走写盘路径让上层拿到失败。
function Get-AdbSyncTargets {
    param([string]$ConfigPath, [string]$ConfirmedDevice, [string[]]$MaaDirs = @())
    $targets = @()
    $ok = $true
    try {
        $cfg = [System.IO.File]::ReadAllText($ConfigPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
        if ([string]$cfg.paths.device -ne $ConfirmedDevice) { $targets += "config" }
    } catch { $ok = $false }
    foreach ($maaDir in @($MaaDirs | Where-Object { $_ } | Select-Object -Unique)) {
        $guiPath = Join-Path $maaDir "config\gui.new.json"
        if (-not (Test-Path -LiteralPath $guiPath)) {
            # 目录在但配置文件不在：MAA 首次运行前可能如此，提示一句免得静默失败
            if (Test-Path -LiteralPath (Split-Path -Parent $guiPath)) { $targets += "missing:$guiPath" }
            continue
        }
        try {
            $data = [System.IO.File]::ReadAllText($guiPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
            $configs = $data.Configurations
            if ($configs -isnot [pscustomobject]) { $targets += $guiPath; continue }
            foreach ($prop in @($configs.PSObject.Properties)) {
                $conf = $prop.Value
                if ($conf -isnot [pscustomobject]) { continue }
                if ([string]$conf.Gui.ConnectSettings.Address -ne $ConfirmedDevice) {
                    $targets += $guiPath
                    break
                }
            }
        } catch { $targets += $guiPath }
    }
    return [pscustomobject]@{ Ok = $ok; Targets = @($targets) }
}

function Sync-ConfirmedAdbDevice {
    param(
        [Parameter(Mandatory)][string]$ConfigPath,
        [Parameter(Mandatory)][string]$ConfirmedDevice,
        [string[]]$MaaDirs = @()
    )
    $script:LastAdbSyncWarning = ""
    if ([string]::IsNullOrWhiteSpace($ConfirmedDevice) -or
        -not (Test-Path -LiteralPath $ConfigPath)) { return $false }

    $plan = Get-AdbSyncTargets -ConfigPath $ConfigPath -ConfirmedDevice $ConfirmedDevice -MaaDirs $MaaDirs
    if ($plan.Ok -and $plan.Targets.Count -eq 0) { return $true }

    # ---- 项目配置：子脚本都从这里读地址，写不进去算硬失败（但要先把 MAA 写完）----
    $configOk = $true
    try {
        $cfg = [System.IO.File]::ReadAllText($ConfigPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
        if ([string]$cfg.paths.device -ne $ConfirmedDevice) {
            if ($cfg.paths -isnot [pscustomobject]) {
                $cfg | Add-Member -NotePropertyName paths -NotePropertyValue ([pscustomobject]@{}) -Force
            }
            $cfg.paths | Add-Member -NotePropertyName device -NotePropertyValue $ConfirmedDevice -Force
            if (-not (Write-JsonWithBackup $ConfigPath $cfg)) {
                $configOk = $false
                $script:LastAdbSyncWarning = "config.json 写入失败（若 $ConfigPath.adb.tmp 被同名目录占用，需要手工清理）"
            }
        }
    } catch {
        $configOk = $false
        $script:LastAdbSyncWarning = "config.json 读取失败：$($_.Exception.Message)"
    }

    # ---- MAA 配置：失败只记警告，不回滚配置、不阻断整轮 ----
    $warnings = @()
    foreach ($maaDir in @($MaaDirs | Where-Object { $_ } | Select-Object -Unique)) {
        $guiPath = Join-Path $maaDir "config\gui.new.json"
        if (-not (Test-Path -LiteralPath $guiPath)) {
            if (Test-Path -LiteralPath (Split-Path -Parent $guiPath)) {
                $warnings += "未找到 MAA 配置：$guiPath（MAA 首次运行后才会生成，端口变了需手动改）"
            }
            continue
        }
        try {
            $data = [System.IO.File]::ReadAllText($guiPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
            $changed = Set-MaaAllSchemeAddress -Data $data -Device $ConfirmedDevice
            if (-not $changed) { continue }
            if (-not (Write-JsonWithBackup $guiPath $data)) {
                $warnings += "写入失败：$guiPath（若同名 .adb.tmp 被目录占用需手工清理）"
            }
        } catch {
            $warnings += ("读取失败：{0}（{1}）" -f $guiPath, $_.Exception.Message)
        }
    }
    if ($warnings.Count -gt 0) {
        $maaWarning = "MAA 连接地址未能同步（$($warnings -join '；')）；ADB 已可用，MAA 可能连不上，下一次启动会重试"
        $script:LastAdbSyncWarning = if ($script:LastAdbSyncWarning) { "$($script:LastAdbSyncWarning)；$maaWarning" } else { $maaWarning }
    }
    return $configOk
}

# 子脚本（slot_switch / login_check）中途重发现端口后的回写入口：
# 从项目配置里取两套 MAA 目录，调用上面的统一同步。
function Sync-AdbDeviceForProject {
    param([string]$ProjectRoot, [string]$Device, $Config)
    $maaDirs = @()
    foreach ($key in @("maa_official", "maa_bilibili")) {
        $path = if ($Config -and $Config.paths) { [string]$Config.paths.$key } else { "" }
        if ($path) {
            $dir = Split-Path -Parent $path
            if ($dir) { $maaDirs += $dir }
        }
    }
    return Sync-ConfirmedAdbDevice -ConfigPath (Join-Path $ProjectRoot "config.json") `
        -ConfirmedDevice $Device -MaaDirs $maaDirs
}
