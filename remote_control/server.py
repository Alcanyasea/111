import hashlib
import hmac
import json
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .protocol import ProtocolError, command_payload


class Auth:
    def __init__(self, password):
        self._salt = secrets.token_bytes(16)
        self._password_hash = hashlib.scrypt(password.encode(), salt=self._salt, n=2**14, r=8, p=1)
        self._sessions = {}

    def verify_password(self, password):
        return hmac.compare_digest(self._password_hash, hashlib.scrypt(password.encode(), salt=self._salt, n=2**14, r=8, p=1))

    def issue_session(self, ttl=86400):
        token = secrets.token_urlsafe(32); self._sessions[token] = int(time.time()) + ttl; return token

    def verify_session(self, token):
        expiry = self._sessions.get(token)
        if not expiry or expiry < int(time.time()):
            self._sessions.pop(token, None); return False
        return True


class ControlHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, store, auth, bind="127.0.0.1", port=8765):
        self.store, self.auth = store, auth
        super().__init__((bind, port), self._handler())

    def _handler(self):
        parent = self
        class Handler(BaseHTTPRequestHandler):
            server_version = "MAARemote/1"

            def log_message(self, *_):
                return

            def _json(self, status, payload):
                data = json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

            def _body(self):
                length = int(self.headers.get("Content-Length", "0")); return json.loads(self.rfile.read(length) or b"{}")

            def _token(self):
                value = self.headers.get("Authorization", "")
                return value[7:] if value.startswith("Bearer ") else ""

            def _admin(self):
                return parent.auth.verify_session(self._token())

            def _agent(self, device_id):
                device = parent.store.get_device(device_id)
                return bool(device and not device["revoked"] and hmac.compare_digest(device["token_hash"], self._token()))

            def do_GET(self):
                if self.path == "/api/v1/health": return self._json(200, {"ok": True})
                if self.path in {"/", "/index.html", "/app.js", "/styles.css"}:
                    name = "index.html" if self.path in {"/", "/index.html"} else self.path.lstrip("/")
                    html = (Path(__file__).parent / "web" / name).read_bytes()
                    content_type = "text/html; charset=utf-8" if name.endswith("html") else ("application/javascript" if name.endswith("js") else "text/css")
                    self.send_response(200); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(html))); self.end_headers(); self.wfile.write(html); return
                parts = self.path.strip("/").split("/")
                if self.path == "/api/v1/devices":
                    if not self._admin(): return self._json(401, {"error": "unauthorized"})
                    return self._json(200, {"devices": []})
                if len(parts) == 5 and parts[:3] == ["api", "v1", "devices"] and parts[4] == "snapshot":
                    if not self._admin(): return self._json(401, {"error": "unauthorized"})
                    return self._json(200, parent.store.get_snapshot(parts[3]) or {})
                if len(parts) == 6 and parts[:4] == ["api", "v1", "devices", parts[3]] and parts[4:] == ["commands", "next"]:
                    device_id = parts[3]
                    if not self._agent(device_id): return self._json(401, {"error": "unauthorized"})
                    item = parent.store.claim_command(device_id)
                    return self._json(200, item or {"command": None})
                return self._json(404, {"error": "not_found"})

            def do_POST(self):
                try: body = self._body()
                except (ValueError, json.JSONDecodeError): return self._json(400, {"error": "invalid_json"})
                if self.path == "/api/v1/session":
                    if parent.auth.verify_password(str(body.get("password", ""))): return self._json(200, {"token": parent.auth.issue_session()})
                    return self._json(401, {"error": "invalid_credentials"})
                parts = self.path.strip("/").split("/")
                if self.path == "/api/v1/pair":
                    if not self._admin(): return self._json(401, {"error": "unauthorized"})
                    device_id = str(body.get("device_id", "")).strip()
                    name = str(body.get("name", device_id)).strip()
                    if not device_id or len(device_id) > 80: return self._json(400, {"error": "invalid_device"})
                    agent_token = secrets.token_urlsafe(32)
                    parent.store.create_device(device_id, name, agent_token)
                    return self._json(201, {"device_id": device_id, "agent_token": agent_token})
                if len(parts) == 5 and parts[:3] == ["api", "v1", "devices"] and parts[4] == "register":
                    if not self._agent(parts[3]): return self._json(401, {"error": "unauthorized"})
                    parent.store.save_snapshot(parts[3], body); return self._json(200, {"ok": True})
                if len(parts) == 6 and parts[:3] == ["api", "v1", "devices"] and parts[4] == "commands" and parts[5] == "result":
                    device_id = parts[3]
                    if not self._agent(device_id): return self._json(401, {"error": "unauthorized"})
                    ok = parent.store.finish_command(body.get("command_id", ""), body.get("lease", ""), body.get("state", "failed"), body.get("result", {}))
                    return self._json(200 if ok else 409, {"ok": ok})
                if len(parts) == 5 and parts[:3] == ["api", "v1", "devices"] and parts[4] == "commands":
                    if not self._admin(): return self._json(401, {"error": "unauthorized"})
                    device_id = parts[3]
                    try:
                        payload = command_payload(body["kind"], body.get("args", {}), body["idempotency_key"], int(body["expires_at"]))
                    except (KeyError, ValueError, ProtocolError) as exc:
                        return self._json(400, {"error": str(exc)})
                    row = parent.store.enqueue_command(device_id, payload, payload["idempotency_key"], payload["expires_at"])
                    return self._json(202, {"command_id": row["id"]})
                return self._json(404, {"error": "not_found"})
        return Handler
