# -*- coding: utf-8 -*-
"""运行控制：启动 / 停止 master.ps1，检测运行状态。

GUI 手动启动时传 -NoShutdown：即使用户在上午手动运行且全部成功，
也不触发自动关机（用户偏好，见记忆 user-shutdown-preference）。
计划任务照常直接调 master.ps1，不带该参数，行为不变。
"""
import json
import subprocess
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000

LOCK_FILE = Path(r"D:\1\scripts\master.lock")
SCRIPT_DIR = Path(r"D:\1\scripts")


def _run(args, timeout=15):
    try:
        r = subprocess.run(
            args, capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW
        )
        return r.returncode, r.stdout, r.stderr
    except (subprocess.TimeoutExpired, OSError):
        return -1, b"", b""


def lock_pid():
    """master.lock 里的 PID；文件缺失/损坏返回 None。"""
    try:
        return int(LOCK_FILE.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid):
    code, out, _ = _run(["tasklist", "/FI", "PID eq %d" % pid])
    return code == 0 and str(pid) in out.decode(errors="ignore")


def is_running():
    """master.ps1 是否在运行：锁文件存在且 PID 是活着的 powershell。"""
    pid = lock_pid()
    if pid is None:
        return False
    return _pid_alive(pid)


def stale_lock():
    """锁文件存在但 PID 已死（中断残留），返回该 PID，否则 None。"""
    pid = lock_pid()
    if pid is None:
        return None
    return None if _pid_alive(pid) else pid


def start(cfg):
    """启动 master.ps1（GUI 触发，带 -NoShutdown），返回 Popen 对象。"""
    master = Path(cfg["paths"]["script_dir"]) / "master.ps1"
    args = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(master), "-NoShutdown",
    ]
    return subprocess.Popen(
        args, creationflags=CREATE_NO_WINDOW, cwd=str(SCRIPT_DIR)
    )


def stop():
    """停止正在运行的挂机流程。

    1) taskkill 进程树杀掉 master.ps1
    2) 杀掉 MAA（master.ps1 的 Run-MAA 结束时会自己杀，强杀时 MAA 会残留）
    3) shutdown /a 取消可能已排定的自动关机（手动停止时不关机）
    4) 清理残留锁文件（master.ps1 正常结束会自删，强杀后必残留）
    """
    pid = lock_pid()
    if pid is not None:
        _run(["taskkill", "/PID", str(pid), "/T", "/F"])
    _run(["taskkill", "/IM", "MAA.exe", "/F"])
    _run(["shutdown", "/a"])
    try:
        LOCK_FILE.unlink()
    except OSError:
        pass
    try:
        (LOCK_FILE.with_suffix(".lock.tmp")).unlink()
    except OSError:
        pass


def check_run_directly(cfg):
    """检查两套 MAA 配置的 RunDirectly 是否都为 true。

    记忆 maa-farm-setup：RunDirectly 必须 true，否则 MAA 启动后不自动跑任务、
    master.ps1 会一直等 maa_done.signal 直到超时。
    返回 {"official": bool|None, "bilibili": bool|None}，None = 配置缺失/读不了。
    """
    result = {}
    for key, path in (
        ("official", Path(cfg["paths"]["maa_official_dir"]) / "config" / "gui.new.json"),
        ("bilibili", Path(cfg["paths"]["maa_bilibili_dir"]) / "config" / "gui.new.json"),
    ):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            stack = [data]
            values = []
            while stack:
                node = stack.pop()
                if isinstance(node, dict):
                    for k, v in node.items():
                        if k == "RunDirectly":
                            values.append(bool(v))
                        elif isinstance(v, (dict, list)):
                            stack.append(v)
                elif isinstance(node, list):
                    stack.extend(node)
            result[key] = bool(values) and all(values)
        except (OSError, json.JSONDecodeError):
            result[key] = None
    return result
