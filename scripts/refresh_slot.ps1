# ============================================================
# refresh_slot.ps1 — 后台槽位回拉：MAA 跑完后把设备上最新的登录数据
# （弹窗处理标记等）拉回槽位。独立于 master.ps1 的主流程，由主脚本
# 在上一个号 MAA 完成后以后台进程拉起，与下一个号的 slot_switch 并行，
# 每号省 ~5-8 秒衔接等待。
#
# 安全性：Refresh 写本地槽位目录（按 uid.txt 校验防跑错号），
# slot_switch 只读本地槽位推设备、写设备上的 /data/data/——两者操作
# 对象不重叠。极端时序下回拉可能拉到已被下一号覆盖的设备数据：
# playerprefs 的 uid 校验不匹配即跳过；三份拉齐落盘前还会重拉一次
# playerprefs 复核 uid 未变（防 sdk/lc 拉取窗口期设备被下一号切走），
# 变了整批丢弃，不写坏任何槽位。
#
# 用法：refresh_slot.ps1 -Server official -Slot official_1
# 退出码：0（尽力而为，失败仅告警）
# ============================================================
param(
    [string]$Server = "official",
    [string]$Slot = ""
)
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

$adb = "D:\软件\MuMu模拟器\MuMuPlayer\nx_main\adb.exe"
$device = "127.0.0.1:16384"
$scriptDir = "D:\1\scripts"
$logFile = "D:\1\scripts\master_log.txt"

. (Join-Path $PSScriptRoot "config_lib.ps1")
$config = Read-AppConfigJson "D:\1\config.json"
if ($config) {
    if ($null -ne $config.paths -and $config.paths.adb)        { $adb = [string]$config.paths.adb }
    if ($null -ne $config.paths -and $config.paths.device)     { $device = [string]$config.paths.device }
    if ($null -ne $config.paths -and $config.paths.script_dir) { $scriptDir = [string]$config.paths.script_dir }
    if ($null -ne $config.paths -and $config.paths.log_file)   { $logFile = [string]$config.paths.log_file }
}

function LogLine($m) {
    $line = "{0} - {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m
    Write-Output $line
    try { Add-Content $logFile $line -Encoding UTF8 -ErrorAction SilentlyContinue } catch {}
}

. (Join-Path $scriptDir "login_device_lib.ps1")

if (-not $Slot) { exit 0 }
$slotDir = Join-Path $scriptDir ("accounts\" + $Slot)
if (-not (Test-Path $slotDir)) { LogLine "  [Refresh-bg] slot '$Slot' not found, skip"; exit 0 }
$pkg = if ($Server -eq "bilibili") { "com.hypergryph.arknights.bilibili" } else { "com.hypergryph.arknights" }

# 临时文件带 $PID：-SkipMAA 快速连跑等场景下可能多个后台回拉进程并存，
# 固定临时名会被并发进程互相覆盖（uid 串号 / Move-Item 撞车）
$tmpPp  = Join-Path $env:TEMP ("ark_refresh_bg_{0}.pp.xml" -f $PID)
$tmpPp2 = Join-Path $env:TEMP ("ark_refresh_bg_{0}.pp2.xml" -f $PID)
$tmpSdk = Join-Path $env:TEMP ("ark_refresh_bg_{0}.sdk.xml" -f $PID)
$tmpLc  = Join-Path $env:TEMP ("ark_refresh_bg_{0}.lc.cache" -f $PID)
Remove-Item $tmpPp, $tmpPp2, $tmpSdk, $tmpLc -Force -ErrorAction SilentlyContinue

$ppName = ""
try {
    $out = (& $adb -s $device shell "ls /data/data/$pkg/shared_prefs/" 2>$null) -join "`n"
    $ppName = ($out -split "`n" | Where-Object { $_ -match '\.v2\.playerprefs\.xml' } | Select-Object -First 1) -replace '\s+',''
} catch {}
if (-not $ppName) { LogLine "  [Refresh-bg] playerprefs not found on device (slot: $Slot)"; exit 0 }

try {
    & $adb -s $device pull ("/data/data/{0}/shared_prefs/{1}" -f $pkg, $ppName) $tmpPp 2>$null | Out-Null
    if (-not (Test-Path $tmpPp)) { LogLine "  [Refresh-bg] pull failed for $Slot"; exit 0 }

    $devUid = ""
    try {
        $ppc = [System.IO.File]::ReadAllText($tmpPp, [System.Text.Encoding]::UTF8)
        $m = [regex]::Match($ppc, 'name="u8sdk_cached_uid">([0-9]+)')
        if ($m.Success) { $devUid = $m.Groups[1].Value }
    } catch {}
    $uidFile = Join-Path $slotDir "uid.txt"
    $expectUid = ""
    if (Test-Path $uidFile) { $expectUid = (Get-Content $uidFile -Raw -ErrorAction SilentlyContinue).Trim() }
    if (-not $devUid -or ($expectUid -and ($devUid -ne $expectUid))) {
        # uid 不匹配 = 设备数据已被下一号覆盖（slot_switch 跑得更快），正常竞争，不告警
        LogLine "  [Refresh-bg] uid mismatch (device=$devUid, slot=$expectUid), slot '$Slot' data already overwritten by next switch, skip"
        exit 0
    }

    # 三份先全部拉到临时目录、复核后再落盘：playerprefs 校验通过之后，sdk/lc
    # 的拉取窗口里设备可能已被下一号 slot_switch 推入新数据，边拉边写会把
    # 下一号的 SDK 凭据/缓存写进本槽位。拉齐后重拉一次 playerprefs 复核
    # uid 未变，变了说明中途被换号，整批丢弃（uid 校验本身只保护第一份）
    & $adb -s $device pull "/data/data/$pkg/shared_prefs/HypergryphSdkPreferences.xml" $tmpSdk 2>$null | Out-Null
    & $adb -s $device pull "/data/data/$pkg/files/zx/lc.cache" $tmpLc 2>$null | Out-Null
    & $adb -s $device pull ("/data/data/{0}/shared_prefs/{1}" -f $pkg, $ppName) $tmpPp2 2>$null | Out-Null
    $devUid2 = ""
    if (Test-Path $tmpPp2) {
        try {
            $ppc2 = [System.IO.File]::ReadAllText($tmpPp2, [System.Text.Encoding]::UTF8)
            $m2 = [regex]::Match($ppc2, 'name="u8sdk_cached_uid">([0-9]+)')
            if ($m2.Success) { $devUid2 = $m2.Groups[1].Value }
        } catch {}
    }
    if ($devUid2 -ne $devUid) {
        LogLine ("  [Refresh-bg] uid recheck failed (first={0}, recheck={1}), device switched mid-refresh, slot '{2}' discarded this round" -f $devUid, $devUid2, $Slot)
        exit 0
    }

    $dstShared = Join-Path $slotDir "shared_prefs"
    $dstFiles = Join-Path $slotDir "files\zx"
    New-Item -ItemType Directory -Force $dstShared, $dstFiles | Out-Null
    Move-Item $tmpPp (Join-Path $dstShared $ppName) -Force
    if (Test-Path $tmpSdk) { Move-Item $tmpSdk (Join-Path $dstShared "HypergryphSdkPreferences.xml") -Force }
    if (Test-Path $tmpLc)  { Move-Item $tmpLc  (Join-Path $dstFiles "lc.cache") -Force }
    $nGuide = Ensure-GuideViewed (Join-Path $dstShared $ppName)
    if ($nGuide -gt 0) { LogLine ("  [Refresh-bg] slot '$Slot' updated ({0} guide keys patched)" -f $nGuide) }
    else               { LogLine "  [Refresh-bg] slot '$Slot' data updated from device (bg)" }
} finally {
    Remove-Item $tmpPp, $tmpPp2, $tmpSdk, $tmpLc -Force -ErrorAction SilentlyContinue
}
