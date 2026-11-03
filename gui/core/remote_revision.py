"""Shared revision and backup helpers for GUI and remote Agent writes."""
import os
import time
from contextlib import contextmanager
from pathlib import Path


def read_revision(cfg):
    try: return int(cfg.get("remote_revision", 0) or 0)
    except (TypeError, ValueError): return 0


def next_revision(cfg):
    value = read_revision(cfg) + 1; cfg["remote_revision"] = value; return value


@contextmanager
def config_write_lock(config_path):
    path = Path(config_path); lock = path.with_name("config.remote.lock")
    deadline = time.time() + 10
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY); os.close(fd); break
        except FileExistsError:
            if time.time() >= deadline: raise TimeoutError("config lock busy")
            time.sleep(.05)
    try: yield
    finally:
        try: lock.unlink()
        except FileNotFoundError: pass


def backup_config(config_path):
    path = Path(config_path); target = path.with_name("config.json.remote-bak-%d" % int(time.time() * 1000)); target.write_bytes(path.read_bytes()); return target
