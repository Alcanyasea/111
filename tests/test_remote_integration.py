import json
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory

from remote_control.server import Auth, ControlHTTPServer
from remote_control.storage import Store


class RemoteIntegrationTests(unittest.TestCase):
    def test_queue_claim_result_and_snapshot(self):
        with TemporaryDirectory() as td:
            store = Store(Path(td) / "db.sqlite"); store.create_device("d", "PC", "tok")
            server = ControlHTTPServer(store, Auth("pw"), "127.0.0.1", 0); thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            conn = HTTPConnection("127.0.0.1", server.server_port)
            conn.request("POST", "/api/v1/session", json.dumps({"password": "pw"}), {"Content-Type": "application/json"}); token = json.loads(conn.getresponse().read())["token"]
            body = {"kind": "stop", "args": {}, "idempotency_key": "i", "expires_at": 4102444800}
            conn.request("POST", "/api/v1/devices/d/commands", json.dumps(body), {"Authorization": "Bearer " + token, "Content-Type": "application/json"}); self.assertEqual(conn.getresponse().status, 202)
            conn.request("GET", "/api/v1/devices/d/commands/next", headers={"Authorization": "Bearer tok"}); item = json.loads(conn.getresponse().read()); self.assertEqual(item["payload"]["kind"], "stop")
            conn.request("POST", "/api/v1/devices/d/commands/result", json.dumps({"command_id": item["id"], "lease": item["lease"], "state": "succeeded", "result": {"ok": True}}), {"Authorization": "Bearer tok", "Content-Type": "application/json"}); self.assertEqual(conn.getresponse().status, 200)
            server.shutdown(); server.server_close(); conn.close()


if __name__ == "__main__": unittest.main()
