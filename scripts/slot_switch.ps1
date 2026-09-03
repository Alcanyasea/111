# ============================================================
# 槽位切号 v1 — 非点击换号：停游戏 → 推入账号数据文件 → 重启游戏 → 校验 UID
# 每个账号的登录数据保存在 D:\1\scripts\accounts\<slot>\ 下（镜像设备相对路径）：
#   shared_prefs\<包名>.v2.playerprefs.xml + HypergryphSdkPreferences.xml
#   files\zx\lc.cache
# 需要 adb root（MuMu 12 自带），推送后修正 owner/permission/SELinux 上下文。
# 容错：shared_prefs（登录态核心）推送失败才判失败；files/lc.cache 是可选的
# 登录缓存，失败只告警不中断；每个文件重试一次，重试前重新读取设备 uid/上下文
# （模拟器刚启动时 SELinux 上下文可能未带齐分类后缀），并先建好 /data/local/tmp
# 暂存目录（模拟器重启会清空该目录）。
# 用法：slot_switch.ps1 -Server official -Slot official_2 [-WaitSec 25] [-NoVerify]
#   -Server: official | bilibili
#   -Slot:   槽位目录名；目录不存在时仅做客户端切换（不推数据）
# 退出码：0 成功；1 推送/校验失败（master.ps1 据此标记该号失败）
# ============================================================
param(
    [string]$Server = "official",
    [string]$Slot = "",
    [int]$WaitSec = 25,
    [switch]$NoVerify
)
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

$adb = "D:\软件\MuMu模拟器\MuMuPlayer\nx_main\adb.exe"
$device = "127.0.0.1:16384"
$accountsDir = "D:\1\scripts\accounts"
$debugDir = "D:\1\scripts\debug"

# ---- 读取 GUI 配置（D:\1\config.json），字段缺失时回退上面的硬编码默认 ----
$configPath = "D:\1\config.json"
if (Test-Path $configPath) {
    try {
        $raw = [System.IO.File]::ReadAllText($configPath, [System.Text.Encoding]::UTF8)
        $cfg = $raw | ConvertFrom-Json
        if ($null -ne $cfg.paths -and $cfg.paths.adb)    { $adb = [string]$cfg.paths.adb }
        if ($null -ne $cfg.paths -and $cfg.paths.device) { $device = [string]$cfg.paths.device }
        if ($null -ne $cfg.paths -and $cfg.paths.script_dir) { $accountsDir = Join-Path ([string]$cfg.paths.script_dir) "accounts" }
    } catch {}
}

function Timestamp { Get-Date -Format "HH:mm:ss" }

if ($Server -eq "bilibili") {
    $pkg = "com.hypergryph.arknights.bilibili"
    $otherPkg = "com.hypergryph.arknights"
    $serverName = "B服"
} else {
    $pkg = "com.hypergryph.arknights"
    $otherPkg = "com.hypergryph.arknights.bilibili"
    $serverName = "官服"
}

$slotDir = ""
if ($Slot) { $slotDir = Join-Path $accountsDir $Slot }

Write-Output ("$(Timestamp) [Switch] -> {0} (slot: {1})" -f $serverName, $(if ($Slot) { $Slot } else { "(无槽位,仅切换客户端)" }))

# ---- adb root（MuMu 自带；失败则后面推送会失败并报错）----
& $adb -s $device root 2>$null | Out-Null
Start-Sleep 2
& $adb -s $device connect $device 2>$null | Out-Null
Start-Sleep 1

# ---- 停止两个客户端，避免同屏互相干扰 ----
Write-Output ("$(Timestamp) [Switch] Stopping apps...")
& $adb -s $device shell "am force-stop $pkg; am force-stop $otherPkg" 2>$null | Out-Null
Start-Sleep 2

# ---- 推送槽位数据（存在才推）----
$slotOk = $true
if ($slotDir -and (Test-Path $slotDir)) {
    # 应用 uid 与 SELinux 上下文（取目标包目录自身属性，兼容不同 MuMu 版本）
    $uidStr = (& $adb -s $device shell "stat -c %u:%g /data/data/$pkg" 2>$null | Select-Object -First 1)
    $ctx = (& $adb -s $device shell "stat -c %C /data/data/$pkg/shared_prefs" 2>$null | Select-Object -First 1)
    if (-not $uidStr -or $uidStr -notmatch '^\d+:\d+') { $uidStr = "10044:10044" }
    if (-not $ctx -or $ctx -notmatch '^u:') { $ctx = "u:object_r:app_data_file:s0" }
    Write-Output ("$(Timestamp) [Switch] Pushing slot files (uid=$uidStr ctx=$ctx)...")

    $files = Get-ChildItem $slotDir -Recurse -File | Where-Object {
        $_.FullName.Substring($slotDir.Length + 1) -match '^(shared_prefs|files)[\\/]'
    }
    $pushed = 0
    foreach ($f in $files) {
        $rel = $f.FullName.Substring($slotDir.Length + 1) -replace '\\','/'
        # shared_prefs 是登录态核心（推送失败判账号失败）；files 下（lc.cache）
        # 是可选的登录缓存，失败只告警不中断——捕获/刷新流程同样容忍其缺失。
        $core = $rel -like "shared_prefs/*"
        $remoteTmp = "/data/local/tmp/ark_switch/" + $rel
        $remoteDst = "/data/data/$pkg/" + $rel
        $remoteDir = Split-Path $remoteDst
        # 权限：shared_prefs 660，files 下 600（与游戏自身写入一致）
        $mode = if ($core) { "660" } else { "600" }
        $fileOk = $false
        foreach ($attempt in 1..2) {
            # 重试前重新读取 uid/上下文：模拟器刚启动时包目录的 SELinux 上下文
            # 可能还没带齐分类后缀（实测首轮读到 s0、就绪后为 s0:c44,...），
            # 重读能拿到就绪后的完整上下文
            if ($attempt -gt 1) {
                Start-Sleep 2
                $uidStr = (& $adb -s $device shell "stat -c %u:%g /data/data/$pkg" 2>$null | Select-Object -First 1)
                $ctx = (& $adb -s $device shell "stat -c %C /data/data/$pkg/shared_prefs" 2>$null | Select-Object -First 1)
                if (-not $uidStr -or $uidStr -notmatch '^\d+:\d+') { $uidStr = "10044:10044" }
                if (-not $ctx -or $ctx -notmatch '^u:') { $ctx = "u:object_r:app_data_file:s0" }
            }
            # 模拟器重启会清空 /data/local/tmp，先建好暂存目录再推
            $tmpDir = Split-Path $remoteTmp
            & $adb -s $device shell "mkdir -p $tmpDir; mkdir -p $remoteDir" 2>$null | Out-Null
            & $adb -s $device push $f.FullName $remoteTmp 2>$null | Out-Null
            & $adb -s $device shell "cp $remoteTmp $remoteDst; chown $uidStr $remoteDst; chmod $mode $remoteDst; chcon $ctx $remoteDst" 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { $fileOk = $true; break }
        }
        if ($fileOk) {
            $pushed++
        } elseif ($core) {
            Write-Output ("$(Timestamp) [Switch] ERROR: push failed for $rel")
            $slotOk = $false
            break
        } else {
            Write-Output ("$(Timestamp) [Switch] WARN: push failed for $rel（可选缓存，忽略，不影响切号）")
        }
    }
    if ($slotOk) { Write-Output ("$(Timestamp) [Switch] Slot files pushed ($pushed files)") }
} else {
    Write-Output ("$(Timestamp) [Switch] Slot dir not found, keeping current data: $slotDir")
}

# ---- 启动目标客户端 ----
Write-Output ("$(Timestamp) [Switch] Launching $serverName app...")
& $adb -s $device shell "monkey -p $pkg -c android.intent.category.LAUNCHER 1" 2>$null | Out-Null
Start-Sleep $WaitSec

# ---- 校验：设备上 playerprefs 的 uid 与槽位 uid.txt 一致 ----
if (-not $NoVerify -and $slotOk -and $slotDir -and (Test-Path $slotDir)) {
    $uidFile = Join-Path $slotDir "uid.txt"
    $ppName = (Get-ChildItem (Join-Path $slotDir "shared_prefs") -Filter "*.v2.playerprefs.xml" -ErrorAction SilentlyContinue | Select-Object -First 1).Name
    if ($uidFile -and (Test-Path $uidFile) -and $ppName) {
        $expectUid = (Get-Content $uidFile -Raw -ErrorAction SilentlyContinue).Trim()
        # 拉回本地解析，避免 adb shell 引号转义问题
        $tmpPp = Join-Path $env:TEMP "ark_verify_pp.xml"
        $devUid = ""
        & $adb -s $device pull ("/data/data/{0}/shared_prefs/{1}" -f $pkg, $ppName) $tmpPp 2>$null | Out-Null
        if (Test-Path $tmpPp) {
            try {
                $ppContent = [System.IO.File]::ReadAllText($tmpPp, [System.Text.Encoding]::UTF8)
                $m = [regex]::Match($ppContent, 'name="u8sdk_cached_uid">([0-9]+)')
                if ($m.Success) { $devUid = $m.Groups[1].Value }
            } catch {}
            Remove-Item $tmpPp -Force -ErrorAction SilentlyContinue
        }
        if ($devUid -and $devUid -eq $expectUid) {
            Write-Output ("$(Timestamp) [Switch] Verify OK: uid=$devUid")
        } else {
            Write-Output ("$(Timestamp) [Switch] VERIFY FAIL: expected uid=$expectUid, device uid=[$devUid]")
            Write-Output ("$(Timestamp) [Switch] 游戏可能停留在登录界面（token 失效或推送异常）")
            $slotOk = $false
        }
    }
}

if ($slotOk) {
    Write-Output ("$(Timestamp) [Switch] Done - $serverName ready")
    exit 0
} else {
    Write-Output ("$(Timestamp) [Switch] FAILED")
    exit 1
}
