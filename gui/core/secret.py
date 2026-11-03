# -*- coding: utf-8 -*-
"""Windows DPAPI（CurrentUser）字符串加解密：账号密码用它落盘。

存储格式 ``dpapi:<base64>``；没有前缀的值按旧版明文原样返回，便于平滑迁移。
解密失败（换了 Windows 用户/机器、密文损坏）返回 ``None``——调用方必须保留原值，
不能把空串写回 config.json 覆盖凭据。
"""
import base64
import ctypes
import sys
from ctypes import wintypes

PREFIX = "dpapi:"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _load_windll():
    if sys.platform != "win32":
        return None, None
    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except OSError:
        return None, None
    blob = ctypes.POINTER(_DataBlob)
    crypt32.CryptProtectData.argtypes = [
        blob, wintypes.LPCWSTR, blob, ctypes.c_void_p, ctypes.c_void_p,
        wintypes.DWORD, blob]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        blob, ctypes.POINTER(wintypes.LPWSTR), blob, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, blob]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


_CRYPT32, _KERNEL32 = _load_windll()


def available():
    """当前平台能否做 DPAPI 加解密。"""
    return _CRYPT32 is not None


def is_protected(value):
    return isinstance(value, str) and value.startswith(PREFIX)


def _call(fn, data):
    """调用 CryptProtectData / CryptUnprotectData，返回 bytes 或 None。"""
    if _CRYPT32 is None:
        return None
    src = ctypes.create_string_buffer(data, len(data))
    src_blob = _DataBlob(len(data), ctypes.cast(src, ctypes.POINTER(ctypes.c_char)))
    out = _DataBlob()
    ok = fn(ctypes.byref(src_blob), None, None, None, None, 0, ctypes.byref(out))
    if not ok:
        return None
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        _KERNEL32.LocalFree(out.pbData)


def protect(text):
    """明文 → ``dpapi:<base64>``；空值/已加密原样返回，加密失败返回 None。"""
    if not isinstance(text, str) or not text or is_protected(text):
        return text
    raw = _call(_CRYPT32.CryptProtectData, text.encode("utf-8")) if _CRYPT32 else None
    if raw is None:
        return None
    return PREFIX + base64.b64encode(raw).decode("ascii")


def unprotect(value):
    """``dpapi:<base64>`` → 明文；旧版明文原样返回；解密失败返回 None。"""
    if not isinstance(value, str):
        return ""
    if not value or not is_protected(value):
        return value
    try:
        blob = base64.b64decode(value[len(PREFIX):], validate=True)
    except (ValueError, TypeError):
        return None
    if not blob:
        return None
    raw = _call(_CRYPT32.CryptUnprotectData, blob) if _CRYPT32 else None
    if raw is None:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
