import copy
import ctypes
import json
import os
import secrets
import socket
import time
from contextlib import contextmanager
from pathlib import Path


class RevisionConflict(ValueError):
    pass


LOCK_STALE_SECONDS = 120


def _pid_alive(pid):
    """Best-effort process liveness check without adding a dependency."""
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        # 87/1168: invalid PID / not found. Access denied still means it exists.
        return ctypes.get_last_error() not in (87, 1168)
    try:
        code = ctypes.c_ulong()
        if kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)):
            return code.value == 259  # STILL_ACTIVE
        return True
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def _lock_stale(path):
    """A lock is stale when its owner died, or it outlived any plausible write."""
    try:
        stat = path.stat()
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return True
    except OSError:
        return False
    try:
        info = json.loads(raw)
        created = float(info.get("created", 0))
        pid = int(info.get("pid", 0))
        host = str(info.get("host", ""))
    except (TypeError, ValueError, AttributeError):
        # A process can be between O_EXCL create and writing its metadata.
        return time.time() - stat.st_mtime > 2
    age = max(0.0, time.time() - created) if created else time.time() - stat.st_mtime
    if age > LOCK_STALE_SECONDS:
        return True
    if host == socket.gethostname() and pid and not _pid_alive(pid):
        return True
    return False


@contextmanager
def _config_write_lock(lock_path, timeout=10):
    token = secrets.token_hex(8)
    deadline = time.time() + timeout
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, json.dumps({
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "created": time.time(),
                "token": token,
            }).encode("utf-8"))
            os.close(fd)
            break
        except FileExistsError:
            if _lock_stale(lock_path):
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
                continue
            if time.time() >= deadline:
                raise TimeoutError("config lock busy")
            time.sleep(.05)
    try:
        yield
    finally:
        try:
            info = json.loads(lock_path.read_text(encoding="utf-8"))
            if info.get("token") == token:
                lock_path.unlink()
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError):
            pass


def _merge_dict(target, patch):
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_dict(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def _merge_account(current, patch):
    for key, value in patch.items():
        if key == "id":
            continue
        if isinstance(value, dict) and isinstance(current.get(key), dict):
            _merge_dict(current[key], value)
        else:
            current[key] = copy.deepcopy(value)


class LocalState:
    def __init__(self, root):
        self.root = Path(root); self.config_path = self.root / "config.json"; self.lock_path = self.root / "config.remote.lock"

    def _read(self):
        with self.config_path.open(encoding="utf-8") as f: return json.load(f)

    def read_config(self):
        """Return the current on-disk config (used after a patch to refresh Agent state)."""
        return self._read()

    @contextmanager
    def acquire_write_lock(self):
        with _config_write_lock(self.lock_path):
            yield

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
            candidate = copy.deepcopy(cfg)
            self._apply_merge(candidate, patch)
            candidate["remote_revision"] = current + 1
            self.backup_config()
            tmp = self.config_path.with_suffix(".json.remote-tmp")
            tmp.write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, self.config_path)
            return candidate["remote_revision"]

    @staticmethod
    def _apply_merge(cfg, patch):
        for key, value in patch.items():
            if key == "remote_revision":
                continue  # revision is server-local; never accept a client value
            if key == "accounts":
                cfg[key] = LocalState._merge_accounts(cfg.get("accounts") or [], value)
            elif key == "schedule":
                if not isinstance(cfg.get("schedule"), dict):
                    cfg["schedule"] = {}
                _merge_dict(cfg["schedule"], value)

    @staticmethod
    def _merge_accounts(current, incoming):
        if not isinstance(incoming, list) or not incoming:
            raise ValueError("accounts patch must be a non-empty array")
        by_id = {}
        order = []
        for account in current:
            if not isinstance(account, dict) or account.get("id") is None:
                continue
            account_id = str(account.get("id"))
            by_id[account_id] = account
            order.append(account_id)
        seen = set()
        for item in incoming:
            if not isinstance(item, dict):
                raise ValueError("each account must be an object")
            account_id = str(item.get("id") or "").strip()
            if not account_id or account_id in seen:
                raise ValueError("account id must be unique")
            if account_id not in by_id:
                raise ValueError("account does not exist: %s" % account_id)
            seen.add(account_id)
            _merge_account(by_id[account_id], item)
        # Only reorder when the patch names every existing account, so a normal
        # single-account edit cannot accidentally delete the rest of the list.
        if seen == set(order):
            order = [str(item.get("id")) for item in incoming]
        return [copy.deepcopy(by_id[account_id]) for account_id in order]

    @staticmethod
    def _validate_patch(patch):
        if not isinstance(patch, dict): raise ValueError("patch must be an object")
        allowed_top = {"accounts", "schedule", "remote_revision"}
        if any(k not in allowed_top for k in patch): raise ValueError("field is not remotely editable")
        if "remote_revision" in patch and not isinstance(patch["remote_revision"], int):
            raise ValueError("invalid revision")
        if "schedule" in patch and not isinstance(patch["schedule"], dict):
            raise ValueError("schedule patch must be an object")
        if "accounts" in patch:
            if not isinstance(patch["accounts"], list) or not patch["accounts"]:
                raise ValueError("accounts patch must be a non-empty array")
            for item in patch["accounts"]:
                if not isinstance(item, dict) or not str(item.get("id") or "").strip():
                    raise ValueError("account id is required")
                if "base_schedule" in item and not isinstance(item["base_schedule"], dict):
                    raise ValueError("base_schedule must be an object")
                if "base_schedule_presets" in item and not isinstance(item["base_schedule_presets"], list):
                    raise ValueError("base_schedule_presets must be an array")
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
