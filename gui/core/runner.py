# -*- coding: utf-8 -*-
"""运行控制：启动 / 停止 master.ps1，检测运行状态。

GUI 手动启动时传 -NoShutdown：即使用户在上午手动运行且全部成功，
也不触发自动关机（用户偏好，见记忆 user-shutdown-preference）。
计划任务照常直接调 master.ps1，不带该参数，行为不变。
"""
import json
import subprocess
from pathlib import Path

from core import proc
from core.util import CREATE_NO_WINDOW

LOCK_FILE = Path(r"D:\1\scripts\master.lock")
SCRIPT_DIR = Path(r"D:\1\scripts")

# 锁文件里的 PID 必须是 powershell 才算挂机在跑：master.ps1 异常退出后 PID
# 被无关进程复用时，不能把别的进程误判成挂机（更不能 taskkill 它的进程树）
MASTER_PROC_NAMES = ("powershell.exe", "pwsh.exe")

# 启动 master.ps1 的 GUI 侧句柄：保住 Popen 与输出文件引用，避免 GC 告警
_proc_ref = None


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


def _pid_name(pid):
    """PID 对应的进程名（小写），进程不存在返回 None。"""
    name = proc.process_name(pid)
    return name.lower() if name else None


def _pid_is_master(pid):
    """PID 是否是活着的 powershell（与 master.ps1 的自检口径一致）。"""
    return _pid_name(pid) in MASTER_PROC_NAMES


def is_running():
    """master.ps1 是否在运行：锁文件存在且 PID 是活着的 powershell。"""
    pid = lock_pid()
    if pid is None:
        return False
    return _pid_is_master(pid)


def stale_lock():
    """锁文件存在但不是活着的 powershell（中断残留/PID 复用），返回该 PID。"""
    pid = lock_pid()
    if pid is None:
        return None
    return None if _pid_is_master(pid) else pid


def clear_stale_lock():
    """清理陈旧锁；返回被清理的 PID，没有陈旧锁返回 None。

    判定与删除之间 master.ps1 可能刚把陈旧锁换成自己的新锁（抢锁序列：
    读旧锁 → 删 → 写新锁），所以删除前重读一次内容，仍是不活的旧 PID 才删。
    """
    pid = stale_lock()
    if pid is None:
        return None
    try:
        if LOCK_FILE.read_text(encoding="ascii").strip() != str(pid):
            return None
        LOCK_FILE.unlink()
    except OSError:
        return None
    return pid


def _launch_master(cfg, extra_args):
    """启动 master.ps1 的 GUI 侧公共路径：extra_args 为运行模式参数。"""
    global _proc_ref
    master = Path(cfg["paths"]["script_dir"]) / "master.ps1"
    args = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(master),
    ] + list(extra_args)
    debug_dir = SCRIPT_DIR / "debug"
    try:
        debug_dir.mkdir(parents=True, exist_ok=True)
        out_f = open(debug_dir / "master_gui_launch.log", "ab")
    except OSError:
        out_f = None
    p = subprocess.Popen(
        args,
        stdout=out_f if out_f is not None else subprocess.DEVNULL,
        stderr=subprocess.STDOUT if out_f is not None else subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW, cwd=str(SCRIPT_DIR),
    )
    _proc_ref = (p, out_f)
    return p


def start(cfg):
    """启动 master.ps1（GUI 触发，带 -NoShutdown），返回 Popen 对象。

    早期语法错误等 stderr 重定向到 debug\\master_gui_launch.log，
    否则无处可去、排障只能靠日志文件。
    """
    return _launch_master(cfg, ["-NoShutdown"])


def start_collect(cfg):
    """启动基建收菜：master.ps1 -InfrastCollect（手动按钮，同样不关机）。

    与常规挂机共用 master.lock，天然互斥；逐个已启用账号只收制造站/
    贸易站，master.ps1 负责备份并在结束恢复 MAA 配置。
    """
    return _launch_master(cfg, ["-InfrastCollect", "-NoShutdown"])


def start_switch(cfg, slot):
    """切换到指定账号后停下（不跑日常）：master.ps1 -SwitchTo <slot>。

    切号 + 登录校验完成即停，模拟器保持运行供手动游戏；同样带
    -NoShutdown（手动操作永不关机）。与常规挂机共用 master.lock 互斥。
    """
    return _launch_master(cfg, ["-SwitchTo", slot, "-NoShutdown"])


def start_switch_fast(cfg, slot):
    """快速启动指定账号：master.ps1 -SwitchTo <slot> -NoLoginCheck。

    只把槽位登录数据（token）推入游戏并启动，跳过更新等待与登录校验即停
    （账号卡片「快速启动」按钮）；模拟器保持运行供手动游戏，同样不关机。
    """
    return _launch_master(cfg, ["-SwitchTo", slot, "-NoLoginCheck", "-NoShutdown"])


def stop():
    """停止正在运行的挂机流程。

    1) taskkill 进程树杀掉 master.ps1（杀前复核 PID 仍是 powershell，
       防止 PID 被复用后误杀无关进程）
    2) 杀掉 MAA（master.ps1 的 Run-MAA 结束时会自己杀，强杀时 MAA 会残留）
    3) shutdown /a 取消可能已排定的自动关机（手动停止时不关机）
    4) 清理残留锁文件（master.ps1 正常结束会自删，强杀后必残留）
    """
    pid = lock_pid()
    if pid is not None and _pid_is_master(pid):
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


# check_run_directly 的读取缓存：文件 (size, mtime) 未变时直接用上次结果。
# gui.new.json 由 MAA 运行/退出时回写，可能读到写了一半的 JSON——
# 解析失败时保留上次成功值，避免状态在「✓/✗/配置缺失」间抖动。
_rd_cache = {}


def _run_directly_state(path):
    """单个 gui.new.json 的 RunDirectly：True/False/None（缺失或解析失败）。"""
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return None
    key = str(path)
    sig = (st.st_size, st.st_mtime)
    cached = _rd_cache.get(key)
    if cached is not None and cached[0] == sig:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # 写了一半的文件：沿用上次成功值（无历史则 None）
        return cached[1] if cached is not None else None
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
    value = bool(values) and all(values)
    _rd_cache[key] = (sig, value)
    return value


def check_run_directly(cfg):
    """检查两套 MAA 配置的 RunDirectly 是否都为 true。

    记忆 maa-farm-setup：RunDirectly 必须 true，否则 MAA 启动后不自动跑任务、
    master.ps1 会一直等 maa_done.signal；仅在该账号长时间（默认 3 分钟）
    没有战斗/任务推进时判超时放弃。
    返回 {"official": bool|None, "bilibili": bool|None}，None = 配置缺失/读不了。
    结果按文件 (size, mtime) 缓存，5 秒轮询不再是全量 JSON 解析。
    """
    return {
        "official": _run_directly_state(
            Path(cfg["paths"]["maa_official_dir"]) / "config" / "gui.new.json"),
        "bilibili": _run_directly_state(
            Path(cfg["paths"]["maa_bilibili_dir"]) / "config" / "gui.new.json"),
    }
