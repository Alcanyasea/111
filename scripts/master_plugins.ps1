# master_plugins.ps1 — 子进程调度层：插件调用 / pwsh 子脚本拉起（心跳看门狗）/
# 槽位回拉。由 master.ps1 dot-source 引入（依赖 master_lib.ps1 已先加载的
# $venvPython / $scriptDir / $adb / $device / Log 等变量与函数）。

# ---- 插件调用模板：统一「venv/脚本存在检查 → 调用 → 逐行记日志 → 退出码检查」。
# 不可用/失败返回 false，由调用方决定中止账号或仅记录警告（例如通知）。
# $tag 用作每行插件输出的日志前缀；$name 用于告警文本；$missNote 是不可用时的后果说明。
function Invoke-Plugin($pyPath, $tag, $name, $argList, $missNote) {
    # 插件自报的 ERROR 明细（如「任务队列缺少 Fight」）存这里，供失败通知引用
    $script:LastPluginError = ""
    if (-not (Test-Path $venvPython) -or -not (Test-Path $pyPath)) {
        Log ("  [WARN] " + $name + "不可用（venv python 或脚本缺失），" + $missNote)
        return $false
    }
    # 插件输出统一 UTF-8：控制台代码页为 cp1252 等编不了中文的环境下，插件
    # print 中文会 UnicodeEncodeError 直接崩（CI 实测）。只对这一次子进程调用
    # 做作用域内 UTF-8（子进程编码与父进程解码两端同时切），不影响 adb 等
    # 其它命令的解码
    $oldConsoleEnc = [Console]::OutputEncoding
    $oldPythonEncoding = $env:PYTHONIOENCODING
    $env:PYTHONIOENCODING = "utf-8"
    try {
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
        $out = & $venvPython $pyPath @argList 2>&1
        $pluginExitCode = $LASTEXITCODE
    } catch {
        $script:LastPluginError = $_.Exception.Message
        Log ("  [WARN] " + $name + "启动失败，" + $missNote)
        return $false
    } finally {
        [Console]::OutputEncoding = $oldConsoleEnc
        $env:PYTHONIOENCODING = $oldPythonEncoding
    }
    foreach ($l in $out) {
        $line = [string]$l
        if ($line) {
            if ($line -match '^ERROR\s+(.+)$') { $script:LastPluginError = $Matches[1] }
            Log ("  [" + $tag + "] " + $line)
        }
    }
    if ($pluginExitCode -ne 0) {
        Log ("  [WARN] " + $name + "执行失败（exit " + $pluginExitCode + "），" + $missNote)
        return $false
    }
    return $true
}

# ---- 基建收菜不再备份/恢复整份配置（2026-09-17 起）：收菜用独立「收菜」方案，
# farm 用的 Default 全程不被触碰；收菜结束由 infrast_collect.py restore 切回
# Default，即使中断，farm_guard 也会在下次挂机启动前强制切回。

# 子脚本统一用 pwsh 绝对路径拉起：计划任务环境不保证按 PATH 解析裸 pwsh.exe
# （商店版执行别名更解析不到，0x80070002）；本机已换 MSI 版固定在 Program Files。
# 后台子进程用 pwsh：安装包内置便携版优先，其次系统安装，最后裸名兜底
$pwshExe = Join-Path $root "gui\runtime\pwsh\pwsh.exe"
if (-not (Test-Path $pwshExe)) { $pwshExe = "C:\Program Files\PowerShell\7\pwsh.exe" }
if (-not (Test-Path $pwshExe)) { $pwshExe = "pwsh" }

# 读取子进程重定向文件。子脚本的输出编码取决于启动环境：bat 里 chcp 65001
# 启动是 UTF-8，GUI/计划任务继承 GBK 控制台。先按严格 UTF-8 解码，失败回退
# GBK，两种情况中文都能还原（此前统一按 UTF-8 读，GBK 时日志全是 U+FFFD）
function Read-ChildText([string]$Path) {
    if (-not (Test-Path $Path)) { return @() }
    # 子脚本退出后句柄可能仍被孙进程持有（adb server 等守护进程会继承重定向
    # 句柄），ReadAllBytes 的共享模式此时撞 sharing violation：切换日志丢失、
    # tmp 残留（2026-10-27 实测）。显式以「允许他人读写删」共享模式打开并短
    # 重试；彻底失败只告警返回空，绝不抛异常打断主流程的退出码处理。
    $bytes = $null
    for ($attempt = 1; $attempt -le 4; $attempt++) {
        try {
            $fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete)
            try {
                $len = [int]$fs.Length
                $bytes = [byte[]]::new($len)
                $read = 0
                while ($read -lt $len) {
                    $n = $fs.Read($bytes, $read, $len - $read)
                    if ($n -le 0) { break }
                    $read += $n
                }
            } finally {
                $fs.Dispose()
            }
            break
        } catch {
            if ($attempt -eq 4) {
                Log ("  [WARN] 子进程输出读取失败（文件仍被占用，已重试 3 次）: " + (Split-Path $Path -Leaf))
                return @()
            }
            Start-Sleep -Milliseconds 800
        }
    }
    if ($null -eq $bytes -or $bytes.Length -eq 0) { return @() }
    try {
        $text = [System.Text.UTF8Encoding]::new($false, $true).GetString($bytes)
    } catch {
        $text = [System.Text.Encoding]::GetEncoding(936).GetString($bytes)
    }
    $text = $text.TrimStart([char]0xFEFF)
    return ($text -split "`r?`n")
}

. (Join-Path $PSScriptRoot "master_process.ps1")
