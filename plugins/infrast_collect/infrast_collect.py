# -*- coding: utf-8 -*-
r"""基建收菜插件：MAA 切到「自定义模式 + 全部房间 skip」，只收产物不换班。

由 master.ps1 -InfrastCollect 在启动每个账号的 MAA 前调用（替代常规的
基建/理智/菲亚梅塔三个插件）。原理（MAA 基建排班协议的官方语义）：
- 房间条目 skip=true 时「仅跳过换干员操作，其他如使用无人机、线索交流
  等仍会正常进行」——即进房间收取产物/订单，但完全不动干员；
- 计划里不含宿舍/会客室等设施，配合 RoomList 只勾制造站（Mfg）与
  贸易站（Trade），MAA 只进这两类设施；
- 计划级 drones 关闭（纯收菜，不用无人机加速生产）；
- 停用理智作战 / 招募 / 信用 / 领奖任务，只留开始唤醒 + 基建。

收菜计划与账号无关（skip + 空 operators 对任何号都成立），两套 MAA
共用同一份 collect_plan.json，由本插件在每次 apply 时生成。

对 gui.new.json 的全部改动由 master.ps1 备份并在收菜结束后恢复；
正常挂机时每个账号也会由基建插件重新写入自己的排班配置。

用法（由 master.ps1 调用）：
    python infrast_collect.py apply --config D:\1\config.json --account acc_xxx
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = Path(r"D:\1\config.json")

# 收菜时停用的任务（开始唤醒 + 基建保留：要先进游戏才收得了菜）
DISABLE_TASKS = ("RecruitTask", "FightTask", "MallTask", "AwardTask")
# 收菜目标设施
COLLECT_ROOMS = ("Mfg", "Trade")


def _atomic_json_write(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def write_collect_plan():
    """生成收菜计划 JSON（字段结构与 MAA 官方 custom_infrast 示例一致）。

    制造站 3 间 + 贸易站 3 间全部 skip=true、operators 留空：进房间只收
    产物/订单，不进干员选择界面；product 字段按协议惯例给出（skip 生效
    时不会被使用，不会改变房间产物线）。plan 级 drones 关闭。
    """
    def room(product):
        return {"skip": True, "operators": [], "sort": False,
                "autofill": False, "product": product}

    plan = {
        "title": "基建收菜（不换班）",
        "description": "所有生产房间 skip：只收取制造站产物与贸易站订单，不更换干员",
        "plans": [
            {
                "name": "收菜",
                "period": [["00:00", "23:59"]],
                "rooms": {
                    "manufacture": [room("Pure Gold")] * 3,
                    "trading": [room("LMD")] * 3,
                },
                "drones": {"room": "trading", "index": 1, "enable": False,
                           "order": "pre"},
            }
        ],
    }
    path = PLUGIN_DIR / "collect_plan.json"
    _atomic_json_write(path, plan)
    return path


def load_config(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def find_account(cfg, acc_id):
    for a in cfg.get("accounts") or []:
        if isinstance(a, dict) and a.get("id") == acc_id:
            return a
    return None


def _maa_dir(cfg, server):
    key = "maa_bilibili_dir" if server == "bilibili" else "maa_official_dir"
    d = (cfg.get("paths") or {}).get(key)
    return Path(d) if d else None


def apply_quick_collect(maa_dir):
    """把 MAA 当前配置改为「自定义·全 skip」收菜模式并停用其他任务。

    返回 (changed, message)；没有可改的条目视为失败，避免悄悄跑错任务。
    """
    gui_new = Path(maa_dir) / "config" / "gui.new.json"
    if not gui_new.exists():
        return False, "未找到 %s" % gui_new
    try:
        data = json.loads(gui_new.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, "读取 %s 失败：%s" % (gui_new, exc)

    cur = data.get("Current") or "Default"
    section = (data.get("Configurations") or {}).get(cur)
    if not isinstance(section, dict):
        return False, "MAA 当前配置「%s」不存在" % cur
    queue = section.get("TaskQueue")
    if not isinstance(queue, list):
        return False, "MAA 当前配置没有任务队列"

    plan_path = write_collect_plan()
    changes = []
    infrast = [t for t in queue
               if isinstance(t, dict) and t.get("$type") == "InfrastTask"]
    if not infrast:
        return False, "MAA 当前配置没有 InfrastTask，无法收菜"
    # 只保留一个基建任务（与基建插件的清理口径一致）
    for extra in infrast[1:]:
        queue.remove(extra)
    task = infrast[0]

    # 自定义模式 + 全 skip 计划：只收产物/订单，不动干员
    task["Mode"] = "Custom"
    task["CustomFileType"] = "user_defined"
    task["Filename"] = str(plan_path)
    task["PlanSelect"] = 0

    # 设施清单：只启用制造站/贸易站，其余禁用（保留条目，恢复备份后原样回来）
    rooms = task.get("RoomList")
    if not isinstance(rooms, list):
        rooms = []
        task["RoomList"] = rooms
    for name in COLLECT_ROOMS:
        hit = next((r for r in rooms if isinstance(r, dict) and r.get("Room") == name), None)
        if hit is None:
            rooms.insert(0, {"Room": name, "IsEnabled": True})
        elif hit.get("IsEnabled", True) is not True:
            hit["IsEnabled"] = True
    for r in rooms:
        if isinstance(r, dict) and r.get("Room") not in COLLECT_ROOMS:
            r["IsEnabled"] = False
    changes.append("全 skip 不换班·仅制造/贸易")

    # 任务队列：开始唤醒 + 基建保留，其余停用
    for t in queue:
        if not isinstance(t, dict):
            continue
        ttype = t.get("$type")
        if ttype == "StartUpTask":
            t["IsEnable"] = True
        elif ttype in DISABLE_TASKS and t.get("IsEnable", True):
            t["IsEnable"] = False
            changes.append(ttype.replace("Task", ""))

    try:
        _atomic_json_write(gui_new, data)
    except OSError as exc:
        return False, "写入 %s 失败：%s" % (gui_new, exc)
    return True, "基建收菜配置已写入（%s，计划 %s）" % ("、".join(changes), plan_path.name)


def _log_file(cfg, msg):
    try:
        lp = (cfg.get("paths") or {}).get("log_file")
        if not lp:
            return
        with open(lp, "a", encoding="utf-8") as f:
            f.write("%s - [基建收菜] %s\n" % (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def cmd_apply(args):
    cfg = load_config(args.config)
    acc = find_account(cfg, args.account)
    if acc is None:
        print("ERROR account not found: %s" % args.account)
        return 2

    server = args.server or acc.get("server") or "official"
    maa_dir = _maa_dir(cfg, server)
    if not maa_dir or not Path(maa_dir).exists():
        _log_file(cfg, "WARN 未找到 %s 的 MAA 目录：%s" % (server, maa_dir))
        print("ERROR no maa dir")
        return 2

    ok, msg = apply_quick_collect(maa_dir)
    _log_file(cfg, "账号「%s」：%s" % (acc.get("label") or args.account, msg))
    print(("OK " if ok else "ERROR ") + msg)
    return 0 if ok else 2


def main(argv=None):
    p = argparse.ArgumentParser(description="基建收菜插件（不换班·仅制造/贸易）")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("apply", help="按账号写入基建收菜配置")
    a.add_argument("--config", default=str(DEFAULT_CONFIG))
    a.add_argument("--account", required=True)
    a.add_argument("--server", choices=["official", "bilibili"], default=None)
    a.set_defaults(func=cmd_apply)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
