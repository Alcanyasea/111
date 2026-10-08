# -*- coding: utf-8 -*-
"""core 公共小工具：无窗口子进程调用、控制台输出解码、安全的整数转换。

此前 CREATE_NO_WINDOW / _run / 解码逻辑在 runner、adb、scheduler、maa_update、
accounts 各复制一份，且解码策略不一致（有的只试 GBK，有的 utf-8→gbk），
统一收拢到这里。
"""
import os
import subprocess
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000

# 随安装包内置的便携版 pwsh（gui/runtime/pwsh/，由安装器解压），优先于系统安装
_PORTABLE_PWSH = str(Path(__file__).resolve().parents[2]
                     / "gui" / "runtime" / "pwsh" / "pwsh.exe")

# GUI 里所有 pwsh 子进程必须用绝对路径启动：GUI 由 pythonw 双击拉起，其 PATH
# 不保证含 pwsh，且系统 PATH 条目可能被其他软件安装改坏（实测出现过丢盘符的
# 坏条目）——裸 "pwsh" 在 Popen 时直接 FileNotFoundError，无 stderr 可排查，
# 表现为 GUI 手动启动/快速启动/捕获账号全部「点了没反应」。
# 商店版（MSIX）执行别名在计划任务下直接 0x80070002 起不来，MSI 版 Program
# Files 优先；两处候选都找不到时回退裸名（PATH 正常的终端调试场景仍可用）。
_PWSH_CANDIDATES = (
    _PORTABLE_PWSH,
    r"C:\Program Files\PowerShell\7\pwsh.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe"),
)


def pwsh_exe():
    """启动 PowerShell 7 子进程用的可执行文件路径（绝对路径优先，见上方注释）。"""
    for p in _PWSH_CANDIDATES:
        if os.path.isfile(p):
            return p
    return "pwsh.exe"


def run(args, timeout=15):
    """一次性命令：返回 (code, stdout, stderr)，超时/启动失败返回 (-1, b"", b"")。"""
    try:
        r = subprocess.run(
            args, capture_output=True, timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        return r.returncode, r.stdout, r.stderr
    except (subprocess.TimeoutExpired, OSError):
        return -1, b"", b""


def decode_console(data):
    """PowerShell 等控制台输出解码：优先 UTF-8，退 GBK，最后丢不可解字节。

    PS 5.1 重定向输出在中文系统上是 ANSI(GBK)，但个别脚本/工具会输出 UTF-8，
    所以维持「先 utf-8 后 gbk」的双探测，与原 scheduler._dec 行为一致。
    """
    if isinstance(data, str):
        return data
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode(errors="replace")


def to_int(value, default):
    """宽松整数转换：config.json 手改成字符串等脏值不抛异常，回退默认。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
