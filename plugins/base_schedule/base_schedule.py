# -*- coding: utf-8 -*-
r"""精确基建派驻插件（MAA 自定义基建计划生成 + 配置切换）。

独立于 MAA 本体：只生成 MAA 官方支持的 custom_infrast JSON（resource/custom_infrast
协议），并在启动 MAA 前改写 MAA 的 gui.json / gui.new.json，把基建任务指向该计划。
因此 MAA 更新版本时不会影响本插件（协议本身长期稳定）。

设施与人数（按需求）：
    控制中枢 5 人、会客室 2 人、制造站 3 人/台、贸易站 3 人/台、
    发电站 1 人/台、办公室 1 人、加工站 1 人（可选，留空则跳过）
    宿舍不手工指定：计划里固定 4 间并启用 autofill，由 MAA 默认算法安排休整。
    布局 333 = 制造 3 台 / 贸易 3 台 / 发电 3 台
    布局 423 = 制造 4 台 / 贸易 2 台 / 发电 3 台
批次：
    4点批  period = 04:00 - 15:59
    16点批 period = 16:00 - 23:59 与 00:00 - 03:59（跨天两段）
MAA 的 PlanSelect=-1 时，GUI 会按当前时间落在哪个 period 区间自动选计划。

用法（由 master.ps1 / 控制台调用）：
    python base_schedule.py apply   --config D:\1\config.json --account official1
    python base_schedule.py generate --config D:\1\config.json --account official1
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
PLANS_DIR = PLUGIN_DIR / "plans"
DEFAULT_CONFIG = Path(r"D:\1\config.json")

BATCHES = ["4点", "16点"]


def default_batch(layout="333"):
    """一个批次的空模板（与界面/配置共用同一种结构）。"""
    m = 4 if layout == "423" else 3
    t = 2 if layout == "423" else 3
    return {
        "control": [""] * 5,
        "meeting": [""] * 2,
        "manufacture": [[""] * 3 for _ in range(m)],
        "trading": [[""] * 3 for _ in range(t)],
        "power": [[""] for _ in range(3)],
        "office": [""],
        "processing": [""],
    }


def default_base_schedule(layout="333"):
    """账号配置里的 base_schedule 默认结构。"""
    return {
        "enabled": False,
        "layout": layout,
        "batches": {b: default_batch(layout) for b in BATCHES},
    }


def normalize(bs):
    """补齐/修正 base_schedule，保证生成器永远拿到完整结构。"""
    if not isinstance(bs, dict):
        bs = {}
    layout = "423" if bs.get("layout") == "423" else "333"
    m = 4 if layout == "423" else 3
    t = 2 if layout == "423" else 3
    batches = bs.get("batches") if isinstance(bs.get("batches"), dict) else {}
    out = {"enabled": bool(bs.get("enabled")), "layout": layout, "batches": {}}

    def single(src, key, n):
        v = src.get(key)
        if not isinstance(v, list):
            v = []
        v = [str(x) for x in v]
        return (v + [""] * n)[:n]

    def multi(src, key, n, inner):
        v = src.get(key)
        if not isinstance(v, list):
            v = []
        rows = []
        for i in range(n):
            row = v[i] if i < len(v) else []
            if not isinstance(row, list):
                row = []
            row = [str(x) for x in row]
            rows.append((row + [""] * inner)[:inner])
        return rows

    for b in BATCHES:
        src = batches.get(b)
        if not isinstance(src, dict):
            src = {}
        out["batches"][b] = {
            "control": single(src, "control", 5),
            "meeting": single(src, "meeting", 2),
            "manufacture": multi(src, "manufacture", m, 3),
            "trading": multi(src, "trading", t, 3),
            "power": multi(src, "power", 3, 1),
            "office": single(src, "office", 1),
            "processing": single(src, "processing", 1),
        }
    return out


def _entry(ops):
    """单个设施槽位条目。留空 → autofill，交给 MAA 默认算法补齐。"""
    ops = [o for o in ops if o and o.strip()]
    return {"skip": False, "operators": ops, "sort": True, "autofill": not ops}


def build_plan_document(bs, title="自定义基建", description="由 MAA 挂机控制台生成"):
    """把账号的 base_schedule 转成 MAA 自定义计划 JSON 内容。"""
    bs = normalize(bs)
    layout = bs["layout"]
    m = 4 if layout == "423" else 3
    t = 2 if layout == "423" else 3
    spec = [
        ("4点", [["04:00", "15:59"]]),
        ("16点", [["16:00", "23:59"], ["00:00", "03:59"]]),
    ]
    plans = []
    for name, period in spec:
        b = bs["batches"][name]
        plans.append({
            "name": name + "批",
            "period": period,
            "rooms": {
                "control": [_entry(b["control"])],
                "meeting": [_entry(b["meeting"])],
                "manufacture": [_entry(row) for row in b["manufacture"]],
                "trading": [_entry(row) for row in b["trading"]],
                "power": [_entry(row) for row in b["power"]],
                "hire": [_entry(b["office"])],
                # 加工站：留空时 MAA 会跳过，不会自动派干员（自定义模式限制）
                "processing": [_entry(b["processing"])],
                # 宿舍：固定 4 间全 autofill，交给 MAA 默认算法安排休整，
                # 否则自定义模式下宿舍会被清空但不再补人
                "dormitory": [
                    {"skip": False, "operators": [], "sort": False, "autofill": True}
                    for _ in range(4)
                ],
            },
        })
    return {
        "author": "MAA 挂机控制台",
        "title": title,
        "description": description,
        "planTimes": "2班",
        "plans": plans,
        "scheduleType": {
            "planTimes": 2,
            "trading": t,
            "manufacture": m,
            "power": 3,
            "dormitory": 4,
        },
    }


def plan_path_for_slot(slot):
    safe = re.sub(r"[^0-9A-Za-z_\-]", "_", slot or "account")
    return PLANS_DIR / (safe + ".json")


def regenerate_for_account(cfg, acc):
    """按账号配置重新生成计划 JSON，返回文件路径。"""
    bs = normalize((acc or {}).get("base_schedule"))
    doc = build_plan_document(
        bs,
        title=(acc or {}).get("label") or "自定义基建",
        description="由 MAA 挂机控制台生成（4点批 / 16点批）",
    )
    path = plan_path_for_slot((acc or {}).get("slot", (acc or {}).get("id", "account")))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def current_batch(now=None):
    """按当前时间返回生效批次（与 MAA period 判断一致）。"""
    if now is None:
        now = datetime.now()
    hhmm = now.strftime("%H:%M")
    if "04:00" <= hhmm <= "15:59":
        return "4点"
    return "16点"


def _atomic_json_write(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def apply_maa_config(maa_dir, plan_path, plan_index=None, log=print):
    """把某套 MAA 当前配置的基建任务切到 Custom(plan) 或恢复 Rotation。

    修改 gui.new.json 的 InfrastTask（Mode/Filename/PlanSelect）与
    gui.json 的 Infrast.InfrastMode。返回改动的文件名列表。
    plan_index：自定义模式下固定使用第几个计划（0=4点批，1=16点批）；
    None 表示按时间自动（PlanSelect=-1）。
    """
    mode = "Custom" if plan_path else "Rotation"
    filename = str(plan_path) if plan_path else ""
    plan_select = -1 if plan_index is None else int(plan_index)
    changed = []

    gui_new = Path(maa_dir) / "config" / "gui.new.json"
    if gui_new.exists():
        data = json.loads(gui_new.read_text(encoding="utf-8"))
        cur = data.get("Current") or "Default"
        section = (data.get("Configurations") or {}).get(cur)
        found = False
        if isinstance(section, dict):
            for task in section.get("TaskQueue") or []:
                if isinstance(task, dict) and task.get("$type") == "InfrastTask":
                    task["Mode"] = mode
                    task["Filename"] = filename
                    task["PlanSelect"] = plan_select
                    found = True
                    break
        if found:
            _atomic_json_write(gui_new, data)
            changed.append("gui.new.json")
        else:
            log("WARN: gui.new.json 当前配置 %s 中未找到 InfrastTask" % cur)

    gui_json = Path(maa_dir) / "config" / "gui.json"
    if gui_json.exists():
        data = json.loads(gui_json.read_text(encoding="utf-8"))
        cur = data.get("Current") or "Default"
        section = (data.get("Configurations") or {}).get(cur)
        if isinstance(section, dict):
            section["Infrast.InfrastMode"] = mode
            _atomic_json_write(gui_json, data)
            changed.append("gui.json")

    return changed


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


def _log_file(cfg, msg):
    try:
        lp = (cfg.get("paths") or {}).get("log_file")
        if not lp:
            return
        with open(lp, "a", encoding="utf-8") as f:
            f.write("%s - [基建插件] %s\n" % (
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
    bs = normalize(acc.get("base_schedule"))
    enabled = bool(bs.get("enabled")) and not args.disable
    label = acc.get("label") or args.account

    batch = args.batch
    if batch in (None, "auto"):
        batch = current_batch()

    plan_path = None
    plan_index = None
    if enabled:
        plan_path = regenerate_for_account(cfg, acc)
        plan_index = 0 if batch == "4点" else 1
        _log_file(cfg, "账号「%s」启用精确基建（%s批），计划已生成：%s"
                  % (label, batch, plan_path.name))

    maa_key = "maa_bilibili_dir" if server == "bilibili" else "maa_official_dir"
    maa_dir = (cfg.get("paths") or {}).get(maa_key)
    if not maa_dir or not Path(maa_dir).exists():
        _log_file(cfg, "WARN 未找到 %s 的 MAA 目录：%s，跳过配置写入"
                  % (server, maa_dir))
        print("WARN no maa dir")
        return 0

    changed = apply_maa_config(
        maa_dir, plan_path, plan_index=plan_index,
        log=lambda m: _log_file(cfg, m))
    if enabled:
        _log_file(cfg, "已切换 MAA 基建为自定义计划 %s（%s批，%s）"
                  % (plan_path.name, batch, ", ".join(changed) or "无改动"))
        print("OK custom %s plan=%d" % (plan_path.name, plan_index))
    else:
        _log_file(cfg, "已恢复 MAA 自带基建换班（Rotation，%s）"
                  % (", ".join(changed) or "无改动"))
        print("OK rotation")
    return 0


def cmd_generate(args):
    cfg = load_config(args.config)
    acc = find_account(cfg, args.account)
    if acc is None:
        print("ERROR account not found: %s" % args.account)
        return 2
    path = regenerate_for_account(cfg, acc)
    print("OK " + str(path))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="精确基建派驻插件")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("apply", help="生成计划并按账号切换 MAA 配置")
    a.add_argument("--config", default=str(DEFAULT_CONFIG))
    a.add_argument("--account", required=True)
    a.add_argument("--server", choices=["official", "bilibili"], default=None)
    a.add_argument("--batch", choices=["4点", "16点", "auto"], default="auto",
                   help="本次运行使用哪个批次（默认按当前时间自动判断）")
    a.add_argument("--disable", action="store_true",
                   help="即使账号已启用也恢复 Rotation（备用）")
    a.set_defaults(func=cmd_apply)

    g = sub.add_parser("generate", help="只重新生成计划 JSON")
    g.add_argument("--config", default=str(DEFAULT_CONFIG))
    g.add_argument("--account", required=True)
    g.set_defaults(func=cmd_generate)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
