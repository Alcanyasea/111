# Telegram 精确基建配置

本功能只同步精确基建人员方案。它不启动/停止挂机，不处理账号密码、token、槽位、日志或其他配置。

所有者使用 `--owner-id` 配置自己的 Telegram 用户 ID；另一位使用 `--editor-id` 配置，并通过 `--editor-account` 绑定到一个账号。编辑者上传 JSON 时，把目标账号 ID 放在 Telegram 文件说明（caption）里。机器人会识别一图流/MAA 自定义基建 JSON，生成草稿并返回预览；只有所有者发送 `/publish 草稿ID` 才会写入本地配置。

启动示例：

```powershell
python -m remote_control.telegram_bot `
  --token "$env:MAA_TELEGRAM_TOKEN" `
  --owner-id 123456789 `
  --editor-id 987654321 `
  --editor-account-label A `
  --project D:\1 `
  --drafts D:\1\.telegram\drafts.json
```

草稿文件只放在本地，不上传到 VPS。建议把机器人作为 Windows 计划任务/后台进程运行，使用环境变量保存 Bot Token；配置改动沿用 `config.json` 的原子写入和每日备份。
