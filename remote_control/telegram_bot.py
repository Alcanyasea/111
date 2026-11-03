import json
from pathlib import Path

from .telegram_base_schedule import DraftStore, RolePolicy, TelegramAPI, build_schedule_patch


class BaseScheduleBot:
    def __init__(self, api, cfg, project_root, drafts, policy):
        self.api, self.cfg, self.project_root, self.drafts, self.policy = api, cfg, Path(project_root), drafts, policy

    def _account_label(self, account_id):
        return next((a.get("label") or account_id for a in self.cfg.get("accounts") or [] if str(a.get("id")) == str(account_id)), account_id)

    def handle_message(self, message):
        user_id = str(((message.get("from") or {}).get("id")))
        chat_id = message.get("chat", {}).get("id", user_id)
        text = str(message.get("text") or "").strip()
        if text == "/start":
            allowed = self.policy.is_owner(user_id) or user_id in self.policy.editor_accounts
            self.api.send(chat_id, "基建配置机器人已就绪。" if allowed else "你没有权限使用此机器人。")
            return
        if text.startswith("/publish "):
            if not self.policy.is_owner(user_id):
                self.api.send(chat_id, "只有所有者可以发布草稿。"); return
            draft_id = text.split(None, 1)[1].strip()
            try:
                draft = self.drafts.take(draft_id, user_id, self.policy.owner_id)
                account = next(a for a in self.cfg.get("accounts") or [] if str(a.get("id")) == draft["account_id"])
                account["base_schedule"] = draft["patch"]["base_schedule"]
                import sys
                sys.path.insert(0, str(self.project_root / "gui"))
                import config as appconfig
                appconfig.save(self.cfg)
                self.api.send(chat_id, "已发布到账号「%s」。" % self._account_label(draft["account_id"]))
            except Exception as exc:
                self.api.send(chat_id, "发布失败：%s" % str(exc))
            return
        document = message.get("document")
        if document:
            try:
                filename = str(document.get("file_name") or "").lower()
                if not filename.endswith(".json"): raise ValueError("只接受 .json 基建排班文件")
                account_id = str((message.get("caption") or "").strip())
                self.policy.account_for(user_id, account_id)
                patch, summary = build_schedule_patch(self.cfg, account_id, self.api.download_document(document["file_id"]), self.project_root)
                draft_id = self.drafts.create(user_id, account_id, patch, summary)
                self.api.send(chat_id, "%s\n草稿 ID：%s\n%s" % (summary, draft_id, "请所有者发送 /publish " + draft_id))
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
    p.add_argument("--token", required=True); p.add_argument("--owner-id", required=True); p.add_argument("--editor-id", action="append", default=[]); p.add_argument("--editor-account", required=True); p.add_argument("--project", required=True); p.add_argument("--drafts", required=True)
    args = p.parse_args()
    import sys
    sys.path.insert(0, str(Path(args.project) / "gui"))
    import config as appconfig
    policy = RolePolicy(args.owner_id, {x: args.editor_account for x in args.editor_id})
    BaseScheduleBot(TelegramAPI(args.token), appconfig.load(), args.project, DraftStore(args.drafts), policy).run()


if __name__ == "__main__": main()
