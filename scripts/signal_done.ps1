# MAA 任务完成信号 — 由 MAA 的 EndsWithScript 调用
# 创建一个文件告诉主控脚本 "MAA跑完了"
New-Item -Path (Join-Path $PSScriptRoot "maa_done.signal") -Force | Out-Null

# 顺带记一行日志：MAA 配置里的回调失效（路径写错/解释器找不到）时，
# 主控日志里能直接看出来，而不是只在「等不到信号」时才暴露
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
try {
    Add-Content -Path (Join-Path $PSScriptRoot "master_log.txt") `
        -Value "$ts - [Signal] MAA 完成信号已写入 maa_done.signal" `
        -Encoding UTF8 -ErrorAction SilentlyContinue
} catch {}

Write-Output "MAA done signal sent"
