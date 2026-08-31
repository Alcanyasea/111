# -*- coding: utf-8 -*-
"""按服务器一键配置 MAA（修正 gui.new.json 关键项）。

每个服务器独立按钮：修正当前配置下的客户端类型 / ADB 路径与地址 /
RunDirectly（直接运行）/ 结束脚本 signal_done.bat / 常用任务启用。
某服 MAA 目录缺失时，自动从另一服复制一份再修正（类似手动复制 MAA 文件夹）。
"""
import json
import os
import shutil
from pathlib import Path

from core import proc

SERVER_NAMES = {"official": "官服", "bilibili": "B服"}
CLIENT_TYPES = {"official": "Official", "bilibili": "Bilibili"}
KEY_TASKS = ("StartUpTask", "RecruitTask", "InfrastTask", "FightTask",
             "MallTask", "AwardTask")


def _maa_dir(cfg, server):
    key = "maa_official_dir" if server == "official" else "maa_bilibili_dir"
    d = (cfg.get("paths") or {}).get(key)
    return Path(d) if d else None


def _other_server(server):
    return "bilibili" if server == "official" else "official"


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _backup(path):
    try:
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    except OSError:
        pass


def ensure_maa_dir(cfg, server):
    """目标 MAA 目录缺失时从另一服复制；返回 (dir, copied, note)。"""
    target = _maa_dir(cfg, server)
    if target is None:
        return None, False, "未配置「%s」MAA 目录（请先在运行设置里填写路径）" % SERVER_NAMES[server]
    if target.is_dir():
        return target, False, ""
    other = _maa_dir(cfg, _other_server(server))
    if other is None or not other.is_dir():
        return None, False, "「%s」与另一服 MAA 目录都不存在，无法复制" % SERVER_NAMES[server]
    if proc.process_running("MAA.exe"):
        return None, False, "MAA 正在运行，请先关闭 MAA 再操作"
    try:
        shutil.copytree(other, target)
    except OSError as exc:
        return None, False, "复制 MAA 失败：%s" % exc
    return target, True, "已从「%s」复制一份 MAA 到 %s" % (
        SERVER_NAMES[_other_server(server)], target)


def apply_server_config(cfg, server):
    """一键配置某服 MAA。返回 (ok: bool, message: str)。"""
    name = SERVER_NAMES.get(server, server)
    target, copied, note = ensure_maa_dir(cfg, server)
    if target is None:
        return False, note

    gui_new = target / "config" / "gui.new.json"
    gui_json = target / "config" / "gui.json"
    if not gui_new.is_file() or not gui_json.is_file():
        return False, "「%s」MAA 缺少 config\\gui.new.json 或 config\\gui.json，无法配置" % name

    _backup(gui_new)
    _backup(gui_json)

    paths = cfg.get("paths") or {}
    adb = str(paths.get("adb", ""))
    device = str(paths.get("device", "127.0.0.1:16384"))
    signal = str(Path(paths.get("script_dir", r"D:\1\scripts")) / "signal_done.bat")

    try:
        data = _read_json(gui_new)
    except (OSError, json.JSONDecodeError) as exc:
        return False, "读取 %s 失败：%s" % (gui_new, exc)

    cur = data.get("Current") or "Default"
    conf = (data.get("Configurations") or {}).get(cur)
    if not isinstance(conf, dict):
        return False, "「%s」gui.new.json 中找不到当前配置「%s」" % (name, cur)

    changes = []
    gui = conf.setdefault("Gui", {})
    conn = gui.setdefault("ConnectSettings", {})
    if str(conn.get("AdbPath", "")) != adb:
        conn["AdbPath"] = adb
        changes.append("ADB 路径")
    if str(conn.get("Address", "")) != device:
        conn["Address"] = device
        changes.append("ADB 地址")

    rt = gui.setdefault("RuntimeSettings", {})
    if rt.get("ClientType") != CLIENT_TYPES[server]:
        rt["ClientType"] = CLIENT_TYPES[server]
        changes.append("客户端类型(%s)" % name)
    if str(rt.get("PostRunScript", "")) != signal:
        rt["PostRunScript"] = signal
        changes.append("结束脚本 signal_done.bat")

    su = gui.setdefault("StartUpSettings", {})
    if su.get("RunDirectly") is not True:
        su["RunDirectly"] = True
        changes.append("RunDirectly 直接运行")

    queue = conf.get("TaskQueue")
    if isinstance(queue, list):
        present = {t.get("$type") for t in queue if isinstance(t, dict)}
        for tname in KEY_TASKS:
            if tname in present:
                for t in queue:
                    if isinstance(t, dict) and t.get("$type") == tname \
                            and not t.get("IsEnable", True):
                        t["IsEnable"] = True
                        changes.append("启用 %s" % tname)
        missing = [t for t in KEY_TASKS if t not in present]
        if missing:
            changes.append("任务队列缺少 %s（未自动创建）" % "、".join(missing))

    try:
        _write_json(gui_new, data)
    except OSError as exc:
        return False, "写入 %s 失败：%s" % (gui_new, exc)

    parts = []
    if note:
        parts.append(note)
    if changes:
        parts.append("已修正：" + "、".join(changes))
    else:
        parts.append("无需修改，配置已正确")
    return True, "；".join(parts)
