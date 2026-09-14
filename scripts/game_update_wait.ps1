# ============================================================
# 游戏更新等待 v2 —【已停用】v1.3.2 起更新等待并入 login_check.ps1（v6），
# master.ps1 不再调用本脚本；文件保留供手动排查时单独使用。
# 用法不变：game_update_wait.ps1 -Server official [-TimeoutSec 5400] [-ProbeSec 45]
# ============================================================
# 特征来源（联网调研，2026-09-04）：
#   - 游戏内更新提示/页面常用文字：正在获取更新、获取更新配置、开始下载、
#     正在下载、校验资源、正在安装、更新完成、重新启动游戏、版本更新、
#     强制更新、更新下载失败、更新资源损坏 等；
#   - 官方公告确认“版本更新维护后需重新下载并安装游戏客户端”，此时系统会
#     弹出包安装器（PackageInstaller），用 dumpsys 前台窗口一并检测；
#   - 「公告/活动公告」页也含“更新”字样，检测时先排除公告页，避免误判。
# 检测到更新 → 只等待、不点击（防止盲点打断下载/安装）；等回到标题/登录/
# 主界面后放行，由 login_check.ps1 继续。未检测到更新时短暂探测后立即放行。
# 用法：game_update_wait.ps1 -Server official [-TimeoutSec 5400] [-ProbeSec 45]
# 退出码：0 = 无需等待或更新已完成；1 = 更新超时/更新失败（跳过该账号）
# v2 优化：画面未变化时跳过重复识别（与 login_check v2 同款）；更新等待轮询 6→10 秒
# （下载/安装以分钟计，更密的识别没有收益）；共用标记收敛到 game_state_lib.ps1
# ============================================================
param(
    [string]$Server = "official",
    [int]$TimeoutSec = 5400,
    [int]$ProbeSec = 45
)
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

$adb = "D:\软件\MuMu模拟器\MuMuPlayer\nx_main\adb.exe"
$device = "127.0.0.1:16384"
$scriptDir = "D:\1\scripts"

# ---- 读取 GUI 配置（D:\1\config.json），字段缺失时回退硬编码默认 ----
$configPath = "D:\1\config.json"
if (Test-Path $configPath) {
    try {
        $raw = [System.IO.File]::ReadAllText($configPath, [System.Text.Encoding]::UTF8)
        $cfg = $raw | ConvertFrom-Json
        if ($null -ne $cfg.paths -and $cfg.paths.adb) { $adb = [string]$cfg.paths.adb }
        if ($null -ne $cfg.paths -and $cfg.paths.device) { $device = [string]$cfg.paths.device }
        if ($null -ne $cfg.paths -and $cfg.paths.script_dir) { $scriptDir = [string]$cfg.paths.script_dir }
    } catch {}
}

function Timestamp { Get-Date -Format "HH:mm:ss" }
function LogLine($m) { Write-Output ("$(Timestamp) [UpdateWait] " + $m) }

if ($Server -eq "bilibili") { $serverName = "B服" } else { $serverName = "官服" }

# OCR 库（主机端 Windows OCR）+ 共用画面标记库，与 login_check.ps1 同一套
. (Join-Path $scriptDir "ocr_lib.ps1")
. (Join-Path $scriptDir "game_state_lib.ps1")
$debugDir = Join-Path $scriptDir "debug"
if (-not (Test-Path $debugDir)) { New-Item -ItemType Directory $debugDir -Force | Out-Null }
$png = Join-Path $debugDir ("game_update_wait_{0}.png" -f $Server)

# 更新失败/卡住文字：检测到即停止等待（避免干等超时）
$failMarkers = @(
    "更新下载失败", "下载更新失败", "下载失败", "更新失败",
    "更新资源损坏", "获取资源更新配置失败", "网络连接已断开",
    "安装更新失败", "安装失败", "储存空间不足", "存储空间不足"
)
# 更新结束后应出现的“可继续”画面（标题/登录/主界面），出现即放行
$readyMarkers = @(
    "开始唤醒", "公开招募", "干员寻访", "理智", "终端", "采购中心",
    "寻访一次", "寻访十次", "账号登录", "密码登录", "本机号码登录",
    "验证码登录", "请输入账号", "请输入密码", "活动公告", "系统公告"
)
# 公告弹窗页也会出现“更新”字样的正文，检测前先排除（标记在 game_state_lib.ps1）
# 强制更新走系统包安装器时的前台包名特征（OCR 常识别不到安装进度）
$installPkgPattern = 'packageinstaller|permissioncontroller'

function Is-InstallerForeground {
    $out = (& $adb -s $device shell "dumpsys window windows" 2>$null) -join "`n"
    return ($out -match $installPkgPattern)
}

LogLine ("=== Game update wait: {0} (timeout {1}s, probe {2}s) ===" -f $serverName, $TimeoutSec, $ProbeSec)
$updateSeen = $false
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$probeEnd = (Get-Date).AddSeconds($ProbeSec)
$lastPngHash = ""
$lastWords = $null

while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
    if ((Get-Date) -gt $probeEnd -and -not $updateSeen) {
        LogLine "未检测到游戏更新界面，直接进入登录检测"
        exit 0
    }
    if (-not (Ocr-Screenshot $adb $device $png)) {
        Start-Sleep 8
        continue
    }
    # 画面与上一轮完全相同（静态进度条/安装页）→ 复用上一轮 OCR 结果，
    # 跳过最耗时的重复识别；画面一变立即重新识别
    $pngHash = (Get-FileHash $png -Algorithm MD5 -ErrorAction SilentlyContinue).Hash
    if ($pngHash -and ($pngHash -eq $lastPngHash) -and ($null -ne $lastWords)) {
        $words = $lastWords
    } else {
        $words = Get-OcrWords $png
        $lastPngHash = $pngHash
        $lastWords = $words
    }

    # 公告页（含“更新公告”正文）不算更新中，跳过标记检测，交给 login_check 处理
    $ann = Find-AnyMarker $words $AnnounceMarkers
    $up = $null
    if (-not $ann) { $up = Find-AnyMarker $words $GameUpdateMarkers }

    # 补查系统包安装器（强制更新重新安装客户端场景，OCR 未必能识别安装进度）
    $installing = $false
    if (-not $up) {
        $installing = Is-InstallerForeground
    }

    if ($up -or $installing) {
        if (-not $updateSeen) {
            if ($installing) {
                LogLine "[update] 检测到系统包安装器，正在安装/重新安装游戏客户端，等待完成（不点击）"
            } else {
                LogLine ("[update] 检测到游戏更新界面（{0}），等待更新完成（不点击）" -f $up.Name)
            }
        }
        $updateSeen = $true
        # 更新以分钟计，更密的轮询没有收益，10 秒足够及时
        Start-Sleep 10
        continue
    }

    # 失败标记只在已确认更新中后检查：探测阶段（游戏刚启动）出现「网络连接已断开」
    # 等弹窗可能是瞬时抖动，login_check 的确认/盲点兜底能点掉恢复，
    # 提前判失败会把可恢复故障变成账号硬失败
    if ($updateSeen) {
        # 更新过程中出现失败提示：立即报错，避免干等到超时
        $fail = Find-AnyMarker $words $failMarkers
        if ($fail) {
            LogLine ("ERROR: 游戏更新失败（{0}），请检查网络/存储后重试" -f $fail.Name)
            exit 1
        }
        $ready = Find-AnyMarker $words $readyMarkers
        if ($ready) {
            LogLine ("[update] 更新完成，检测到可继续画面（{0}），开始登录检测" -f $ready.Name)
            exit 0
        }
        Start-Sleep 6
        continue
    }

    # 尚未见到更新：已有可继续画面则立即放行
    $ready = Find-AnyMarker $words $readyMarkers
    if ($ready) {
        LogLine "未检测到游戏更新，直接进入登录检测"
        exit 0
    }
    Start-Sleep 5
}

if ($updateSeen) {
    LogLine ("ERROR: 游戏更新超过 {0} 秒仍未完成，跳过该账号，请手动确认游戏可正常进入" -f $TimeoutSec)
    exit 1
}
LogLine "未检测到游戏更新界面，直接进入登录检测"
exit 0
