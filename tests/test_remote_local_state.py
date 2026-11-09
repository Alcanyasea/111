import json
import os
import socket
import tempfile
import time
import unittest
from pathlib import Path

from remote_control.local_state import LocalState, RevisionConflict


class LocalStateTests(unittest.TestCase):
    def test_patch_creates_backup_and_rejects_stale_revision(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root / "config.json").write_text(json.dumps({"remote_revision": 1, "accounts": []}), encoding="utf-8")
            state = LocalState(root)
            with self.assertRaises(RevisionConflict): state.apply_patch({"remote_revision": 2}, 0)
            new_revision = state.apply_patch({"remote_revision": 2}, 1)
            self.assertEqual(new_revision, 2)
            self.assertTrue(list(root.glob("config.json.remote-bak-*")))

    def test_patch_rejects_nested_secrets_and_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root / "config.json").write_text(json.dumps({"remote_revision": 1, "accounts": []}), encoding="utf-8")
            state = LocalState(root)
            with self.assertRaises(ValueError): state.apply_patch({"accounts": [{"password": "bad"}]}, 1)
            with self.assertRaises(ValueError): state.apply_patch({"paths": {"script_dir": "C:\\evil"}}, 1)

    def test_patch_merges_accounts_and_schedule_without_dropping_fields(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            original = {
                "remote_revision": 1,
                "accounts": [
                    {"id": "a", "label": "A", "username": "u", "password": "p", "slot": "slot-a",
                     "enabled": True, "base_schedule": {"enabled": False, "layout": "333"}},
                    {"id": "b", "label": "B", "username": "u2", "password": "p2", "slot": "slot-b"},
                ],
                "schedule": {"times": [{"time": "04:00", "enabled": True}], "collect_times": [{"time": "12:00"}]},
            }
            (root / "config.json").write_text(json.dumps(original), encoding="utf-8")
            state = LocalState(root)
            state.apply_patch({
                "accounts": [{"id": "a", "enabled": False, "base_schedule": {"enabled": True}}],
                "schedule": {"times": [{"time": "16:00", "enabled": True}]},
            }, 1)
            data = json.loads((root / "config.json").read_text(encoding="utf-8"))
            self.assertEqual([a["id"] for a in data["accounts"]], ["a", "b"])
            self.assertEqual(data["accounts"][0]["username"], "u")
            self.assertEqual(data["accounts"][0]["password"], "p")
            self.assertEqual(data["accounts"][0]["slot"], "slot-a")
            self.assertFalse(data["accounts"][0]["enabled"])
            self.assertTrue(data["accounts"][0]["base_schedule"]["enabled"])
            self.assertEqual(data["accounts"][0]["base_schedule"]["layout"], "333")
            self.assertEqual(data["accounts"][1]["label"], "B")
            self.assertEqual(data["schedule"]["times"], [{"time": "16:00", "enabled": True}])
            self.assertEqual(data["schedule"]["collect_times"], [{"time": "12:00"}])

    def test_patch_rejects_null_empty_and_unknown_accounts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "config.json").write_text(json.dumps({
                "remote_revision": 1, "accounts": [{"id": "a", "label": "A"}],
            }), encoding="utf-8")
            state = LocalState(root)
            with self.assertRaises(ValueError): state.apply_patch({"accounts": None}, 1)
            with self.assertRaises(ValueError): state.apply_patch({"accounts": []}, 1)
            with self.assertRaises(ValueError): state.apply_patch({"accounts": [{"id": "missing", "label": "X"}]}, 1)

    def test_stale_lock_from_dead_process_is_recovered(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "config.json").write_text(json.dumps({"remote_revision": 1, "accounts": []}), encoding="utf-8")
            lock = root / "config.remote.lock"
            lock.write_text(json.dumps({
                "pid": 99999999, "host": socket.gethostname(),
                "created": time.time() - 3600, "token": "old",
            }), encoding="utf-8")
            os.utime(lock, (time.time() - 3600, time.time() - 3600))
            self.assertEqual(LocalState(root).apply_patch({}, 1), 2)
            self.assertFalse(lock.exists())

    def test_snapshot_reports_local_slot_presence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); cfg = {"remote_revision": 1, "accounts": [{"id": "a", "label": "A", "slot": "slot-a"}]}
            (root / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
            (root / "scripts" / "accounts" / "slot-a").mkdir(parents=True)
            snapshot = LocalState(root).read_snapshot()
            self.assertTrue(snapshot["accounts"][0]["slot_present"])


if __name__ == "__main__": unittest.main()
