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

function Write-LoginHeartbeat {
    # 心跳：每轮主循环刷新一次，master.ps1 的看门狗按「距上次心跳 30 秒」判挂死。
    # 循环还活着（游戏更新等待、慢截图、识别中等）心跳就一直续命；卡死在
    # vision ReadLine / adb 截图时心跳停止，30 秒后被看门狗强杀。
    try {
        Set-Content -Path (Join-Path $scriptDir "login_heartbeat.tmp") `
            -Value (Get-Date).ToString('HH:mm:ss') -Encoding ascii -ErrorAction Stop
    } catch {}
}

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

# 「界面首次进入引导」已看过标记清单（key_GB_viewed#...）：游戏把这些 int=1
# 标记写进 playerprefs 后，对应界面的首次引导不再弹出。清单是四个账号槽位
# 里实际出现过的全部引导标记并集（2026-09-16 收集）；以后游戏新加引导时，
# 从任一老账号的槽位 playerprefs 里找到对应键名，在此补一行即可。
# 注意：B服槽位里的 key_shop_extra_qc_viewed 是 string 时间戳（采购相关），
# 不是 GB_viewed 引导标记，语义不明，不在此补写。
$guideViewedKeys = @(
    'BUILDING_STATION_MANAGE',                              # 基建「建筑管理」引导
    'CHAR_INFO',                                            # 干员信息界面引导
    'CRISIS_V2#entry',                                      # 危机合约入口引导
    'CRISIS_V2#map',                                        # 危机合约地图引导
    'CRISIS_V2#shop',                                       # 危机合约商店引导
    'ROGUELIKE_BP#bp',                                      # 集成战略数据（月报）引导
    'ROGUELIKE_CHARSELECT#select',                          # 肉鸽选人界面引导
    'ROGUELIKE_CHARSELECT#rl05_stash_select',               # 肉鸽仓库引导（秘宝楼台）
    'ROGUELIKE_CHARSELECT#rl06_stash_select',               # 肉鸽仓库引导（新主题）
    'ROGUELIKE_DUNGEON#rogue_5',                            # 肉鸽关卡引导（秘宝楼台）
    'ROGUELIKE_DUNGEON#rogue_5_copper',                     # 肉鸽券兑换引导
    'ROGUELIKE_DUNGEON#rogue_5_sp',                         # 肉鸽特殊层引导
    'ROGUELIKE_DUNGEON#rogue_6',                            # 肉鸽关卡引导（新主题）
    'SPECIAL_OPERATOR#board',                               # 特殊干员看板引导
    'STAGE_CAMPAIGN#campaign_world_home_state',             # 作战（主线/活动）主页引导
    'ART_MAGAZINE'                                          # 艺术杂志活动界面引导
)

function Ensure-GuideViewed($ppPath) {
    # 给槽位 playerprefs 补写上面清单里全部缺失的「引导已看过」标记：
    # 键为 <uid>#key_GB_viewed#<组>#<名>（XML 里 # 转义为 %23，int=1，
    # 与游戏自身写入一致）。切号推回设备后这些首次提示不再弹出。
    # 返回补写条数；0 = 本已齐全；-1 = 文件缺失/无 uid/读写失败。
    if (-not (Test-Path $ppPath)) { return -1 }
    try {
        $xml = [System.IO.File]::ReadAllText($ppPath, [System.Text.Encoding]::UTF8)
        $m = [regex]::Match($xml, 'name="u8sdk_cached_uid">([0-9]+)')
        if (-not $m.Success) { return -1 }
        $uid = $m.Groups[1].Value
        if (-not $xml.Contains('</map>')) { return -1 }
        $added = 0
        foreach ($g in $guideViewedKeys) {
            $key = $uid + '%23key_GB_viewed%23' + ($g -replace '#', '%23')
            if ($xml.Contains('name="' + $key + '"')) { continue }
            $marker = '<int name="' + $key + '" value="1" />'
            $xml = $xml.Replace('</map>', $marker + "`n</map>")
            $added++
        }
        if ($added -gt 0) {
            [System.IO.File]::WriteAllText($ppPath, $xml, (New-Object System.Text.UTF8Encoding($false)))
        }
        return $added
    } catch { return -1 }
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
    # 引导已看过标记：把清单里缺失的全部补写进槽位 playerprefs，
    # 各界面首次提示不再弹出（清单与说明见上方 $guideViewedKeys）
    $n = Ensure-GuideViewed (Join-Path $dstShared $pp)
    if ($n -lt 0) {
        LogLine "WARN: 未能补写引导已看过标记（槽位数据可能不完整）"
    } elseif ($n -gt 0) {
        LogLine ("[slot] 补写引导已看过标记 {0} 条（首次进入提示不再弹出）" -f $n)
    }
    $devUid | Out-File $uidFile -Encoding ascii -NoNewline
    LogLine ("[slot] 槽位数据已刷新（uid={0}）" -f $devUid)
    return $true
}

# ============================================================
# 登录表单录入（login_check.ps1 与 capture_account.ps1 共用，v4.1 从两份
# 逐字拷贝收敛到这里；依赖调用方作用域的 $adb / $device 与库内 Invoke-Tap）
# ============================================================

# input text 可靠字符集（其余字符会让整串丢失，见实测）
$SAFE_CHARS = '^[A-Za-z0-9@.\!\#\$&\*\(\)\- ]+$'

function Type-Field($x, $y, $text) {
    # 可靠输入序列：点字段聚焦 → 收键盘 → 再点一次重新聚焦 → 输入。
    # 实测：收键盘后直接 input text，首字符会被吞（首个按键用于重新聚焦）；
    # 收键盘后补一次点击再输入，字符串完整落框。
    Invoke-Tap $x $y
    Start-Sleep 2
    & $adb -s $device shell "input keyevent 4" 2>$null | Out-Null
    Start-Sleep 1
    Invoke-Tap $x $y
    Start-Sleep 2
    & $adb -s $device shell ("input text '" + ($text -replace "'","") + "'") 2>$null | Out-Null
    Start-Sleep 1
}
