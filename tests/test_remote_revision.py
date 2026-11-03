import json
import tempfile
import unittest
from pathlib import Path

from remote_control.local_state import LocalState
from gui.core.remote_revision import ConfigRevisionConflict, save_cfg


class RemoteRevisionTests(unittest.TestCase):
    def test_revision_round_trip_preserves_unknown_fields(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root / "config.json").write_text(json.dumps({"custom": 1, "accounts": [], "remote_revision": 0}), encoding="utf-8")
            state = LocalState(root)
            self.assertEqual(state.apply_patch({"schedule": {"times": []}}, 0), 1)
            data = json.loads((root / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(data["custom"], 1); self.assertEqual(data["remote_revision"], 1)

    def test_gui_save_rejects_disk_revision_change(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); path = root / "config.json"; path.write_text(json.dumps({"remote_revision": 2}), encoding="utf-8")
            with self.assertRaises(ConfigRevisionConflict): save_cfg(path, {"remote_revision": 1})


if __name__ == "__main__": unittest.main()
