# ============================================================
# 登录检查共用设备/槽位库 — login_check.ps1（官服）与
# login_check_bilibili.ps1（B服）共用
# 这些函数原本只在 login_check.ps1 里；B服拆出独立校验脚本后收敛到这里
# 统一维护（与 game_state_lib.ps1 同一初衷：两份脚本各复制一份，改一处容易漏一处）。
# 依赖：调用方脚本需先定义 $adb / $device / $pkg / $slotDir
#（PowerShell 动态作用域，函数体在调用时从调用方取这些变量）。
# 用法：. (Join-Path $scriptDir "login_device_lib.ps1")
# ============================================================

# 强制更新走系统包安装器时的前台包名特征（OCR 常识别不到安装进度）
$installPkgPattern = 'packageinstaller|permissioncontroller'

function Is-InstallerForeground {
    $out = (& $adb -s $device shell "dumpsys window windows" 2>$null) -join "`n"
    return ($out -match $installPkgPattern)
}

function Invoke-Tap($x, $y) {
    & $adb -s $device shell "input tap $x $y" 2>$null | Out-Null
}

function Get-PlayerPrefsName {
    $out = (& $adb -s $device shell "ls /data/data/$pkg/shared_prefs/" 2>$null) -join "`n"
    $name = ($out -split "`n" | Where-Object { $_ -match '\.v2\.playerprefs\.xml' } | Select-Object -First 1)
    return ($name -replace '\s+','')
}

function Get-DeviceUid($ppName) {
    # 拉回本地解析 u8sdk_cached_uid（adb shell 引号转义不可靠）
    $tmpPp = Join-Path $env:TEMP "ark_check_pp.xml"
    $uid = ""
    & $adb -s $device pull ("/data/data/{0}/shared_prefs/{1}" -f $pkg, $ppName) $tmpPp 2>$null | Out-Null
    if (Test-Path $tmpPp) {
        try {
            $ppContent = [System.IO.File]::ReadAllText($tmpPp, [System.Text.Encoding]::UTF8)
            $m = [regex]::Match($ppContent, 'name="u8sdk_cached_uid">([0-9]+)')
            if ($m.Success) { $uid = $m.Groups[1].Value }
        } catch {}
        Remove-Item $tmpPp -Force -ErrorAction SilentlyContinue
    }
    return $uid
}

function Ensure-InfraGuideViewed($ppPath) {
    # 基建「建筑管理」引导标记：新账号首次进基建会弹引导
    # （key_GB_viewed#BUILDING_STATION_MANAGE），缺失时每次进基建都重弹。
    # 这里在槽位 playerprefs 上补写该标记（与游戏自身写入一致），保证
    # 切号/MAA 运行不再出现首次提示。返回 $true 表示已确保存在。
    if (-not (Test-Path $ppPath)) { return $false }
    try {
        $xml = [System.IO.File]::ReadAllText($ppPath, [System.Text.Encoding]::UTF8)
        if ($xml.Contains('key_GB_viewed%23BUILDING_STATION_MANAGE')) { return $true }
        $m = [regex]::Match($xml, 'name="u8sdk_cached_uid">([0-9]+)')
        if (-not $m.Success) { return $false }
        $key = $m.Groups[1].Value + '%23key_GB_viewed%23BUILDING_STATION_MANAGE'
        $marker = '<int name="' + $key + '" value="1" />'
        if (-not $xml.Contains('</map>')) { return $false }
        $xml = $xml.Replace('</map>', $marker + "`n</map>")
        [System.IO.File]::WriteAllText($ppPath, $xml, (New-Object System.Text.UTF8Encoding($false)))
        return $true
    } catch { return $false }
}

function Update-SlotData([bool]$ExpectVoiceKeys) {
    # 已确认登录后，把设备上最新的登录数据拉回槽位（镜像设备相对路径）：
    # - 首次启动配音弹窗处理完，游戏会写入 KEY_GLOBAL_VOICE_LANG / KEY_VOICE_LANG_PREF_DONTCG；
    # - 公告弹窗关掉后会写入按 uid 缓存的 key_home_annouce* 版本号。
    # 不拉回的话，下次切号会用旧槽位数据覆盖设备，弹窗每次重新出现。
    # 写入前校验设备 uid 与槽位 uid 一致（防跑错号）。
    if (-not $slotDir) { return $false }
    $pp = Get-PlayerPrefsName
    if (-not $pp) { LogLine "WARN: 无法获取 playerprefs 文件名，跳过槽位刷新"; return $false }
    $devUid = ""
    for ($i = 0; $i -lt 6; $i++) {
        $devUid = Get-DeviceUid $pp
        if ($devUid) { break }
        Start-Sleep 5
    }
    if (-not $devUid) { LogLine "WARN: 未读取到设备 uid，跳过槽位刷新"; return $false }
    $uidFile = Join-Path $slotDir "uid.txt"
    $expectUid = ""
    if (Test-Path $uidFile) { $expectUid = (Get-Content $uidFile -Raw -ErrorAction SilentlyContinue).Trim() }
    if ($expectUid -and ($devUid -ne $expectUid)) {
        LogLine ("WARN: 设备 uid（{0}）与槽位 uid（{1}）不一致，拒绝刷新槽位" -f $devUid, $expectUid)
        return $false
    }
    # 弹窗刚点掉时游戏写入语音标记可能有延迟：最多等 ~25 秒（标记出现即拉取）
    $deadlineV = (Get-Date).AddSeconds(25)
    while ($ExpectVoiceKeys -and ((Get-Date) -lt $deadlineV)) {
        $tmpPp = Join-Path $env:TEMP "ark_refresh_pp.xml"
        & $adb -s $device pull ("/data/data/{0}/shared_prefs/{1}" -f $pkg, $pp) $tmpPp 2>$null | Out-Null
        if (Test-Path $tmpPp) {
            $ppc = [System.IO.File]::ReadAllText($tmpPp, [System.Text.Encoding]::UTF8)
            Remove-Item $tmpPp -Force -ErrorAction SilentlyContinue
            if ($ppc -match 'KEY_VOICE_LANG_PREF_DONTCG') { break }
        }
        Start-Sleep 5
    }
    $dstShared = Join-Path $slotDir "shared_prefs"
    $dstFiles = Join-Path $slotDir "files\zx"
    New-Item -ItemType Directory -Force $dstShared, $dstFiles | Out-Null
    & $adb -s $device pull ("/data/data/{0}/shared_prefs/{1}" -f $pkg, $pp) (Join-Path $dstShared $pp) 2>$null | Out-Null
    & $adb -s $device pull "/data/data/$pkg/shared_prefs/HypergryphSdkPreferences.xml" (Join-Path $dstShared "HypergryphSdkPreferences.xml") 2>$null | Out-Null
    # lc.cache 是可选缓存：登录后游戏可能还没写它（首次启动实测 zx 目录下无此文件）
    & $adb -s $device pull "/data/data/$pkg/files/zx/lc.cache" (Join-Path $dstFiles "lc.cache") 2>$null | Out-Null
    if (-not (Test-Path (Join-Path $dstFiles "lc.cache"))) { LogLine "WARN: 设备暂无 lc.cache（登录后尚未生成，忽略）" }
    # 基建引导标记：补写进槽位 playerprefs，避免首次进基建弹提示、之后每次都重弹
    Ensure-InfraGuideViewed (Join-Path $dstShared $pp) | Out-Null
    $devUid | Out-File $uidFile -Encoding ascii -NoNewline
    LogLine ("[slot] 槽位数据已刷新（uid={0}）" -f $devUid)
    return $true
}
