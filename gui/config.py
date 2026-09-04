# -*- coding: utf-8 -*-
r"""统一配置：D:\1\config.json

GUI 与 PowerShell 脚本（master.ps1 / slot_switch.ps1 等）共享这份配置。
脚本顶部尝试读取，读不到（文件缺失或字段缺失）时回退到各自的硬编码默认值。
"""
import json
import os
import re
from copy import deepcopy
from pathlib import Path

CONFIG_PATH = Path(r"D:\1\config.json")


def batch_name(time):
    """HH:MM → 班次名：04:00 → 4点，00:00 → 24点。"""
    m = re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(time))
    if not m:
        return str(time)
    hour = int(m.group(1))
    return "%d点" % (24 if hour == 0 else hour)


def schedule_batches(cfg):
    """当前配置里的班次名（按时间升序）；缺配置时回退 4点/16点。"""
    sched = cfg.get("schedule") if isinstance(cfg, dict) else {}
    times = sched.get("times") if isinstance(sched.get("times"), list) else []
    order = []
    seen = set()
    for it in times:
        if not isinstance(it, dict):
            continue
        t = str(it.get("time", "")).strip()
        if t in seen or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
            continue
        seen.add(t)
        order.append((t, batch_name(t)))
    order.sort()
    return [name for _t, name in order] or ["4点", "16点"]


def default_base_schedule(layout="333", batches=None):
    """账号级「精确基建派驻」配置默认结构（与 plugins\base_schedule 一致）。

    layout 名称按「贸易站/制造站/发电站」顺序读：
    333 = 贸易3台/制造3台/发电3台；243 = 贸易2台/制造4台/发电3台（旧名423）。
    batches 每个班次各含 6 类设施：
        control    控制中枢 5 人（列表）
        meeting    会客室   2 人（列表）
        manufacture 制造站  3 人/台（每台 {product, operators}，台数随 layout；
                    product: Pure Gold 赤金 / Originium Shard 原石碎片 /
                    Battle Record 作战记录）
        trading    贸易站  3 人/台（每台 {product, operators}；
                    product: LMD 赤金订单 / Orundum 原石碎片订单）
        power      发电站  1 人/台（二维列表，固定 3 台）
        dormitory  干员休整宿舍 5 人/间（二维列表，固定 4 间，留空自动安排）
        office     办公室  1 人（列表）
        processing 加工站  1 人（列表，可选；留空时 MAA 在自定义模式下跳过加工站）
    （可选）每个批次还可带 fiammetta：{enable, target, order} —— 该批次是否使用
    菲亚梅塔、目标干员、换班前/后；一般由「导入排班文件」功能写入。
    drones      无人机（全局，每个批次都投放）：
        {room: manufacture/trading, index: 站号 1 起,
         enable: 是否启用, order: pre 换班前/post 换班后}
    """
    m = 4 if layout in ("423", "243") else 3
    t = 2 if layout in ("423", "243") else 3

    def batch():
        return {
            "control": [""] * 5,
            "meeting": [""] * 2,
            "manufacture": [
                {"product": "Pure Gold", "operators": [""] * 3} for _ in range(m)],
            "trading": [
                {"product": "LMD", "operators": [""] * 3} for _ in range(t)],
            "power": [[""] for _ in range(3)],
            "dormitory": [[""] * 5 for _ in range(4)],
            "office": [""],
            "processing": [""],
        }

    if not batches:
        batches = ["4点", "16点"]
    return {
        "enabled": False,
        "layout": layout,
        "drones": {
            "room": "manufacture",
            "index": 1,
            "enable": False,
            "order": "pre",
        },
        "batches": {b: batch() for b in batches},
    }


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
        "game_update_min": 90,    # 游戏更新检测最长等待（分钟）
    },
    "accounts": [
        # 账号数组（顺序即运行顺序）。slot = scripts\accounts\<slot> 登录数据目录
        # 旧版 {official1: bool, ...} 对象形式由 _migrate_accounts() 自动迁移
        {"id": "official1", "label": "官服 1", "server": "official",
         "enabled": True, "slot": "official_1", "username": "", "password": "",
         "base_schedule": default_base_schedule()},
        {"id": "official2", "label": "官服 2", "server": "official",
         "enabled": True, "slot": "official_2", "username": "", "password": "",
         "base_schedule": default_base_schedule()},
        {"id": "bilibili", "label": "B 服", "server": "bilibili",
         "enabled": True, "slot": "bilibili_1", "username": "", "password": "",
         "base_schedule": default_base_schedule()},
    ],
    "behavior": {
        "close_emulator": True,   # 完成后关模拟器
        "wait_game_update": True, # 检测到游戏更新时先等更新完成，再开始登录检测
        # 旧版早晚班关机开关（新格式迁移进 schedule.times 后不再使用，仅兼容回退）
        "morning_shutdown": True,
        "evening_shutdown": False,
    },
    "schedule": {
        "times": [
            {"time": "04:00", "enabled": True, "shutdown": True},
            {"time": "16:00", "enabled": True, "shutdown": False},
        ],
    },
    "maa_update": {
        "use_vpn": True,   # 更新前启动 Clash、全部结束后关闭（更新前已开着则复用）
        "vpn_exe": r"D:\软件\Flclash\clash-verge.exe",  # Clash Verge Rev 主程序
        "proxy_port": 7897,  # Clash 混合端口（verge.yaml verge_mixed_port）
        "timeout_min": 15,   # 单套 MAA 更新（下载+安装）等待上限
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
            # 第二理智作战候选关卡（对应 MAA StagePlan 列表；空 = 跟随 MAA 原设置）
            if not isinstance(a.get("second_fight_plan"), list):
                legacy = a.pop("second_fight_stage", None)
                if isinstance(legacy, list):
                    plan = [str(x).strip() for x in legacy if str(x).strip()]
                elif isinstance(legacy, str) and legacy.strip():
                    plan = [legacy.strip()]
                else:
                    plan = []
                a["second_fight_plan"] = plan
            a.setdefault("second_fight_use_optional", True)
            a.setdefault("base_schedule", default_base_schedule())


def _migrate_schedule(cfg):
    """旧版 schedule.morning/evening → schedule.times 列表，并规范化每项字段。

    每项：{"time": "HH:MM", "enabled": bool, "shutdown": bool}。
    shutdown 从旧 behavior.morning_shutdown / evening_shutdown 迁移。
    """
    sched = cfg.get("schedule")
    if not isinstance(sched, dict):
        cfg["schedule"] = deepcopy(DEFAULTS["schedule"])
        return

    # 旧格式优先：morning/evening 存在时按它重建
    old = []
    for key, tkey, def_shutdown in (
        ("morning", "morning_shutdown", True),
        ("evening", "evening_shutdown", False),
    ):
        item = sched.get(key)
        if isinstance(item, dict):
            t = str(item.get("time", "")).strip()
            if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
                old.append({
                    "time": t,
                    "enabled": bool(item.get("enabled", True)),
                    "shutdown": bool((cfg.get("behavior") or {}).get(tkey, def_shutdown)),
                })
    if old:
        sched["times"] = old
        sched.pop("morning", None)
        sched.pop("evening", None)
        return

    # 新格式：规范化 times 列表
    times = []
    for it in sched.get("times") if isinstance(sched.get("times"), list) else []:
        if not isinstance(it, dict):
            continue
        t = str(it.get("time", "")).strip()
        if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
            times.append({
                "time": t,
                "enabled": bool(it.get("enabled", True)),
                "shutdown": bool(it.get("shutdown", False)),
            })
    if not times:
        times = deepcopy(DEFAULTS["schedule"]["times"])
    sched["times"] = times
    sched.pop("morning", None)
    sched.pop("evening", None)

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
    _migrate_schedule(cfg)
    return cfg


def save(cfg: dict):
    """原子写入（临时文件 + 替换），UTF-8 无 BOM，中文不转义。"""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, CONFIG_PATH)
