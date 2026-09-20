# ============================================================
# 登录校验 v2 — 槽位切号后、MAA 启动前：屏幕级确认游戏已登录
# 已登录（无登录界面标记）→ 直接放行；检测到登录界面 →
#   官服：自动输入账号密码登录（凭据取自 config.accounts，不经命令行传递），
#         成功后校验 uid 与槽位一致并刷新槽位数据（token 续期）
#   无凭据/验证码：失败退出，master.ps1 将该号标记失败（防 MAA 对着登录界面空跑）
# 用法：login_check.ps1 -Server official -Slot official_1 [-ScreenTimeoutSec 120] [-LoginTimeoutSec 180]
# 本脚本自 v8 起只跑官服流程；B服 与官服差异大（无标题画面/token 预检不可用/
# 登录界面不可自动登录），拆为独立的 login_check_bilibili.ps1 单独校验。
# 退出码：0 已登录/已到标题画面（或自动登录成功）；1 失败
# 槽位自刷新：确认已登录后把设备上的最新登录数据拉回槽位（游戏处理完首次启动弹窗
# 会写入 KEY_GLOBAL_VOICE_LANG / KEY_VOICE_LANG_PREF_DONTCG 等标记），否则下次切号
# 推送旧槽位数据时，配音选择/首次启动弹窗会每次都重新弹出，卡住 MAA。
# v2 优化：OCR 引擎全程复用（引擎创建是单次识别最慢的部分）；画面未变化时跳过重复
# 识别；轮询节奏自适应（动作后 2 秒、有文字 3 秒、纯加载 6 秒）；已过标题的稳定确认
# 由 4 帧减为 3 帧——成功路径每号可省 10~25 秒，失败路径判定不变。
# v3 优化：更新/公告标记与匹配函数收敛到 game_state_lib.ps1（一次行分组匹配全部
# 标记）；公告页判定同一轮内复用；更新等待只在标记变化时记日志、轮询 6→10 秒；
# 自动登录提交改用本轮 OCR 结果，删掉重复的二次截图识别。
# v4 变更：检测到标题画面（开始唤醒按钮 + 已登录账号）不再点击，直接放行交给 MAA——
# 点开始唤醒、进主界面、关后续弹窗全部由 MAA 的「开始唤醒」任务完成；见过标题后的
# 稳定帧判定随之移除。阶段 2 自动登录成功回到标题画面时同样不点击、直接判成功。
# v5 变更：启动 MAA 前先做 token 预检——官服槽位缓存的通行证 token 直接向官方
# 接口校验（不点开始唤醒即可判明登录态），失效则写 token_status.json（控制台
# 仪表盘标红提醒）并快速失败；探测失败不阻塞，按屏幕检测继续。
# v6 变更：game_update_wait.ps1 并入本脚本（每号省去其 45 秒探测窗 + 一次进程
# 启动）——更新标记等待原本就在阶段 1 里，补齐其独有能力：更新失败文字快速
# 失败、系统包安装器前台检测（强制更新重装）、检测到更新后禁用盲点（防打断
# 下载/安装）。轮询收紧：有文字 3→2 秒、无文字 6→4 秒、动作后 3→2 秒、
# 主界面确认间隔 2→1 秒、OCR 失败重试 5→3 秒、盲点 20/45→15/30 秒、
# 公告点击限频 8→6 秒。实测顺利路径每号 ~37 秒 → ~25 秒，另省更新检查 ~52 秒。
# v7 变更：配合 slot_switch 轮询式 uid 校验（更快交棒），游戏仍在加载的阶段
# 禁用盲点兜底（见到任意文字或 60 秒后才允许，避免对加载画面空点）；token
# 预检对瞬时网络抖动自动重试一次（401 失效不重试）；纯加载画面轮询 4→3 秒。
# v8 变更：B服 拆出独立校验（login_check_bilibili.ps1），本脚本只跑官服；
# 设备/槽位共用函数（安装器检测/点击/playerprefs/uid/槽位刷新）收敛到
# login_device_lib.ps1，与 B服 脚本共用一份。
# v9 变更（MAA 同款识别升级）：屏幕识别整体换为 vision_lib.ps1（vision.py：
# OpenCV 模板匹配 + PaddleOCR det/rec ONNX，模板/模型/阈值与 MAA 同源）——
#   1. 标题画面判定改为「开始唤醒」模板匹配为主、OCR 文字兜底（MAA 同款双保险）；
#   2. 公告弹窗改为 CloseAnno 模板定位关闭（替代文字标记 + 固定坐标 1215,75），
#      删除「盲点右上角公告关闭位」交替兜底（v4 后已绝迹，见日志统计）；
#   3. 主界面判定加主界面设置齿轮模板，特征词降为同帧并列判据；
#   4. 截图改 MAA 同款 gzip 快速通道（~100-170ms/帧）；OCR 由 Windows OCR
#      换 PaddleOCR（MAA 同款模型），识别耗时 ~0.1-0.5 秒/帧（常驻进程）。
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
. (Join-Path $PSScriptRoot "config_lib.ps1")
$config = Read-AppConfigJson "D:\1\config.json"
if ($config) {
    if ($null -ne $config.paths -and $config.paths.adb)    { $adb = [string]$config.paths.adb }
    if ($null -ne $config.paths -and $config.paths.device) { $device = [string]$config.paths.device }
    if ($null -ne $config.paths -and $config.paths.script_dir) { $scriptDir = [string]$config.paths.script_dir }
    # 凭据从 config.accounts 按 server+slot 匹配（不经命令行传递，避免泄露）
    if ($config.accounts -is [System.Array]) {
        foreach ($a in $config.accounts) {
            if ([string]$a.server -eq $Server -and [string]$a.slot -eq $Slot) {
                if ($null -ne $a.username) { $Username = [string]$a.username }
                if ($null -ne $a.password) { $Password = [string]$a.password }
                break
            }
        }
    }
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

# MAA 同款识别库（vision.py 常驻进程 + gzip 截图）+ 共用设备/槽位函数
# （与 login_check_bilibili.ps1 同一套）
. (Join-Path $scriptDir "vision_lib.ps1")
. (Join-Path $scriptDir "login_device_lib.ps1")
# 首次心跳：先于 Start-Vision 等重初始化写入，识别进程启动阶段挂死也能被看门狗覆盖
Write-LoginHeartbeat
if (-not (Test-Path $debugDir)) { New-Item -ItemType Directory $debugDir -Force | Out-Null }
$png = Join-Path $debugDir ("login_check_{0}.png" -f $(if ($Slot) { $Slot } else { $Server }))
$vp = Start-Vision
if (-not $vp) { LogLine "ERROR: 识别进程（vision.py）启动失败，请检查 gui\.venv 与 scripts\vision"; exit 1 }

# input text 可靠字符集（$SAFE_CHARS）与登录表单录入函数（Type-Field）
# 共用实现在 login_device_lib.ps1（v4.1 从两份逐字拷贝收敛）
# 登录界面标记（出现任一 = 未登录）；验证码标记 = 无人值守无法处理，直接失败
$loginMarkers = @("账号登录", "密码登录", "本机号码登录", "验证码登录", "请输入账号", "请输入密码")
$captchaMarkers = @("安全验证", "依次点击", "滑动验证", "拼图")
# 主界面特征词（游戏内 UI，登录/标题/弹窗界面不会出现）：出现即已登录
# 实测 B服 启动后不进标题画面直接进主界面，必须有不依赖标题的「已登录」判定
# 「寻访一次/寻访十次」为干员寻访页独有按钮：B服 启动公告弹窗盖住主界面时，
# 盲点中央会点进寻访页（2026-08-28 实测卡死 240s 超时），此页无主界面特征词
$inGameMarkers = @("公开招募", "干员寻访", "理智", "终端", "采购中心", "寻访一次", "寻访十次")
# 游戏更新进行中的界面文字标记（v9 起原 game_state_lib.ps1 收敛到此）
$GameUpdateMarkers = @(
    "正在获取更新", "获取更新配置", "获取资源更新配置", "更新配置",
    "开始下载更新", "开始下载", "正在下载更新", "正在下载", "下载更新包",
    "正在校验资源", "正在校验", "校验资源", "资源校验",
    "正在解压", "解压资源", "资源解压",
    "正在安装更新", "正在安装", "安装更新", "安装中",
    "正在更新", "正在更新资源", "更新中", "资源更新", "更新资源",
    "版本更新", "强制更新", "更新内容",
    "更新完成", "重新启动游戏", "正在重新启动",
    "更新下载失败", "下载更新失败", "更新资源损坏", "安装更新失败"
)
# 更新失败/卡住文字：更新出现过之后检测到即快速失败（原 game_update_wait 的判定）
$failMarkers = @(
    "更新下载失败", "下载更新失败", "下载失败", "更新失败",
    "更新资源损坏", "获取资源更新配置失败", "网络连接已断开",
    "安装更新失败", "安装失败", "储存空间不足", "存储空间不足"
)

LogLine ("=== Login check: {0} (slot: {1}) ===" -f $serverName, $(if ($Slot) { $Slot } else { "(无槽位)" }))

# ---- token 预检（仅官服）：不点开始唤醒，直接拿槽位缓存的通行证 token 向
# 官方接口校验（与游戏点「开始唤醒」时同一判定）：
#   HTTP 200 / status=0 → 有效，按屏幕检测继续；
#   HTTP 401 / status=3「登录已过期」→ 失效：写 token_status.json（控制台
#   仪表盘据此标红提醒）并快速失败——此时点开「开始唤醒」必然落在登录界面，
#   MAA 无法登录，与其空跑超时不如立刻标失败等重新捕获。
#   探测失败（网络/接口改版）不阻塞：保留原状态（error 不覆盖 expired，
#   网络抖动不能洗掉标红），按屏幕检测继续（游戏直接落在登录界面时阶段 2 仍兜底）。
# B 服走 B 站 SDK，槽位 SDK 配置无 USER_CACHE，自然跳过。
if ($Server -eq "official" -and $slotDir) {
    $sdkPrefs = Join-Path $slotDir "shared_prefs\HypergryphSdkPreferences.xml"
    $tokenStatusPath = Join-Path $slotDir "token_status.json"
    $tok = $null
    if (Test-Path $sdkPrefs) {
        try {
            $sdkRaw = [System.IO.File]::ReadAllText($sdkPrefs, [System.Text.Encoding]::UTF8)
            $mUserCache = [regex]::Match($sdkRaw, 'name="USER_CACHE"[^>]*>([^<]*)<')
            if ($mUserCache.Success) {
                $ucEntries = [System.Net.WebUtility]::HtmlDecode($mUserCache.Groups[1].Value) | ConvertFrom-Json
                # 取 lastLoginTime 最新的一条：每次成功登录/跑完回刷槽位后即该槽位账号
                foreach ($e in @($ucEntries)) {
                    if ($e.token -and ($null -eq $tok -or [string]$e.lastLoginTime -gt [string]$tok.lastLoginTime)) { $tok = $e }
                }
            }
        } catch {}
    }
    if ($tok) {
        $tStat = "error"; $tDetail = ""
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            $uri = "https://as.hypergryph.com/user/info/v1/basic?token=" + [uri]::EscapeDataString([string]$tok.token)
            $resp = $null
            foreach ($probe in 1..2) {
                try {
                    $resp = Invoke-RestMethod -Uri $uri -TimeoutSec 10 -UseBasicParsing
                    break
                } catch {
                    $code = 0
                    try { $code = [int]$_.Exception.Response.StatusCode } catch {}
                    # 401 = 明确失效，不重试；瞬时网络抖动只补一次
                    if ($code -eq 401 -or $probe -eq 2) { throw }
                    Start-Sleep 2
                }
            }
            if ("$($resp.status)" -eq "0") { $tStat = "ok"; $tDetail = "token 有效" }
            else { $tStat = "expired"; $tDetail = [string]$resp.msg }
        } catch {
            $code = 0
            try { $code = [int]$_.Exception.Response.StatusCode } catch {}
            if ($code -eq 401) { $tStat = "expired"; $tDetail = "登录已过期，请重新登录" }
            else { $tStat = "error"; $tDetail = "探测失败（HTTP $code）" }
        }
        $keepExpired = $false
        if ($tStat -eq "error" -and (Test-Path $tokenStatusPath)) {
            try { $keepExpired = ((Get-Content $tokenStatusPath -Raw | ConvertFrom-Json).status -eq "expired") } catch {}
        }
        if (-not $keepExpired) {
            $rec = @{ status = $tStat; checked_at = (Get-Date -Format "yyyy-MM-dd HH:mm:ss"); detail = $tDetail } | ConvertTo-Json
            [System.IO.File]::WriteAllText($tokenStatusPath, $rec, (New-Object System.Text.UTF8Encoding($false)))
        }
        if ($tStat -eq "expired") {
            LogLine ("ERROR: 官服 token 已失效（{0}），不启动 MAA —— 请在控制台「账号管理」重新捕获该账号" -f $tDetail)
            exit 1
        }
        if ($tStat -eq "ok") { LogLine ("[token] 槽位 token 有效（上次登录 {0}），继续登录检查" -f $tok.lastLoginTime) }
        else { LogLine ("WARN: token 预检失败（{0}），按屏幕检测继续" -f $tDetail) }
    }
}

# ---- 阶段 1：轮询屏幕，区分「已登录」与「登录界面」----
# 每轮一次 Invoke-VisionFrame 拿到模板 + OCR 全部判定，再按序处理（每轮最多
# 一个动作，动作后下一轮必重新截图检测，不会连续盲点）：
# 验证码（失败）→ 登录标记（进入阶段 2）→ 公告弹窗 X（CloseAnno 模板定位关闭，
# 替代旧「文字标记 + 固定坐标」）→ 游戏更新/安装器（只等待，失败文字快速失败）→
# 主界面（特征词或主界面模板，已登录放行）→ 首次启动弹窗（配音选择/确认/同意）→
# 开始唤醒（模板/文字，不点击直接放行）→ 盲点兜底（屏幕中央；检测到过更新后禁用）。
# 「已登录」两条路径：主界面（不依赖标题）；标题画面（开始唤醒按钮 + 已登录账号）
# 出现即放行——槽位推送的登录数据有效时标题即登录态，点击开始唤醒、进主界面、
# 关后续弹窗都由 MAA 的「开始唤醒」任务完成（v4；token 失效时游戏点开才会出
# 登录界面，此路检测不到）。
$reachedLogin = $false
$loginHit = $null
$voiceKept = $false
$dialogHandled = $false
$posCount = 0
$lastActionAt = (Get-Date)
$lastStartupTapAt = (Get-Date).AddSeconds(-60)
$blindPokeCount = 0
$lastPngHash = ""
$lastFrame = $null
$lastUpdateMarker = ""
$updateSeen = $false
$lastUpdateAt = [datetime]::MinValue
$sawText = $false
$stageStart = (Get-Date)
$deadline = (Get-Date).AddSeconds($ScreenTimeoutSec)
while ((Get-Date) -lt $deadline) {
    Write-LoginHeartbeat
    if (-not (Invoke-VisionScreenshot $adb $device $png)) { Start-Sleep 3; continue }
    # 画面与上一轮完全相同（静态加载/弹窗/表单）→ 复用上一轮识别结果；
    # 画面一变立即重新识别。
    $pngHash = (Get-FileHash $png -Algorithm MD5 -ErrorAction SilentlyContinue).Hash
    if ($pngHash -and ($pngHash -eq $lastPngHash) -and ($null -ne $lastFrame)) {
        $frame = $lastFrame
    } else {
        $frame = Invoke-VisionFrame $vp $png @{
            templates = @(
                @{ name = "StartToWakeUp" }
                @{ name = "StartButton" }
                @{ name = "CloseAnno" }
                @{ name = "MainUiToggleSettings" }
            )
            ocr = @(
                @{ name = "captcha";  text = $captchaMarkers }
                @{ name = "login";    text = $loginMarkers }
                @{ name = "loginBtn"; text = @("登录"); exact = $true }
                @{ name = "startup";  text = @("清除缓存", "网络检测", "START") }
                @{ name = "startBtn"; text = @("START") }
                @{ name = "update";   text = $GameUpdateMarkers }
                @{ name = "fail";     text = $failMarkers }
                @{ name = "ingame";   text = $inGameMarkers }
                @{ name = "voice";    text = @("维持原有配置") }
                @{ name = "confirm";  text = @("确认"); exact = $true }
                @{ name = "agree";    text = @("同意并继续") }
                @{ name = "wake";     text = @("开始唤醒") }
            )
        }
        if (-not $frame -or $frame.error) { Start-Sleep 3; continue }
        $lastPngHash = $pngHash
        $lastFrame = $frame
    }
    $wordCount = @($frame.ocr.captcha.lines).Count
    if ($wordCount -gt 0) { $sawText = $true }
    $cap = $frame.ocr.captcha
    if ($cap.matched) {
        LogLine ("ERROR: 检测到验证码界面（{0}），无人值守无法处理" -f $cap.hit)
        exit 1
    }
    $lm = $null
    if ($frame.ocr.login.matched) {
        $lm = [PSCustomObject]@{ X = $frame.ocr.login.center[0]; Y = $frame.ocr.login.center[1]; Name = $frame.ocr.login.hit }
    } elseif ($frame.ocr.loginBtn.matched) {
        # 密码表单可能只剩裸「登录」按钮（无本机/密码登录链接），用整行精确匹配兜底
        $lm = [PSCustomObject]@{ X = $frame.ocr.loginBtn.center[0]; Y = $frame.ocr.loginBtn.center[1]; Name = "登录" }
    }
    if ($lm) { $reachedLogin = $true; $loginHit = $lm; break }
    # 公告弹窗：CloseAnno 模板命中即点其中心（右上角 X 纯图标，不再依赖固定坐标）；
    # 不刷新 $lastActionAt，X 点不掉仍保留盲点兜底
    $annoX = Get-VisionFrameCenter $frame.templates.CloseAnno
    if ($annoX) {
        Invoke-Tap $annoX.X $annoX.Y
        LogLine "[dialog] 公告弹窗（CloseAnno 模板命中），点击右上角关闭"
        Start-Sleep 2
        continue
    }
    # 开屏剧情 START 页（v9 实测官服新增：~4 页轮换的世界观介绍，左上角清除缓存/
    # 网络检测，底部中央菱形 START 按钮，点击后才进标题画面）。页面会轮换、文字
    # 不可依赖，按 START 按钮模板识别；模板未命中时用左上角按钮文字兜底判定，
    # 点击坐标依次：模板中心 → OCR「START」→ 实测固定 (640,675)。限频 3 秒防连点
    if ($frame.templates.StartButton.matched -or $frame.ocr.startup.matched) {
        if (((Get-Date) - $lastStartupTapAt).TotalSeconds -ge 3) {
            if ($frame.templates.StartButton.matched) {
                $c = Get-VisionFrameCenter $frame.templates.StartButton
                Invoke-Tap $c.X $c.Y
                $how = "START按钮模板"
            } elseif ($frame.ocr.startBtn.matched) {
                Invoke-Tap $frame.ocr.startBtn.center[0] $frame.ocr.startBtn.center[1]
                $how = "START文字"
            } else {
                Invoke-Tap 640 675
                $how = $frame.ocr.startup.hit
            }
            $lastStartupTapAt = Get-Date
            LogLine ("[dialog] 开屏剧情（{0}），点击 START 跳过" -f $how)
        }
        Start-Sleep 2
        continue
    }
    # 游戏更新中：只等待不点击（下载/安装期间盲点可能打断更新）。
    # 系统包安装器（强制更新重装客户端）OCR 识别不到，用前台包名补判；
    # 只在更新已出现过或画面无文字时查（每轮 dumpsys 有 adb 开销）
    $upNow = $frame.ocr.update
    $installing = $false
    if (-not $upNow.matched -and ($updateSeen -or ($wordCount -eq 0))) {
        $installing = Is-InstallerForeground
    }
    if ($upNow.matched -or $installing) {
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
        } elseif ($upNow.hit -ne $lastUpdateMarker) {
            LogLine ("[update] 检测到游戏更新界面（{0}），等待更新完成（不点击）" -f $upNow.hit)
            $lastUpdateMarker = $upNow.hit
        }
        # 更新下载/安装可能超过默认登录超时：每次检测到更新顺延一轮。
        # 不设封顶——更新状态由心跳看门狗兜底挂死（30 秒无心跳即杀），
        # 正常等待多久都合法（用户设定：正常运行不限时）
        $deadline = (Get-Date).AddSeconds($ScreenTimeoutSec)
        # 更新以分钟计，更密的轮询没有收益，10 秒足够及时
        Start-Sleep 10
        continue
    }
    # 更新出现过之后：失败提示立即报错（原 game_update_wait 的快速失败判定；
    # 探测阶段不判——游戏刚启动的「网络连接已断开」可能是瞬时抖动，可恢复）
    if ($updateSeen -and $frame.ocr.fail.matched) {
        LogLine ("ERROR: 游戏更新失败（{0}），请检查网络/存储后重试" -f $frame.ocr.fail.hit)
        exit 1
    }
    # 主界面（特征词或主界面模板任一命中；B服 无标题直接进主界面，官服正常路径
    # 也能提前放行）
    $igName = $null
    if ($frame.ocr.ingame.matched) { $igName = $frame.ocr.ingame.hit }
    elseif ($frame.templates.MainUiToggleSettings.matched) { $igName = "主界面模板" }
    if ($igName) {
        $posCount++
        if ($posCount -ge 2) {
            LogLine ("[screen] 检测到主界面（{0}），已登录" -f $igName)
            Update-SlotData $dialogHandled | Out-Null
            exit 0
        }
        Start-Sleep 1
        continue
    }
    $posCount = 0
    # 首次启动弹窗（清登录态/缺 lc.cache 时会走这轮，纯文字画面）
    if ($frame.ocr.voice.matched -and (-not $voiceKept)) {
        Invoke-Tap $frame.ocr.voice.center[0] $frame.ocr.voice.center[1]
        $voiceKept = $true
        $dialogHandled = $true
        LogLine "[dialog] 勾选「维持原有配置」"
        $lastActionAt = Get-Date
        Start-Sleep 2
        continue
    }
    if ($frame.ocr.confirm.matched) {
        Invoke-Tap $frame.ocr.confirm.center[0] $frame.ocr.confirm.center[1]
        $dialogHandled = $true
        LogLine "[dialog] 点击「确认」"
        $lastActionAt = Get-Date
        Start-Sleep 2
        continue
    }
    if ($frame.ocr.agree.matched) {
        Invoke-Tap $frame.ocr.agree.center[0] $frame.ocr.agree.center[1]
        $dialogHandled = $true
        LogLine "[dialog] 点击「同意并继续」"
        $lastActionAt = Get-Date
        Start-Sleep 2
        continue
    }
    # 标题画面（开始唤醒按钮 + 已登录账号）：不点击，直接放行交给 MAA。
    # 模板为主、OCR 文字兜底（MAA StartToWakeUp + StartToWakeUpOCR 同款双层）；
    # 点开始唤醒、进主界面、关后续弹窗都由 MAA 的「开始唤醒」任务完成。
    if ($frame.templates.StartToWakeUp.matched -or $frame.ocr.wake.matched) {
        $src = "文字"; if ($frame.templates.StartToWakeUp.matched) { $src = "模板" }
        LogLine ("[screen] 标题画面（开始唤醒可见-{0}），视为已登录，直接放行交给 MAA" -f $src)
        Update-SlotData $dialogHandled | Out-Null
        exit 0
    }
    # 无可识别动作时的盲点兜底（屏幕中央）。节奏分档：有文字画面（剧情对白/
    # 未知页面）15 秒一次，无文字画面（加载/过渡）30 秒一次。
    # 检测到游戏更新时禁用盲点：下载/安装期间乱点可能打断更新。但只禁 30 秒
    # 缓冲窗（更新标记消失即计时）——「正在获取更新」是冷启动必经的过渡文字，
    # 被误判一次就永久禁盲点的话，开屏页没人推进，会空转到 240 秒超时
    # （2026-09-17 官服实测；B服 同款问题 2026-09-14 已修，此处对齐其 30 秒冷却）。
    # 游戏冷启动加载阶段（还没见过任何文字）也禁盲点：此时点了也是空点，还可能在
    # 加载完的瞬间落在标题画面上多戳一下（slot_switch 校验提前交棒后加载窗更长）
    $updateCool = $updateSeen -and (((Get-Date) - $lastUpdateAt).TotalSeconds -lt 30)
    $pokeAllowed = $sawText -or (((Get-Date) - $stageStart).TotalSeconds -gt 60)
    $pokeAfterSec = if (($wordCount -gt 0)) { 15 } else { 30 }
    if (-not $updateCool -and $pokeAllowed -and ((Get-Date) - $lastActionAt).TotalSeconds -gt $pokeAfterSec) {
        Invoke-Tap 640 360
        LogLine "[screen] 无可识别动作，盲点屏幕中央兜底"
        $blindPokeCount++
        $lastActionAt = Get-Date
    }
    # 轮询节奏自适应：无动作且画面有文字 → 2 秒（弹窗/对白可能变化，保持较快响应）；
    # 画面完全没有文字（加载/过渡）→ 3 秒（游戏本身需要时间，频繁识别没有收益）
    if (($wordCount -gt 0)) { Start-Sleep 2 } else { Start-Sleep 3 }
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
        Write-LoginHeartbeat
        Start-Sleep 3
        if (Invoke-VisionScreenshot $adb $device $png) {
            $f = Invoke-VisionFrame $vp $png @{ ocr = @(@{ name = "pw"; text = @("密码登录") }) }
            if ($f -and $f.ocr.pw.matched) { $hit = Get-OcrHit $f.ocr.pw; break }
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
$fields = $null
while ((Get-Date) -lt $deadline4) {
    Write-LoginHeartbeat
    Start-Sleep 3
    if (Invoke-VisionScreenshot $adb $device $png) {
        $f = Invoke-VisionFrame $vp $png @{ ocr = @(
            @{ name = "acct"; text = @("请输入账号") }
            @{ name = "pwd";  text = @("请输入密码") }
        ) }
        if ($f -and $f.ocr.acct.matched -and $f.ocr.pwd.matched) { $fields = $f.ocr; break }
    }
}
if ($null -eq $fields) { LogLine "ERROR: 密码表单未出现，自动登录失败"; exit 1 }

# 输入账号（Type-Field 内含收键盘+聚焦，防首字符被吞）
Type-Field $fields.acct.center[0] $fields.acct.center[1] $Username
# 账号框内容自校验（可见字段）：前 5 字符不匹配则清空重输一次
if (Invoke-VisionScreenshot $adb $device $png) {
    $fv = Invoke-VisionFrame $vp $png @{ ocr = @(@{ name = "band"; all = $true; roi = @(0, 245, 1280, 80) }) }
    $fieldText = if ($fv) { (@($fv.ocr.band.lines) | ForEach-Object { $_.text }) -join '' } else { "" }
    $u = $Username
    $headOk = ($u.Length -lt 5) -or ($fieldText -match [regex]::Escape($u.Substring(0, 5)))
    if (-not $headOk) {
        LogLine ("[login] 账号框内容异常（{0}），清空重输一次" -f $fieldText)
        & $adb -s $device shell "input keyevent 123; for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24; do input keyevent 67; done" 2>$null | Out-Null
        Start-Sleep 2
        Type-Field $fields.acct.center[0] $fields.acct.center[1] $Username
    }
}
# 输入密码（掩码不可校验，同样用可靠输入序列）
Type-Field $fields.pwd.center[0] $fields.pwd.center[1] $Password

# ---- 提交并轮询结果 ----
# 首次提交不点协议复选框（恢复的登录态通常已勾选过）；25 秒仍停在表单则补点一次重提。
$submitCount = 0
$lastSubmitAt = (Get-Date).AddSeconds(-60)   # 使首次进入循环立即提交
$loggedIn = $false
$stableCount2 = 0
$deadline5 = (Get-Date).AddSeconds($LoginTimeoutSec)
while ((Get-Date) -lt $deadline5) {
    Write-LoginHeartbeat
    Start-Sleep 5
    if (-not (Invoke-VisionScreenshot $adb $device $png)) { continue }
    $f = Invoke-VisionFrame $vp $png @{
        templates = @(
            @{ name = "StartToWakeUp" }
            @{ name = "MainUiToggleSettings" }
        )
        ocr = @(
            @{ name = "captcha";  text = $captchaMarkers }
            @{ name = "login";    text = $loginMarkers }
            @{ name = "loginBtn"; text = @("登录"); exact = $true }
            @{ name = "pwerr";    text = @("密码错误") }
            @{ name = "ingame";   text = $inGameMarkers }
            @{ name = "wake";     text = @("开始唤醒") }
        )
    }
    if (-not $f -or $f.error) { continue }

    if ($f.ocr.captcha.matched) {
        LogLine ("ERROR: 登录触发验证码（{0}），无人值守无法处理" -f $f.ocr.captcha.hit)
        exit 1
    }
    if ($f.ocr.pwerr.matched) {
        LogLine "ERROR: 提示账号或密码错误，请检查配置后重新捕获"
        exit 1
    }

    $lm2 = ($f.ocr.login.matched -or $f.ocr.loginBtn.matched)
    if (-not $lm2) {
        if ($f.templates.StartToWakeUp.matched -or $f.ocr.wake.matched) {
            LogLine "[login] 已回标题画面（开始唤醒可见），登录成功"
            $loggedIn = $true
            break
        }
        if ($f.ocr.ingame.matched) {
            LogLine ("[login] 检测到主界面（{0}），登录成功" -f $f.ocr.ingame.hit)
            $loggedIn = $true
            break
        }
        if ((@($f.ocr.captcha.lines).Count) -gt 0) {
            $stableCount2++
            if ($stableCount2 -ge 3) { LogLine "[login] 登录界面消失且画面稳定，视为登录成功"; $loggedIn = $true; break }
        } else { $stableCount2 = 0 }
    } else {
        $stableCount2 = 0
        # 提交点击复用本轮识别结果（原先在这里再截一次屏识别「登录」按钮，
        # 既多一次最耗时的截图+识别，又与画面状态产生竞态）
        if (($submitCount -eq 0) -and (((Get-Date) - $lastSubmitAt).TotalSeconds -ge 25)) {
            $btn = Get-OcrHit $f.ocr.loginBtn
            if ($btn) { Invoke-Tap $btn.X $btn.Y } else { Invoke-Tap 640 516 }
            LogLine "[login] 提交登录..."
            $submitCount = 1
            $lastSubmitAt = Get-Date
        } elseif (($submitCount -eq 1) -and (((Get-Date) - $lastSubmitAt).TotalSeconds -ge 25)) {
            Invoke-Tap 440 440
            Start-Sleep 1
            $btn = Get-OcrHit $f.ocr.loginBtn
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
Update-SlotData $true | Out-Null
LogLine "=== Login check SUCCESS ==="
exit 0
