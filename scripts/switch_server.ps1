# ============================================================
# 切换客户端：官服 <-> B服（switch_to_*.ps1 的实际实现）
#
# 用法：switch_server.ps1 -To official|bilibili
# 两个方向只差包名、提示语、启动等待（官服标题画面更快）和截图文件名，
# 因此合并成一份；旧的两个脚本保留为兼容入口，调用点无需改动。
# ============================================================
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('official', 'bilibili')]
    [string]$To
)

$adb = "D:\软件\MuMu模拟器\MuMuPlayer\nx_main\adb.exe"
$device = "127.0.0.1:16384"
$debugDir = Join-Path $PSScriptRoot "debug"
$packageOfficial = "com.hypergryph.arknights"
$packageBilibili = "com.hypergryph.arknights.bilibili"

# ---- 读取 GUI 配置（项目根 config.json）覆盖 adb / device，缺失时用上面的默认值 ----
. (Join-Path $PSScriptRoot "config_lib.ps1")
$config = Read-AppConfigJson (Join-Path (Split-Path -Parent $PSScriptRoot) "config.json")
if ($config) {
    if ($null -ne $config.paths -and $config.paths.adb)    { $adb = [string]$config.paths.adb }
    if ($null -ne $config.paths -and $config.paths.device) { $device = [string]$config.paths.device }
}

if (-not (Test-Path $debugDir)) { New-Item -ItemType Directory $debugDir -Force | Out-Null }

if ($To -eq 'official') {
    $sourceName = 'Bilibili'; $targetName = 'Official'
    $sourcePkg = $packageBilibili; $targetPkg = $packageOfficial
    $launchWait = 18
    $shotLabel = 'Official'
} else {
    $sourceName = 'Official'; $targetName = 'Bilibili'
    $sourcePkg = $packageOfficial; $targetPkg = $packageBilibili
    $launchWait = 25
    $shotLabel = 'B'
}

Write-Output "[Switch] $sourceName -> $targetName"

Write-Output "  Closing $sourceName app..."
$null = & $adb -s $device shell am force-stop $sourcePkg 2>$null
Start-Sleep 3

Write-Output "  Launching $targetName app..."
$null = & $adb -s $device shell monkey -p $targetPkg -c android.intent.category.LAUNCHER 1 2>$null
Start-Sleep $launchWait

# Screenshot after launch for debugging
# Use Start-Process -RedirectStandardOutput to write raw binary PNG
# (PowerShell > / Out-File corrupt binary data by re-encoding as UTF-16)
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$path = Join-Path $debugDir ("switch_{0}_{1}.png" -f $shotLabel, $timestamp)
try {
    $tmpFile = "$path.tmp"
    Start-Process -FilePath $adb -ArgumentList "-s `"$device`" exec-out screencap -p" -NoNewWindow -Wait -RedirectStandardOutput $tmpFile
    if ((Test-Path $tmpFile) -and (Get-Item $tmpFile).Length -gt 100) {
        Move-Item $tmpFile $path -Force
        Write-Output "  [screenshot] $path"
    } else {
        Remove-Item $tmpFile -Force -ErrorAction SilentlyContinue
        Write-Output "  [WARN] Screenshot empty"
    }
} catch {
    Write-Output "  [WARN] Screenshot failed"
    Remove-Item $tmpFile -Force -ErrorAction SilentlyContinue
}

Write-Output "[Switch] $targetName ready"
