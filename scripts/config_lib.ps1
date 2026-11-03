# ============================================================
# config.json 统一读取库 — 所有 scripts\ 下 PowerShell 脚本共用
# （此前「读 config.json → ConvertFrom-Json」的前导块在 8 个脚本各复制一份，
#   改一处漏一处的温床，v4.1 收敛到这里）
# 用法：
#   . (Join-Path $scriptDir "config_lib.ps1")
#   $config = Read-AppConfigJson "D:\1\config.json"
#   if ($config) { ...字段级回退由调用方自行处理（各脚本所需字段不同）... }
# 注意：config.json 由 GUI 生成；PS 5.1 的 Get-Content -Raw 会按 ANSI 解码
# 导致中文路径乱码，必须显式 UTF-8（本函数已处理）。文件缺失/损坏返回 $null，
# 调用方走各自的硬编码默认值（与历史行为一致）。
# ============================================================

function Read-AppConfigJson([string]$Path) {
    if (-not $Path -or -not (Test-Path $Path)) { return $null }
    try {
        $cfg = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8) |
            ConvertFrom-Json
        # 与 plugins/project_paths.py 一致：旧目录即使还在，也不能调用旧副本。
        # 只修正内存中的项目内部路径，保留外部软件和自定义日志位置。
        if ($cfg -is [pscustomobject]) {
            if ($cfg.paths -isnot [pscustomobject]) {
                $cfg | Add-Member -NotePropertyName paths -NotePropertyValue ([pscustomobject]@{}) -Force
            }
            $scripts = Join-Path (Split-Path -Parent ([IO.Path]::GetFullPath($Path))) 'scripts'
            $oldScripts = $cfg.paths.script_dir
            $oldLog = $cfg.paths.log_file
            if (-not $oldLog -or ($oldScripts -and
                    [IO.Path]::GetFullPath($oldLog) -eq
                    [IO.Path]::GetFullPath((Join-Path $oldScripts 'master_log.txt')))) {
                $cfg.paths | Add-Member -NotePropertyName log_file -NotePropertyValue (Join-Path $scripts 'master_log.txt') -Force
            }
            $cfg.paths | Add-Member -NotePropertyName script_dir -NotePropertyValue $scripts -Force
        }
        return $cfg
    } catch { return $null }
}
