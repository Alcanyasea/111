# ============================================================
# 游戏画面标记库 — game_update_wait.ps1 / login_check.ps1 共用
# 更新/公告等界面文字标记此前在两份脚本里各复制一份，改一处容易漏一处，
# 收敛到这里统一维护。依赖 ocr_lib.ps1（须先 dot-source）。
# 用法：. (Join-Path $scriptDir "game_state_lib.ps1")
# ============================================================

# 游戏更新进行中的界面文字标记（更新检查与登录检查共用同一套）
$GameUpdateMarkers = @(
    "正在获取更新", "获取更新配置", "获取资源更新配置", "更新配置",
    "开始下载更新", "开始下载", "正在下载更新", "正在下载", "下载更新包",
    "正在校验资源", "正在校验", "校验资源", "资源校验",
    "正在解压", "解压资源", "资源解压",
    "正在安装更新", "正在安装", "安装更新", "安装中",
    "正在更新", "正在更新资源", "更新中", "资源更新", "更新资源",
    "版本更新", "强制更新", "更新内容",
    "更新完成", "重新启动游戏", "正在重新启动",
    "更新下载失败", "下载更新失败", "更新资源损坏", "安装更新失败"
)
# 启动公告弹窗页签（弹窗正文也含“更新”字样，检测更新前须先排除公告页）
$AnnounceMarkers = @("活动公告", "系统公告", "资讯速报")

function Find-AnyMarker($words, $markers) {
    # 与「foreach 标记调 Find-OcrText」同语义，但只做一次行分组：
    # Find-OcrText 每次调用都重新按行分组，更新标记有 30 个，
    # 一张截图要多做 29 次无谓分组。按 markers 顺序返回第一个命中的
    # {X,Y,Name}（Name = 命中的标记），无命中返回 $null。
    if (-not $words -or @($words).Count -eq 0) { return $null }
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
    foreach ($m in $markers) {
        foreach ($ln in $lines) {
            $texts = @($ln.Words | ForEach-Object { $_.Text })
            $joined = $texts -join ''
            $idx = $joined.IndexOf($m)
            if ($idx -ge 0) {
                # 只取覆盖匹配串的词（同行可能混着多个按钮）
                $matched = @()
                $pos = 0
                foreach ($i in 0..($texts.Count - 1)) {
                    $wStart = $pos
                    $wEnd = $pos + $texts[$i].Length
                    $pos = $wEnd
                    if ($wEnd -gt $idx -and $wStart -lt ($idx + $m.Length)) {
                        $matched += $ln.Words[$i]
                    }
                }
                if ($matched.Count -eq 0) { $matched = @($ln.Words) }
                $x0 = ($matched | ForEach-Object { $_.X } | Measure-Object -Minimum).Minimum
                $x1 = ($matched | ForEach-Object { $_.X + $_.W } | Measure-Object -Maximum).Maximum
                $y0 = ($matched | ForEach-Object { $_.Y } | Measure-Object -Minimum).Minimum
                $y1 = ($matched | ForEach-Object { $_.Y + $_.H } | Measure-Object -Maximum).Maximum
                return [PSCustomObject]@{ X = [int](($x0 + $x1) / 2); Y = [int](($y0 + $y1) / 2); Name = $m }
            }
        }
    }
    return $null
}
