# ============================================================
# vision_lib.ps1 — MAA 同款屏幕识别封装（vision.py：模板匹配 + PaddleOCR）
# 识别算法与 MAA MaaCore 一致（cv2 matchTemplate / PP-OCR det+rec ONNX），
# 模板与模型在 vision\templates、vision\models（拷自 MAA resource，720p 基准）。
#
# 用法（先 dot-source 本文件）：
#   $vp = Start-Vision                            # 启动常驻识别进程（模型只加载一次）
#   Invoke-VisionScreenshot $adb $device $png     # MAA 同款 gzip 快速截图（失败回退裸 screencap）
#   $frame = Invoke-VisionFrame $vp $png @{       # 一帧多判定（模板 + OCR 一次返回）
#       templates = @(@{ name = "StartToWakeUp" })
#       ocr       = @(@{ name = "login"; text = @("账号登录") })
#   }
#   $frame.templates.StartToWakeUp.matched / .score / .x .y .w .h
#   $frame.ocr.login.matched / .hit / .center / .lines（ocr 项加 all=$true 返回全部行不做匹配）
#   Stop-Vision $vp
# 常驻进程随 PS 进程退出自动结束（管道 EOF）；意外退出时 Invoke-VisionFrame
# 返回 $null，调用方按「截图失败」节奏重试即可。
# 模板清单：vision\templates\manifest.json（roi/threshold 取自 MAA tasks.json）
# Python 解释器：gui\.venv（可经 config.json paths.python 覆盖）
# ============================================================
Add-Type -AssemblyName System.IO.Compression 2>$null

# config.json 统一读取（config_lib.ps1）
. (Join-Path $PSScriptRoot "config_lib.ps1")

$script:VisionPythonPath = $null

function Get-VisionPython {
    if ($script:VisionPythonPath) { return $script:VisionPythonPath }
    $py = "D:\1\gui\.venv\Scripts\python.exe"
    $cfg = Read-AppConfigJson "D:\1\config.json"
    if ($null -ne $cfg -and $null -ne $cfg.paths -and $cfg.paths.python) { $py = [string]$cfg.paths.python }
    $script:VisionPythonPath = $py
    return $py
}

function Start-Vision {
    # 启动 vision.py --serve；返回 @{ Proc; In; Out }，失败返回 $null
    $visionPy = Join-Path $PSScriptRoot "vision\vision.py"
    if (-not (Test-Path $visionPy)) { return $null }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = Get-VisionPython
    $psi.Arguments = ('"' + $visionPy + '" --serve')
    $psi.WorkingDirectory = $PSScriptRoot
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.CreateNoWindow = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8"
    try {
        $proc = [System.Diagnostics.Process]::Start($psi)
    } catch {
        return $null   # python 路径不存在等启动失败，调用方按「识别进程启动失败」处理
    }
    if (-not $proc) { return $null }
    return @{
        Proc = $proc
        In   = $proc.StandardInput
        Out  = $proc.StandardOutput
    }
}

function Stop-Vision($vision) {
    if ($vision -and -not $vision.Proc.HasExited) {
        try { $vision.In.Close() } catch {}
        try {
            if (-not $vision.Proc.WaitForExit(3000)) { $vision.Proc.Kill() }
        } catch {}
    }
}

function ConvertTo-AsciiJson([string]$s) {
    # stdin 编码无关化：非 ASCII 全部转成 \uXXXX（ConvertTo-Json 会保留中文原字符）
    return [regex]::Replace($s, '[^\x20-\x7E]', { param($m) ('\u{0:x4}' -f [int][char]$m.Value) })
}

function Invoke-VisionFrame($vision, $pngPath, $ops) {
    # 一帧多判定；进程意外退出返回 $null
    if (-not $vision -or $vision.Proc.HasExited) { return $null }
    $req = @{ image = $pngPath }
    foreach ($k in $ops.Keys) { $req[$k] = $ops[$k] }
    $json = ConvertTo-AsciiJson ($req | ConvertTo-Json -Depth 6 -Compress)
    $line = $null
    try {
        $vision.In.WriteLine($json)
        $line = $vision.Out.ReadLine()
    } catch { return $null }
    if (-not $line) { return $null }
    try { return ($line | ConvertFrom-Json) } catch { return $null }
}

function Invoke-VisionScreenshot($adb, $device, $pngPath) {
    # 快速截图：exec-out "screencap -p | gzip -1"（实测单帧 ~100-170ms）。
    # 注意必须带 -p：MuMu 的 screencap 不带 -p 输出裸 RGBA（不是 PNG），
    # 解出来 vision.py 读不了（2026-09-18 官服实测踩坑）。输出二进制必须走
    # 重定向文件（> 会损坏 PNG）；gzip 不可用时回退裸 screencap -p
    $gz = "$pngPath.gz"
    Start-Process -FilePath $adb -ArgumentList "-s `"$device`" exec-out `"screencap -p | gzip -1`"" -NoNewWindow -Wait -RedirectStandardOutput $gz
    if ((Test-Path $gz) -and (Get-Item $gz).Length -gt 100) {
        try {
            $in = [System.IO.File]::OpenRead($gz)
            $out = [System.IO.File]::Create($pngPath)
            $g = New-Object System.IO.Compression.GzipStream($in, [System.IO.Compression.CompressionMode]::Decompress)
            $g.CopyTo($out)
            $g.Dispose(); $out.Dispose(); $in.Dispose()
            Remove-Item $gz -Force
            return $true
        } catch {
            # 解压失败视为无效输出，走兜底
        }
    }
    Remove-Item $gz -Force -ErrorAction SilentlyContinue
    $tmpFile = "$pngPath.tmp"
    Start-Process -FilePath $adb -ArgumentList "-s `"$device`" exec-out screencap -p" -NoNewWindow -Wait -RedirectStandardOutput $tmpFile
    if ((Test-Path $tmpFile) -and (Get-Item $tmpFile).Length -gt 100) {
        Move-Item $tmpFile $pngPath -Force
        return $true
    }
    Remove-Item $tmpFile -Force -ErrorAction SilentlyContinue
    return $false
}

function Get-VisionFrameCenter($hit) {
    # 模板命中结果 → 中心点（Invoke-Tap 用）
    if (-not $hit -or -not $hit.matched) { return $null }
    return [PSCustomObject]@{ X = [int]($hit.x + $hit.w / 2); Y = [int]($hit.y + $hit.h / 2) }
}

function Get-OcrHit($ocrResult) {
    # ocr 命中结果 → 与旧 Find-OcrText 同形的 {X,Y,Text}，未命中 $null
    if (-not $ocrResult -or -not $ocrResult.matched) { return $null }
    return [PSCustomObject]@{
        X    = $ocrResult.center[0]
        Y    = $ocrResult.center[1]
        Text = $ocrResult.text
    }
}
