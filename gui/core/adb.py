# -*- coding: utf-8 -*-
"""ADB 与 MuMu 模拟器交互：连接检测、进程检测。

设备截图/点击已迁往 scripts\vision（vision.py，MAA 同款模板匹配 + PaddleOCR），
GUI 自身不再需要屏幕交互能力（v2 删除 screenshot_bytes/tap 死代码）；
模拟器的拉起与关闭由 master.ps1 负责，GUI 只做状态查询。
"""
from core import proc
from core.util import decode_console, run as _run

_TIMEOUT_QUICK = 12   # adb devices / connect：偶发卡顿时也要让关窗等待等得起


def is_connected(cfg):
    """ADB 设备是否在线（adb devices 输出包含 device 且状态为 device）。"""
    paths = cfg["paths"]
    code, out, _ = _run([paths["adb"], "devices"], timeout=_TIMEOUT_QUICK)
    text = decode_console(out)
    return code == 0 and f"{paths['device']}\tdevice" in text


def maa_running():
    """MAA 进程是否在运行。"""
    return proc.process_running("MAA.exe")


def emulator_running():
    """MuMu 主界面进程是否在运行。"""
    return proc.process_running("MuMuNxMain.exe")
