<#
构建 MAA 挂机控制台自解压安装包（7-Zip SFX）。

产物：build\dist\MAA-Farm-Console-v<版本>-Setup.exe
安装包结构：7zS.sfx 引导 + config.txt（解压后运行 setup.bat）+ 压缩的项目文件。
setup.bat -> install.ps1 负责：选安装目录、复制文件、修正硬编码路径、
创建 Python 虚拟环境并装依赖、生成 config.json 并自动检测 MAA/MuMu 路径、
创建桌面快捷方式、可选创建计划任务。

用法：
    powershell -ExecutionPolicy Bypass -File build\build_installer.ps1
    powershell -ExecutionPolicy Bypass -File build\build_installer.ps1 -Version 1.1.3
    powershell -ExecutionPolicy Bypass -File build\build_installer.ps1 -SevenZip "C:\Program Files\7-Zip\7z.exe"

7-Zip 缺失时自动引导（无需管理员）：
    1) 若当前有管理员权限，尝试 winget 安装 7zip.7zip；
    2) 否则从 7-zip.org 下载官方 MSI，用 msiexec /a 免管理员解压到 build\tools。
    7zS.sfx 自解压模块从官方 7-Zip Extra 包下载并缓存到 build\tools。
#>
param(
    [string]$Version = "",
    [string]$RepoRoot = "",
    [string]$OutputDir = "",
    [string]$SevenZip = "",
    [switch]$SkipBootstrap
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Write-Step($m) { Write-Host "[构建] $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "[完成] $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "[提示] $m" -ForegroundColor Yellow }

if (-not $RepoRoot) { $RepoRoot = Split-Path -Parent $PSScriptRoot }
$RepoRoot = [System.IO.Path]::GetFullPath($RepoRoot)
if (-not $OutputDir) { $OutputDir = Join-Path $RepoRoot "build\dist" }
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
$toolsDir = Join-Path $RepoRoot "build\tools"
New-Item -ItemType Directory -Path $toolsDir -Force | Out-Null

# ---------- 版本 ----------
if (-not $Version) {
    $tag = git -C $RepoRoot describe --tags --abbrev=0 2>$null
    if ($tag) { $Version = $tag.Trim() -replace '^v','' }
}
if (-not $Version) { $Version = "0.0.0" }
$Version = $Version.Trim()
Write-Step "版本：$Version"

# ---------- 定位 7-Zip ----------
function Find-7z {
    if ($SevenZip -and (Test-Path $SevenZip)) { return $SevenZip }
    $c = Get-Command 7z -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    foreach ($p in @(
        "C:\Program Files\7-Zip\7z.exe",
        "C:\Program Files (x86)\7-Zip\7z.exe"
    )) { if (Test-Path $p) { return $p } }
    $cached = Get-ChildItem $toolsDir -Recurse -Filter "7z.exe" -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($cached) { return $cached.FullName }
    return $null
}

$exe7z = Find-7z
if (-not $exe7z -and -not $SkipBootstrap) {
    Write-Step "未找到 7-Zip，开始自动引导..."
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if ($isAdmin -and (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Step "以管理员身份通过 winget 安装 7-Zip..."
        winget install --id 7zip.7zip -e --accept-source-agreements --accept-package-agreements --silent | Out-Null
    } else {
        Write-Step "从 7-zip.org 下载官方 MSI 并免管理员解压（msiexec /a）..."
        try {
            $page = (Invoke-WebRequest -Uri "https://www.7-zip.org/download.html" -UseBasicParsing -TimeoutSec 60).Content
            $m = [regex]::Match($page, 'href="(https://github\.com/ip7z/7zip/releases/download/[^"]+/7z\d+-x64\.msi)"')
            if (-not $m.Success) { $m = [regex]::Match($page, 'href="(a/7z\d+-x64\.msi)"') }
            if (-not $m.Success) { $m = [regex]::Match($page, 'href="(7z\d+-x64\.msi)"') }
            if (-not $m.Success) { throw "无法从 7-zip.org 解析当前版本 MSI" }
            $msiUrl = $m.Groups[1].Value
            if (-not $msiUrl.StartsWith("http")) { $msiUrl = "https://www.7-zip.org/" + $msiUrl }
            $msiPath = Join-Path $toolsDir ([System.IO.Path]::GetFileName($msiUrl))
            if (-not (Test-Path $msiPath)) {
                Invoke-WebRequest -Uri $msiUrl -OutFile $msiPath -UseBasicParsing -TimeoutSec 120
            }
            $extractDir = Join-Path $toolsDir "7z"
            New-Item -ItemType Directory -Path $extractDir -Force | Out-Null
            Start-Process msiexec.exe -ArgumentList "/a `"$msiPath`" /qn TARGETDIR=`"$extractDir`"" -Wait -NoNewWindow
        } catch {
            Write-Warn "自动引导失败：$($_.Exception.Message)"
            Write-Warn "请手动安装 7-Zip 后用 -SevenZip 指定 7z.exe 路径，或去掉 -SkipBootstrap 重试"
        }
    }
    $exe7z = Find-7z
}
if (-not $exe7z) { throw "未找到 7-Zip（7z.exe）。请安装 7-Zip 或用 -SevenZip 指定路径。" }
Write-Ok "7-Zip：$exe7z"

# ---------- 7zS.sfx ----------
$sfxPath = Join-Path $toolsDir "7zS.sfx"
if (-not (Test-Path $sfxPath)) {
    # 新版（23.01+）Extra 包已不含 7zS.sfx，从 9.20 Extra 包获取官方自解压模块
    Write-Step "下载 7-Zip 9.20 Extra（含 7zS.sfx 自解压模块）..."
    $extraUrl = "https://www.7-zip.org/a/7z920_extra.7z"
    $extraPath = Join-Path $toolsDir ([System.IO.Path]::GetFileName($extraUrl))
    Invoke-WebRequest -Uri $extraUrl -OutFile $extraPath -UseBasicParsing -TimeoutSec 120
    & $exe7z x $extraPath ("-o" + $toolsDir) -y | Out-Null
}
if (-not (Test-Path $sfxPath)) {
    $sfxPath = Get-ChildItem $toolsDir -Recurse -Filter "7zS.sfx" -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $sfxPath) { throw "未找到 7zS.sfx 自解压模块（build\tools 下应有 7zS.sfx）" }
Write-Ok "SFX 模块：$sfxPath"

# ---------- 暂存项目文件 ----------
$work = Join-Path $env:TEMP ("maa_setup_" + [guid]::NewGuid().ToString("N"))
$staging = Join-Path $work "stage"
New-Item -ItemType Directory -Path $staging -Force | Out-Null

Write-Step "收集项目文件（排除 .git/.venv/config.json/账号数据/运行残留）..."
robocopy $RepoRoot $staging /E /NFL /NDL /NJH /NJS /NP `
    /XD .git .venv __pycache__ accounts debug plans dist tools .claude `
    /XF config.json config.json.bak master_log.txt master.lock maa_done.signal `
        switch_output.tmp _shot.png _shot.py *.pyc | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy 失败（exit=$LASTEXITCODE）" }

# 注入安装脚本（版本号占位符替换）
$installSrc = Join-Path $PSScriptRoot "installer"
Copy-Item (Join-Path $installSrc "setup.bat") (Join-Path $staging "setup.bat") -Force
$installPs1 = [System.IO.File]::ReadAllText((Join-Path $installSrc "install.ps1"), [System.Text.Encoding]::UTF8)
$installPs1 = $installPs1.Replace("@VERSION@", $Version)
[System.IO.File]::WriteAllText((Join-Path $staging "install.ps1"), $installPs1, (New-Object System.Text.UTF8Encoding($false)))

# ---------- SFX 配置 ----------
$configPath = Join-Path $work "config.txt"
$config = @"
;!@Install@!UTF-8!
Title="MAA 挂机控制台 v$Version"
BeginPrompt="MAA 挂机控制台 v$Version 安装`n将解压并运行安装脚本（MuMu 模拟器与 MAA 请预先安装）"
RunProgram="setup.bat"
;!@InstallEnd@!
"@
[System.IO.File]::WriteAllText($configPath, $config, (New-Object System.Text.UTF8Encoding($false)))

# ---------- 打包 + 拼接 ----------
$archive = Join-Path $work "payload.7z"
Write-Step "压缩项目文件..."
Push-Location $staging
& $exe7z a -t7z -m0=LZMA -mx=9 $archive "*" | Out-Null
Pop-Location
if (-not (Test-Path $archive)) { throw "7z 打包失败" }

$outName = "MAA-Farm-Console-v$Version-Setup.exe"
$outPath = Join-Path $OutputDir $outName
Write-Step "拼接自解压安装包 -> $outPath"
[System.IO.File]::WriteAllBytes($outPath, [System.IO.File]::ReadAllBytes($sfxPath))
$fs = [System.IO.File]::Open($outPath, "Append")
$bw = New-Object System.IO.BinaryWriter($fs)
$bw.Write([System.IO.File]::ReadAllBytes($configPath))
$bw.Write([System.IO.File]::ReadAllBytes($archive))
$bw.Close()
$fs.Close()

# ---------- 校验 ----------
$list = & $exe7z l $outPath
if ($LASTEXITCODE -ne 0) { Write-Warn "校验警告：7z 无法读取安装包内容，请检查" }
$size = (Get-Item $outPath).Length
Write-Ok "安装包已生成：$outPath（$([math]::Round($size/1KB,1)) KB）"

# 清理临时目录
Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
