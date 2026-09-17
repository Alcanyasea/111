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

# OCR 库（主机端 Windows OCR）+ 共用画面标记库（与 game_update_wait.ps1 同一套）
# + 共用设备/槽位函数（与 login_check_bilibili.ps1 同一套）
. (Join-Path $scriptDir "ocr_lib.ps1")
. (Join-Path $scriptDir "game_state_lib.ps1")
. (Join-Path $scriptDir "login_device_lib.ps1")
if (-not (Test-Path $debugDir)) { New-Item -ItemType Directory $debugDir -Force | Out-Null }
$png = Join-Path $debugDir ("login_check_{0}.png" -f $(if ($Slot) { $Slot } else { $Server }))

# input text 可靠字符集（其余字符会让整串丢失，见实测）
$SAFE_CHARS = '^[A-Za-z0-9@.\!\#\$&\*\(\)\- ]+$'
# 登录界面标记（出现任一 = 未登录）；验证码标记 = 无人值守无法处理，直接失败
$loginMarkers = @("账号登录", "密码登录", "本机号码登录", "验证码登录", "请输入账号", "请输入密码")
$captchaMarkers = @("安全验证", "依次点击", "滑动验证", "拼图")
# 主界面特征词（游戏内 UI，登录/标题/弹窗界面不会出现）：出现即已登录
# 实测 B服 启动后不进标题画面直接进主界面，必须有不依赖标题的「已登录」判定
# 「寻访一次/寻访十次」为干员寻访页独有按钮：B服 启动公告弹窗盖住主界面时，
# 盲点中央会点进寻访页（2026-08-28 实测卡死 240s 超时），此页无主界面特征词
$inGameMarkers = @("公开招募", "干员寻访", "理智", "终端", "采购中心", "寻访一次", "寻访十次")
# 启动公告弹窗页签（弹窗盖住主界面）：右上角 X 是纯图标、OCR 无文本，坐标实测固定
# （2026-08-28 实测：点 (1215,75) 弹窗即关，主界面特征词立即出现）
# 页签标记与游戏更新标记在 game_state_lib.ps1（与 game_update_wait.ps1 共用）
$announceCloseX = 1215
$announceCloseY = 75
# 更新失败/卡住文字：更新出现过之后检测到即快速失败（原 game_update_wait 的判定）
$failMarkers = @(
    "更新下载失败", "下载更新失败", "下载失败", "更新失败",
    "更新资源损坏", "获取资源更新配置失败", "网络连接已断开",
    "安装更新失败", "安装失败", "储存空间不足", "存储空间不足"
)
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
# 判定顺序：验证码（失败）→ 登录标记（进入阶段 2）→ 游戏更新/安装器（只等待，
# 失败文字快速失败）→ 启动公告弹窗（优先点右上角 X 关闭）→ 主界面特征词（已登录
# 放行）→ 首次启动弹窗（配音选择/确认/同意）→ 开始唤醒（不点击，直接放行）→
# 盲点兜底（中央、右上角公告关闭位交替；检测到过更新后禁用）。
# 每轮只做最多一个动作，动作后下一轮必重新截图检测，不会连续盲点。
# 「已登录」两条路径：主界面特征词（不依赖标题，B服 无标题直接进主界面）；
# 标题画面（开始唤醒按钮 + 已登录账号）出现即放行——槽位推送的登录数据有效时
# 标题即登录态，点击开始唤醒、进主界面、关后续弹窗都由 MAA 的「开始唤醒」任务
# 完成，脚本不再代点（v4；token 失效时游戏点开才会出登录界面，此路检测不到）。
$reachedLogin = $false
$loginHit = $null
$voiceKept = $false
$dialogHandled = $false
$posCount = 0
$lastActionAt = (Get-Date)
$lastAnnounceTapAt = (Get-Date).AddSeconds(-60)
$blindPokeCount = 0
$lastPngHash = ""
$lastWords = $null
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
        # 密码表单可能只剩裸「登录」按钮（无本机/密码登录链接），用整行精确匹配兜底
        $lmExact = Find-OcrText $words "登录" -Exact
        if ($lmExact) { $lm = [PSCustomObject]@{ X = $lmExact.X; Y = $lmExact.Y; Name = "登录" } }
    }
    if ($lm) { $reachedLogin = $true; $loginHit = $lm; break }
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
    # 更新出现过之后：失败提示立即报错（原 game_update_wait 的快速失败判定；
    # 探测阶段不判——游戏刚启动的「网络连接已断开」可能是瞬时抖动，可恢复）
    if ($updateSeen -and -not $annNow) {
        $fail = Find-AnyMarker $words $failMarkers
        if ($fail) {
            LogLine ("ERROR: 游戏更新失败（{0}），请检查网络/存储后重试" -f $fail.Name)
            exit 1
        }
    }
    # 启动公告弹窗：优先处理（盖住主界面时特征词不可见，且要求优先关弹窗再看主界面）。
    # 点右上角 X 关闭；限频 6 秒防连点；不刷新 $lastActionAt，若 X 点不掉仍保留盲点兜底
    # $annNow 与上面更新检测共用同一轮判定结果，不再重复扫描
    $ann = $annNow
    if ($ann) {
        if (((Get-Date) - $lastAnnounceTapAt).TotalSeconds -ge 6) {
            Invoke-Tap $announceCloseX $announceCloseY
            $lastAnnounceTapAt = Get-Date
            LogLine ("[dialog] 公告弹窗（{0}），点击右上角关闭" -f $ann.Name)
        }
        Start-Sleep 2
        continue
    }
    # 主界面特征词（B服 无标题直接进主界面；官服正常路径也能提前放行）
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
    # 标题画面（开始唤醒按钮 + 已登录账号）：不点击，直接放行交给 MAA。
    # 点开始唤醒、进主界面、关后续弹窗都由 MAA 的「开始唤醒」任务完成；
    # 原先点完还要等 3 张稳定帧，每号白等 10~25 秒。
    $wake = Find-OcrText $words "开始唤醒"
    if ($wake) {
        LogLine "[screen] 标题画面（开始唤醒可见），视为已登录，直接放行交给 MAA"
        Update-SlotData $dialogHandled | Out-Null
        exit 0
    }
    # 无可识别动作时的盲点兜底。节奏分档：有文字画面（剧情对白/未知页面/公告弹窗
    # 改版）15 秒一次，无文字画面（加载/过渡）30 秒一次。首次点中央（推进标题画面/
    # 剧情对白，历史行为不变），之后中央、右上角 X 交替：公告弹窗页签文字若改版
    # 识别不到，X 盲点仍能关掉常见弹窗（X 位置实测固定）。
    # 检测到游戏更新时禁用盲点：下载/安装期间乱点可能打断更新。但只禁 30 秒
    # 缓冲窗（更新标记消失即计时）——「正在获取更新」是冷启动必经的过渡文字，
    # 被误判一次就永久禁盲点的话，开屏页没人推进，会空转到 240 秒超时
    # （2026-09-17 官服实测；B服 同款问题 2026-09-14 已修，此处对齐其 30 秒冷却）。
    # 游戏冷启动加载阶段（还没见过任何文字）也禁盲点：此时点了也是空点，还可能在
    # 加载完的瞬间落在标题画面上多戳一下（slot_switch 校验提前交棒后加载窗更长）
    $updateCool = $updateSeen -and (((Get-Date) - $lastUpdateAt).TotalSeconds -lt 30)
    $pokeAllowed = $sawText -or (((Get-Date) - $stageStart).TotalSeconds -gt 60)
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
    Start-Sleep 5
    if (-not (Ocr-Screenshot $adb $device $png)) { continue }
    $w = Get-OcrWords $png

    $cap2 = Find-AnyMarker $w $captchaMarkers
    if ($cap2) {
        LogLine ("ERROR: 登录触发验证码（{0}），无人值守无法处理" -f $cap2.Name)
        exit 1
    }
    if (Find-OcrText $w "密码错误") {
        LogLine "ERROR: 提示账号或密码错误，请检查配置后重新捕获"
        exit 1
    }

    $lm2 = Find-AnyMarker $w $loginMarkers
    if (-not $lm2) { $e = Find-OcrText $w "登录" -Exact; if ($e) { $lm2 = [PSCustomObject]@{ X = $e.X; Y = $e.Y; Name = "登录" } } }
    if (-not $lm2) {
        $wake = Find-OcrText $w "开始唤醒"
        if ($wake) {
            LogLine "[login] 已回标题画面（开始唤醒可见），登录成功"
            $loggedIn = $true
            break
        }
        $ig2 = Find-AnyMarker $w $inGameMarkers
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
        # 提交点击复用本轮 OCR 结果 $w（原先在这里再截一次屏识别「登录」按钮，
        # 既多一次最耗时的截图+识别，又与画面状态产生竞态）
        if (($submitCount -eq 0) -and (((Get-Date) - $lastSubmitAt).TotalSeconds -ge 25)) {
            $btn = Find-OcrText $w "登录" -Exact
            if ($btn) { Invoke-Tap $btn.X $btn.Y } else { Invoke-Tap 640 516 }
            LogLine "[login] 提交登录..."
            $submitCount = 1
            $lastSubmitAt = Get-Date
        } elseif (($submitCount -eq 1) -and (((Get-Date) - $lastSubmitAt).TotalSeconds -ge 25)) {
            Invoke-Tap 440 440
            Start-Sleep 1
            $btn = Find-OcrText $w "登录" -Exact
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
