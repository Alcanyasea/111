# MAA Farm Console - install script
# Prereq: MuMu emulator and MAA are already installed (this package does not install them).
$Version = "@VERSION@"
$ErrorActionPreference = "Stop"
$Host.UI.RawUI.WindowTitle = "MAA 挂机控制台 v$Version 安装"

function Write-Step($m) { Write-Host "[安装] $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "[完成] $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "[提示] $m" -ForegroundColor Yellow }

$srcDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$defaultTarget = "D:\1"

Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  MAA 挂机控制台 v$Version 安装" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Warn "本安装包只安装/配置挂机脚本；MuMu 模拟器与 MAA 请预先安装好。"

# 1) install directory
$dirInput = Read-Host "安装目录（直接回车 = $defaultTarget）"
if ([string]::IsNullOrWhiteSpace($dirInput)) { $target = $defaultTarget } else { $target = $dirInput.TrimEnd('\') }
Write-Ok "安装目录：$target"

# 2) copy files
Write-Step "复制程序文件..."
New-Item -ItemType Directory -Path $target -Force | Out-Null
foreach ($t in @(".gitignore","README.md","config.example.json","启动挂机.bat","启动控制台.bat","gui","scripts","plugins")) {
    $s = Join-Path $srcDir $t
    if (Test-Path $s) { Copy-Item $s (Join-Path $target (Split-Path $t -Leaf)) -Recurse -Force }
}
Write-Ok "程序文件复制完成"

# 3) fix hardcoded paths when installing to a non-default directory
if ($target -ne $defaultTarget) {
    Write-Step "修正脚本中的硬编码路径..."
    Get-ChildItem $target -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -in ".py",".ps1",".bat" } |
        ForEach-Object {
            try {
                $c = [System.IO.File]::ReadAllText($_.FullName)
                if ($c.Contains($defaultTarget)) {
                    [System.IO.File]::WriteAllText($_.FullName, $c.Replace($defaultTarget, $target))
                    Write-Ok ("已修正：" + $_.FullName)
                }
            } catch {}
        }
}

# 4) python venv + dependencies
$venvPy = Join-Path $target "gui\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Step "创建 Python 虚拟环境并安装依赖（首次较慢，请稍候）..."
    $python = "python"
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) { $python = "py" }
    try {
        & $python -m venv (Join-Path $target "gui\.venv")
        & (Join-Path $target "gui\.venv\Scripts\python.exe") -m pip install --upgrade pip
        & (Join-Path $target "gui\.venv\Scripts\python.exe") -m pip install -r (Join-Path $target "gui\requirements.txt")
        Write-Ok "虚拟环境与依赖安装完成"
    } catch {
        Write-Warn "虚拟环境创建失败：$($_.Exception.Message)"
        Write-Warn "可稍后手动执行：python -m venv $target\gui\.venv"
        Write-Warn "然后：$target\gui\.venv\Scripts\pip install -r $target\gui\requirements.txt"
    }
} else {
    Write-Ok "已存在虚拟环境，跳过依赖安装"
}

# 5) config.json generation + path auto-detection
$cfgPath = Join-Path $target "config.json"
if (-not (Test-Path $cfgPath)) {
    Write-Step "生成 config.json 并自动检测 MAA/MuMu 路径..."
    Copy-Item (Join-Path $target "config.example.json") $cfgPath -Force
    $adb = $null
    $cli = $null
    foreach ($p in @(
        "D:\软件\MuMu模拟器\MuMuPlayer\nx_main\adb.exe",
        "C:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
        "$env:ProgramFiles\Netease\MuMuPlayer-12.0\shell\adb.exe",
        "D:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe"
    )) { if (Test-Path $p) { $adb = $p; break } }
    foreach ($p in @(
        "D:\软件\MuMu模拟器\MuMuPlayer\nx_main\mumu-cli.exe",
        "C:\Program Files\Netease\MuMuPlayer-12.0\MuMuManager.exe",
        "$env:ProgramFiles\Netease\MuMuPlayer-12.0\MuMuManager.exe",
        "D:\Program Files\Netease\MuMuPlayer-12.0\MuMuManager.exe"
    )) { if (Test-Path $p) { $cli = $p; break } }
    $maaOfficial = Get-ChildItem "D:\软件\MAA" -Recurse -Filter "MAA.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    $maaBili = Get-ChildItem "D:\软件\MAA（b）" -Recurse -Filter "MAA.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $maaBili) {
        $maaBili = Get-ChildItem "D:\软件\MAA*" -Recurse -Filter "MAA.exe" -ErrorAction SilentlyContinue |
            Where-Object { -not $maaOfficial -or $_.FullName -ne $maaOfficial.FullName } |
            Select-Object -First 1
    }
    try {
        $cfg = [System.IO.File]::ReadAllText($cfgPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
        if ($adb) { $cfg.paths.adb = $adb; Write-Ok ("检测到 ADB：" + $adb) }
        else { Write-Warn "未检测到 MuMu ADB，请稍后在「运行设置」页填写" }
        if ($cli) { $cfg.paths.cli = $cli; Write-Ok ("检测到 MuMu CLI：" + $cli) }
        if ($maaOfficial) {
            $cfg.paths.maa_official = $maaOfficial.FullName
            $cfg.paths.maa_official_dir = $maaOfficial.DirectoryName
            Write-Ok ("检测到 MAA（官服）：" + $maaOfficial.FullName)
        } else { Write-Warn "未检测到官服 MAA，请稍后在「运行设置」页填写" }
        if ($maaBili) {
            $cfg.paths.maa_bilibili = $maaBili.FullName
            $cfg.paths.maa_bilibili_dir = $maaBili.DirectoryName
            Write-Ok ("检测到 MAA（B服）：" + $maaBili.FullName)
        }
        $json = $cfg | ConvertTo-Json -Depth 10
        [System.IO.File]::WriteAllText($cfgPath, $json, (New-Object System.Text.UTF8Encoding($false)))
        Write-Ok "config.json 已生成"
    } catch {
        Write-Warn "config.json 自动配置失败：$($_.Exception.Message)"
    }
} else {
    Write-Ok "已存在 config.json，跳过配置生成（可打开「运行设置」修改）"
}

# 6) desktop shortcut
try {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $lnkPath = Join-Path $desktop "MAA 挂机控制台.lnk"
    $ws = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut($lnkPath)
    $lnk.TargetPath = Join-Path $target "启动控制台.bat"
    $lnk.WorkingDirectory = $target
    $lnk.IconLocation = (Join-Path $target "gui\app.ico")
    $lnk.Description = "MAA 挂机控制台 v$Version"
    $lnk.Save()
    Write-Ok "已创建桌面快捷方式「MAA 挂机控制台」"
} catch {
    Write-Warn "创建桌面快捷方式失败：$($_.Exception.Message)"
}

# 7) scheduled tasks (optional)
$ans = Read-Host "是否创建计划任务（每天 04:00 / 16:00 自动挂机，需管理员权限）？[Y/N]"
if ($ans -match '^[Yy]') {
    try {
        & schtasks /Create /F /TN "MAA_明日方舟自动挂机" /TR "`"$target\启动挂机.bat`"" /SC DAILY /ST 04:00 | Out-Null
        & schtasks /Create /F /TN "MAA_明日方舟自动挂机_下午" /TR "`"$target\启动挂机.bat`"" /SC DAILY /ST 16:00 | Out-Null
        Write-Ok "计划任务创建完成"
    } catch {
        Write-Warn "计划任务创建失败（需要管理员权限）。可手动执行："
        Write-Warn "schtasks /Create /F /TN MAA_明日方舟自动挂机 /TR $target\启动挂机.bat /SC DAILY /ST 04:00"
        Write-Warn "schtasks /Create /F /TN MAA_明日方舟自动挂机_下午 /TR $target\启动挂机.bat /SC DAILY /ST 16:00"
    }
}

Write-Ok "安装完成！"
Write-Host ""
Write-Host "接下来："
Write-Host "  1. 双击桌面「MAA 挂机控制台」或 $target\启动控制台.bat 启动"
Write-Host "  2. 若路径未自动检测到，到「运行设置」页填写 MAA / MuMu ADB 路径"
Write-Host "  3. 到「账号管理」页添加账号并「捕获」登录"
Write-Host "  4. 手动挂机：双击 $target\启动挂机.bat"
Write-Host ""
Read-Host "按回车退出"
