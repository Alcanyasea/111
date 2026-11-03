# DPAPI（CurrentUser）密码解密：config.accounts[].password 的 "dpapi:<base64>" 形式。
# 与 GUI 的 core/secret.py 使用同一套 Windows API（CryptProtectData），互相可读。
#
# - 没有 "dpapi:" 前缀 = 旧版明文配置，原样返回（保存一次设置后自动加密）。
# - 解密失败（换了 Windows 用户/机器、密文损坏）返回 $null：调用方必须按
#   「没有可用密码」处理（转人工登录），不能拿空串或密文反复去打登录接口。

function Unprotect-DpapiSecret([string]$Value) {
    if (-not $Value) { return "" }
    if (-not $Value.StartsWith("dpapi:", [StringComparison]::Ordinal)) { return $Value }
    try {
        $blob = [Convert]::FromBase64String($Value.Substring(6))
        $bytes = [System.Security.Cryptography.ProtectedData]::Unprotect(
            $blob, $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)
        return [Text.Encoding]::UTF8.GetString($bytes)
    } catch {
        return $null
    }
}
