# -*- coding: utf-8 -*-
r"""任务开关自检插件：farm 模式启动 MAA 前锁定 Default 方案并校验任务开关。

farm 每次启动 MAA 前调用，做两件事（都只针对 farm 用的 Default 方案）：
1. Current 指针强制归位 Default——收菜中断可能把指针留在「收菜」方案上，
   MAA 界面手动切方案、人工覆盖配置文件同理；指针不对，后面所有插件和
   MAA 都会跑错方案。
2. 关键任务（开始唤醒/公招/基建/理智作战/信用/领奖）的 IsEnable 强制补开
   ——MAA 只跑勾着的任务且正常退出，开关被关会导致理智/日常静默没刷
   （2026-09-17 官服漏刷的根因），调度层全绿无告警。

关键任务条目缺失（而非关闭）属于配置损坏，无法安全自建，报错退出，
由 master.ps1 按失败处理走重试 + 失败通知，绝不静默放行。
基建收菜模式不调用本插件（收菜需要独立方案的关闭态）。

用法（由 master.ps1 调用）：
    python farm_guard.py apply --config D:\1\config.json --account acc_xxx --server official
"""

import argparse
import sys
from pathlib import Path

DEFAULT_CONFIG = Path(r"D:\1\config.json")

# 插件公共工具（plugins\common.py）——此前本文件与其它四个插件各复制一份
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import (atomic_json_write as _atomic_json_write, find_account,
                    load_config, log_file, maa_dir_for as _maa_dir,
                    read_json as _read_json)


def _log_file(cfg, msg):
    log_file(cfg, "任务自检", msg)

# farm 模式必须开启的任务；与 gui/core/maa_setup.py 的 KEY_TASKS 同口径
KEY_TASKS = ("StartUpTask", "RecruitTask", "InfrastTask", "FightTask",
             "MallTask", "AwardTask")
# 任务中文名（日志/失败通知里不露内部类型名）；与 gui/core/maa_setup.py 的 TASK_NAMES 同名同序
TASK_NAMES = {
    "StartUpTask": "开始唤醒",
    "RecruitTask": "公招",
    "InfrastTask": "基建",
    "FightTask": "理智作战",
    "MallTask": "信用",
    "AwardTask": "领奖",
}
FARM_SCHEME = "Default"


def apply_guard(maa_dir):
    """锁定 Default 方案：Current 归位 + 关键任务补开。返回 (ok, message)。"""
    gui_new = Path(maa_dir) / "config" / "gui.new.json"
    data = _read_json(gui_new)
    if not isinstance(data, dict):
        return False, "读取 %s 失败" % gui_new
    cfgs = data.get("Configurations")
    conf = cfgs.get(FARM_SCHEME) if isinstance(cfgs, dict) else None
    if not isinstance(conf, dict):
        return False, "Default 方案缺失（请用控制台「MAA 服务器配置」修复）"
    queue = conf.get("TaskQueue")
    if not isinstance(queue, list):
        return False, "Default 方案没有任务队列"

    present = {}
    for t in queue:
        if isinstance(t, dict) and t.get("$type") in KEY_TASKS:
            present.setdefault(t["$type"], []).append(t)
    missing = [k for k in KEY_TASKS if k not in present]
    if missing:
        return False, "任务队列缺少 %s（无法自动创建，请用控制台「MAA 服务器配置」修复）" % (
            "、".join(TASK_NAMES[k] for k in missing))

    fixed = []
    count = 0
    for k in KEY_TASKS:
        for t in present[k]:
            if t.get("IsEnable") is not True:
                t["IsEnable"] = True
                if TASK_NAMES[k] not in fixed:
                    fixed.append(TASK_NAMES[k])
                count += 1
    changed = count > 0

    pointer = ""
    if data.get("Current") != FARM_SCHEME:
        data["Current"] = FARM_SCHEME
        pointer = "Current 已归位 Default"
        changed = True

    if changed:
        _atomic_json_write(gui_new, data)

    # gui.json（旧版设置文件）的 Current 一并归位；读不了就算了，任务队列以
    # gui.new.json 为准，别为它挡住挂机
    note = ""
    gui_json = Path(maa_dir) / "config" / "gui.json"
    gj = _read_json(gui_json)
    if isinstance(gj, dict) and gj.get("Current") != FARM_SCHEME:
        gj["Current"] = FARM_SCHEME
        _atomic_json_write(gui_json, gj)
        note = "；gui.json 同步归位"
    elif not isinstance(gj, dict):
        note = "；gui.json 不可读（已忽略）"

    if count:
        return True, "任务开关已补开：%s（共 %d 项）%s%s" % (
            "、".join(fixed), count,
            "；" + pointer if pointer else "", note)
    if pointer:
        return True, "%s%s；任务开关完整" % (pointer, note)
    return True, "任务开关完整、Current 正常%s" % note


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

    ok, msg = apply_guard(maa_dir)
    _log_file(cfg, "账号「%s」：%s" % (acc.get("label") or args.account, msg))
    print(("OK " if ok else "ERROR ") + msg)
    return 0 if ok else 2


def main(argv=None):
    p = argparse.ArgumentParser(description="任务开关自检插件（farm 防静默跳过）")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("apply", help="锁定 Default 方案：Current 归位 + 关键任务补开")
    a.add_argument("--config", default=str(DEFAULT_CONFIG))
    a.add_argument("--account", required=True)
    a.add_argument("--server", choices=["official", "bilibili"], default=None)
    a.set_defaults(func=cmd_apply)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
