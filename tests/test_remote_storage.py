import tempfile
import unittest
from pathlib import Path

from remote_control.storage import Store


class RemoteStorageTests(unittest.TestCase):
    def test_device_idempotent_command_and_lease(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(Path(td) / "db.sqlite")
            store.create_device("d1", "Device", "hash")
            first = store.enqueue_command("d1", {"kind": "run"}, "same", 4102444800)
            second = store.enqueue_command("d1", {"kind": "run"}, "same", 4102444800)
            self.assertEqual(first["id"], second["id"])
            claimed = store.claim_command("d1", now=100, lease_seconds=30)
            self.assertEqual(claimed["id"], first["id"])
            self.assertIsNone(store.claim_command("d1", now=100, lease_seconds=30))
            self.assertTrue(store.finish_command(first["id"], claimed["lease"], "succeeded", {"ok": True}))

    def test_revoked_device_cannot_claim(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(Path(td) / "db.sqlite")
            store.create_device("d1", "Device", "hash")
            store.enqueue_command("d1", {"kind": "stop"}, "stop-1", 4102444800)
            store.revoke_device("d1")
            self.assertIsNone(store.claim_command("d1", now=100, lease_seconds=30))


if __name__ == "__main__":
    unittest.main()
