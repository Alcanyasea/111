import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from remote_control.telegram_base_schedule import RolePolicy, build_schedule_patch, DraftStore, InviteStore
from remote_control.telegram_bot import BaseScheduleBot


class TelegramBaseScheduleTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "schedule": {"times": [{"time": "04:00"}, {"time": "16:00"}]},
            "accounts": [
                {"id": "a1", "label": "官服1", "base_schedule": {"enabled": False}},
                {"id": "a2", "label": "官服2", "base_schedule": {"enabled": False}},
            ],
        }

    def test_owner_can_edit_all_and_editor_only_assigned_account(self):
        policy = RolePolicy(owner_id="1", editor_accounts={"2": "a1"})
        self.assertEqual(policy.account_for("1", "a2"), "a2")
        self.assertEqual(policy.account_for("2", "a1"), "a1")
        with self.assertRaises(PermissionError): policy.account_for("2", "a2")
        with self.assertRaises(PermissionError): policy.account_for("9", "a1")

    def test_upload_rejects_full_config_secrets(self):
        raw = json.dumps({"accounts": [{"password": "secret"}]}).encode()
        with self.assertRaises(ValueError): build_schedule_patch(self.cfg, "a1", raw)

    def test_upload_converts_maa_plan_to_account_patch(self):
        rooms = {"manufacture": [{"product": "Pure Gold", "operators": ["能天使"]}] * 4, "trading": [{"product": "LMD", "operators": []}] * 2}
        raw = json.dumps({"plans": [{"name": "夜班", "period": [["00:00", "07:59"]], "rooms": rooms}]}).encode()
        patch, summary = build_schedule_patch(self.cfg, "a1", raw)
        self.assertTrue(patch["base_schedule"]["enabled"])
        self.assertEqual(patch["base_schedule"]["batches"]["4点"]["manufacture"][0]["operators"][0], "能天使")
        self.assertIn("官服1", summary)

    def test_draft_store_publish_requires_owner(self):
        with tempfile.TemporaryDirectory() as td:
            store = DraftStore(Path(td) / "drafts.json")
            draft_id = store.create("2", "a1", {"enabled": True}, "preview")
            with self.assertRaises(PermissionError): store.take(draft_id, "2", "1")
            draft = store.take(draft_id, "1", "1")
            self.assertEqual(draft["account_id"], "a1")

    def test_editor_uploads_bound_account_directly(self):
        class Api:
            def __init__(self): self.sent = []
            def send(self, chat, text): self.sent.append(text)
            def download_document(self, file_id):
                rooms = {"manufacture": [{"product": "Pure Gold", "operators": []}] * 4, "trading": [{"product": "LMD", "operators": []}] * 2}
                return json.dumps({"plans": [{"period": [["00:00", "07:59"]], "rooms": rooms}]}).encode()
        with tempfile.TemporaryDirectory() as td:
            project = Path(__file__).parents[1]
            api = Api(); bot = BaseScheduleBot(api, self.cfg, project, DraftStore(Path(td) / "d.json"), RolePolicy("1", {"2": "a1"}))
            msg = {"from": {"id": 2}, "chat": {"id": 2}, "caption": "a2", "document": {"file_name": "base.json", "file_id": "f"}}
            sys.path.insert(0, str(project / "gui"))
            try:
                import config as appconfig
                with patch.object(appconfig, "save") as save:
                    bot.handle_message(msg)
            finally:
                sys.path.remove(str(project / "gui"))
            self.assertTrue(self.cfg["accounts"][0]["base_schedule"]["enabled"])
            self.assertFalse(self.cfg["accounts"][1]["base_schedule"]["enabled"])
            save.assert_called_once_with(self.cfg)
            self.assertNotIn("草稿 ID", api.sent[-1])
            self.assertIn("官服1", api.sent[-1])
            self.assertFalse((Path(td) / "d.json").exists())

    def test_owner_invite_code_binds_one_user_once(self):
        with tempfile.TemporaryDirectory() as td:
            store = InviteStore(Path(td) / "invites.json")
            code = store.create("a1", ttl=600)
            self.assertEqual(store.redeem(code, "editor-1"), "a1")
            with self.assertRaises(ValueError): store.redeem(code, "editor-2")

    def test_owner_generates_code_and_recipient_binds_automatically(self):
        class Api:
            def __init__(self): self.sent = []
            def send(self, chat, text): self.sent.append(text)
        with tempfile.TemporaryDirectory() as td:
            api = Api(); policy = RolePolicy("1"); invites = InviteStore(Path(td) / "i.json")
            from remote_control.telegram_bot import BaseScheduleBot
            bot = BaseScheduleBot(api, self.cfg, Path(td), DraftStore(Path(td) / "d.json"), policy, invites, "a1")
            bot.handle_message({"from": {"id": "1"}, "chat": {"id": "1"}, "text": "/invite"})
            code = api.sent[-1].splitlines()[-1]
            bot.handle_message({"from": {"id": "2"}, "chat": {"id": "2"}, "text": "/bind " + code})
            self.assertEqual(policy.editor_accounts["2"], "a1")

    def test_command_menu_separates_owner_commands_from_editor_commands(self):
        class Api:
            def __init__(self): self.menus = []
            def set_my_commands(self, commands, scope=None): self.menus.append((commands, scope))

        api = Api()
        bot = BaseScheduleBot(api, self.cfg, Path("."), DraftStore(Path(tempfile.mkdtemp()) / "d.json"), RolePolicy("1", {"2": "a1"}))
        bot.configure_commands()
        self.assertEqual(len(api.menus), 2)
        default, owner = api.menus
        self.assertEqual([item["command"] for item in default[0]], ["start", "upload", "bind"])
        self.assertEqual([item["command"] for item in owner[0]], ["start", "upload", "invite", "publish"])
        self.assertEqual(owner[1], {"type": "chat", "chat_id": "1"})


if __name__ == "__main__": unittest.main()
