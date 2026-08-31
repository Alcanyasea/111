# -*- coding: utf-8 -*-
"""ADB 与 MuMu 模拟器交互：连接检测、截图、点击、启停。

截图走 adb exec-out screencap（mumu-cli 没有截图子命令）。
"""
import subprocess
import time

from core import proc

CREATE_NO_WINDOW = 0x08000000


def _run(args, timeout=20):
    try:
        r = subprocess.run(
            args, capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW
        )
        return r.returncode, r.stdout, r.stderr
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        return -1, b"", b""


def is_connected(cfg):
    """ADB 设备是否在线（adb devices 输出包含 device 且状态为 device）。"""
    paths = cfg["paths"]
    code, out, _ = _run([paths["adb"], "devices"])
    text = out.decode(errors="ignore")
    return code == 0 and f"{paths['device']}\tdevice" in text


def connect(cfg):
    """连接设备（与 master.ps1 相同的探测方式），返回输出文本。"""
    paths = cfg["paths"]
    code, out, _ = _run([paths["adb"], "connect", paths["device"]])
    return out.decode(errors="ignore").strip()


def screenshot_bytes(cfg):
    """截取模拟器当前画面，返回 PNG 字节；失败返回 None。"""
    paths = cfg["paths"]
    code, out, _ = _run(
        [paths["adb"], "-s", paths["device"], "exec-out", "screencap", "-p"],
        timeout=30,
    )
    if code != 0 or not out:
        return None
    # adb 输出可能带 \r\n 前缀杂质，按 PNG 头定位
    png_head = b"\x89PNG\r\n\x1a\n"
    idx = out.find(png_head)
    return out[idx:] if idx > 0 else out


def tap(cfg, x, y):
    """在设备上实际点击（取点验证用）。"""
    paths = cfg["paths"]
    _run(
        [
            paths["adb"], "-s", paths["device"], "shell", "input", "tap",
            str(int(x)), str(int(y)),
        ],
        timeout=10,
    )


def maa_running():
    """MAA 进程是否在运行。"""
    return proc.process_running("MAA.exe")


def emulator_running():
    """MuMu 主界面进程是否在运行。"""
    return proc.process_running("MuMuNxMain.exe")


def launch_emulator(cfg):
    """拉起模拟器（控制台 launch，不等待 ADB 就绪）。"""
    _run([cfg["paths"]["cli"], "control", "-v", "0", "launch"], timeout=30)


def close_emulator(cfg):
    """关闭模拟器：先 shutdown 虚拟机，再 main close 关主界面。

    注意：直接杀 MuMuNxMain 会被服务拉起（respawn），
    必须用 mumu-cli main close 正常退出。
    """
    _run([cfg["paths"]["cli"], "control", "-v", "0", "shutdown"], timeout=30)
    time.sleep(3)
    _run([cfg["paths"]["cli"], "main", "close"], timeout=30)
