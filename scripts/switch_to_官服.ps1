# ============================================================
# 切换到官服：关B服App → 开官服App
# ============================================================
$adb = "D:\软件\MuMu模拟器\MuMuPlayer\nx_main\adb.exe"
$device = "127.0.0.1:16384"
$debugDir = "D:\1\scripts\debug"
$packageOfficial = "com.hypergryph.arknights"
$packageBilibili = "com.hypergryph.arknights.bilibili"

# ---- 读取 GUI 配置（D:\1\config.json）覆盖 adb / device，缺失时用上面的默认值 ----
$configPath = "D:\1\config.json"
if (Test-Path $configPath) {
    try {
        $raw = [System.IO.File]::ReadAllText($configPath, [System.Text.Encoding]::UTF8)
        $cfg = $raw | ConvertFrom-Json
        if ($null -ne $cfg.paths -and $cfg.paths.adb)    { $adb = [string]$cfg.paths.adb }
        if ($null -ne $cfg.paths -and $cfg.paths.device) { $device = [string]$cfg.paths.device }
    } catch {}
}

if (-not (Test-Path $debugDir)) { New-Item -ItemType Directory $debugDir -Force | Out-Null }

Write-Output "[Switch] Bilibili -> Official"

Write-Output "  Closing Bilibili app..."
$null = & $adb -s $device shell am force-stop $packageBilibili 2>$null
Start-Sleep 3

Write-Output "  Launching Official app..."
$null = & $adb -s $device shell monkey -p $packageOfficial -c android.intent.category.LAUNCHER 1 2>$null
Start-Sleep 18

# Screenshot after launch for debugging
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$path = Join-Path $debugDir ("switch_Official_{0}.png" -f $timestamp)
try {
    & $adb -s $device exec-out screencap -p > $path 2>$null
    Write-Output "  [screenshot] $path"
} catch {
    Write-Output "  [WARN] Screenshot failed"
}

Write-Output "[Switch] Official ready"
