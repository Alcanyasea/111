# -*- coding: utf-8 -*-
r"""基建收菜插件：独立「收菜」配置方案 + 切 Current 指针，只收产物不换班。

由 master.ps1 -InfrastCollect 在启动每个账号的 MAA 前调用（替代常规的
基建/理智/菲亚梅塔三个插件）。原理（MAA 基建排班协议的官方语义）：
- 房间条目 skip=true 时「仅跳过换干员操作，其他如使用无人机、线索交流
  等仍会正常进行」——即进房间收取产物/订单，但完全不动干员；
- 计划里不含宿舍/会客室等设施，配合 RoomList 只勾制造站（Mfg）与
  贸易站（Trade），MAA 只进这两类设施；
- 计划级 drones 关闭（纯收菜，不用无人机加速生产）；
- 停用理智作战 / 招募 / 信用 / 领奖任务，只留开始唤醒 + 基建。

方案隔离（2026-09-17 起）：收菜配置永久存放在独立的「收菜」方案里，
每次 apply 幂等地确保其形态正确，然后把 Current 指针切过去（gui.new.json
与 gui.json 同步）。farm 用的 Default 方案全程不被收菜触碰，不再需要
整份备份/恢复；收菜中断最多留下 Current 停在「收菜」上，farm_guard 会在
下次挂机启动前强制切回 Default。运行时设置（ADB 地址/客户端类型/直接
运行等）每次从 Default 同步，避免改过连接参数后收菜方案用旧值。

收菜计划与账号无关（skip + 空 operators 对任何号都成立），两套 MAA
共用同一份 collect_plan.json，由本插件在每次 apply 时生成。

用法（由 master.ps1 调用）：
    python infrast_collect.py apply --config D:\1\config.json --account acc_xxx
    python infrast_collect.py restore --config D:\1\config.json   # 收菜结束切回 Default
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = Path(r"D:\1\config.json")

# 收菜方案里停用的任务（开始唤醒 + 基建保留：要先进游戏才收得了菜）
DISABLE_TASKS = ("RecruitTask", "FightTask", "MallTask", "AwardTask")
# 其余非必需任务一并压掉，保证「收菜」方案自包含且形态恒定
IDLE_TASKS = ("RoguelikeTask", "ReclamationTask")
# 收菜目标设施
COLLECT_ROOMS = ("Mfg", "Trade")
COLLECT_SCHEME = "收菜"
FARM_SCHEME = "Default"


def _atomic_json_write(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _deepcopy(obj):
    return json.loads(json.dumps(obj))


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


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def apply_collect(maa_dir):
    """确保「收菜」方案存在且形态正确，把 Current 切到它。返回 (ok, message)。

    同步基准永远是 Default（farm 方案）：不存在时退而取任一非收菜方案；
    连它也没有则视为配置损坏，报错交由上层告警。
    """
    gui_new = Path(maa_dir) / "config" / "gui.new.json"
    data = _read_json(gui_new)
    if not isinstance(data, dict):
        return False, "读取 %s 失败" % gui_new
    cfgs = data.get("Configurations")
    if not isinstance(cfgs, dict) or not cfgs:
        return False, "gui.new.json 没有任何配置方案"

    base = cfgs.get(FARM_SCHEME)
    if not isinstance(base, dict):
        base = next((v for k, v in cfgs.items()
                     if isinstance(v, dict) and k != COLLECT_SCHEME), None)
    if not isinstance(base, dict):
        return False, "找不到可作同步基准的配置方案（Default 缺失）"

    plan_path = write_collect_plan()

    scheme = cfgs.get(COLLECT_SCHEME)
    created = False
    if not isinstance(scheme, dict):
        scheme = _deepcopy(base)
        cfgs[COLLECT_SCHEME] = scheme
        created = True

    # 运行时设置跟随 Default：连接参数改过后收菜方案不用旧值
    base_gui = base.get("Gui") if isinstance(base.get("Gui"), dict) else {}
    scheme["Gui"] = _deepcopy(base_gui)

    queue = scheme.get("TaskQueue")
    if not isinstance(queue, list):
        src = base.get("TaskQueue")
        if not isinstance(src, list):
            return False, "「收菜」方案与 Default 都没有任务队列"
        queue = _deepcopy(src)
        scheme["TaskQueue"] = queue

    infrast = [t for t in queue
               if isinstance(t, dict) and t.get("$type") == "InfrastTask"]
    if not infrast:
        return False, "「收菜」方案没有 InfrastTask，无法收菜"
    # 只保留一个基建任务。按对象身份删：remove 按值删第一个相等的，
    # 两任务内容相同时会误删要保留的那个、留下没配置过的
    drop = {id(t) for t in infrast[1:]}
    queue[:] = [t for t in queue if id(t) not in drop]
    task = infrast[0]

    # 自定义模式 + 全 skip 计划：只收产物/订单，不动干员
    task["Mode"] = "Custom"
    task["CustomFileType"] = "user_defined"
    task["Filename"] = str(plan_path)
    task["PlanSelect"] = 0

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

    # 任务队列：开始唤醒 + 基建保留，其余一律停用（幂等，每次收菜前压一遍）
    for t in queue:
        if not isinstance(t, dict):
            continue
        ttype = t.get("$type")
        if ttype == "InfrastTask":
            t["IsEnable"] = True
        elif ttype in ("StartUpTask",) + DISABLE_TASKS + IDLE_TASKS:
            t["IsEnable"] = (ttype == "StartUpTask")

    if data.get("Current") != COLLECT_SCHEME:
        data["Current"] = COLLECT_SCHEME
    _atomic_json_write(gui_new, data)

    # gui.json（旧版设置文件）：同步方案条目与 Current，缺失则从 Default 补
    gui_json = Path(maa_dir) / "config" / "gui.json"
    gj = _read_json(gui_json)
    if isinstance(gj, dict):
        gcfgs = gj.get("Configurations")
        if isinstance(gcfgs, dict):
            gscheme = gcfgs.get(COLLECT_SCHEME)
            gbase = gcfgs.get(FARM_SCHEME)
            if not isinstance(gscheme, dict) and isinstance(gbase, dict):
                gscheme = dict(gbase)
                gcfgs[COLLECT_SCHEME] = gscheme
            if isinstance(gscheme, dict):
                gscheme["Infrast.InfrastMode"] = "Custom"
                gscheme["Infrast.DefaultInfrast"] = "user_defined"
        gj["Current"] = COLLECT_SCHEME
        _atomic_json_write(gui_json, gj)

    note = "已创建「收菜」方案" if created else "「收菜」方案已就绪"
    return True, "%s并已切换（全 skip 不换班·仅制造/贸易，计划 %s）" % (
        note, plan_path.name)


def restore_default(maa_dir):
    """把 Current 指针切回 Default（两份配置文件都改）。返回 (ok, message)。"""
    changed, missing = [], []
    for name in ("gui.new.json", "gui.json"):
        path = Path(maa_dir) / "config" / name
        data = _read_json(path)
        if not isinstance(data, dict):
            missing.append(name)
            continue
        cfgs = data.get("Configurations")
        has_default = isinstance(cfgs, dict) and isinstance(cfgs.get(FARM_SCHEME), dict)
        if data.get("Current") != FARM_SCHEME:
            if not has_default:
                return False, "%s 没有 Default 方案，无法切回" % name
            data["Current"] = FARM_SCHEME
            _atomic_json_write(path, data)
            changed.append(name)
    if missing and not changed:
        return False, "读取失败：%s" % "、".join(missing)
    if changed:
        return True, "Current 已切回 Default（%s）" % "、".join(changed)
    return True, "Current 本就是 Default，无需切换"


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

    ok, msg = apply_collect(maa_dir)
    _log_file(cfg, "账号「%s」：%s" % (acc.get("label") or args.account, msg))
    print(("OK " if ok else "ERROR ") + msg)
    return 0 if ok else 2


def cmd_restore(args):
    cfg = load_config(args.config)
    results = []
    ok_all = True
    for server in ("official", "bilibili"):
        maa_dir = _maa_dir(cfg, server)
        if not maa_dir or not Path(maa_dir).exists():
            results.append("%s：无 MAA 目录" % server)
            continue
        ok, msg = restore_default(maa_dir)
        ok_all = ok_all and ok
        results.append("%s：%s" % (server, msg))
        _log_file(cfg, "收菜结束 [%s] %s" % (server, msg))
    print(("OK " if ok_all else "ERROR ") + "；".join(results))
    return 0 if ok_all else 2


def main(argv=None):
    p = argparse.ArgumentParser(description="基建收菜插件（独立方案·切 Current）")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("apply", help="确保「收菜」方案并切换 Current")
    a.add_argument("--config", default=str(DEFAULT_CONFIG))
    a.add_argument("--account", required=True)
    a.add_argument("--server", choices=["official", "bilibili"], default=None)
    a.set_defaults(func=cmd_apply)
    r = sub.add_parser("restore", help="收菜结束：两套 MAA 的 Current 切回 Default")
    r.add_argument("--config", default=str(DEFAULT_CONFIG))
    r.set_defaults(func=cmd_restore)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
