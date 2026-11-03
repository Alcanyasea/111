import json
import hashlib
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS devices (id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, device_id TEXT NOT NULL, payload TEXT NOT NULL, idempotency_key TEXT NOT NULL, expires_at INTEGER NOT NULL, state TEXT NOT NULL, lease TEXT, lease_until INTEGER, result TEXT, created_at INTEGER NOT NULL, FOREIGN KEY(device_id) REFERENCES devices(id));
            CREATE UNIQUE INDEX IF NOT EXISTS commands_device_idem ON commands(device_id, idempotency_key);
            CREATE TABLE IF NOT EXISTS snapshots (device_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at INTEGER NOT NULL, FOREIGN KEY(device_id) REFERENCES devices(id));
            CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT, event TEXT NOT NULL, payload TEXT, created_at INTEGER NOT NULL);
            """)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def create_device(self, device_id, name, token_hash):
        token_hash = hashlib.sha256(str(token_hash).encode("utf-8")).hexdigest()
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO devices(id,name,token_hash,revoked,created_at) VALUES(?,?,?,?,?)", (device_id, name, token_hash, 0, int(time.time())))

    def get_device(self, device_id):
        with self._db() as db:
            row = db.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
            return dict(row) if row else None

    def revoke_device(self, device_id):
        with self._db() as db:
            db.execute("UPDATE devices SET revoked=1 WHERE id=?", (device_id,))

    def enqueue_command(self, device_id, payload, idempotency_key, expires_at):
        now = int(time.time())
        with self._db() as db:
            row = db.execute("SELECT * FROM commands WHERE device_id=? AND idempotency_key=?", (device_id, idempotency_key)).fetchone()
            if row:
                return dict(row)
            cid = secrets.token_urlsafe(12)
            db.execute("INSERT INTO commands(id,device_id,payload,idempotency_key,expires_at,state,created_at) VALUES(?,?,?,?,?,?,?)", (cid, device_id, json.dumps(payload), idempotency_key, expires_at, "queued", now))
            row = db.execute("SELECT * FROM commands WHERE id=?", (cid,)).fetchone()
            return dict(row)

    def claim_command(self, device_id, now=None, lease_seconds=60):
        now = int(time.time() if now is None else now)
        with self._db() as db:
            dev = db.execute("SELECT revoked FROM devices WHERE id=?", (device_id,)).fetchone()
            if not dev or dev[0]:
                return None
            db.execute("UPDATE commands SET state='expired' WHERE device_id=? AND state='queued' AND expires_at<=?", (device_id, now))
            row = db.execute("SELECT * FROM commands WHERE device_id=? AND state='queued' ORDER BY created_at,id LIMIT 1", (device_id,)).fetchone()
            if not row:
                return None
            lease = secrets.token_urlsafe(12)
            until = now + lease_seconds
            db.execute("UPDATE commands SET state='claimed',lease=?,lease_until=? WHERE id=?", (lease, until, row["id"]))
            result = dict(row)
            result.update({"state": "claimed", "lease": lease, "lease_until": until, "payload": json.loads(row["payload"])})
            return result

    def finish_command(self, command_id, lease, state, result):
        with self._db() as db:
            cur = db.execute("UPDATE commands SET state=?,result=? WHERE id=? AND lease=? AND state IN ('claimed','running')", (state, json.dumps(result), command_id, lease))
            return cur.rowcount == 1

    def save_snapshot(self, device_id, payload):
        with self._db() as db:
            db.execute("INSERT INTO snapshots(device_id,payload,updated_at) VALUES(?,?,?) ON CONFLICT(device_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at", (device_id, json.dumps(payload), int(time.time())))

    def get_snapshot(self, device_id):
        with self._db() as db:
            row = db.execute("SELECT payload FROM snapshots WHERE device_id=?", (device_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def audit(self, device_id, event, payload=None):
        with self._db() as db:
            db.execute("INSERT INTO audit(device_id,event,payload,created_at) VALUES(?,?,?,?)", (device_id, event, json.dumps(payload) if payload is not None else None, int(time.time())))
