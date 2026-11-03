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


if __name__ == "__main__": unittest.main()
