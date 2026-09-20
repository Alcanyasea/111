# ============================================================
# 账号捕获 v2 — 清空登录态 → 进入登录界面 → 输入账号密码 → 拉取登录数据到槽位
# v2（MAA 同款识别升级，与 login_check v9 同批）：识别换 vision_lib.ps1
#（OpenCV 模板匹配 + PaddleOCR ONNX）；弹窗关闭改 CloseAnno 模板定位、删除
# 右上角固定坐标盲点兜底；删除与 login_device_lib.ps1 重复的本地函数定义
#（Invoke-Tap/Get-DeviceUid/Get-PlayerPrefsName，此前本地定义遮蔽了库版本）。
# 流程（官服）：
#   停游戏 → 备份并移走 3 个登录数据文件 → 写入最小 SDK prefs → 启动游戏
#   → 识别循环点掉首次启动弹窗（配音包下载/选择）→ 标题画面 → 登录界面
#   → 账号登录 → 密码登录 → 输入账号/密码 → 登录
#   → 轮询 u8sdk_cached_uid 出现即登录成功 → 处理剩余首次启动弹窗（公告/配音
#     选择等）并等标记写入 playerprefs → 拉取文件到 accounts\<slot>\
#   （uid 出现只代表登录成功：配音选择/公告的「已处理」标记要进入主界面后才写入，
#     不处理就拉取的话，下次切号会再次弹出首次启动弹窗）
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

# 账号密码可经环境变量传入（GUI 侧这样传，避免密码留在命令行上被本机其他进程读到）：
# 命令行参数优先，为空时回退读环境变量；读完全进程内清掉
if (-not $Username) { $Username = [Environment]::GetEnvironmentVariable("MAA_CAPTURE_USERNAME") }
if (-not $Password) { $Password = [Environment]::GetEnvironmentVariable("MAA_CAPTURE_PASSWORD") }
[Environment]::SetEnvironmentVariable("MAA_CAPTURE_USERNAME", $null)
[Environment]::SetEnvironmentVariable("MAA_CAPTURE_PASSWORD", $null)

$adb = "D:\软件\MuMu模拟器\MuMuPlayer\nx_main\adb.exe"
$device = "127.0.0.1:16384"
$cli = "D:\软件\MuMu模拟器\MuMuPlayer\nx_main\mumu-cli.exe"
$scriptDir = "D:\1\scripts"
$debugDir = "D:\1\scripts\debug"
# 引导已看过标记补写等共用函数（Ensure-GuideViewed 在此库，登录检查两脚本共用同一份）
. (Join-Path $scriptDir "login_device_lib.ps1")

# ---- 读取 GUI 配置（D:\1\config.json），字段缺失时回退上面的硬编码默认 ----
$configPath = "D:\1\config.json"
if (Test-Path $configPath) {
    try {
        $raw = [System.IO.File]::ReadAllText($configPath, [System.Text.Encoding]::UTF8)
        $cfg = $raw | ConvertFrom-Json
        if ($null -ne $cfg.paths -and $cfg.paths.adb)    { $adb = [string]$cfg.paths.adb }
        if ($null -ne $cfg.paths -and $cfg.paths.device) { $device = [string]$cfg.paths.device }
        if ($null -ne $cfg.paths -and $cfg.paths.cli)    { $cli = [string]$cfg.paths.cli }
        if ($null -ne $cfg.paths -and $cfg.paths.script_dir) { $scriptDir = [string]$cfg.paths.script_dir }
        if ($null -ne $cfg.paths -and $cfg.paths.script_dir) { $debugDir = Join-Path ([string]$cfg.paths.script_dir) "debug" }
    } catch {}
}
$accountsDir = Join-Path $scriptDir "accounts"

function Start-MuMu {
    # 与 master.ps1 一致：模拟器未连接时自动启动并等待 ADB 就绪。
    # 已连接时不做任何操作（避免影响正在运行的挂机任务）。
    LogLine "检查模拟器连接..."
    $r = & $adb connect $device 2>&1
    if ($r -match "connected|already") {
        LogLine "MuMu 已连接"
        return $true
    }
    LogLine "模拟器未连接，正在启动 MuMu..."
    & $adb kill-server 2>$null | Out-Null
    Start-Sleep 1
    & $cli control -v 0 launch 2>$null | Out-Null
    Start-Sleep 5
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt 120) {
        $r = & $adb connect $device 2>&1
        if ($r -match "connected|already") {
            LogLine "MuMu 已连接，等待游戏服务就绪"
            Start-Sleep 15
            return $true
        }
        Start-Sleep 3
    }
    LogLine "ERROR: MuMu 启动超时（120 秒内未连接 ADB）"
    return $false
}

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

# MAA 同款识别库（vision.py 常驻进程 + gzip 截图）
. (Join-Path $scriptDir "vision_lib.ps1")
$vp = Start-Vision
if (-not $vp) { Write-Output "ERROR: 识别进程（vision.py）启动失败，请检查 gui\.venv 与 scripts\vision"; exit 1 }

if (-not (Test-Path $debugDir)) { New-Item -ItemType Directory $debugDir -Force | Out-Null }

# input text 可靠字符集（其余字符会让整串丢失，见实测）
$SAFE_CHARS = '^[A-Za-z0-9@.\!\#\$&\*\(\)\- ]+$'

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

# Invoke-Tap / Get-DeviceUid / Get-PlayerPrefsName 共用实现在 login_device_lib.ps1
#（此前本地重复定义遮蔽了库版本，v2 删除；Type-Field 仅捕获流程使用，保留在此）

# Invoke-Tap / Get-DeviceUid / Get-PlayerPrefsName 共用实现在 login_device_lib.ps1
#（此前本地重复定义遮蔽了库版本，v2 删除）；Type-Field 仅捕获流程使用，保留在此

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

# 模拟器未运行/ADB 未连接是「新增账号」失败的最常见原因：
# playerprefs 属于游戏私有目录，设备不可达时直接报「找不到文件」。
if (-not (Start-MuMu)) {
    LogLine "ERROR: 无法连接模拟器（ADB 连接失败且启动超时）"
    LogLine "ERROR: 请确认 MuMu 模拟器已安装、实例 0 可用；也可手动打开 MuMu 后重试"
    exit 1
}

& $adb -s $device root 2>$null | Out-Null
Start-Sleep 2

# 游戏是否安装（未安装时游戏私有目录不存在，捕获无从谈起）
$installed = (& $adb -s $device shell "pm list packages" 2>$null) -join "`n"
if ($installed -notmatch [regex]::Escape($pkg)) {
    LogLine ("ERROR: 设备上未安装 {0}（{1}），无法捕获" -f $pkg, $serverName)
    LogLine "ERROR: 请先安装对应的明日方舟客户端（官服/B服）"
    exit 1
}

$ppName = Get-PlayerPrefsName
if (-not $ppName) {
    LogLine "ERROR: 未找到游戏的登录数据文件（playerprefs）"
    LogLine "ERROR: 请先手动启动一次游戏并进入主界面，再重试捕获"
    exit 1
}
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

# ---- 校验清空生效（v4.1 加固，防旧账号数据误存新槽位）----
# mv 经 adb shell 执行且错误被吞掉：一旦静默失败，旧 playerprefs（含旧
# u8sdk_cached_uid）留在设备上，后面「轮询登录成功」会在第一轮就把它当成
# 登录成功、把旧账号数据拉进新槽位且不报任何错。这里轮询确认三个文件确实
# 已移走；有残留 → 还原备份后明确失败（原登录态完好，可直接重试）。
# 注意：清空验证通过后，后续任何 uid 都只能来自本次新登录，因此无需
# （也不应）比对「捕获前后 uid 是否相同」——同账号重新捕获时 uid 本就相同。
$cleared = $false
for ($i = 0; $i -lt 3; $i++) {
    Start-Sleep 2
    $ls = (& $adb -s $device shell "ls /data/data/$pkg/shared_prefs/ /data/data/$pkg/files/zx/ 2>/dev/null") -join "`n"
    if ($ls -notmatch [regex]::Escape($ppName) -and $ls -notmatch 'HypergryphSdkPreferences\.xml' -and $ls -notmatch 'lc\.cache') {
        $cleared = $true
        break
    }
    LogLine "清空登录态尚未确认生效，重试验证..."
}
if (-not $cleared) {
    LogLine "ERROR: 登录数据清空失败（旧登录文件仍留在设备上），捕获中止"
    LogLine "ERROR: 为防止把旧账号数据存进新槽位，本次捕获不继续；原登录态已还原，可直接重试"
    Restore-Backup $ppName
    exit 1
}

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

# ---- 识别弹窗循环（官服：配音包弹窗 → 解压 → 配音选择 → 标题画面 → 登录界面）----
$reachedLogin = $false
$png = Join-Path $debugDir ("cap_{0}_screen.png" -f $Slot)
if ($Server -eq "official") {
    $deadline = (Get-Date).AddMinutes($DialogTimeoutMin)
    $lastBlindTap = (Get-Date).AddSeconds(-60)
    $voiceKept = $false
    $act = $null
    while ((Get-Date) -lt $deadline) {
        if (-not (Invoke-VisionScreenshot $adb $device $png)) { Start-Sleep 5; continue }
        $frame = Invoke-VisionFrame $vp $png @{
            templates = @(
                @{ name = "StartToWakeUp" }
                @{ name = "StartButton" }
                @{ name = "CloseAnno" }
            )
            ocr = @(
                @{ name = "acctLogin"; text = @("账号登录") }
                @{ name = "voice";     text = @("维持原有配置") }
                @{ name = "confirm";   text = @("确认"); exact = $true }
                @{ name = "agree";     text = @("同意并继续") }
            )
        }
        if (-not $frame -or $frame.error) { Start-Sleep 5; continue }
        $act = $null
        if ($frame.ocr.acctLogin.matched) { break }
        if ($frame.ocr.voice.matched -and (-not $voiceKept)) {
            # 配音选择弹窗：先勾选「维持原有配置」，下一轮再点确认（避免只点选项不确认）
            Invoke-Tap $frame.ocr.voice.center[0] $frame.ocr.voice.center[1]
            $voiceKept = $true
            $act = "voice_keep"
        }
        elseif ($c = Get-VisionFrameCenter $frame.templates.CloseAnno) {
            Invoke-Tap $c.X $c.Y; $act = "announce_close"
        }
        elseif ($c = Get-VisionFrameCenter $frame.templates.StartButton) {
            # 开屏剧情 START 页（v9 实测官服新增）：点菱形 START 进入下一页/标题画面
            Invoke-Tap $c.X $c.Y; $act = "start_tap"
        }
        elseif ($frame.ocr.confirm.matched) { Invoke-Tap $frame.ocr.confirm.center[0] $frame.ocr.confirm.center[1]; $act = "confirm" }
        elseif ($frame.ocr.agree.matched) { Invoke-Tap $frame.ocr.agree.center[0] $frame.ocr.agree.center[1]; $act = "agree" }
        elseif ($frame.templates.StartToWakeUp.matched) {
            # 标题画面：捕获流程要点开始唤醒进入登录界面（MAA StartToWakeUp 模板同款 ClickSelf）
            $c = Get-VisionFrameCenter $frame.templates.StartToWakeUp
            Invoke-Tap $c.X $c.Y; $act = "wake"
        }
        elseif (((Get-Date) - $lastBlindTap).TotalSeconds -gt 45) {
            Invoke-Tap 640 360; $lastBlindTap = Get-Date; $act = "blind_tap"
        }
        if ($act) { LogLine ("[dialog] " + $act) }
        Start-Sleep 5
    }
    if ($act -eq "acctLogin" -or $frame.ocr.acctLogin.matched) {
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
        # 1) 登录界面 → 点「账号登录」（$frame 为弹窗循环最后一张截图，含该按钮）
        if ($frame.ocr.acctLogin.matched) {
            Invoke-Tap $frame.ocr.acctLogin.center[0] $frame.ocr.acctLogin.center[1]
        }
        # 2) 轮询等待「密码登录」链接出现（手机号表单渲染可能需要几秒）
        $deadline3 = (Get-Date).AddSeconds(20)
        $hit = $null
        while ((Get-Date) -lt $deadline3) {
            Start-Sleep 3
            if (Invoke-VisionScreenshot $adb $device $png) {
                $f = Invoke-VisionFrame $vp $png @{ ocr = @(@{ name = "pw"; text = @("密码登录") }) }
                if ($f -and $f.ocr.pw.matched) { $hit = Get-OcrHit $f.ocr.pw; break }
            }
        }
        if ($hit) { Invoke-Tap $hit.X $hit.Y } else {
            LogLine "WARN: 20 秒内未找到「密码登录」，用固定坐标重试"
            Invoke-Tap 810 568
        }
        # 3) 轮询等待密码表单出现（「请输入账号」占位符可见）
        $deadline4 = (Get-Date).AddSeconds(20)
        $fields = $null
        while ((Get-Date) -lt $deadline4) {
            Start-Sleep 3
            if (Invoke-VisionScreenshot $adb $device $png) {
                $f = Invoke-VisionFrame $vp $png @{ ocr = @(
                    @{ name = "acct"; text = @("请输入账号") }
                    @{ name = "pwd";  text = @("请输入密码") }
                ) }
                if ($f -and $f.ocr.acct.matched -and $f.ocr.pwd.matched) { $fields = $f.ocr; break }
            }
        }
        if ($null -eq $fields) {
            LogLine "WARN: 密码表单未出现，放弃自动输入，转人工登录"
        } else {
            # 4) 输入账号（Type-Field 内含收键盘+聚焦+3秒等待，防首字符被吞）
            Type-Field $fields.acct.center[0] $fields.acct.center[1] $Username
            # 账号框内容自校验（可见字段）：必须匹配前 5 个字符，否则清空重输一次
            if (Invoke-VisionScreenshot $adb $device $png) {
                $fv = Invoke-VisionFrame $vp $png @{ ocr = @(@{ name = "band"; all = $true; roi = @(0, 245, 1280, 80) }) }
                $fieldText = if ($fv) { (@($fv.ocr.band.lines) | ForEach-Object { $_.text }) -join '' } else { "" }
                $u = $Username
                $headOk = ($u.Length -lt 5) -or ($fieldText -match [regex]::Escape($u.Substring(0, 5)))
                if (-not $headOk) {
                    LogLine "账号框内容异常（$fieldText），清空重输一次"
                    & $adb -s $device shell "input keyevent 123; for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24; do input keyevent 67; done" 2>$null | Out-Null
                    Start-Sleep 2
                    Type-Field $fields.acct.center[0] $fields.acct.center[1] $Username
                }
            }
            # 5) 输入密码（掩码不可校验，同样用可靠输入序列）
            Type-Field $fields.pwd.center[0] $fields.pwd.center[1] $Password
            # 6) 勾选用户协议（清空登录态后必为未勾选，实测空心圆），再点登录
            Invoke-Tap 440 440
            Start-Sleep 1
            $btn = $null
            if (Invoke-VisionScreenshot $adb $device $png) {
                $f = Invoke-VisionFrame $vp $png @{ ocr = @(@{ name = "btn"; text = @("登录"); exact = $true }) }
                if ($f) { $btn = Get-OcrHit $f.ocr.btn }
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
        if (Invoke-VisionScreenshot $adb $device $png) {
            $f = Invoke-VisionFrame $vp $png @{
                ocr = @(
                    @{ name = "agree";  text = @("用户注册协议") }
                    @{ name = "band";   all = $true; roi = @(0, 245, 1280, 80) }
                    @{ name = "btn";    text = @("登录"); exact = $true }
                )
            }
            if ($f -and $f.ocr.agree.matched) {
                $fieldText = if ($f) { (@($f.ocr.band.lines) | ForEach-Object { $_.text }) -join '' } else { "" }
                $u = $Username
                $stillForm = ($u.Length -lt 5) -or ($fieldText -match [regex]::Escape($u.Substring(0, 5)))
                if ($stillForm) {
                    LogLine "仍在登录表单，重新点一次登录..."
                    $btn = Get-OcrHit $f.ocr.btn
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

# ---- 登录成功：处理剩余首次启动弹窗并等主界面 ----
# 与 login_check.ps1 的判定一致：公告弹窗 CloseAnno 模板点右上角 X，配音选择勾
# 「维持原有配置」，再点确认/同意并继续/开始唤醒，直到主界面特征词出现
#（理智/公开招募等）。这样拉回来的 playerprefs 才带 KEY_GLOBAL_VOICE_LANG /
# KEY_VOICE_LANG_PREF_DONTCG 与公告版本号等「已处理」标记，下次切号不会重复弹窗。
$inGameMarkers = @("公开招募", "干员寻访", "理智", "终端", "采购中心", "寻访一次", "寻访十次")
$voiceKeptSettle = $false
$settlePng = Join-Path $debugDir ("cap_{0}_settle.png" -f $Slot)
$settleDeadline = (Get-Date).AddMinutes(3)
$lastPokeAt = (Get-Date)
$pokeCount = 0
$settled = $false
while ((Get-Date) -lt $settleDeadline) {
    if (-not (Invoke-VisionScreenshot $adb $device $settlePng)) { Start-Sleep 4; continue }
    $frame = Invoke-VisionFrame $vp $settlePng @{
        templates = @(
            @{ name = "StartToWakeUp" }
            @{ name = "CloseAnno" }
            @{ name = "MainUiToggleSettings" }
        )
        ocr = @(
            @{ name = "ingame";  text = $inGameMarkers }
            @{ name = "voice";   text = @("维持原有配置") }
            @{ name = "confirm"; text = @("确认"); exact = $true }
            @{ name = "agree";   text = @("同意并继续") }
        )
    }
    if (-not $frame -or $frame.error) { Start-Sleep 4; continue }
    # 主界面（特征词或主界面模板任一命中；登录后仍在弹窗/加载时不会出现）
    $igName = $null
    if ($frame.ocr.ingame.matched) { $igName = $frame.ocr.ingame.hit }
    elseif ($frame.templates.MainUiToggleSettings.matched) { $igName = "主界面模板" }
    if ($igName) {
        LogLine ("[settle] 检测到主界面（{0}），首次启动弹窗处理完成" -f $igName)
        $settled = $true
        break
    }
    $act = $null
    # 公告弹窗：CloseAnno 模板命中点右上角 X（纯图标，不再依赖固定坐标）
    if ($c = Get-VisionFrameCenter $frame.templates.CloseAnno) {
        Invoke-Tap $c.X $c.Y
        $act = "announce_close"
        Start-Sleep 3
    } elseif ($frame.ocr.voice.matched -and (-not $voiceKeptSettle)) {
        Invoke-Tap $frame.ocr.voice.center[0] $frame.ocr.voice.center[1]
        $voiceKeptSettle = $true
        $act = "voice_keep"
        Start-Sleep 3
    } elseif ($frame.ocr.confirm.matched) {
        Invoke-Tap $frame.ocr.confirm.center[0] $frame.ocr.confirm.center[1]
        $act = "confirm"
        Start-Sleep 3
    } elseif ($frame.ocr.agree.matched) {
        Invoke-Tap $frame.ocr.agree.center[0] $frame.ocr.agree.center[1]
        $act = "agree"
        Start-Sleep 3
    } elseif ($frame.templates.StartToWakeUp.matched) {
        $c = Get-VisionFrameCenter $frame.templates.StartToWakeUp
        Invoke-Tap $c.X $c.Y
        $act = "wake"
        Start-Sleep 4
    }
    if ($act) {
        LogLine ("[settle] " + $act)
    } else {
        # 无可识别动作：盲点屏幕中央兜底
        if (((Get-Date) - $lastPokeAt).TotalSeconds -gt 20) {
            Invoke-Tap 640 360
            LogLine "[settle] 无可识别动作，盲点屏幕中央兜底"
            $pokeCount++
            $lastPokeAt = Get-Date
        }
        Start-Sleep 4
        continue
    }
    Start-Sleep 2
}
if (-not $settled) {
    LogLine "WARN: 3 分钟内未识别到主界面（弹窗可能未完全处理），仍按当前状态拉取"
}

# 弹窗刚点掉时语音标记写入有延迟：最多等 ~25 秒，标记出现即拉取
$deadlineV = (Get-Date).AddSeconds(25)
while ((Get-Date) -lt $deadlineV) {
    $tmpPp2 = Join-Path $env:TEMP "ark_cap_pp2.xml"
    & $adb -s $device pull ("/data/data/{0}/shared_prefs/{1}" -f $pkg, $ppName) $tmpPp2 2>$null | Out-Null
    if (Test-Path $tmpPp2) {
        $ppc2 = [System.IO.File]::ReadAllText($tmpPp2, [System.Text.Encoding]::UTF8)
        Remove-Item $tmpPp2 -Force -ErrorAction SilentlyContinue
        if ($ppc2 -match 'KEY_VOICE_LANG_PREF_DONTCG') {
            LogLine "首次启动语音标记已写入 playerprefs"
            break
        }
    }
    Start-Sleep 5
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
# 引导已看过标记：把清单里缺失的全部补写进槽位 playerprefs（共用清单见
# login_device_lib.ps1 的 $guideViewedKeys），各界面首次提示不再弹出
$nGuide = Ensure-GuideViewed (Join-Path $dstShared $ppName)
if ($nGuide -lt 0) {
    LogLine "WARN: 未能补写引导已看过标记（槽位数据可能不完整）"
} elseif ($nGuide -gt 0) {
    LogLine ("已补写引导已看过标记 {0} 条（各界面首次进入提示不再弹出）" -f $nGuide)
} else {
    LogLine "引导已看过标记齐全，无需补写"
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
