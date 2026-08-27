# ============================================================
# 账号捕获 v1 — 清空登录态 → 进入登录界面 → 输入账号密码 → 拉取登录数据到槽位
# 流程（官服）：
#   停游戏 → 备份并移走 3 个登录数据文件 → 写入最小 SDK prefs → 启动游戏
#   → OCR 循环点掉首次启动弹窗（配音包下载/选择）→ 标题画面 → 登录界面
#   → 账号登录 → 密码登录 → 输入账号/密码 → 登录
#   → 轮询 u8sdk_cached_uid 出现即成功 → 拉取文件到 accounts\<slot>\
# 兜底：自动输入不可用（特殊字符密码）或登录失败时，转「人工登录」：
#   提示用户在模拟器窗口手动完成登录，脚本轮询 uid，成功后自动拉取文件。
# 失败时自动恢复备份的原登录态。
# 用法：capture_account.ps1 -Server official -Slot official_1 -Username xxx -Password xxx [-Label 官服1]
# 退出码：0 成功；1 失败
# ============================================================
param(
    [string]$Server = "official",
    [string]$Slot = "",
    [string]$Username = "",
    [string]$Password = "",
    [string]$Label = "",
    [int]$DialogTimeoutMin = 8,     # 弹窗识别循环上限（分钟）
    [int]$ManualTimeoutMin = 12     # 人工登录等待上限（分钟）
)
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

$adb = "D:\软件\MuMu模拟器\MuMuPlayer\nx_main\adb.exe"
$device = "127.0.0.1:16384"
$scriptDir = "D:\1\scripts"
$debugDir = "D:\1\scripts\debug"

# ---- 读取 GUI 配置（D:\1\config.json），字段缺失时回退上面的硬编码默认 ----
$configPath = "D:\1\config.json"
if (Test-Path $configPath) {
    try {
        $raw = [System.IO.File]::ReadAllText($configPath, [System.Text.Encoding]::UTF8)
        $cfg = $raw | ConvertFrom-Json
        if ($null -ne $cfg.paths -and $cfg.paths.adb)    { $adb = [string]$cfg.paths.adb }
        if ($null -ne $cfg.paths -and $cfg.paths.device) { $device = [string]$cfg.paths.device }
        if ($null -ne $cfg.paths -and $cfg.paths.script_dir) { $scriptDir = [string]$cfg.paths.script_dir }
        if ($null -ne $cfg.paths -and $cfg.paths.script_dir) { $debugDir = Join-Path ([string]$cfg.paths.script_dir) "debug" }
    } catch {}
}
$accountsDir = Join-Path $scriptDir "accounts"

function Timestamp { Get-Date -Format "HH:mm:ss" }
$captureLog = Join-Path $debugDir ("capture_{0}.log" -f $Slot)
try { "==== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') Capture start (slot=$Slot) ====" | Set-Content $captureLog -Encoding UTF8 } catch {}
function LogLine($m) {
    $line = "$(Timestamp) [Capture] " + $m
    Write-Output $line
    try { Add-Content $captureLog $line -Encoding UTF8 -ErrorAction SilentlyContinue } catch {}
}

if (-not $Slot) { Write-Output "ERROR: -Slot is required"; exit 1 }
if ($Server -eq "bilibili") {
    $pkg = "com.hypergryph.arknights.bilibili"
    $otherPkg = "com.hypergryph.arknights"
    $serverName = "B服"
} else {
    $pkg = "com.hypergryph.arknights"
    $otherPkg = "com.hypergryph.arknights.bilibili"
    $serverName = "官服"
}
$slotDir = Join-Path $accountsDir $Slot

# OCR 库（主机端 Windows OCR）
. (Join-Path $scriptDir "ocr_lib.ps1")

if (-not (Test-Path $debugDir)) { New-Item -ItemType Directory $debugDir -Force | Out-Null }

# input text 可靠字符集（其余字符会让整串丢失，见实测）
$SAFE_CHARS = '^[A-Za-z0-9@.\!\#\$&\*\(\)\- ]+$'

function Invoke-Tap($x, $y) {
    & $adb -s $device shell "input tap $x $y" 2>$null | Out-Null
}

function Type-Field($x, $y, $text) {
    # 可靠输入序列：点字段聚焦 → 收键盘 → 再点一次重新聚焦 → 输入。
    # 实测：收键盘后直接 input text，首字符会被吞（首个按键用于重新聚焦）；
    # 收键盘后补一次点击再输入，字符串完整落框（两次实测通过）。
    Invoke-Tap $x $y
    Start-Sleep 2
    & $adb -s $device shell "input keyevent 4" 2>$null | Out-Null
    Start-Sleep 1
    Invoke-Tap $x $y
    Start-Sleep 2
    & $adb -s $device shell ("input text '" + ($text -replace "'","") + "'") 2>$null | Out-Null
    Start-Sleep 1
}

function Get-DeviceUid($ppName) {
    # 拉回本地解析 u8sdk_cached_uid（adb shell 引号转义不可靠）
    $tmpPp = Join-Path $env:TEMP "ark_cap_pp.xml"
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

function Get-PlayerPrefsName {
    $out = (& $adb -s $device shell "ls /data/data/$pkg/shared_prefs/" 2>$null) -join "`n"
    $name = ($out -split "`n" | Where-Object { $_ -match '\.v2\.playerprefs\.xml' } | Select-Object -First 1)
    return ($name -replace '\s+','')
}

function Write-FileRemote($localPath, $remotePath, $mode) {
    # 推送到设备并修正 owner/权限/上下文
    $uidStr = (& $adb -s $device shell "stat -c %u:%g /data/data/$pkg" 2>$null | Select-Object -First 1)
    $ctx = (& $adb -s $device shell "stat -c %C /data/data/$pkg/shared_prefs" 2>$null | Select-Object -First 1)
    if (-not $uidStr -or $uidStr -notmatch '^\d+:\d+') { $uidStr = "10044:10044" }
    if (-not $ctx -or $ctx -notmatch '^u:') { $ctx = "u:object_r:app_data_file:s0" }
    $tmp = "/data/local/tmp/ark_cap_push"
    & $adb -s $device push $localPath $tmp 2>$null | Out-Null
    $remoteDir = Split-Path $remotePath
    & $adb -s $device shell "mkdir -p $remoteDir; cp $tmp $remotePath; chown $uidStr $remotePath; chmod $mode $remotePath; chcon $ctx $remotePath" 2>$null | Out-Null
}

function Restore-Backup($ppName) {
    # 把 /data/local/tmp/ark_cap 里的备份还原到设备
    LogLine "恢复原登录态..."
    $uidStr = (& $adb -s $device shell "stat -c %u:%g /data/data/$pkg" 2>$null | Select-Object -First 1)
    $ctx = (& $adb -s $device shell "stat -c %C /data/data/$pkg/shared_prefs" 2>$null | Select-Object -First 1)
    if (-not $uidStr -or $uidStr -notmatch '^\d+:\d+') { $uidStr = "10044:10044" }
    if (-not $ctx -or $ctx -notmatch '^u:') { $ctx = "u:object_r:app_data_file:s0" }
    & $adb -s $device shell "cp /data/local/tmp/ark_cap/playerprefs.xml /data/data/$pkg/shared_prefs/$ppName; cp /data/local/tmp/ark_cap/sdk.xml /data/data/$pkg/shared_prefs/HypergryphSdkPreferences.xml; cp /data/local/tmp/ark_cap/lc.cache /data/data/$pkg/files/zx/lc.cache; chown $uidStr /data/data/$pkg/shared_prefs/$ppName /data/data/$pkg/shared_prefs/HypergryphSdkPreferences.xml /data/data/$pkg/files/zx/lc.cache; chmod 660 /data/data/$pkg/shared_prefs/$ppName /data/data/$pkg/shared_prefs/HypergryphSdkPreferences.xml; chmod 600 /data/data/$pkg/files/zx/lc.cache; chcon $ctx /data/data/$pkg/shared_prefs/$ppName /data/data/$pkg/shared_prefs/HypergryphSdkPreferences.xml /data/data/$pkg/files/zx/lc.cache; rm -rf /data/local/tmp/ark_cap" 2>$null | Out-Null
}

# ================= 开始 =================
LogLine ("=== Capture {0} -> slot '{1}' (uid target unknown) ===" -f $serverName, $Slot)

& $adb -s $device root 2>$null | Out-Null
Start-Sleep 2
& $adb -s $device connect $device 2>$null | Out-Null
Start-Sleep 1

$ppName = Get-PlayerPrefsName
if (-not $ppName) { LogLine "ERROR: cannot find playerprefs name on device"; exit 1 }
LogLine "playerprefs: $ppName"

# ---- 停游戏 ----
& $adb -s $device shell "am force-stop $pkg; am force-stop $otherPkg" 2>$null | Out-Null
Start-Sleep 2

# ---- 若上次捕获中断残留了备份，先还原 ----
$backupExists = (& $adb -s $device shell "ls /data/local/tmp/ark_cap/" 2>$null) -join ''
if ($backupExists -match 'playerprefs|sdk|lc') {
    LogLine "检测到上次捕获残留备份，先还原原登录态"
    Restore-Backup $ppName
    Start-Sleep 1
}

# ---- 备份并移走登录数据文件 ----
LogLine "备份并清空登录态..."
& $adb -s $device shell "mkdir -p /data/local/tmp/ark_cap; mv /data/data/$pkg/shared_prefs/$ppName /data/local/tmp/ark_cap/playerprefs.xml; mv /data/data/$pkg/shared_prefs/HypergryphSdkPreferences.xml /data/local/tmp/ark_cap/sdk.xml; mv /data/data/$pkg/files/zx/lc.cache /data/local/tmp/ark_cap/lc.cache" 2>$null | Out-Null

# ---- 写入最小 SDK prefs（已同意用户协议，跳过协议弹窗）----
$minSdk = Join-Path $env:TEMP "ark_min_sdk.xml"
@'
<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <string name="HypergryphUserProtocol">1</string>
</map>
'@ | Set-Content $minSdk -Encoding UTF8
Write-FileRemote $minSdk "/data/data/$pkg/shared_prefs/HypergryphSdkPreferences.xml" "660"
Remove-Item $minSdk -Force -ErrorAction SilentlyContinue

# ---- 启动游戏 ----
LogLine "启动游戏..."
& $adb -s $device shell "monkey -p $pkg -c android.intent.category.LAUNCHER 1" 2>$null | Out-Null

# ---- OCR 弹窗循环（官服：配音包弹窗 → 解压 → 配音选择 → 标题画面 → 登录界面）----
$reachedLogin = $false
$png = Join-Path $debugDir ("cap_{0}_screen.png" -f $Slot)
if ($Server -eq "official") {
    $deadline = (Get-Date).AddMinutes($DialogTimeoutMin)
    $lastBlindTap = (Get-Date).AddSeconds(-60)
    $voiceKept = $false
    while ((Get-Date) -lt $deadline) {
        if (-not (Ocr-Screenshot $adb $device $png)) { Start-Sleep 5; continue }
        $words = Get-OcrWords $png
        $act = $null
        if (Find-OcrText $words "账号登录") { $act = "login"; break }
        if ((Find-OcrText $words "维持原有配置") -and (-not $voiceKept)) {
            # 配音选择弹窗：先勾选「维持原有配置」，下一轮再点确认（避免只点选项不确认）
            $hit = Find-OcrText $words "维持原有配置"
            Invoke-Tap $hit.X $hit.Y
            $voiceKept = $true
            $act = "voice_keep"
        }
        elseif (Find-OcrText $words "确认") { $hit = Find-OcrText $words "确认"; Invoke-Tap $hit.X $hit.Y; $act = "confirm" }
        elseif (Find-OcrText $words "同意并继续") { $hit = Find-OcrText $words "同意并继续"; Invoke-Tap $hit.X $hit.Y; $act = "agree" }
        elseif (Find-OcrText $words "开始唤醒") { $hit = Find-OcrText $words "开始唤醒"; Invoke-Tap $hit.X $hit.Y; $act = "wake" }
        elseif (((Get-Date) - $lastBlindTap).TotalSeconds -gt 45) {
            Invoke-Tap 640 360; $lastBlindTap = Get-Date; $act = "blind_tap"
        }
        if ($act) { LogLine ("[dialog] " + $act) }
        Start-Sleep 5
    }
    if ($act -eq "login") {
        $reachedLogin = $true
        LogLine "已到达登录界面"
    } else {
        LogLine ("WARN: {0} 分钟内未识别到登录界面（可能弹窗形态有变），转人工登录" -f $DialogTimeoutMin)
    }
} else {
    # B服：登录界面结构未自动化，等待启动后直接转人工登录
    Start-Sleep 60
}

# ---- 输入账号密码（官服且凭据字符集安全时）----
$autoTyped = $false
if ($reachedLogin -and $Server -eq "official" -and $Username -and $Password) {
    if ($Username -match $SAFE_CHARS -and $Password -match $SAFE_CHARS) {
        LogLine "自动输入账号密码..."
        # 1) 登录界面 → 点「账号登录」（$words 为弹窗循环最后一张截图，含该按钮）
        $hit = Find-OcrText $words "账号登录"
        if ($hit) { Invoke-Tap $hit.X $hit.Y }
        # 2) 轮询等待「密码登录」链接出现（手机号表单渲染可能需要几秒）
        $deadline3 = (Get-Date).AddSeconds(20)
        $hit = $null
        while ((Get-Date) -lt $deadline3) {
            Start-Sleep 3
            if (Ocr-Screenshot $adb $device $png) {
                $hit = Find-OcrText (Get-OcrWords $png) "密码登录"
                if ($hit) { break }
            }
        }
        if ($hit) { Invoke-Tap $hit.X $hit.Y } else {
            LogLine "WARN: 20 秒内未找到「密码登录」，用固定坐标重试"
            Invoke-Tap 810 568
        }
        # 3) 轮询等待密码表单出现（「请输入账号」占位符可见）
        $deadline4 = (Get-Date).AddSeconds(20)
        $words3 = $null
        while ((Get-Date) -lt $deadline4) {
            Start-Sleep 3
            if (Ocr-Screenshot $adb $device $png) {
                $w3 = Get-OcrWords $png
                if ((Find-OcrText $w3 "请输入账号") -and (Find-OcrText $w3 "请输入密码")) {
                    $words3 = $w3
                    break
                }
            }
        }
        if ($null -eq $words3) {
            LogLine "WARN: 密码表单未出现，放弃自动输入，转人工登录"
        } else {
            # 4) 输入账号（Type-Field 内含收键盘+聚焦+3秒等待，防首字符被吞）
            $f1 = Find-OcrText $words3 "请输入账号"
            if ($f1) { Type-Field $f1.X $f1.Y $Username } else { Type-Field 545 283 $Username }
            # 账号框内容自校验（可见字段）：必须匹配前 5 个字符，否则清空重输一次
            if (Ocr-Screenshot $adb $device $png) {
                $wv = Get-OcrWords $png
                $fieldText = (($wv | Where-Object { $_.Y -gt 250 -and $_.Y -lt 320 } | ForEach-Object { $_.Text }) -join '')
                $u = $Username
                $headOk = ($u.Length -lt 5) -or ($fieldText -match [regex]::Escape($u.Substring(0, 5)))
                if (-not $headOk) {
                    LogLine "账号框内容异常（$fieldText），清空重输一次"
                    & $adb -s $device shell "input keyevent 123; for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24; do input keyevent 67; done" 2>$null | Out-Null
                    Start-Sleep 2
                    if ($f1) { Type-Field $f1.X $f1.Y $Username } else { Type-Field 545 283 $Username }
                }
            }
            # 5) 输入密码（掩码不可校验，同样用可靠输入序列）
            $f2 = Find-OcrText $words3 "请输入密码"
            if ($f2) { Type-Field $f2.X $f2.Y $Password } else { Type-Field 545 363 $Password }
            # 6) 勾选用户协议（清空登录态后必为未勾选，实测空心圆），再点登录
            Invoke-Tap 440 440
            Start-Sleep 1
            $btn = $null
            if (Ocr-Screenshot $adb $device $png) {
                $btn = Find-OcrText (Get-OcrWords $png) "登录" -Exact
            }
            if ($btn) { Invoke-Tap $btn.X $btn.Y } else { Invoke-Tap 640 516 }
            LogLine "已提交登录，等待结果..."
            $autoTyped = $true
        }
    } else {
        LogLine "WARN: 账号/密码含特殊字符（% ^ _ + = [ ] 等），input text 会丢字，转人工登录"
    }
}

# ---- 轮询登录成功（u8sdk_cached_uid 出现）----
$uid = Get-DeviceUid $ppName
if ($uid) { LogLine "设备已有 uid=$uid（尚未登录新账号？继续等待变化）" }

LogLine "=============================================="
LogLine ">>> 若自动登录未完成，可在模拟器窗口中手动登录（含验证码等人工操作）<<<"
LogLine (">>> 脚本自动检测登录成功并拉取数据，最多等待 {0} 分钟 <<<" -f $ManualTimeoutMin)
LogLine "=============================================="
$deadline2 = (Get-Date).AddMinutes($ManualTimeoutMin)
$checkboxTried = $false
while ((Get-Date) -lt $deadline2) {
    Start-Sleep 6
    $uid = Get-DeviceUid $ppName
    if ($uid) {
        LogLine ("检测到登录成功 uid=" + $uid)
        break
    }
    # 自动输入后仍停留在密码表单 → 再点一次登录（协议已在提交前勾选，避免重复点复选框反而取消勾选）
    # 判定：协议行在屏 且 账号框还显示着输入的用户名（表单未被 SDK 重置）
    if ($autoTyped -and -not $checkboxTried) {
        if (Ocr-Screenshot $adb $device $png) {
            $w = Get-OcrWords $png
            if (Find-OcrText $w "用户注册协议") {
                $fieldText = (($w | Where-Object { $_.Y -gt 250 -and $_.Y -lt 320 } | ForEach-Object { $_.Text }) -join '')
                $u = $Username
                $stillForm = ($u.Length -lt 5) -or ($fieldText -match [regex]::Escape($u.Substring(0, 5)))
                if ($stillForm) {
                    LogLine "仍在登录表单，重新点一次登录..."
                    $btn = Find-OcrText $w "登录" -Exact
                    if ($btn) { Invoke-Tap $btn.X $btn.Y } else { Invoke-Tap 640 516 }
                    $checkboxTried = $true
                }
            }
        }
    }
}
if (-not $uid) {
    LogLine ("ERROR: {0} 分钟内未检测到登录成功，捕获失败" -f $ManualTimeoutMin)
    Restore-Backup $ppName
    exit 1
}

# ---- 拉取文件到槽位 ----
LogLine ("拉取登录数据到 slot: " + $Slot)
$dstShared = Join-Path $slotDir "shared_prefs"
$dstFiles = Join-Path $slotDir "files\zx"
New-Item -ItemType Directory -Force $dstShared, $dstFiles | Out-Null
& $adb -s $device pull "/data/data/$pkg/shared_prefs/$ppName" (Join-Path $dstShared $ppName) 2>$null | Out-Null
& $adb -s $device pull "/data/data/$pkg/shared_prefs/HypergryphSdkPreferences.xml" (Join-Path $dstShared "HypergryphSdkPreferences.xml") 2>$null | Out-Null
# lc.cache 是可选缓存：刚登录完游戏可能还没写它（首次启动实测 zx 目录下无此文件）
& $adb -s $device pull "/data/data/$pkg/files/zx/lc.cache" (Join-Path $dstFiles "lc.cache") 2>$null | Out-Null
if (-not (Test-Path (Join-Path $dstFiles "lc.cache"))) {
    LogLine "WARN: 设备上暂无 lc.cache（登录后尚未生成，属正常现象，忽略）"
}
$uid | Out-File (Join-Path $slotDir "uid.txt") -Encoding ascii -NoNewline
$label | Out-File (Join-Path $slotDir "label.txt") -Encoding utf8

# 校验拉取结果：两份 shared_prefs 是登录态核心，缺一不可；lc.cache 可缺
$okFiles = @(Get-ChildItem $slotDir -Recurse -File | Where-Object {
    $_.Name -match 'playerprefs|HypergryphSdkPreferences'
})
if ($okFiles.Count -lt 2) {
    LogLine "ERROR: 拉取文件不完整（$($okFiles.Count)/2 核心文件）"
    Restore-Backup $ppName
    exit 1
}

# 清理设备侧备份（捕获成功，原登录态由槽位保存）
& $adb -s $device shell "rm -rf /data/local/tmp/ark_cap" 2>$null | Out-Null

LogLine ("=== Capture SUCCESS: $serverName slot=$Slot uid=$uid ===")
exit 0
