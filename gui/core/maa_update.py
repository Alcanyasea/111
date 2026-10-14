# -*- coding: utf-8 -*-
"""打开 MAA 主程序（更新交给 MAA 自身）+ 版本号读取 + 更新中断残留恢复。

历史：控制台曾自带一套「开 Clash → 临时改 MAA 配置 → 启动 MAA → 盯日志
驱动安装」的更新流程。MAA 6.18.0 起资源热更包改为启动时自动下载安装，
与该流程的日志监听标记互相误判（把资源包下载当成版本包下载 → 反复强杀
重启 → 15 分钟假超时， worst case 把正在应用资源包的 MAA 杀成「资源损坏」）。
2026-10 起控制台不再驱动更新：MAA 自己的 CheckOnStartup / AutoDownload /
AutoInstall 已开启，打开主程序即自动检查并安装更新（含资源热更）。

本模块现在只负责三件事：
- open_maa：打开一套 MAA 主程序；
- 版本号读取（仪表盘「连接状态与 MAA」卡片的版本徽章）；
- recover_all：旧版控制台更新中断残留的临时配置恢复（main.py 启动时调用，
  防御从旧版本升级上来的场景——残留的 RunDirectly=false 会让每轮挂机超时）。

MAA 是单实例程序（互斥锁）：两套不能同时开，后开的一套会立刻退出。
"""
import ctypes
import json
import os
import subprocess
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

from core.util import CREATE_NO_WINDOW

MAA_EXE = "MAA.exe"


# ---------- 版本号 ----------

_version_dll = ctypes.WinDLL("version", use_last_error=True)


def file_version(exe_path):
    """读取 exe 文件版本号（如 "6.17.1.0"）；读不到返回 None。"""
    path = str(exe_path)
    try:
        size = _version_dll.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        data = ctypes.create_string_buffer(size)
        if not _version_dll.GetFileVersionInfoW(path, 0, size, data):
            return None
        ptr = ctypes.c_void_p()
        blen = wintypes.UINT()
        if not _version_dll.VerQueryValueW(data, "\\", ctypes.byref(ptr),
                                           ctypes.byref(blen)):
            return None
        # VS_FIXEDFILEINFO：签名/结构版本之后是版本号高低两个 DWORD
        fixed = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint32 * 4)).contents
        ms, ls = fixed[2], fixed[3]
        return "%d.%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except OSError:
        return None


def _ver_tuple(v):
    try:
        return tuple(int(x) for x in str(v).lstrip("vV").split("."))
    except ValueError:
        return None


def _ver_gt(a, b):
    """a 的版本号是否比 b 新（位数不同按 0 补齐比较；带不带 v 前缀均可）。"""
    ta, tb = _ver_tuple(a), _ver_tuple(b)
    if ta is None or tb is None:
        return False
    n = max(len(ta), len(tb))
    ta += (0,) * (n - len(ta))
    tb += (0,) * (n - len(tb))
    return ta > tb


def has_update(cur, latest):
    """最新版本是否比当前新（任一未知返回 False）。"""
    return bool(cur and latest and _ver_gt(latest, cur))


def fmt_version(v):
    """展示用版本号：去掉 v 前缀和末尾多余的 .0（"v6.17.1" / "6.11.1.0" → "6.17.1"）。"""
    t = _ver_tuple(v)
    if t is None:
        return "未知"
    while len(t) > 3 and t[-1] == 0:
        t = t[:-1]
    return ".".join(str(x) for x in t) or "未知"


def _maa_targets(cfg):
    """[(key, 名称, 目录), ...]，目录可能为 None（未配置）。"""
    paths = cfg.get("paths") or {}
    return (
        ("official", "官服", paths.get("maa_official_dir")),
        ("bilibili", "B服", paths.get("maa_bilibili_dir")),
    )


def latest_version(maa_dir):
    """MAA 缓存的最新版本号（cache\\version\\stable.json 的 version 字段）。"""
    try:
        data = json.loads((Path(maa_dir) / "cache" / "version" / "stable.json")
                          .read_text(encoding="utf-8"))
        v = str(data.get("version") or data.get("tag_name") or "").strip()
        return v or None
    except (OSError, json.JSONDecodeError):
        return None


def version_rows(cfg):
    """仪表盘展示用：[(名称, 当前版本, 最新版本, 缓存检查时间戳), ...]。

    最新版本来自 MAA 本地缓存（cache\\version\\stable.json，MAA 自己检查后写入），
    缓存检查时间戳 = 该文件的 mtime；两者缺失为 None。
    """
    rows = []
    for _key, name, d in _maa_targets(cfg):
        if not d or not Path(d).is_dir():
            rows.append((name, None, None, None))
            continue
        exe = Path(d) / MAA_EXE
        ver_file = Path(d) / "cache" / "version" / "stable.json"
        try:
            checked = ver_file.stat().st_mtime
        except OSError:
            checked = None
        rows.append((name, file_version(exe) if exe.is_file() else None,
                     latest_version(d), checked))
    return rows


# ---------- 打开 MAA ----------

def open_maa(cfg, key):
    """打开指定一套 MAA 主程序（key: "official" / "bilibili"）。

    更新完全由 MAA 自身完成：启动时按 gui.new.json 顶层 Update 块自动检查
    并安装更新（CheckOnStartup / AutoDownloadUpdatePackage /
    AutoInstallUpdatePackage），原地升级、装完自动重启。

    返回 (ok: bool, message: str)；调用方负责挂机互斥与「已在运行」提示。
    """
    for k, name, d in _maa_targets(cfg):
        if k != key:
            continue
        if not d or not Path(d).is_dir():
            return False, "%s MAA 目录未配置或不存在" % name
        exe = Path(d) / MAA_EXE
        if not exe.is_file():
            return False, "找不到 %s" % exe
        try:
            subprocess.Popen([str(exe)], cwd=str(d),
                             creationflags=CREATE_NO_WINDOW)
        except OSError as exc:
            return False, "启动 %s MAA 失败：%s" % (name, exc)
        return True, "%s MAA 已打开" % name
    return False, "未知的 MAA 标识：%s" % key


# ---------- 更新中断残留恢复 ----------

def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path, data):
    tmp = Path(path).with_suffix(Path(path).suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# 崩溃恢复标记：旧版控制台驱动式更新改 gui.new.json 前会把「原值快照」写进
# 该标记，正常结束时删除。旧版在更新途中被强杀/断电时 finally 恢复不会执行，
# 残留的 RunDirectly=false 会让之后每轮挂机等 maa_done.signal 全部超时失败。
# 新版控制台不再改 MAA 配置，但启动（main.py）检测到旧版残留的标记仍会按
# 快照自动复原，兼容从旧版本升级上来的场景。
RECOVER_MARKER = "gui.new.json.update-recover"


def _master_log(cfg, msg):
    """恢复事件写进 master_log.txt（控制台日志页可见）；失败静默。"""
    try:
        lp = (cfg.get("paths") or {}).get("log_file") if isinstance(cfg, dict) else None
        if not lp:
            return
        with open(lp, "a", encoding="utf-8") as f:
            f.write("%s - [MAA更新恢复] %s\n" % (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def _restore_update_config(maa_dir, saved):
    """按原值快照恢复 gui.new.json（MAA 进程须已退出，否则会被回写覆盖）。"""
    gui_new = Path(maa_dir) / "config" / "gui.new.json"
    try:
        data = _read_json(gui_new)
    except (OSError, json.JSONDecodeError):
        return
    update = data.setdefault("Update", {})
    update["Proxy"] = saved.get("Proxy", "")
    update["ProxyType"] = saved.get("ProxyType", "Http")
    update["CheckOnStartup"] = saved.get("CheckOnStartup", False)
    update["AutoDownloadUpdatePackage"] = saved.get("AutoDownloadUpdatePackage", False)
    update["AutoInstallUpdatePackage"] = saved.get("AutoInstallUpdatePackage", False)
    conf = (data.get("Configurations") or {}).get(data.get("Current") or "Default")
    if isinstance(conf, dict) and "RunDirectly" in saved:
        conf.setdefault("Gui", {}).setdefault("StartUpSettings", {})[
            "RunDirectly"] = saved["RunDirectly"]
    try:
        _write_json(gui_new, data)
    except OSError:
        return
    # 恢复成功才删标记：恢复本身失败（读损坏/写失败）时标记保留，
    # 下次控制台启动会再试一次
    try:
        (Path(maa_dir) / "config" / RECOVER_MARKER).unlink()
    except OSError:
        pass


def recover_pending_update(maa_dir):
    """检测并恢复上次更新崩溃残留的临时配置。返回是否执行了恢复。"""
    marker = Path(maa_dir) / "config" / RECOVER_MARKER
    try:
        saved = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        saved = None
    if not isinstance(saved, dict):
        # 标记缺失或损坏（写一半被断电）：损坏则删掉，防每次启动都白检测
        try:
            if marker.exists():
                marker.unlink()
        except OSError:
            pass
        return False
    _restore_update_config(maa_dir, saved)
    return not marker.exists()


def recover_all(cfg, log=None):
    """对两套 MAA 逐一做崩溃恢复。返回恢复的套数。

    GUI 启动（main.py）调用：挂机流程要求 MAA 的 RunDirectly=true，
    旧版残留不恢复则每轮挂机必超时失败。
    """
    n = 0
    for _key, name, d in _maa_targets(cfg):
        if not d or not Path(d).is_dir():
            continue
        try:
            if recover_pending_update(d):
                n += 1
                msg = "检测到 %s MAA 上次更新异常中断，已自动恢复其临时配置修改" % name
                if log:
                    log(msg)
                _master_log(cfg, msg)
        except Exception:  # 恢复失败不阻塞启动，标记保留待下次再试
            pass
    return n
