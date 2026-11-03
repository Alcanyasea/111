"""Telegram-only precise base-schedule editor.

This module never starts or stops MAA.  It accepts only base-schedule JSON,
creates a local draft, and lets the owner publish it into config.json.
"""
import copy
import json
import os
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class RolePolicy:
    def __init__(self, owner_id, editor_accounts=None):
        self.owner_id = str(owner_id)
        self.editor_accounts = {str(k): str(v) for k, v in (editor_accounts or {}).items()}

    def is_owner(self, user_id):
        return str(user_id) == self.owner_id

    def bound_account(self, user_id):
        """Return the one account assigned to an editor, or None."""
        return self.editor_accounts.get(str(user_id))

    def account_for(self, user_id, account_id):
        user_id, account_id = str(user_id), str(account_id)
        if self.is_owner(user_id):
            return account_id
        if self.editor_accounts.get(user_id) == account_id:
            return account_id
        raise PermissionError("账号权限不足")

    def accounts_for(self, user_id, accounts):
        user_id = str(user_id)
        if self.is_owner(user_id):
            return list(accounts)
        bound = self.editor_accounts.get(user_id)
        return [a for a in accounts if str(a.get("id")) == bound]


def _load_base_plugin(project_root):
    plugin_dir = str(Path(project_root) / "plugins" / "base_schedule")
    if plugin_dir not in sys.path:
        sys.path.insert(0, plugin_dir)
    import base_schedule as plugin
    return plugin


def build_schedule_patch(cfg, account_id, raw_bytes, project_root=None):
    try:
        doc = json.loads(raw_bytes.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("文件不是有效的 UTF-8 JSON") from exc
    if not isinstance(doc, dict):
        raise ValueError("只接受基建排班 JSON")
    forbidden = {"accounts", "password", "username", "token", "uid", "paths", "notify", "appearance"}
    if forbidden.intersection(doc):
        raise ValueError("不能上传整份配置或含账号机密的文件")
    plugin = _load_base_plugin(project_root or Path(__file__).resolve().parents[1])
    result = plugin.convert_import_document(cfg, doc)
    account = next((a for a in cfg.get("accounts") or [] if str(a.get("id")) == str(account_id)), None)
    if not isinstance(account, dict):
        raise ValueError("账号不存在")
    current = copy.deepcopy(account.get("base_schedule") or {})
    current["enabled"] = True
    current["layout"] = result["layout"]
    current["batches"] = result["batches"]
    if result.get("drones") is not None:
        current["drones"] = result["drones"]
    label = account.get("label") or account_id
    summary = "账号「%s」：布局 %s，更新 %d 个批次" % (label, result["layout"], len(result["batches"]))
    return {"base_schedule": current}, summary


class DraftStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self):
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, value):
        tmp = self.path.with_suffix(".tmp-%d" % os.getpid())
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def create(self, user_id, account_id, patch, summary):
        drafts = self._read()
        draft_id = secrets.token_urlsafe(8)
        drafts[draft_id] = {"id": draft_id, "user_id": str(user_id), "account_id": str(account_id), "patch": patch, "summary": summary, "created_at": int(time.time()), "status": "draft"}
        self._write(drafts)
        return draft_id

    def take(self, draft_id, user_id, owner_id):
        if str(user_id) != str(owner_id):
            raise PermissionError("只有所有者可以发布配置")
        drafts = self._read(); draft = drafts.get(str(draft_id))
        if not draft or draft.get("status") != "draft":
            raise ValueError("草稿不存在或已发布")
        draft["status"] = "published"; draft["published_at"] = int(time.time()); drafts[str(draft_id)] = draft; self._write(drafts)
        return draft


class InviteStore:
    def __init__(self, path):
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self):
        if not self.path.exists(): return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8")); return value if isinstance(value, dict) else {}
        except (OSError, ValueError): return {}

    def _write(self, value):
        tmp = self.path.with_suffix(".tmp-%d" % os.getpid()); tmp.write_text(json.dumps(value), encoding="utf-8"); os.replace(tmp, self.path)

    def create(self, account_id, ttl=600):
        invites = self._read(); code = secrets.token_urlsafe(6).upper(); invites[code] = {"account_id": str(account_id), "expires_at": int(time.time()) + int(ttl), "used": False}; self._write(invites); return code

    def redeem(self, code, user_id):
        invites = self._read(); item = invites.get(str(code).strip().upper())
        if not item or item.get("used") or int(item.get("expires_at", 0)) < int(time.time()): raise ValueError("绑定码无效或已过期")
        item["used"] = True; item["user_id"] = str(user_id); invites[str(code).strip().upper()] = item; self._write(invites); return item["account_id"]


class TelegramAPI:
    def __init__(self, token, opener=urlopen):
        self.base = "https://api.telegram.org/bot" + token
        self.opener = opener

    def call(self, method, data=None, timeout=35):
        body = urlencode(data or {}).encode()
        req = Request(self.base + "/" + method, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
        with self.opener(req, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not payload.get("ok"):
            raise RuntimeError(payload.get("description", "Telegram API error"))
        return payload.get("result")

    def send(self, chat_id, text):
        return self.call("sendMessage", {"chat_id": str(chat_id), "text": text})

    def updates(self, offset=None):
        data = {"timeout": 25, "allowed_updates": json.dumps(["message"])}
        if offset is not None: data["offset"] = offset
        return self.call("getUpdates", data, timeout=35)

    def download_document(self, file_id):
        info = self.call("getFile", {"file_id": file_id})
        token = self.base.split("/bot", 1)[1]
        with self.opener("https://api.telegram.org/file/bot%s/%s" % (token, info["file_path"]), timeout=35) as response:
            return response.read()
