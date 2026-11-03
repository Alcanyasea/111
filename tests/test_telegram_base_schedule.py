import json
import tempfile
import unittest
from pathlib import Path

from remote_control.telegram_base_schedule import RolePolicy, build_schedule_patch, DraftStore
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

    def test_editor_uploads_bound_account_but_cannot_publish(self):
        class Api:
            def __init__(self): self.sent = []
            def send(self, chat, text): self.sent.append(text)
            def download_document(self, file_id):
                rooms = {"manufacture": [{"product": "Pure Gold", "operators": []}] * 4, "trading": [{"product": "LMD", "operators": []}] * 2}
                return json.dumps({"plans": [{"period": [["00:00", "07:59"]], "rooms": rooms}]}).encode()
        with tempfile.TemporaryDirectory() as td:
            project = Path(__file__).parents[1]
            api = Api(); bot = BaseScheduleBot(api, self.cfg, project, DraftStore(Path(td) / "d.json"), RolePolicy("1", {"2": "a1"}))
            msg = {"from": {"id": 2}, "chat": {"id": 2}, "caption": "a1", "document": {"file_name": "base.json", "file_id": "f"}}
            bot.handle_message(msg)
            self.assertIn("草稿 ID", api.sent[-1])
            bot.handle_message({"from": {"id": 2}, "chat": {"id": 2}, "text": "/publish nope"})
            self.assertIn("只有所有者", api.sent[-1])


if __name__ == "__main__": unittest.main()
