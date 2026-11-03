import json
import tempfile
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

    def test_snapshot_reports_local_slot_presence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); cfg = {"remote_revision": 1, "accounts": [{"id": "a", "label": "A", "slot": "slot-a"}]}
            (root / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
            (root / "scripts" / "accounts" / "slot-a").mkdir(parents=True)
            snapshot = LocalState(root).read_snapshot()
            self.assertTrue(snapshot["accounts"][0]["slot_present"])


if __name__ == "__main__": unittest.main()
