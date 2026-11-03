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
        cfg = self._read(); snapshot = redact_config(cfg)
        for source, target in zip(cfg.get("accounts") or [], snapshot.get("accounts") or []):
            slot = str(source.get("slot") or "") if isinstance(source, dict) else ""
            target["slot_present"] = bool(slot) and (self.root / "scripts" / "accounts" / slot).is_dir()
        return snapshot

    def apply_patch(self, patch, base_revision):
        with self.acquire_write_lock():
            cfg = self._read(); current = int(cfg.get("remote_revision", 0) or 0)
            if current != int(base_revision): raise RevisionConflict("configuration revision changed")
            self._validate_patch(patch)
            self.backup_config(); cfg.update(patch); cfg["remote_revision"] = current + 1
            tmp = self.config_path.with_suffix(".json.remote-tmp")
            tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, self.config_path)
            return cfg["remote_revision"]

    @staticmethod
    def _validate_patch(patch):
        if not isinstance(patch, dict): raise ValueError("patch must be an object")
        allowed_top = {"accounts", "schedule", "remote_revision"}
        if any(k not in allowed_top for k in patch): raise ValueError("field is not remotely editable")
        forbidden = {"password", "token", "username", "uid", "slot", "paths", "script_dir", "log_file"}
        account_fields = {"id", "label", "server", "enabled", "second_fight_plan", "second_fight_use_optional", "base_schedule", "base_schedule_presets"}
        def walk(value, key=""):
            if key in forbidden: raise ValueError("secret or path field rejected")
            if isinstance(value, dict):
                for name, child in value.items():
                    if name not in account_fields and key == "accounts": raise ValueError("account field is not remotely editable")
                    walk(child, str(name))
            elif isinstance(value, list):
                for child in value: walk(child, key)
        walk(patch)
