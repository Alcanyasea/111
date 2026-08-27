# ============================================================
# 登录校验 v1 — 槽位切号后、MAA 启动前：屏幕级确认游戏已登录
# 已登录（无登录界面标记）→ 直接放行；检测到登录界面 →
#   官服：自动输入账号密码登录（凭据取自 config.accounts，不经命令行传递），
#         成功后校验 uid 与槽位一致并刷新槽位数据（token 续期）
#   B服/无凭据/验证码：失败退出，master.ps1 将该号标记失败（防 MAA 对着登录界面空跑）
# 用法：login_check.ps1 -Server official -Slot official_1 [-ScreenTimeoutSec 120] [-LoginTimeoutSec 180]
# 退出码：0 已登录（或自动登录成功）；1 失败
# ============================================================
param(
    [string]$Server = "official",
    [string]$Slot = "",
    [int]$ScreenTimeoutSec = 240,
    [int]$LoginTimeoutSec = 180
)
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

$adb = "D:\软件\MuMu模拟器\MuMuPlayer\nx_main\adb.exe"
$device = "127.0.0.1:16384"
$scriptDir = "D:\1\scripts"

# ---- 读取 GUI 配置（D:\1\config.json），字段缺失时回退上面的硬编码默认 ----
$Username = ""
$Password = ""
$configPath = "D:\1\config.json"
if (Test-Path $configPath) {
    try {
        $raw = [System.IO.File]::ReadAllText($configPath, [System.Text.Encoding]::UTF8)
        $cfg = $raw | ConvertFrom-Json
        if ($null -ne $cfg.paths -and $cfg.paths.adb)    { $adb = [string]$cfg.paths.adb }
        if ($null -ne $cfg.paths -and $cfg.paths.device) { $device = [string]$cfg.paths.device }
        if ($null -ne $cfg.paths -and $cfg.paths.script_dir) { $scriptDir = [string]$cfg.paths.script_dir }
        # 凭据从 config.accounts 按 server+slot 匹配（不经命令行传递，避免泄露）
        if ($cfg.accounts -is [System.Array]) {
            foreach ($a in $cfg.accounts) {
                if ([string]$a.server -eq $Server -and [string]$a.slot -eq $Slot) {
                    if ($null -ne $a.username) { $Username = [string]$a.username }
                    if ($null -ne $a.password) { $Password = [string]$a.password }
                    break
                }
            }
        }
    } catch {}
}
$debugDir = Join-Path $scriptDir "debug"
$accountsDir = Join-Path $scriptDir "accounts"
$slotDir = if ($Slot) { Join-Path $accountsDir $Slot } else { "" }

function Timestamp { Get-Date -Format "HH:mm:ss" }
function LogLine($m) { Write-Output ("$(Timestamp) [LoginCheck] " + $m) }

if ($Server -eq "bilibili") {
    $pkg = "com.hypergryph.arknights.bilibili"
    $serverName = "B服"
} else {
    $pkg = "com.hypergryph.arknights"
    $serverName = "官服"
}

# OCR 库（主机端 Windows OCR）
. (Join-Path $scriptDir "ocr_lib.ps1")
if (-not (Test-Path $debugDir)) { New-Item -ItemType Directory $debugDir -Force | Out-Null }
$png = Join-Path $debugDir ("login_check_{0}.png" -f $(if ($Slot) { $Slot } else { $Server }))

# input text 可靠字符集（其余字符会让整串丢失，见实测）
$SAFE_CHARS = '^[A-Za-z0-9@.\!\#\$&\*\(\)\- ]+$'
# 登录界面标记（出现任一 = 未登录）；验证码标记 = 无人值守无法处理，直接失败
$loginMarkers = @("账号登录", "密码登录", "本机号码登录", "验证码登录", "请输入账号", "请输入密码")
$captchaMarkers = @("安全验证", "依次点击", "滑动验证", "拼图")
# 主界面特征词（游戏内 UI，登录/标题/弹窗界面不会出现）：出现即已登录
# 实测 B服 启动后不进标题画面直接进主界面，必须有不依赖标题的「已登录」判定
$inGameMarkers = @("公开招募", "干员寻访", "理智", "终端", "采购中心")

function Invoke-Tap($x, $y) {
    & $adb -s $device shell "input tap $x $y" 2>$null | Out-Null
}

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

function Find-Marker($words, $markers) {
    # 返回第一个命中的 {X,Y,Name}；无命中返回 $null
    foreach ($m in $markers) {
        $hit = Find-OcrText $words $m
        if ($hit) { return [PSCustomObject]@{ X = $hit.X; Y = $hit.Y; Name = $m } }
    }
    return $null
}

LogLine ("=== Login check: {0} (slot: {1}) ===" -f $serverName, $(if ($Slot) { $Slot } else { "(无槽位)" }))

# ---- 阶段 1：轮询屏幕，区分「已登录」与「登录界面」----
# 判定顺序：验证码（失败）→ 登录标记（进入阶段 2）→ 主界面特征词（已登录放行）→
# 首次启动弹窗（配音选择/确认/同意）→ 开始唤醒（点击后继续）→
# 见过标题后连续 4 张稳定非登录画面（已登录放行）→ 盲点兜底。
# 「已登录」两条路径：主界面特征词（不依赖标题，B服 无标题直接进主界面）；
# 标题画面「开始唤醒」登录/未登录都会出现，必须点掉后才能用稳定画面判断
# （首次启动弹窗也是纯文字画面，不能误判）。
$reachedLogin = $false
$loginHit = $null
$sawTitle = $false
$voiceKept = $false
$stableCount = 0
$posCount = 0
$lastActionAt = (Get-Date)
$deadline = (Get-Date).AddSeconds($ScreenTimeoutSec)
while ((Get-Date) -lt $deadline) {
    if (-not (Ocr-Screenshot $adb $device $png)) { Start-Sleep 5; continue }
    $words = Get-OcrWords $png
    $cap = Find-Marker $words $captchaMarkers
    if ($cap) {
        LogLine ("ERROR: 检测到验证码界面（{0}），无人值守无法处理" -f $cap.Name)
        exit 1
    }
    $lm = Find-Marker $words $loginMarkers
    if (-not $lm) {
        # 密码表单可能只剩裸「登录」按钮（无本机/密码登录链接），用整行精确匹配兜底
        $lmExact = Find-OcrText $words "登录" -Exact
        if ($lmExact) { $lm = [PSCustomObject]@{ X = $lmExact.X; Y = $lmExact.Y; Name = "登录" } }
    }
    if ($lm) { $reachedLogin = $true; $loginHit = $lm; break }
    # 主界面特征词（B服 无标题直接进主界面；官服正常路径也能提前放行）
    $ig = Find-Marker $words $inGameMarkers
    if ($ig) {
        $posCount++
        if ($posCount -ge 2) {
            LogLine ("[screen] 检测到主界面（{0}），已登录" -f $ig.Name)
            exit 0
        }
        Start-Sleep 5
        continue
    }
    $posCount = 0
    # 首次启动弹窗（清登录态/缺 lc.cache 时会走这轮，纯文字画面）
    $voiceKeep = Find-OcrText $words "维持原有配置"
    if ($voiceKeep -and (-not $voiceKept)) {
        Invoke-Tap $voiceKeep.X $voiceKeep.Y
        $voiceKept = $true
        LogLine "[dialog] 勾选「维持原有配置」"
        $lastActionAt = Get-Date
        $stableCount = 0
        Start-Sleep 5
        continue
    }
    $confirm = Find-OcrText $words "确认"
    if ($confirm) {
        Invoke-Tap $confirm.X $confirm.Y
        LogLine "[dialog] 点击「确认」"
        $lastActionAt = Get-Date
        $stableCount = 0
        Start-Sleep 5
        continue
    }
    $agree = Find-OcrText $words "同意并继续"
    if ($agree) {
        Invoke-Tap $agree.X $agree.Y
        LogLine "[dialog] 点击「同意并继续」"
        $lastActionAt = Get-Date
        $stableCount = 0
        Start-Sleep 5
        continue
    }
    $wake = Find-OcrText $words "开始唤醒"
    if ($wake) {
        Invoke-Tap $wake.X $wake.Y
        LogLine ("[screen] 标题画面，点击开始唤醒 ({0},{1})" -f $wake.X, $wake.Y)
        $sawTitle = $true
        $lastActionAt = Get-Date
        $stableCount = 0
        Start-Sleep 6
        continue
    }
    if ((@($words).Count -gt 0) -and $sawTitle) {
        $stableCount++
        if ($stableCount -ge 4) {
            LogLine "[screen] 已过标题且画面稳定无登录界面标记，视为已登录"
            exit 0
        }
        Start-Sleep 5
        continue
    }
    $stableCount = 0
    # 无任何可识别动作（无文字/纯过渡画面/未见标题的无动作文字画面）：45 秒一次盲点
    if (((Get-Date) - $lastActionAt).TotalSeconds -gt 45) {
        Invoke-Tap 640 360
        LogLine "[screen] 无可识别动作，盲点屏幕中央兜底"
        $lastActionAt = Get-Date
    }
    Start-Sleep 6
}
if (-not $reachedLogin) {
    LogLine ("ERROR: {0} 秒内无法确认登录状态" -f $ScreenTimeoutSec)
    exit 1
}

# ---- 阶段 2：登录界面 → 自动登录（仅官服；B服登录界面结构未自动化）----
if ($Server -ne "official") {
    LogLine "ERROR: B服 检测到登录界面，暂不支持自动登录，请在控制台「账号管理」重新捕获该账号"
    exit 1
}
if (-not $Username -or -not $Password) {
    LogLine "ERROR: config.accounts 未配置该槽位的账号/密码，请在控制台「账号管理」重新捕获该账号"
    exit 1
}
if (-not (($Username -match $SAFE_CHARS) -and ($Password -match $SAFE_CHARS))) {
    LogLine "ERROR: 账号/密码含 input text 不支持的字符，无法自动登录，请重新捕获（走人工登录）"
    exit 1
}
$ppName = Get-PlayerPrefsName
if (-not $ppName) { LogLine "ERROR: 无法获取 playerprefs 文件名"; exit 1 }

LogLine ("[login] 检测到登录界面（标记: {0}），开始自动登录..." -f $loginHit.Name)
if ($loginHit.Name -eq "账号登录") {
    Invoke-Tap $loginHit.X $loginHit.Y
    LogLine "[login] 点击「账号登录」"
    Start-Sleep 3
}
# 「密码登录」链接（密码表单入口）；已直接在密码表单则跳过
if (($loginHit.Name -ne "登录") -and ($loginHit.Name -ne "请输入账号") -and ($loginHit.Name -ne "请输入密码")) {
    $deadline3 = (Get-Date).AddSeconds(20)
    $hit = $null
    while ((Get-Date) -lt $deadline3) {
        Start-Sleep 3
        if (Ocr-Screenshot $adb $device $png) {
            $hit = Find-OcrText (Get-OcrWords $png) "密码登录"
            if ($hit) { break }
        }
    }
    if ($hit) { Invoke-Tap $hit.X $hit.Y; LogLine "[login] 点击「密码登录」" }
    else {
        LogLine "WARN: 20 秒内未找到「密码登录」，用固定坐标重试"
        Invoke-Tap 810 568
    }
}
# 密码表单（「请输入账号」+「请输入密码」占位符可见）
$deadline4 = (Get-Date).AddSeconds(20)
$words3 = $null
while ((Get-Date) -lt $deadline4) {
    Start-Sleep 3
    if (Ocr-Screenshot $adb $device $png) {
        $w3 = Get-OcrWords $png
        if ((Find-OcrText $w3 "请输入账号") -and (Find-OcrText $w3 "请输入密码")) { $words3 = $w3; break }
    }
}
if ($null -eq $words3) { LogLine "ERROR: 密码表单未出现，自动登录失败"; exit 1 }

# 输入账号（Type-Field 内含收键盘+聚焦，防首字符被吞）
$f1 = Find-OcrText $words3 "请输入账号"
if ($f1) { Type-Field $f1.X $f1.Y $Username } else { Type-Field 545 283 $Username }
# 账号框内容自校验（可见字段）：前 5 字符不匹配则清空重输一次
if (Ocr-Screenshot $adb $device $png) {
    $wv = Get-OcrWords $png
    $fieldText = (($wv | Where-Object { $_.Y -gt 250 -and $_.Y -lt 320 } | ForEach-Object { $_.Text }) -join '')
    $u = $Username
    $headOk = ($u.Length -lt 5) -or ($fieldText -match [regex]::Escape($u.Substring(0, 5)))
    if (-not $headOk) {
        LogLine ("[login] 账号框内容异常（{0}），清空重输一次" -f $fieldText)
        & $adb -s $device shell "input keyevent 123; for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24; do input keyevent 67; done" 2>$null | Out-Null
        Start-Sleep 2
        if ($f1) { Type-Field $f1.X $f1.Y $Username } else { Type-Field 545 283 $Username }
    }
}
# 输入密码（掩码不可校验，同样用可靠输入序列）
$f2 = Find-OcrText $words3 "请输入密码"
if ($f2) { Type-Field $f2.X $f2.Y $Password } else { Type-Field 545 363 $Password }

# ---- 提交并轮询结果 ----
# 首次提交不点协议复选框（恢复的登录态通常已勾选过）；25 秒仍停在表单则补点一次重提。
$submitCount = 0
$lastSubmitAt = (Get-Date).AddSeconds(-60)   # 使首次进入循环立即提交
$loggedIn = $false
$stableCount2 = 0
$deadline5 = (Get-Date).AddSeconds($LoginTimeoutSec)
while ((Get-Date) -lt $deadline5) {
    Start-Sleep 6
    if (-not (Ocr-Screenshot $adb $device $png)) { continue }
    $w = Get-OcrWords $png

    $cap2 = Find-Marker $w $captchaMarkers
    if ($cap2) {
        LogLine ("ERROR: 登录触发验证码（{0}），无人值守无法处理" -f $cap2.Name)
        exit 1
    }
    if (Find-OcrText $w "密码错误") {
        LogLine "ERROR: 提示账号或密码错误，请检查配置后重新捕获"
        exit 1
    }

    $lm2 = Find-Marker $w $loginMarkers
    if (-not $lm2) { $e = Find-OcrText $w "登录" -Exact; if ($e) { $lm2 = [PSCustomObject]@{ X = $e.X; Y = $e.Y; Name = "登录" } } }
    if (-not $lm2) {
        $wake = Find-OcrText $w "开始唤醒"
        if ($wake) {
            Invoke-Tap $wake.X $wake.Y
            LogLine "[login] 已回标题画面，登录成功"
            $loggedIn = $true
            break
        }
        $ig2 = Find-Marker $w $inGameMarkers
        if ($ig2) {
            LogLine ("[login] 检测到主界面（{0}），登录成功" -f $ig2.Name)
            $loggedIn = $true
            break
        }
        if (@($w).Count -gt 0) {
            $stableCount2++
            if ($stableCount2 -ge 3) { LogLine "[login] 登录界面消失且画面稳定，视为登录成功"; $loggedIn = $true; break }
        } else { $stableCount2 = 0 }
    } else {
        $stableCount2 = 0
        if (($submitCount -eq 0) -and (((Get-Date) - $lastSubmitAt).TotalSeconds -ge 25)) {
            $btn = $null
            if (Ocr-Screenshot $adb $device $png) { $btn = Find-OcrText (Get-OcrWords $png) "登录" -Exact }
            if ($btn) { Invoke-Tap $btn.X $btn.Y } else { Invoke-Tap 640 516 }
            LogLine "[login] 提交登录..."
            $submitCount = 1
            $lastSubmitAt = Get-Date
        } elseif (($submitCount -eq 1) -and (((Get-Date) - $lastSubmitAt).TotalSeconds -ge 25)) {
            Invoke-Tap 440 440
            Start-Sleep 1
            $btn = $null
            if (Ocr-Screenshot $adb $device $png) { $btn = Find-OcrText (Get-OcrWords $png) "登录" -Exact }
            if ($btn) { Invoke-Tap $btn.X $btn.Y } else { Invoke-Tap 640 516 }
            LogLine "[login] 仍停在表单，补点协议复选框并重新提交"
            $submitCount = 2
            $lastSubmitAt = Get-Date
        }
    }
}
if (-not $loggedIn) {
    LogLine ("ERROR: {0} 秒内未检测到登录成功" -f $LoginTimeoutSec)
    exit 1
}

# ---- 登录成功：校验 uid 与槽位一致（防跑错号），并刷新槽位数据 ----
$devUid = ""
for ($i = 0; $i -lt 6; $i++) {
    $devUid = Get-DeviceUid $ppName
    if ($devUid) { break }
    Start-Sleep 5
}
if (-not $devUid) {
    LogLine "WARN: 登录成功后未读取到 uid，跳过槽位刷新（MAA 照常运行）"
    exit 0
}
$expectUid = ""
$uidFile = if ($slotDir) { Join-Path $slotDir "uid.txt" } else { "" }
if ($uidFile -and (Test-Path $uidFile)) { $expectUid = (Get-Content $uidFile -Raw -ErrorAction SilentlyContinue).Trim() }
if ($expectUid -and ($devUid -ne $expectUid)) {
    LogLine ("ERROR: 登录成功但 uid 与槽位不一致（device={0}, slot={1}）——凭据与槽位不匹配，拒绝刷新" -f $devUid, $expectUid)
    exit 1
}
if ($slotDir) {
    $dstShared = Join-Path $slotDir "shared_prefs"
    $dstFiles = Join-Path $slotDir "files\zx"
    New-Item -ItemType Directory -Force $dstShared, $dstFiles | Out-Null
    & $adb -s $device pull ("/data/data/{0}/shared_prefs/{1}" -f $pkg, $ppName) (Join-Path $dstShared $ppName) 2>$null | Out-Null
    & $adb -s $device pull "/data/data/$pkg/shared_prefs/HypergryphSdkPreferences.xml" (Join-Path $dstShared "HypergryphSdkPreferences.xml") 2>$null | Out-Null
    # lc.cache 是可选缓存：登录后游戏可能还没写它（首次启动实测 zx 目录下无此文件）
    & $adb -s $device pull "/data/data/$pkg/files/zx/lc.cache" (Join-Path $dstFiles "lc.cache") 2>$null | Out-Null
    if (-not (Test-Path (Join-Path $dstFiles "lc.cache"))) { LogLine "WARN: 设备暂无 lc.cache（登录后尚未生成，忽略）" }
    $devUid | Out-File $uidFile -Encoding ascii -NoNewline
    LogLine ("[login] 槽位数据已刷新（uid={0}）" -f $devUid)
}
LogLine "=== Login check SUCCESS ==="
exit 0
