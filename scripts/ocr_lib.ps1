# ============================================================
# OCR 辅助库 — 主机端 Windows OCR（WinRT），供 capture_account.ps1 使用
# 用途：识别模拟器截图上的文字并返回像素坐标，用于「找按钮 → 点击」。
# 依赖：Windows 10/11 + 系统已安装中文识别语言包（控制台本机满足）。
# 用法：. "$PSScriptRoot\ocr_lib.ps1"
#   Ocr-Screenshot $adb $device $pngPath          # 截屏（PNG 二进安全）
#   $words = Get-OcrWords $pngPath                # 词列表：Text/X/Y/W/H
#   Find-OcrText $words "确认"                    # 找文本（行内子串），返回中心坐标或 $null
#   Find-OcrText $words "登录" -Exact             # 整行精确匹配（区分「登录」按钮与「账号登录」）
# ============================================================
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$script:asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]
# OCR 引擎创建很慢（WinRT 语言包初始化），全程只建一次，大幅降低每轮截图识别耗时
$script:ocrEngine = $null

function Await-WinRt($WinRtTask, $ResultType) {
    $asTask = $script:asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}

function Get-OcrResult($path) {
    $null = [Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
    $null = [Windows.Storage.StorageFile,Windows.Foundation,ContentType=WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime]
    $file = Await-WinRt ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
    $stream = Await-WinRt ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await-WinRt ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bmp = Await-WinRt ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    if ($null -eq $script:ocrEngine) {
        $script:ocrEngine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    }
    return Await-WinRt ($script:ocrEngine.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
}

function Get-OcrWords($path) {
    $result = Get-OcrResult $path
    $words = @()
    foreach ($line in $result.Lines) {
        foreach ($w in $line.Words) {
            $r = $w.BoundingRect   # 已是像素坐标（相对截图）
            $words += [PSCustomObject]@{
                Text = $w.Text
                X = [int][math]::Round($r.X)
                Y = [int][math]::Round($r.Y)
                W = [int][math]::Round($r.Width)
                H = [int][math]::Round($r.Height)
            }
        }
    }
    return @($words)
}

function Ocr-Screenshot($adb, $device, $pngPath) {
    # adb exec-out screencap 输出是二进制，必须走重定向文件（> 会损坏 PNG）
    $tmpFile = "$pngPath.tmp"
    Start-Process -FilePath $adb -ArgumentList "-s `"$device`" exec-out screencap -p" -NoNewWindow -Wait -RedirectStandardOutput $tmpFile
    if ((Test-Path $tmpFile) -and (Get-Item $tmpFile).Length -gt 100) {
        Move-Item $tmpFile $pngPath -Force
        return $true
    }
    Remove-Item $tmpFile -Force -ErrorAction SilentlyContinue
    return $false
}

function Find-OcrText($words, $target, [switch]$Exact) {
    # 按行分组（Y 中心差 < 词高一半视为同行），行内拼接后匹配
    $lines = @()
    foreach ($w in $words) {
        $cy = $w.Y + $w.H / 2
        $placed = $false
        foreach ($ln in $lines) {
            if ([math]::Abs($ln.CenterY - $cy) -lt [math]::Max(10, $ln.MaxH / 2)) {
                $ln.Words += $w
                $ln.MaxH = [math]::Max($ln.MaxH, $w.H)
                $placed = $true
                break
            }
        }
        if (-not $placed) {
            $lines += [PSCustomObject]@{ CenterY = $cy; MaxH = $w.H; Words = @($w) }
        }
    }
    foreach ($ln in $lines) {
        $texts = @($ln.Words | ForEach-Object { $_.Text })
        $joined = $texts -join ''
        $idx = -1
        if ($Exact) { if ($joined -ceq $target) { $idx = 0 } }
        else { $idx = $joined.IndexOf($target) }
        if ($idx -ge 0) {
            # 只取覆盖匹配串的词（同行可能混着多个按钮，如「本机号码登录」与「密码登录」）
            $matched = @()
            $pos = 0
            foreach ($i in 0..($texts.Count - 1)) {
                $wStart = $pos
                $wEnd = $pos + $texts[$i].Length
                $pos = $wEnd
                if ($wEnd -gt $idx -and $wStart -lt ($idx + $target.Length)) {
                    $matched += $ln.Words[$i]
                }
            }
            if ($matched.Count -eq 0) { $matched = @($ln.Words) }
            $x0 = ($matched | ForEach-Object { $_.X } | Measure-Object -Minimum).Minimum
            $x1 = ($matched | ForEach-Object { $_.X + $_.W } | Measure-Object -Maximum).Maximum
            $y0 = ($matched | ForEach-Object { $_.Y } | Measure-Object -Minimum).Minimum
            $y1 = ($matched | ForEach-Object { $_.Y + $_.H } | Measure-Object -Maximum).Maximum
            return [PSCustomObject]@{ X = [int](($x0 + $x1) / 2); Y = [int](($y0 + $y1) / 2); Text = $joined }
        }
    }
    return $null
}
