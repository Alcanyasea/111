# -*- coding: utf-8 -*-
r"""统一配置：D:\1\config.json

GUI 与 PowerShell 脚本（master.ps1 / slot_switch.ps1 等）共享这份配置。
脚本顶部尝试读取，读不到（文件缺失或字段缺失）时回退到各自的硬编码默认值。
"""
import json
import os
from copy import deepcopy
from pathlib import Path

CONFIG_PATH = Path(r"D:\1\config.json")

DEFAULTS = {
    "paths": {
        "maa_official": r"D:\软件\MAA\MAA-v6.11.1-win-x64\MAA.exe",
        "maa_official_dir": r"D:\软件\MAA\MAA-v6.11.1-win-x64",
        "maa_bilibili": r"D:\软件\MAA（b）\MAA.exe",
        "maa_bilibili_dir": r"D:\软件\MAA（b）",
        "adb": r"D:\软件\MuMu模拟器\MuMuPlayer\nx_main\adb.exe",
        "cli": r"D:\软件\MuMu模拟器\MuMuPlayer\nx_main\mumu-cli.exe",
        "device": "127.0.0.1:16384",
        "script_dir": r"D:\1\scripts",
        "log_file": r"D:\1\scripts\master_log.txt",
    },
    "timeouts": {
        "maa_min": 30,            # 单个 MAA 任务超时（分钟）
        "launch_wait_sec": 120,   # 模拟器启动等待上限（秒）
    },
    "accounts": [
        # 账号数组（顺序即运行顺序）。slot = scripts\accounts\<slot> 登录数据目录
        # 旧版 {official1: bool, ...} 对象形式由 _migrate_accounts() 自动迁移
        {"id": "official1", "label": "官服 1", "server": "official",
         "enabled": True, "slot": "official_1", "username": "", "password": ""},
        {"id": "official2", "label": "官服 2", "server": "official",
         "enabled": True, "slot": "official_2", "username": "", "password": ""},
        {"id": "bilibili", "label": "B 服", "server": "bilibili",
         "enabled": True, "slot": "bilibili_1", "username": "", "password": ""},
    ],
    "behavior": {
        "close_emulator": True,   # 完成后关模拟器
        "morning_shutdown": True, # 早班成功后 60 秒倒计时关机
    },
    "schedule": {
        "morning": {"time": "04:00", "enabled": True},
        "evening": {"time": "16:00", "enabled": True},
    },
    "cleanup": {
        "auto": True,          # 自动清理开关（控制台运行期间定期清理）
        "interval_days": 7,    # 自动清理间隔（天）
        "last_run": "",        # 上次清理时间 "YYYY-MM-DD HH:MM"，空 = 从未清理
    },
}


def _deep_merge(base, override):
    """override 深合并进 base（base 为默认结构，override 是用户文件内容）。"""
    out = deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _migrate_accounts(cfg):
    """旧版 accounts 布尔对象 → 新版账号数组（保留启用状态与顺序）。

    数组形式则补齐缺失字段。GUI 保存后 config.json 即为数组格式；
    master.ps1 只认数组格式（槽位切号），非数组时拒绝运行。
    """
    accs = cfg.get("accounts")
    if isinstance(accs, dict):
        cfg["accounts"] = [
            {"id": "official1", "label": "官服 1", "server": "official",
             "enabled": bool(accs.get("official1", True)), "slot": "official_1",
             "username": "", "password": ""},
            {"id": "official2", "label": "官服 2", "server": "official",
             "enabled": bool(accs.get("official2", True)), "slot": "official_2",
             "username": "", "password": ""},
            {"id": "bilibili", "label": "B 服", "server": "bilibili",
             "enabled": bool(accs.get("bilibili", True)), "slot": "bilibili_1",
             "username": "", "password": ""},
        ]
    if isinstance(cfg.get("accounts"), list):
        for i, a in enumerate(cfg["accounts"]):
            if not isinstance(a, dict):
                cfg["accounts"][i] = {"label": str(a), "enabled": True}
                a = cfg["accounts"][i]
            a.setdefault("id", a.get("label") or ("acc%d" % (i + 1)))
            a.setdefault("label", a["id"])
            a.setdefault("server", "official")
            a.setdefault("enabled", True)
            a.setdefault("slot", "")
            a.setdefault("username", "")
            a.setdefault("password", "")


def load() -> dict:
    """读取配置；文件缺失/损坏/字段缺失时用默认值补齐。"""
    cfg = deepcopy(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = _deep_merge(cfg, json.load(f))
        except (json.JSONDecodeError, OSError):
            pass  # 损坏时回退默认，不阻塞 GUI 启动
    _migrate_accounts(cfg)
    return cfg


def save(cfg: dict):
    """原子写入（临时文件 + 替换），UTF-8 无 BOM，中文不转义。"""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, CONFIG_PATH)
