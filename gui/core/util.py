# -*- coding: utf-8 -*-
"""core 公共小工具：无窗口子进程调用、控制台输出解码、安全的整数转换。

此前 CREATE_NO_WINDOW / _run / 解码逻辑在 runner、adb、scheduler、maa_update、
accounts 各复制一份，且解码策略不一致（有的只试 GBK，有的 utf-8→gbk），
统一收拢到这里。
"""
import subprocess

CREATE_NO_WINDOW = 0x08000000


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
