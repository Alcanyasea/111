# 一键运行单元测试（unittest 标准库，零第三方依赖）
# 用法：pwsh -File run_tests.ps1   或在 CI 里直接：python -m unittest discover -s tests -v
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

$py = Join-Path $root "gui\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = Join-Path $root "gui\runtime\python.exe" }
if (-not (Test-Path $py)) {
    $py = "python"
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) { $py = "py" }
}

& $py -m unittest discover -s $root -v
exit $LASTEXITCODE
