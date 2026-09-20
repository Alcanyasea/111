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

function Find-FileUnder($roots, $file, $depth = 4) {
    foreach ($r in $roots) {
        if (-not $r -or -not (Test-Path $r)) { continue }
        $hit = Get-ChildItem -Path $r -Recurse -Depth $depth -Filter $file -File `
            -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $null
}

function Find-MaaExes {
    $roots = @(
        "D:\软件",
        "$env:ProgramFiles",
        "D:\Program Files",
        "$env:LOCALAPPDATA\Programs"
    )
    $dirs = @()
    foreach ($r in $roots) {
        if (Test-Path $r) {
            $dirs += @(Get-ChildItem -Path $r -Directory -Filter "MAA*" `
                -ErrorAction SilentlyContinue)
        }
    }
    $exes = @()
    foreach ($d in $dirs) {
        $exes += @(Get-ChildItem -Path $d.FullName -Recurse -Depth 3 -Filter "MAA.exe" `
            -File -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)
    }
    return @($exes | Sort-Object -Unique)
}

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
    # 目标路径会被逐字写进脚本内容（替换默认路径字样）。含引号/美元符/括号等
    # 字符时会改坏代码（字符串提前闭合、被当表达式解析），先拒绝并说明。
    if ([regex]::IsMatch($target, '[\"''`$;(){}%!<>*?]') -or
        $target -match '[.\s]$' -or $target -match '^[A-Za-z]:$') {
        Write-Warn "安装目录含有无法安全改写脚本的特殊字符（引号/美元符/括号/结尾空格或点等），中止安装。"
        Write-Warn "请换一个仅含字母、数字、横线、下划线、空格与中文的安装目录后重试。"
        Read-Host "按回车退出"
        exit 1
    }
    Write-Step "修正脚本中的硬编码路径..."
    Get-ChildItem $target -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Extension -in ".py",".ps1",".bat" -and
            $_.FullName -notmatch '\\gui\\(runtime|\.venv)\\'
        } |
        ForEach-Object {
            try {
                $c = [System.IO.File]::ReadAllText($_.FullName)
                if ($c.Contains($defaultTarget)) {
                    [System.IO.File]::WriteAllText($_.FullName, $c.Replace($defaultTarget, $target))
                    Write-Ok ("已修正：" + $_.FullName)
                }
            } catch {}
        }

    # 替换后语法自检：万一替换撞上代码里的字符串/正则上下文把文件改坏，
    # 在这里拦下并中止安装，别等用户运行时才炸
    Write-Step "校验改写后的脚本语法..."
    $bad = @()
    $py = Join-Path $target "gui\runtime\python.exe"
    if (-not (Test-Path $py)) { $py = Join-Path $target "gui\.venv\Scripts\python.exe" }
    Get-ChildItem $target -Recurse -File -Filter *.py -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch '\\gui\\(runtime|\.venv)\\' } |
        ForEach-Object {
            if (Test-Path $py) {
                & $py -m py_compile $_.FullName 2>$null
                if ($LASTEXITCODE -ne 0) { $bad += $_.FullName }
            }
        }
    Get-ChildItem $target -Recurse -File -Filter *.ps1 -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch '\\gui\\(runtime|\.venv)\\' } |
        ForEach-Object {
            $errs = $null
            $null = [System.Management.Automation.Language.Parser]::ParseFile(
                $_.FullName, [ref]$null, [ref]$errs)
            if ($errs) { $bad += $_.FullName }
        }
    if ($bad) {
        Write-Warn "以下文件改写后语法校验失败（请截图反馈）："
        $bad | ForEach-Object { Write-Warn ("  " + $_) }
        Write-Warn "已中止安装，请换安装目录或联系开发者。"
        Read-Host "按回车退出"
        exit 1
    }
    Write-Ok "脚本语法校验通过"
}

# 4) Python 运行环境：优先使用安装包内置的 gui\runtime
$runtimePyw = Join-Path $target "gui\runtime\pythonw.exe"
$venvPy = Join-Path $target "gui\.venv\Scripts\python.exe"
if (Test-Path $runtimePyw) {
    Write-Ok "已内置 Python 运行环境与依赖，无需联网安装"
} elseif (Test-Path $venvPy) {
    Write-Ok "已存在虚拟环境，使用现有 gui\.venv"
} else {
    Write-Warn "未找到内置 Python 运行环境，尝试用系统 Python 创建虚拟环境（需要联网）..."
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
}

# 5) config.json generation + path auto-detection
$cfgPath = Join-Path $target "config.json"
if (-not (Test-Path $cfgPath)) {
    Write-Step "生成 config.json 并自动检测 MAA/MuMu 路径..."
    Copy-Item (Join-Path $target "config.example.json") $cfgPath -Force
    $mumuRoots = @(
        "D:\软件\MuMu模拟器",
        "C:\Program Files\Netease\MuMuPlayer-12.0",
        "C:\Program Files\Netease",
        "D:\Program Files\Netease\MuMuPlayer-12.0",
        "D:\Program Files\Netease",
        "$env:ProgramFiles\Netease",
        "$env:LOCALAPPDATA\Netease"
    )
    $adb = Find-FileUnder $mumuRoots "adb.exe" 5
    $cli = Find-FileUnder $mumuRoots "mumu-cli.exe" 5
    if (-not $cli) { $cli = Find-FileUnder $mumuRoots "MuMuManager.exe" 5 }
    $maaExes = Find-MaaExes
    $maaOfficial = $maaExes |
        Where-Object { $_ -notmatch '[（(]b[）)]|Bilibili' } |
        Select-Object -First 1
    if (-not $maaOfficial) { $maaOfficial = $maaExes | Select-Object -First 1 }
    $maaBili = $maaExes |
        Where-Object { $_ -ne $maaOfficial -and ($_ -match '[（(]b[）)]|Bilibili') } |
        Select-Object -First 1
    if (-not $maaBili) {
        $maaBili = $maaExes | Where-Object { $_ -ne $maaOfficial } | Select-Object -First 1
    }
    try {
        $cfg = [System.IO.File]::ReadAllText($cfgPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
        $cfg.paths.script_dir = Join-Path $target "scripts"
        $cfg.paths.log_file = Join-Path $target "scripts\master_log.txt"
        if ($adb) { $cfg.paths.adb = $adb; Write-Ok ("检测到 ADB：" + $adb) }
        else { Write-Warn "未检测到 MuMu ADB，请稍后在「运行设置」页填写" }
        if ($cli) { $cfg.paths.cli = $cli; Write-Ok ("检测到 MuMu CLI：" + $cli) }
        if ($maaOfficial) {
            $cfg.paths.maa_official = $maaOfficial
            $cfg.paths.maa_official_dir = Split-Path -Parent $maaOfficial
            Write-Ok ("检测到 MAA（官服）：" + $maaOfficial)
        } else { Write-Warn "未检测到官服 MAA，请稍后在「运行设置」页填写" }
        if ($maaBili) {
            $cfg.paths.maa_bilibili = $maaBili
            $cfg.paths.maa_bilibili_dir = Split-Path -Parent $maaBili
            Write-Ok ("检测到 MAA（B服）：" + $maaBili)
        }
        # Depth 100：accounts[].base_schedule.batches[].manufacture[].operators
        # 这类深层结构将来再加一层就会被 Depth 10 静默截断（丢配置不报错）
        $json = $cfg | ConvertTo-Json -Depth 100
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

# 7) scheduled task (optional) — 与 GUI 控制台同一个任务名、同一套动作定义：
#    pwsh 绝对路径 + master.ps1。不走启动挂机.bat（bat 结尾的 pause 会让计划
#    任务进程在挂机结束后永不退出），任务名/触发也与 GUI 保持一致便于管理
$ans = Read-Host "是否创建计划任务（每天 04:00 / 16:00 自动挂机，需管理员权限）？[Y/N]"
if ($ans -match '^[Yy]') {
    try {
        $pwshExe = @(
            "$env:ProgramFiles\PowerShell\7\pwsh.exe",
            "$env:LOCALAPPDATA\Microsoft\WindowsApps\pwsh.exe"
        ) | Where-Object { Test-Path $_ } | Select-Object -First 1
        if (-not $pwshExe) { throw "未找到 PowerShell 7（pwsh），请先安装 PowerShell 7" }
        $action = New-ScheduledTaskAction -Execute $pwshExe `
            -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$target\scripts\master.ps1`""
        $triggers = @(
            New-ScheduledTaskTrigger -Daily -At 04:00
            New-ScheduledTaskTrigger -Daily -At 16:00
        )
        Register-ScheduledTask -TaskName "MAA_明日方舟自动挂机" -Action $action `
            -Trigger $triggers -Force | Out-Null
        Write-Ok "计划任务创建完成（单任务含两个触发时间，与控制台「班次计划」共用）"
    } catch {
        Write-Warn "计划任务创建失败（需管理员权限 + PowerShell 7）：$($_.Exception.Message)"
        Write-Warn "可稍后打开控制台「仪表盘 → 班次计划」一键创建/同步计划任务"
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
