import json
import copy
from pathlib import Path

from .telegram_base_schedule import DraftStore, InviteStore, RolePolicy, TelegramAPI, build_schedule_patch


class BaseScheduleBot:
    def __init__(self, api, cfg, project_root, drafts, policy, invites=None, invite_account=None):
        self.api, self.cfg, self.project_root, self.drafts, self.policy, self.invites, self.invite_account = api, cfg, Path(project_root), drafts, policy, invites, invite_account

    def _account_label(self, account_id):
        return next((a.get("label") or account_id for a in self.cfg.get("accounts") or [] if str(a.get("id")) == str(account_id)), account_id)

    def _save_account_patch(self, account_id, patch):
        """Save a candidate config atomically, then update the in-memory config."""
        candidate = copy.deepcopy(self.cfg)
        account = next(a for a in candidate.get("accounts") or [] if str(a.get("id")) == str(account_id))
        account["base_schedule"] = patch["base_schedule"]
        import sys
        sys.path.insert(0, str(self.project_root / "gui"))
        import config as appconfig
        appconfig.save(candidate)
        self.cfg.clear()
        self.cfg.update(candidate)

    def configure_commands(self):
        """Install compact menus; owner gets publishing and invite commands."""
        editor_commands = [
            {"command": "start", "description": "查看机器人状态"},
            {"command": "upload", "description": "上传基建配置 JSON"},
            {"command": "config", "description": "查看当前基建配置"},
            {"command": "bind", "description": "使用绑定码关联账号"},
        ]
        owner_commands = editor_commands[:3] + [
            {"command": "invite", "description": "生成编辑者绑定码"},
            {"command": "publish", "description": "发布草稿到本地配置"},
        ]
        self.api.set_my_commands(editor_commands)
        self.api.set_my_commands(owner_commands, {"type": "chat", "chat_id": self.policy.owner_id})

    def _config_messages(self, user_id):
        if not (self.policy.is_owner(user_id) or self.policy.bound_account(user_id)):
            raise PermissionError("账号权限不足")
        messages = []
        for account in self.policy.accounts_for(user_id, self.cfg.get("accounts") or []):
            safe = {
                "label": account.get("label") or account.get("id"),
                "base_schedule": copy.deepcopy(account.get("base_schedule") or {}),
            }
            if self.policy.is_owner(user_id):
                safe["id"] = str(account.get("id"))
            messages.append(json.dumps(safe, ensure_ascii=False, indent=2))
        return messages or ["暂无可查看的账号配置。"]

    def _send_config(self, chat_id, user_id):
        for body in self._config_messages(user_id):
            text = "当前基建配置：\n" + body
            for start in range(0, len(text), 3500):
                self.api.send(chat_id, text[start:start + 3500])

    def handle_message(self, message):
        user_id = str(((message.get("from") or {}).get("id")))
        chat_id = message.get("chat", {}).get("id", user_id)
        text = str(message.get("text") or "").strip()
        if text == "/start":
            allowed = self.policy.is_owner(user_id) or user_id in self.policy.editor_accounts
            self.api.send(chat_id, "基建配置机器人已就绪。" if allowed else "你没有权限使用此机器人。")
            return
        if text == "/upload":
            self.api.send(chat_id, "请直接发送基建排班 JSON 文件。编辑者会写入绑定账号；所有者会生成草稿。")
            return
        if text == "/config":
            try:
                self._send_config(chat_id, user_id)
            except Exception as exc:
                self.api.send(chat_id, "查看失败：%s" % str(exc))
            return
        if text == "/invite":
            if not self.policy.is_owner(user_id): self.api.send(chat_id, "只有所有者可以生成绑定码。"); return
            code = self.invites.create(self.invite_account)
            self.api.send(chat_id, "绑定码（10分钟内有效、只能使用一次）：\n" + code); return
        if text.startswith("/bind "):
            try:
                account_id = self.invites.redeem(text.split(None, 1)[1], user_id); self.policy.editor_accounts[user_id] = account_id
                self.api.send(chat_id, "绑定成功，只能管理账号「%s」。" % self._account_label(account_id))
            except Exception as exc: self.api.send(chat_id, "绑定失败：%s" % exc)
            return
        if text.startswith("/publish "):
            if not self.policy.is_owner(user_id):
                self.api.send(chat_id, "只有所有者可以发布草稿。"); return
            draft_id = text.split(None, 1)[1].strip()
            try:
                draft = self.drafts.take(draft_id, user_id, self.policy.owner_id)
                self._save_account_patch(draft["account_id"], draft["patch"])
                self.api.send(chat_id, "已发布到账号「%s」。" % self._account_label(draft["account_id"]))
            except Exception as exc:
                self.api.send(chat_id, "发布失败：%s" % str(exc))
            return
        document = message.get("document")
        if document:
            try:
                filename = str(document.get("file_name") or "").lower()
                if not filename.endswith(".json"): raise ValueError("只接受 .json 基建排班文件")
                if self.policy.is_owner(user_id):
                    account_id = str((message.get("caption") or "").strip())
                    self.policy.account_for(user_id, account_id)
                else:
                    account_id = self.policy.bound_account(user_id)
                    if not account_id:
                        raise PermissionError("账号权限不足")
                patch, summary = build_schedule_patch(self.cfg, account_id, self.api.download_document(document["file_id"]), self.project_root)
                if self.policy.is_owner(user_id):
                    draft_id = self.drafts.create(user_id, account_id, patch, summary)
                    self.api.send(chat_id, "%s\n草稿 ID：%s\n%s" % (summary, draft_id, "请所有者发送 /publish " + draft_id))
                else:
                    self._save_account_patch(account_id, patch)
                    self.api.send(chat_id, summary + "\n已直接写入本地配置。")
            except Exception as exc:
                self.api.send(chat_id, "上传失败：%s" % str(exc))

    def run(self):
        offset = None
        while True:
            for item in self.api.updates(offset):
                offset = item["update_id"] + 1
                self.handle_message(item.get("message") or {})


def main():
    import argparse
    p = argparse.ArgumentParser(description="Telegram 精确基建配置机器人")
    p.add_argument("--token", default=None); p.add_argument("--token-env", default="MAA_TELEGRAM_TOKEN"); p.add_argument("--owner-id", required=True); p.add_argument("--editor-id", action="append", default=[]); p.add_argument("--editor-account", default=None); p.add_argument("--editor-account-label", default=None); p.add_argument("--project", required=True); p.add_argument("--drafts", required=True); p.add_argument("--invites", required=True)
    args = p.parse_args()
    import os
    token = args.token or os.environ.get(args.token_env)
    if not token:
        p.error("需要 --token 或环境变量 " + args.token_env)
    import sys
    sys.path.insert(0, str(Path(args.project) / "gui"))
    import config as appconfig
    editor_account = args.editor_account
    if not editor_account and args.editor_account_label:
        matches = [a for a in appconfig.load().get("accounts") or [] if str(a.get("label") or "") == args.editor_account_label]
        if len(matches) != 1:
            p.error("账号名称未唯一匹配：%s" % args.editor_account_label)
        editor_account = str(matches[0].get("id"))
    if not editor_account:
        p.error("需要 --editor-account 或 --editor-account-label")
    policy = RolePolicy(args.owner_id, {x: editor_account for x in args.editor_id})
    bot = BaseScheduleBot(TelegramAPI(token), appconfig.load(), args.project, DraftStore(args.drafts), policy, InviteStore(args.invites), editor_account)
    bot.configure_commands()
    bot.run()


if __name__ == "__main__": main()
