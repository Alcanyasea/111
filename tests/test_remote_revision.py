import json
import os
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_failed_replace_does_not_advance_in_memory_revision(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); path = root / "config.json"
            path.write_text(json.dumps({"remote_revision": 1, "x": 1}), encoding="utf-8")
            cfg = {"remote_revision": 1, "x": 2}
            with patch("gui.core.remote_revision.os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError): save_cfg(path, cfg)
            self.assertEqual(cfg["remote_revision"], 1)
            self.assertFalse((root / "config.remote.lock").exists())

    def test_stale_lock_from_dead_process_is_recovered(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); path = root / "config.json"
            path.write_text(json.dumps({"remote_revision": 1}), encoding="utf-8")
            lock = root / "config.remote.lock"
            lock.write_text(json.dumps({
                "pid": 99999999, "host": socket.gethostname(),
                "created": time.time() - 3600, "token": "old",
            }), encoding="utf-8")
            os.utime(lock, (time.time() - 3600, time.time() - 3600))
            self.assertEqual(save_cfg(path, {"remote_revision": 1}), 2)
            self.assertFalse(lock.exists())


if __name__ == "__main__": unittest.main()
