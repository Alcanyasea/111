# ============================================================
# 切换到B服：关官服App → 开B服App
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

Write-Output "[Switch] Official -> Bilibili"

Write-Output "  Closing Official app..."
$null = & $adb -s $device shell am force-stop $packageOfficial 2>$null
Start-Sleep 3

Write-Output "  Launching Bilibili app..."
$null = & $adb -s $device shell monkey -p $packageBilibili -c android.intent.category.LAUNCHER 1 2>$null
Start-Sleep 25

# Screenshot after launch for debugging
# Use Start-Process -RedirectStandardOutput to write raw binary PNG
# (PowerShell > / Out-File corrupt binary data by re-encoding as UTF-16)
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$path = Join-Path $debugDir ("switch_B_{0}.png" -f $timestamp)
try {
    $tmpFile = "$path.tmp"
    Start-Process -FilePath $adb -ArgumentList "-s `"$device`" exec-out screencap -p" -NoNewWindow -Wait -RedirectStandardOutput $tmpFile
    if ((Get-Item $tmpFile).Length -gt 100) {
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

Write-Output "[Switch] Bilibili ready"
