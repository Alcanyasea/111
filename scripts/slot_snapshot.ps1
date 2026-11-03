# 提交已校验的槽位快照。备份放在槽位根 .refresh-backup/，不会被切号脚本推到设备。
# 写入前设置恢复标记；中断后禁止再次覆盖备份或将混合数据推到设备。
function Publish-SlotSnapshot([string]$SlotDir, [System.Collections.IDictionary]$Files) {
    $recoveryMarker = Join-Path $SlotDir '.refresh-backup\recovery-required.txt'
    if (Test-Path -LiteralPath $recoveryMarker) { throw '上次槽位回滚未完成，请先从 .refresh-backup 恢复，拒绝覆盖备份' }
    $rootPath = [IO.Path]::GetFullPath($SlotDir).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    $entries = @()
    foreach ($relative in $Files.Keys) {
        $target = [IO.Path]::GetFullPath((Join-Path $SlotDir $relative))
        if (-not $target.StartsWith($rootPath, [StringComparison]::OrdinalIgnoreCase)) { throw '槽位快照路径越界' }
        $source = [string]$Files[$relative]
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "快照文件缺失: $relative" }
        $entries += @{
            Source=$source; Target=$target; Existed=(Test-Path -LiteralPath $target)
            Backup=(Join-Path (Join-Path $SlotDir '.refresh-backup') $relative)
        }
    }
    # 所有原件都备份成功后才允许改第一个目标。
    foreach ($entry in $entries) {
        if ($entry.Existed) {
            $null = New-Item -ItemType Directory -Path (Split-Path -Parent $entry.Backup) -Force -ErrorAction Stop
            Copy-Item -LiteralPath $entry.Target -Destination $entry.Backup -Force -ErrorAction Stop
        }
    }
    $changed = @()
    $null = New-Item -ItemType Directory -Path (Split-Path -Parent $recoveryMarker) -Force -ErrorAction Stop
    [IO.File]::WriteAllText($recoveryMarker, '槽位快照写入中；若进程已结束，请从此目录恢复后移除此标记。')
    try {
        foreach ($entry in $entries) {
            $null = New-Item -ItemType Directory -Path (Split-Path -Parent $entry.Target) -Force -ErrorAction Stop
            $changed += $entry  # 当前写入也可能只写了一半，必须纳入回滚。
            Copy-Item -LiteralPath $entry.Source -Destination $entry.Target -Force -ErrorAction Stop
        }
        Remove-Item -LiteralPath $recoveryMarker -ErrorAction Stop
    } catch {
        $originalError = $_
        $restoreErrors = @()
        foreach ($entry in $changed) {
            try {
                if ($entry.Existed) {
                    Copy-Item -LiteralPath $entry.Backup -Destination $entry.Target -Force -ErrorAction Stop
                } else {
                    Remove-Item -LiteralPath $entry.Target -Force -ErrorAction Stop
                }
            } catch { $restoreErrors += $_.Exception.Message }
        }
        if ($restoreErrors.Count -gt 0) {
            [IO.File]::WriteAllText($recoveryMarker, ($restoreErrors -join "`n"))
            throw ('槽位写入失败且部分文件无法回滚，备份已保留: ' + ($restoreErrors -join '; '))
        }
        Remove-Item -LiteralPath $recoveryMarker -ErrorAction Stop
        throw $originalError
    }
}

function Sync-SlotSnapshot([string]$Adb, [string]$Device, [string]$Package, [string]$SlotDir, [string]$PlayerPrefs) {
    if (-not $PlayerPrefs) {
        $listing = (& $Adb -s $Device shell "ls /data/data/$Package/shared_prefs/" 2>$null) -join "`n"
        if ($LASTEXITCODE -ne 0) { throw '无法列出设备登录文件' }
        $PlayerPrefs = (($listing -split "`n" | Where-Object { $_ -match '\.v2\.playerprefs\.xml' } | Select-Object -First 1) -replace '\s+', '')
    }
    if (-not $PlayerPrefs -or [IO.Path]::GetFileName($PlayerPrefs) -ne $PlayerPrefs) { throw 'playerprefs 文件名无效' }
    $tempRoot = Join-Path ([IO.Path]::GetTempPath()) ('maa-snapshot-' + [guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $tempRoot -ErrorAction Stop
    $prefs = Join-Path $tempRoot 'prefs.xml'
    $verify = Join-Path $tempRoot 'verify.xml'
    $sdk = Join-Path $tempRoot 'sdk.xml'
    $cache = Join-Path $tempRoot 'lc.cache'
    $uidTemp = Join-Path $tempRoot 'uid.txt'
    try {
        $remote = "/data/data/$Package/shared_prefs/$PlayerPrefs"
        & $Adb -s $Device pull $remote $prefs 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'playerprefs 回拉失败' }
        # XML 完整性 + UID 校验，拒绝带有 UID 但内容已截断的下载。
        $xml = [xml][IO.File]::ReadAllText($prefs)
        $uid = [string]($xml.map.string | Where-Object { $_.name -eq 'u8sdk_cached_uid' } | Select-Object -First 1).'#text'
        if (-not $uid -or $uid -notmatch '^\d+$') { throw '设备 UID 缺失' }
        $uidPath = Join-Path $SlotDir 'uid.txt'
        if (Test-Path -LiteralPath $uidPath) {
            $expected = [IO.File]::ReadAllText($uidPath).Trim()
            if ($expected -and $uid -ne $expected) { throw '设备 UID 与槽位不一致，保留原数据' }
        }
        $files = [ordered]@{}
        $files["shared_prefs\$PlayerPrefs"] = $prefs
        foreach ($item in @(
            @{Temp=$sdk; Relative='shared_prefs/HypergryphSdkPreferences.xml'},
            @{Temp=$cache; Relative='files/zx/lc.cache'}
        )) {
            & $Adb -s $Device pull "/data/data/$Package/$($item.Relative)" $item.Temp 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $item.Temp)) { $files[$item.Relative] = $item.Temp }
            elseif (Test-Path -LiteralPath (Join-Path $SlotDir $item.Relative)) {
                throw ('回拉已有文件失败，保留整份原槽位: ' + $item.Relative)
            }
        }
        & $Adb -s $Device pull $remote $verify 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'UID 复核回拉失败' }
        $xml2 = [xml][IO.File]::ReadAllText($verify)
        $uid2 = [string]($xml2.map.string | Where-Object { $_.name -eq 'u8sdk_cached_uid' } | Select-Object -First 1).'#text'
        if ($uid2 -ne $uid) { throw '回拉期间设备 UID 变化，丢弃本次快照' }
        if ((Ensure-GuideViewed $prefs) -lt 0) { throw '登录文件不完整，无法补写引导标记' }
        if (-not (Test-Path -LiteralPath $uidPath)) {
            [IO.File]::WriteAllText($uidTemp, $uid)
            $files['uid.txt'] = $uidTemp
        }
        Publish-SlotSnapshot $SlotDir $files
        return $uid
    } finally {
        Remove-Item -LiteralPath $prefs, $verify, $sdk, $cache, $uidTemp -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $tempRoot -ErrorAction SilentlyContinue
    }
}
