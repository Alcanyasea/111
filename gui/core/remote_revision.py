"""Shared revision and backup helpers for GUI and remote Agent writes."""
import os
import json
import time
from contextlib import contextmanager
from pathlib import Path


class ConfigRevisionConflict(RuntimeError):
    pass


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


def save_cfg(config_path, cfg):
    path = Path(config_path)
    with config_write_lock(path):
        disk_revision = 0
        if path.exists():
            with path.open(encoding="utf-8") as f: disk_revision = int((json.load(f) or {}).get("remote_revision", 0) or 0)
            if read_revision(cfg) != disk_revision: raise ConfigRevisionConflict("configuration changed on disk")
            backup_config(path)
        next_revision(cfg)
        tmp = path.with_suffix(".json.remote-tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return read_revision(cfg)
