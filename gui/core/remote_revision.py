"""Shared revision and backup helpers for GUI and remote Agent writes."""
import copy
import ctypes
import json
import os
import secrets
import socket
import time
from contextlib import contextmanager
from pathlib import Path


class ConfigRevisionConflict(OSError):
    pass


LOCK_STALE_SECONDS = 120


def read_revision(cfg):
    try: return int(cfg.get("remote_revision", 0) or 0)
    except (TypeError, ValueError): return 0


def next_revision(cfg):
    value = read_revision(cfg) + 1; cfg["remote_revision"] = value; return value


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
def config_write_lock(config_path, timeout=10):
    path = Path(config_path); lock = path.with_name("config.remote.lock")
    token = secrets.token_hex(8)
    deadline = time.time() + timeout
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, json.dumps({
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "created": time.time(),
                "token": token,
            }).encode("utf-8"))
            os.close(fd)
            break
        except FileExistsError:
            if _lock_stale(lock):
                try:
                    lock.unlink()
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
            info = json.loads(lock.read_text(encoding="utf-8"))
            if info.get("token") == token:
                lock.unlink()
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError):
            pass


def backup_config(config_path):
    path = Path(config_path); target = path.with_name("config.json.remote-bak-%d" % int(time.time() * 1000)); target.write_bytes(path.read_bytes()); return target


def save_cfg(config_path, cfg):
    path = Path(config_path)
    with config_write_lock(path):
        disk_revision = 0
        if path.exists():
            with path.open(encoding="utf-8") as f: disk_revision = int((json.load(f) or {}).get("remote_revision", 0) or 0)
            if read_revision(cfg) != disk_revision: raise ConfigRevisionConflict("configuration changed on disk")
            backup_config(path)
        payload = copy.deepcopy(cfg)
        next_revision(payload)
        tmp = path.with_suffix(".json.remote-tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        # Do not advance the caller's in-memory revision until the atomic write
        # succeeded; otherwise a transient disk failure poisons every retry.
        cfg["remote_revision"] = payload["remote_revision"]
        return read_revision(cfg)
