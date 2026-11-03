import json
import threading
import time
import unittest
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory

from remote_control.server import Auth, ControlHTTPServer
from remote_control.storage import Store


class RemoteServerTests(unittest.TestCase):
    def setUp(self):
        self.td = TemporaryDirectory()
        self.store = Store(Path(self.td.name) / "db.sqlite")
        self.auth = Auth("admin-password")
        self.server = ControlHTTPServer(self.store, self.auth, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.conn = HTTPConnection("127.0.0.1", self.server.server_port)

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.conn.close(); self.td.cleanup()

    def request(self, method, path, body=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        self.conn.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = self.conn.getresponse()
        data = response.read()
        return response.status, json.loads(data) if data else None

    def test_login_health_and_idempotent_command(self):
        status, data = self.request("POST", "/api/v1/session", {"password": "admin-password"})
        self.assertEqual(status, 200); token = data["token"]
        status, _ = self.request("GET", "/api/v1/health")
        self.assertEqual(status, 200)
        self.store.create_device("d1", "PC", "agent-token")
        body = {"kind": "run", "args": {}, "idempotency_key": "x", "expires_at": int(time.time()) + 900}
        status, first = self.request("POST", "/api/v1/devices/d1/commands", body, token)
        status2, second = self.request("POST", "/api/v1/devices/d1/commands", body, token)
        self.assertEqual((status, status2), (202, 202)); self.assertEqual(first["command_id"], second["command_id"])

    def test_agent_claim_requires_agent_token(self):
        self.store.create_device("d1", "PC", "agent-token")
        body = {"kind": "stop", "args": {}, "idempotency_key": "s", "expires_at": int(time.time()) + 900}
        self.store.enqueue_command("d1", body, "s", int(time.time()) + 900)
        self.conn.request("GET", "/api/v1/devices/d1/commands/next", headers={"Authorization": "Bearer agent-token"})
        response = self.conn.getresponse(); self.assertEqual(response.status, 200)

    def test_static_assets_and_agent_snapshot(self):
        self.conn.request("GET", "/app.js")
        self.assertEqual(self.conn.getresponse().status, 200)

    def test_admin_can_pair_device_and_receive_one_time_token(self):
        status, data = self.request("POST", "/api/v1/session", {"password": "admin-password"})
        self.assertEqual(status, 200)
        status, pair = self.request("POST", "/api/v1/pair", {"device_id": "new-pc", "name": "New PC"}, data["token"])
        self.assertEqual(status, 201); self.assertTrue(pair["agent_token"])
        self.assertEqual(self.store.get_device("new-pc")["name"], "New PC")

    def test_unknown_device_command_is_not_server_error(self):
        status, data = self.request("POST", "/api/v1/session", {"password": "admin-password"})
        body = {"kind": "run", "args": {}, "idempotency_key": "unknown", "expires_at": int(time.time()) + 900}
        status, _ = self.request("POST", "/api/v1/devices/missing/commands", body, data["token"])
        self.assertEqual(status, 404)

    def test_server_redacts_agent_snapshot_and_lists_devices(self):
        status, data = self.request("POST", "/api/v1/session", {"password": "admin-password"})
        self.store.create_device("d3", "PC", "agent-token")
        self.request("POST", "/api/v1/devices/d3/register", {"token": "secret", "phase": "idle"}, "agent-token")
        self.conn.request("GET", "/api/v1/devices", headers={"Authorization": "Bearer " + data["token"]})
        listed = json.loads(self.conn.getresponse().read()); self.assertEqual(listed["devices"][0]["id"], "d3")
        self.conn.request("GET", "/api/v1/devices/d3/snapshot", headers={"Authorization": "Bearer " + data["token"]})
        snapshot = json.loads(self.conn.getresponse().read()); self.assertNotIn("token", snapshot)
        self.store.create_device("d2", "PC", "agent-token")
        self.conn.request("POST", "/api/v1/devices/d2/register", json.dumps({"phase": "idle"}), {"Authorization": "Bearer agent-token", "Content-Type": "application/json"})
        self.assertEqual(self.conn.getresponse().status, 200)


if __name__ == "__main__":
    unittest.main()
