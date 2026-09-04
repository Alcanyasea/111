# -*- coding: utf-8 -*-
r"""账号级第二理智作战候选关卡插件（写入 MAA 当前配置的第二个 FightTask）。

由 master.ps1 在启动每个账号的 MAA 前调用，按 config.json 中该账号的
second_fight_plan（候选关卡列表）原样改写 MAA 当前配置（gui.new.json 的
Current）里第二个理智作战任务的 StagePlan，与 MAA「候选关卡」一致。
列表为空时不调用本插件，保持 MAA 原设置。

用法（由 master.ps1 调用）：
    python fight_stage.py apply --config D:\1\config.json --account official1
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = Path(r"D:\1\config.json")


def _atomic_json_write(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


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


def apply_plan_to_maa(maa_dir, plan, use_optional=True):
    """把 MAA 当前配置中第二个理智作战（FightTask）的候选关卡改为 plan。

    返回 (changed, message)；未找到第二个 FightTask 视为失败（返回 False），
    避免悄悄跑错关卡。
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
    fights = [t for t in queue
              if isinstance(t, dict) and t.get("$type") == "FightTask"]
    if len(fights) < 2:
        return False, "MAA 当前配置只有 %d 个理智作战任务，找不到第二个 FightTask" % len(fights)
    task = fights[1]

    if not use_optional and len(plan) > 1:
        plan = plan[:1]
    task["StagePlan"] = plan
    task["UseOptionalStage"] = bool(use_optional)
    task["StageResetMode"] = "Ignore" if use_optional else "Current"
    try:
        _atomic_json_write(gui_new, data)
    except OSError as exc:
        return False, "写入 %s 失败：%s" % (gui_new, exc)
    names = ["当前/上次" if not str(s).strip() else str(s) for s in plan]
    return True, "第二个理智作战候选关卡已映射为 %s（%s）" % (
        "、".join(names), gui_new.name)


def _log_file(cfg, msg):
    try:
        lp = (cfg.get("paths") or {}).get("log_file")
        if not lp:
            return
        with open(lp, "a", encoding="utf-8") as f:
            f.write("%s - [理智关卡] %s\n" % (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def cmd_apply(args):
    cfg = load_config(args.config)
    acc = find_account(cfg, args.account)
    if acc is None:
        print("ERROR account not found: %s" % args.account)
        return 2
    plan = acc.get("second_fight_plan")
    if not isinstance(plan, list):
        legacy = str(acc.get("second_fight_stage") or "").strip()
        plan = [legacy] if legacy else []
    plan = [str(s).strip() for s in plan]
    if not plan:
        print("SKIP second fight plan not configured")
        return 0
    use_optional = bool(acc.get("second_fight_use_optional", True))

    server = args.server or acc.get("server") or "official"
    maa_dir = _maa_dir(cfg, server)
    if not maa_dir or not Path(maa_dir).exists():
        _log_file(cfg, "WARN 未找到 %s 的 MAA 目录：%s，跳过第二理智关卡写入"
                  % (server, maa_dir))
        print("WARN no maa dir")
        return 0

    ok, msg = apply_plan_to_maa(maa_dir, plan, use_optional=use_optional)
    _log_file(cfg, "账号「%s」候选关卡：%s" % (acc.get("label") or args.account, msg))
    print(("OK " if ok else "ERROR ") + msg)
    return 0 if ok else 2


def main(argv=None):
    p = argparse.ArgumentParser(description="账号级第二理智作战关卡插件")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("apply", help="按账号写入 MAA 第二个理智作战候选关卡")
    a.add_argument("--config", default=str(DEFAULT_CONFIG))
    a.add_argument("--account", required=True)
    a.add_argument("--server", choices=["official", "bilibili"], default=None)
    a.set_defaults(func=cmd_apply)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
