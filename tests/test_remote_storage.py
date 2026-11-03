import tempfile
import time
import unittest
from pathlib import Path

from remote_control.storage import Store


class RemoteStorageTests(unittest.TestCase):
    def test_device_idempotent_command_and_lease(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(Path(td) / "db.sqlite")
            store.create_device("d1", "Device", "hash")
            first = store.enqueue_command("d1", {"kind": "run"}, "same", int(time.time()) + 900)
            second = store.enqueue_command("d1", {"kind": "run"}, "same", int(time.time()) + 900)
            self.assertEqual(first["id"], second["id"])
            claimed = store.claim_command("d1", now=100, lease_seconds=30)
            self.assertEqual(claimed["id"], first["id"])
            self.assertIsNone(store.claim_command("d1", now=100, lease_seconds=30))
            self.assertTrue(store.finish_command(first["id"], claimed["lease"], "succeeded", {"ok": True}, now=100))

    def test_revoked_device_cannot_claim(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(Path(td) / "db.sqlite")
            store.create_device("d1", "Device", "hash")
            store.enqueue_command("d1", {"kind": "stop"}, "stop-1", int(time.time()) + 900)
            store.revoke_device("d1")
            self.assertIsNone(store.claim_command("d1", now=100, lease_seconds=30))

    def test_device_token_is_hashed_at_rest(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(Path(td) / "db.sqlite"); store.create_device("d1", "Device", "agent-token")
            self.assertNotEqual(store.get_device("d1")["token_hash"], "agent-token")

    def test_expired_lease_can_be_reclaimed(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(Path(td) / "db.sqlite"); store.create_device("d1", "Device", "hash")
            row = store.enqueue_command("d1", {"kind": "stop"}, "stop-1", int(time.time()) + 900)
            first = store.claim_command("d1", now=100, lease_seconds=10)
            second = store.claim_command("d1", now=111, lease_seconds=10)
            self.assertEqual(first["id"], second["id"]); self.assertNotEqual(first["lease"], second["lease"])
            self.assertFalse(store.finish_command(row["id"], first["lease"], "succeeded", {}))


if __name__ == "__main__":
    unittest.main()
