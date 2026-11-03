# Telegram 精确基建配置

本功能只同步精确基建人员方案。它不启动/停止挂机，不处理账号密码、token、槽位、日志或其他配置。

所有者使用 `--owner-id` 配置自己的 Telegram 用户 ID；另一位使用 `--editor-id` 配置，并通过 `--editor-account` 绑定到一个账号。机器人会识别一图流/MAA 自定义基建 JSON。编辑者上传后会直接写入绑定账号；文件说明（caption）里的账号 ID 对编辑者无效，因此她不能借此操作其他账号。所有者上传仍生成草稿并返回预览，只有所有者发送 `/publish 草稿ID` 才会写入本地配置。

启动示例：

```powershell
python -m remote_control.telegram_bot `
  --token "$env:MAA_TELEGRAM_TOKEN" `
  --owner-id 123456789 `
  --editor-account-label A `
  --invites D:\1\.telegram\invites.json `
  --project D:\1 `
  --drafts D:\1\.telegram\drafts.json
```

你发送 `/invite` 生成绑定码，另一位发送 `/bind 绑定码` 即可自动绑定到 A；绑定码 10 分钟有效且只能使用一次。

机器人启动时会自动设置命令菜单。编辑者可从菜单选择 `/start`、`/upload`、`/config`、`/bind`；所有者额外拥有 `/invite` 和 `/publish`。选择 `/upload` 后直接发送基建 JSON 文件；选择 `/config` 可查看当前精确基建配置。编辑者只会看到绑定的 A 账号，所有者可看到全部账号，输出不包含密码、token、路径等敏感字段。

草稿文件只放在本地，不上传到 VPS。建议把机器人作为 Windows 计划任务/后台进程运行，使用环境变量保存 Bot Token；配置改动沿用 `config.json` 的原子写入和每日备份。
