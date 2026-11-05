# 槽位快照回拉：主控同步等待，完成后才开始下一账号。
# 拉取失败或 UID 不符保留原数据；发布前备份，异常回滚。
param(
    [string]$Server = "official",
    [string]$Slot = "",
    [switch]$NoLogFile
)
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

$adb = "D:\软件\MuMu模拟器\MuMuPlayer\nx_main\adb.exe"
$device = "127.0.0.1:16384"
$scriptDir = $PSScriptRoot
$logFile = Join-Path $scriptDir "master_log.txt"

. (Join-Path $PSScriptRoot "config_lib.ps1")
$config = Read-AppConfigJson (Join-Path (Split-Path -Parent $PSScriptRoot) "config.json")
if ($config) {
    if ($null -ne $config.paths -and $config.paths.adb)        { $adb = [string]$config.paths.adb }
    if ($null -ne $config.paths -and $config.paths.device)     { $device = [string]$config.paths.device }
    if ($null -ne $config.paths -and $config.paths.script_dir) { $scriptDir = [string]$config.paths.script_dir }
    if ($null -ne $config.paths -and $config.paths.log_file)   { $logFile = [string]$config.paths.log_file }
}

function LogLine($m) {
    $line = "{0} - {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m
    Write-Output $line
    if (-not $NoLogFile) {
        try { Add-Content $logFile $line -Encoding UTF8 -ErrorAction SilentlyContinue } catch {}
    }
}

. (Join-Path $PSScriptRoot "adb_lib.ps1")
. (Join-Path $PSScriptRoot "adb_sync.ps1")
. (Join-Path $scriptDir "login_device_lib.ps1")
. (Join-Path $PSScriptRoot "slot_snapshot.ps1")

if (-not $Slot) { exit 0 }
$slotDir = Join-Path $scriptDir ("accounts\" + $Slot)
if (-not (Test-Path $slotDir)) { LogLine "  [Refresh] slot '$Slot' not found, skip"; exit 0 }
$pkg = if ($Server -eq "bilibili") { "com.hypergryph.arknights.bilibili" } else { "com.hypergryph.arknights" }

# 回拉前确认设备可用：本脚本没有别的自愈入口，端口漂移时直接跑会报
# "playerprefs 回拉失败"，把该号误判成切号问题。
$preferPort = 0
if ($device -match ':(\d+)$') { $preferPort = [int]$Matches[1] }
$vmConfigPath = Get-MuMuVmConfigPath -ToolPath $adb -PreferPort $preferPort
$confirmed = Ensure-AdbOnline -Device $device -Attempts 3 -RetryDelaySec 2 -VmConfigPath $vmConfigPath -RequireAnyPackage @($pkg)
if (-not $confirmed) {
    LogLine ("  [WARN] Refresh failed: {0}" -f $script:LastAdbError)
    exit 1
}
if ([string]$confirmed -ne $device) {
    LogLine ("  [Refresh] ADB 端口已重新发现：{0} -> {1}" -f $device, $confirmed)
    $device = [string]$confirmed
    $syncOk = Sync-AdbDeviceForProject -ProjectRoot (Split-Path -Parent $PSScriptRoot) -Device $device -Config $config
    if (-not $syncOk) {
        LogLine "  [WARN] ADB 地址回写配置失败，后续脚本可能仍用旧地址"
    }
    if ($script:LastAdbSyncWarning) {
        LogLine ("  [WARN] " + $script:LastAdbSyncWarning)
    }
}

try {
    $uid = Sync-SlotSnapshot $adb $device $pkg $slotDir
    LogLine "  [Refresh] slot '$Slot' updated; previous snapshot saved in .refresh-backup"
} catch {
    LogLine ('  [WARN] Refresh failed: ' + $_.Exception.Message)
    exit 1
}
