# 账号卡片专用入口。旧 master 会静默忽略陌生参数并运行整个班次。
param(
    [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$AccountId,
    [switch]$InfrastCollect
)
$ErrorActionPreference = 'Stop'
$master = Get-Command -Name (Join-Path $PSScriptRoot 'master.ps1') -CommandType ExternalScript
if (-not $master.Parameters.ContainsKey('AccountId')) {
    throw '当前 master.ps1 缺少单账号任务入口，请同步更新控制台和脚本。'
}
& $master -AccountId $AccountId -InfrastCollect:$InfrastCollect -NoShutdown
exit $LASTEXITCODE
