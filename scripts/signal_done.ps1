# MAA 任务完成信号 — 由 MAA 的 EndsWithScript 调用
# 创建一个文件告诉主控脚本 "MAA跑完了"
New-Item -Path "D:\1\scripts\maa_done.signal" -Force | Out-Null
Write-Output "MAA done signal sent"
