import json
import os
import time
from contextlib import contextmanager
from pathlib import Path


class RevisionConflict(ValueError):
    pass


class LocalState:
    def __init__(self, root):
        self.root = Path(root); self.config_path = self.root / "config.json"; self.lock_path = self.root / "config.remote.lock"

    def _read(self):
        with self.config_path.open(encoding="utf-8") as f: return json.load(f)

    @contextmanager
    def acquire_write_lock(self):
        deadline = time.time() + 10
        while True:
            try:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd); break
            except FileExistsError:
                if time.time() >= deadline: raise TimeoutError("config lock busy")
                time.sleep(.05)
        try: yield
        finally:
            try: self.lock_path.unlink()
            except FileNotFoundError: pass

    def backup_config(self):
        target = self.config_path.with_name("config.json.remote-bak-%d" % int(time.time() * 1000))
        target.write_bytes(self.config_path.read_bytes()); return target

    def read_snapshot(self):
        from .protocol import redact_config
        return redact_config(self._read())

    def apply_patch(self, patch, base_revision):
        with self.acquire_write_lock():
            cfg = self._read(); current = int(cfg.get("remote_revision", 0) or 0)
            if current != int(base_revision): raise RevisionConflict("configuration revision changed")
            if not isinstance(patch, dict) or any(k in {"password", "token", "username", "uid", "slot"} for k in patch): raise ValueError("secret patch rejected")
            self.backup_config(); cfg.update(patch); cfg["remote_revision"] = current + 1
            tmp = self.config_path.with_suffix(".json.remote-tmp")
            tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, self.config_path)
            return cfg["remote_revision"]
