# ============================================================
# B服登录校验 — 槽位切号后、MAA 启动前：屏幕级确认 B服 已登录
# v1（2026-09-14）：从 login_check.ps1 拆出，B服 与官服登录差异大，
# 不再共用同一条轮询流程（官服流程见 login_check.ps1）：
#   1. B服 无标题画面（开始唤醒按钮），启动后直接进主界面 → 成功判定
#      只有主界面特征词（公开招募/理智/终端等），出现两帧稳定即放行；
#   2. B服 走 B站 SDK，槽位镜像文件里没有可离线校验的会话凭据
#      （HypergryphSdkPreferences.xml 只有协议标记，无 USER_CACHE），
#      无法做官服式 token 预检快速失败，登录态只能屏幕级确认；
#   3. B站登录界面（扫码/短信/验证码）无人值守无法自动化 → 检测到即
#      快速失败，master.ps1 将该号标记失败（防 MAA 对着登录界面空跑）；
#   4. 盲点兜底只在更新进行中禁用：更新标记消失 30 秒后即恢复。
#      起因：2026-09-14 16:52 批次 B服 热更新，共用脚本检测到一次
#      「正在获取更新」后永久禁用盲点，更新画面消失后游戏重启进无文字
#      加载/弹窗画面，脚本空转 240 秒超时判失败；17:50 重跑同一路径
#      靠盲点（中央+右上角 X）通过，证实盲点是被禁掉的关键动作。
#      官服更新完落在有「开始唤醒」文字的标题画面不受此影响，故只在
#      B服 流程修正。
# v2 变更（2026-09-15）：B服 启动先播开屏世界观剧情（左上角「清除缓存/网络
#      检测」+ 底部 START 菱形按钮的多页介绍，盖住主界面，主界面特征词不可
#      见），新增开屏分支：识别到即点 START 跳过（OCR 定位，识别不到用实测
#      固定坐标 640,680；多页剧情逐页点）。开屏消失后 20 秒内未出现登录界面
#      /验证码即视为已登录、直接放行交给 MAA——开屏剧情只在登录态出现，不再
#      等主界面特征词（每号省 20~40 秒盲点推进）；等待窗内禁用盲点防过渡画面
#      误触。实测截图：scripts\debug\manual_bilibili_current.png
# 用法：login_check_bilibili.ps1 -Slot bilibili_1 [-ScreenTimeoutSec 240]
# 退出码：0 已登录进主界面；1 失败（登录态失效/更新失败/超时）
# 成功后与官服一致：校验设备 uid 与槽位一致并刷新槽位数据
#（共用函数在 login_device_lib.ps1）。
# ============================================================
param(
    [string]$Server = "bilibili",
    [string]$Slot = "",
    [int]$ScreenTimeoutSec = 240
)
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

if ($Server -ne "bilibili") {
    Write-Output "login_check_bilibili.ps1 仅用于 B服，官服请用 login_check.ps1"
    exit 1
}

$adb = "D:\软件\MuMu模拟器\MuMuPlayer\nx_main\adb.exe"
$device = "127.0.0.1:16384"
$scriptDir = "D:\1\scripts"

# ---- 读取 GUI 配置（D:\1\config.json），字段缺失时回退上面的硬编码默认 ----
$configPath = "D:\1\config.json"
if (Test-Path $configPath) {
    try {
        $raw = [System.IO.File]::ReadAllText($configPath, [System.Text.Encoding]::UTF8)
        $cfg = $raw | ConvertFrom-Json
        if ($null -ne $cfg.paths -and $cfg.paths.adb)    { $adb = [string]$cfg.paths.adb }
        if ($null -ne $cfg.paths -and $cfg.paths.device) { $device = [string]$cfg.paths.device }
        if ($null -ne $cfg.paths -and $cfg.paths.script_dir) { $scriptDir = [string]$cfg.paths.script_dir }
    } catch {}
}
$debugDir = Join-Path $scriptDir "debug"
$accountsDir = Join-Path $scriptDir "accounts"
$slotDir = if ($Slot) { Join-Path $accountsDir $Slot } else { "" }
$pkg = "com.hypergryph.arknights.bilibili"
$serverName = "B服"

function Timestamp { Get-Date -Format "HH:mm:ss" }
function LogLine($m) { Write-Output ("$(Timestamp) [LoginCheck-B] " + $m) }

# OCR 库（主机端 Windows OCR）+ 共用画面标记库 + 共用设备/槽位函数
. (Join-Path $scriptDir "ocr_lib.ps1")
. (Join-Path $scriptDir "game_state_lib.ps1")
. (Join-Path $scriptDir "login_device_lib.ps1")
if (-not (Test-Path $debugDir)) { New-Item -ItemType Directory $debugDir -Force | Out-Null }
$png = Join-Path $debugDir ("login_check_{0}.png" -f $(if ($Slot) { $Slot } else { "bilibili" }))

# 验证码标记 = 无人值守无法处理，直接失败
$captchaMarkers = @("安全验证", "依次点击", "滑动验证", "拼图")
# B站登录界面标记（出现任一 = 未登录）：基础标记与官服同一套（B站 SDK
# 登录页同样有密码/验证码登录入口），另加 B站 特有的扫码/短信入口；
# 「快速登录」按钮页不加标记——活动公告正文可能出现近似文字，误判代价
# 是整号判失败，宁可落到超时兜底。无凭据自动登录能力，命中即失败。
$loginMarkers = @("账号登录", "密码登录", "本机号码登录", "验证码登录",
                  "请输入账号", "请输入密码", "扫码登录", "短信登录")
# 主界面特征词（游戏内 UI，登录/加载/弹窗界面不会出现）：出现两帧 = 已登录
# 实测 B服 启动后不进标题画面直接进主界面（有时被公告弹窗盖住）
# 「寻访一次/寻访十次」为干员寻访页独有按钮：B服 启动公告弹窗盖住主界面时，
# 盲点中央会点进寻访页（2026-08-28 实测卡死 240s 超时），此页无主界面特征词
$inGameMarkers = @("公开招募", "干员寻访", "理智", "终端", "采购中心", "寻访一次", "寻访十次")
# 启动公告弹窗页签在 game_state_lib.ps1；右上角 X 是纯图标、OCR 无文本，
# 坐标实测固定（2026-08-28 实测：点 (1215,75) 弹窗即关，主界面特征词立即出现）
$announceCloseX = 1215
$announceCloseY = 75
# 开屏世界观剧情页标记（左上角工具按钮 + START 按钮文字，仅此界面出现）：
# 出现任一 = 还在开屏剧情中，需点 START 跳过
$startupMarkers = @("清除缓存", "网络检测", "START")
# START 菱形按钮实测固定坐标（OCR 识别不到 START 文字时的兜底）
$startupTapX = 640
$startupTapY = 680
# 更新失败/卡住文字：更新出现过之后检测到即快速失败
$failMarkers = @(
    "更新下载失败", "下载更新失败", "下载失败", "更新失败",
    "更新资源损坏", "获取资源更新配置失败", "网络连接已断开",
    "安装更新失败", "安装失败", "储存空间不足", "存储空间不足"
)

LogLine ("=== Login check: {0} (slot: {1}) ===" -f $serverName, $(if ($Slot) { $Slot } else { "(无槽位)" }))

# ---- 轮询屏幕：更新等待 → 公告弹窗 → 主界面 → 首次启动弹窗 → 盲点兜底 ----
# 判定顺序：验证码（失败）→ B站登录界面（失败）→ 游戏更新/安装器（只等待，
# 失败文字快速失败）→ 启动公告弹窗（点右上角 X）→ 主界面特征词两帧（已登录
# 放行）→ 首次启动弹窗（配音选择/确认/同意）→ 盲点兜底。
# 与官服流程的关键差异：官服更新完落在标题画面（「开始唤醒」文字可识别、
# 被动放行），盲点永久禁用无害；B服 无标题画面，更新重启后的无文字加载/
# 图标弹窗画面只能靠盲点推进，因此盲点只在更新进行中禁用，更新标记消失
# 30 秒后恢复（防误点下载收尾画面）。每轮只做最多一个动作。
$reachedLogin = $false
$voiceKept = $false
$dialogHandled = $false
$posCount = 0
$lastActionAt = (Get-Date)
$lastAnnounceTapAt = (Get-Date).AddSeconds(-60)
$lastStartupTapAt = (Get-Date).AddSeconds(-60)
$sawStartup = $false
$startupGoneAt = $null
$blindPokeCount = 0
$lastPngHash = ""
$lastUpdateMarker = ""
$updateSeen = $false
$lastUpdateAt = [datetime]::MinValue
$sawText = $false
$stageStart = (Get-Date)
$deadline = (Get-Date).AddSeconds($ScreenTimeoutSec)
# 游戏更新等待的最长封顶：超过后即使仍在更新也不断延长（防止无限卡死）
$hardDeadline = (Get-Date).AddHours(2)
while ((Get-Date) -lt $deadline) {
    if (-not (Ocr-Screenshot $adb $device $png)) { Start-Sleep 3; continue }
    # 画面与上一轮完全相同（静态加载/弹窗/表单）→ 复用上一轮 OCR 结果，
    # 跳过最耗时的重复识别；画面一变立即重新识别。
    $pngHash = (Get-FileHash $png -Algorithm MD5 -ErrorAction SilentlyContinue).Hash
    if ($pngHash -and ($pngHash -eq $lastPngHash) -and ($null -ne $lastWords)) {
        $words = $lastWords
    } else {
        $words = Get-OcrWords $png
        $lastPngHash = $pngHash
        $lastWords = $words
    }
    if (@($words).Count -gt 0) { $sawText = $true }
    $cap = Find-AnyMarker $words $captchaMarkers
    if ($cap) {
        LogLine ("ERROR: 检测到验证码界面（{0}），无人值守无法处理" -f $cap.Name)
        exit 1
    }
    $lm = Find-AnyMarker $words $loginMarkers
    if (-not $lm) {
        # B站登录页可能只剩裸「登录」按钮，用整行精确匹配兜底
        $lmExact = Find-OcrText $words "登录" -Exact
        if ($lmExact) { $lm = [PSCustomObject]@{ X = $lmExact.X; Y = $lmExact.Y; Name = "登录" } }
    }
    if ($lm) { $reachedLogin = $true; break }
    # 游戏更新中：只等待不点击（下载/安装期间盲点可能打断更新）；
    # 公告页的“更新公告”正文用公告分支处理，不在此误判。
    # 系统包安装器（强制更新重装客户端）OCR 识别不到，用前台包名补判；
    # 只在更新已出现过或画面无文字时查（每轮 dumpsys 有 adb 开销）
    $annNow = Find-AnyMarker $words $AnnounceMarkers
    $upNow = $null
    if (-not $annNow) { $upNow = Find-AnyMarker $words $GameUpdateMarkers }
    $installing = $false
    if (-not $annNow -and -not $upNow -and ($updateSeen -or (@($words).Count -eq 0))) {
        $installing = Is-InstallerForeground
    }
    if ($upNow -or $installing) {
        $updateSeen = $true
        $lastUpdateAt = Get-Date
        $posCount = 0
        $lastActionAt = Get-Date
        # 只在命中的标记变化时记日志：更新下载常持续几十分钟，每轮都记会刷屏
        if ($installing) {
            if ($lastUpdateMarker -ne "(安装器)") {
                LogLine "[update] 检测到系统包安装器，正在安装/重新安装客户端，等待完成（不点击）"
                $lastUpdateMarker = "(安装器)"
            }
        } elseif ($upNow.Name -ne $lastUpdateMarker) {
            LogLine ("[update] 检测到游戏更新界面（{0}），等待更新完成（不点击）" -f $upNow.Name)
            $lastUpdateMarker = $upNow.Name
        }
        if ((Get-Date) -lt $hardDeadline) {
            # 更新下载/安装可能超过默认登录超时：每次检测到更新顺延一轮
            $deadline = (Get-Date).AddSeconds($ScreenTimeoutSec)
        }
        # 更新以分钟计，更密的轮询没有收益，10 秒足够及时
        Start-Sleep 10
        continue
    }
    # 更新出现过之后：失败提示立即报错（探测阶段不判——游戏刚启动的
    # 「网络连接已断开」可能是瞬时抖动，可恢复）
    if ($updateSeen -and -not $annNow) {
        $fail = Find-AnyMarker $words $failMarkers
        if ($fail) {
            LogLine ("ERROR: 游戏更新失败（{0}），请检查网络/存储后重试" -f $fail.Name)
            exit 1
        }
    }
    # 启动公告弹窗：优先处理（盖住主界面时特征词不可见）。
    # 点右上角 X 关闭；限频 6 秒防连点；不刷新 $lastActionAt，
    # 若 X 点不掉仍保留盲点兜底
    if ($annNow) {
        if (((Get-Date) - $lastAnnounceTapAt).TotalSeconds -ge 6) {
            Invoke-Tap $announceCloseX $announceCloseY
            $lastAnnounceTapAt = Get-Date
            LogLine ("[dialog] 公告弹窗（{0}），点击右上角关闭" -f $annNow.Name)
        }
        Start-Sleep 2
        continue
    }
    # 开屏世界观剧情（B服 启动先播多页介绍，盖住主界面）：点 START 逐页跳过；
    # 限频 3 秒防连点。2026-09-15 实测：清除缓存/网络检测仅此界面出现
    $startup = Find-AnyMarker $words $startupMarkers
    if ($startup) {
        $sawStartup = $true
        $startupGoneAt = $null
        if (((Get-Date) - $lastStartupTapAt).TotalSeconds -ge 3) {
            $st = Find-OcrText $words "START"
            if ($st) { Invoke-Tap $st.X $st.Y } else { Invoke-Tap $startupTapX $startupTapY }
            $lastStartupTapAt = Get-Date
            LogLine ("[dialog] 开屏剧情（{0}），点击 START 跳过" -f $startup.Name)
        }
        Start-Sleep 2
        continue
    }
    # 开屏跳过后的放行判定：开屏剧情只在登录态出现，消失后 20 秒内没冒出
    # 登录界面/验证码（每轮顶部已查）即视为已登录、直接放行交给 MAA，
    # 不再等主界面特征词；等待窗内禁用盲点，防止对过渡画面误触。
    $settleWatching = $false
    if ($sawStartup) {
        if ($null -eq $startupGoneAt) {
            $startupGoneAt = Get-Date
            $settleWatching = $true
        } elseif (((Get-Date) - $startupGoneAt).TotalSeconds -ge 20) {
            LogLine "[screen] 开屏已跳过且未出现登录界面，视为已登录，直接放行交给 MAA"
            Update-SlotData $dialogHandled | Out-Null
            exit 0
        } else {
            $settleWatching = $true
        }
    }
    # 主界面特征词：出现两帧稳定 = 已登录（开屏放行之外的另一条成功路径）
    $ig = Find-AnyMarker $words $inGameMarkers
    if ($ig) {
        $posCount++
        if ($posCount -ge 2) {
            LogLine ("[screen] 检测到主界面（{0}），已登录" -f $ig.Name)
            Update-SlotData $dialogHandled | Out-Null
            exit 0
        }
        Start-Sleep 1
        continue
    }
    $posCount = 0
    # 首次启动弹窗（清登录态/缺 lc.cache 时会走这轮，纯文字画面）
    $voiceKeep = Find-OcrText $words "维持原有配置"
    if ($voiceKeep -and (-not $voiceKept)) {
        Invoke-Tap $voiceKeep.X $voiceKeep.Y
        $voiceKept = $true
        $dialogHandled = $true
        LogLine "[dialog] 勾选「维持原有配置」"
        $lastActionAt = Get-Date
        Start-Sleep 2
        continue
    }
    $confirm = Find-OcrText $words "确认"
    if ($confirm) {
        Invoke-Tap $confirm.X $confirm.Y
        $dialogHandled = $true
        LogLine "[dialog] 点击「确认」"
        $lastActionAt = Get-Date
        Start-Sleep 2
        continue
    }
    $agree = Find-OcrText $words "同意并继续"
    if ($agree) {
        Invoke-Tap $agree.X $agree.Y
        $dialogHandled = $true
        LogLine "[dialog] 点击「同意并继续」"
        $lastActionAt = Get-Date
        Start-Sleep 2
        continue
    }
    # 无可识别动作时的盲点兜底。节奏分档：有文字画面（未知页面/弹窗改版）
    # 15 秒一次，无文字画面（加载/过渡）30 秒一次。首次点中央（推进加载间
    # 的剧情/确认），之后中央、右上角 X 交替：公告弹窗页签文字若改版识别
    # 不到，X 盲点仍能关掉常见弹窗（X 位置实测固定）。
    # 更新进行中禁盲点（见上）；更新标记消失后 30 秒缓冲即恢复——B服 更新
    # 完会重启进无文字加载画面，再禁下去就是 2026-09-14 的 240 秒空转超时。
    # 游戏冷启动加载阶段（还没见过任何文字）也禁盲点：此时点了也是空点。
    $updateCool = $updateSeen -and (((Get-Date) - $lastUpdateAt).TotalSeconds -lt 30)
    # 开屏放行等待窗内禁盲点：过渡画面上乱点可能误触按钮，马上就要放行给 MAA
    $pokeAllowed = (-not $updateCool) -and (-not $settleWatching) -and `
        ($sawText -or (((Get-Date) - $stageStart).TotalSeconds -gt 60))
    $pokeAfterSec = if ((@($words).Count -gt 0)) { 15 } else { 30 }
    if (-not $updateCool -and $pokeAllowed -and ((Get-Date) - $lastActionAt).TotalSeconds -gt $pokeAfterSec) {
        if ($blindPokeCount -gt 0 -and ($blindPokeCount % 2 -eq 0)) {
            Invoke-Tap $announceCloseX $announceCloseY
            LogLine "[screen] 无可识别动作，盲点右上角公告关闭位兜底"
        } else {
            Invoke-Tap 640 360
            LogLine "[screen] 无可识别动作，盲点屏幕中央兜底"
        }
        $blindPokeCount++
        $lastActionAt = Get-Date
    }
    # 轮询节奏自适应：无动作且画面有文字 → 2 秒（弹窗/对白可能变化，保持较快响应）；
    # 画面完全没有文字（加载/过渡）→ 3 秒（游戏本身需要时间，频繁识别没有收益）
    if ((@($words).Count -gt 0)) { Start-Sleep 2 } else { Start-Sleep 3 }
}
if ($reachedLogin) {
    LogLine ("ERROR: B服 检测到登录界面（{0}），B站账号无法自动登录，请在控制台「账号管理」重新捕获该账号" -f $lm.Name)
    exit 1
}
$elapsed = [int]((Get-Date) - $stageStart).TotalSeconds
LogLine ("ERROR: {0} 秒内无法确认 B服 登录状态（最后截图：debug\{1}）" -f $elapsed, (Split-Path $png -Leaf))
exit 1
