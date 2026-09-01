# -*- coding: utf-8 -*-
r"""精确基建派驻插件（MAA 自定义基建计划生成 + 配置切换）。

独立于 MAA 本体：只生成 MAA 官方支持的 custom_infrast JSON（resource/custom_infrast
协议），并在启动 MAA 前改写 MAA 的 gui.json / gui.new.json，把基建任务指向该计划。
因此 MAA 更新版本时不会影响本插件（协议本身长期稳定）。

设施与人数（按需求）：
    控制中枢 5 人、会客室 2 人、制造站 3 人/台、贸易站 3 人/台、
    发电站 1 人/台、办公室 1 人、加工站 1 人（可选，留空则跳过）
    干员休整（宿舍）：4 间 × 5 人，填入的干员放入，剩余空位（含全留空）
    由 MAA 自动补满。
    布局名称按「贸易站 / 制造站 / 发电站」顺序读：
    布局 333 = 贸易 3 台 / 制造 3 台 / 发电 3 台
    布局 243 = 贸易 2 台 / 制造 4 台 / 发电 3 台（旧名 423，仅改名、数量不变）
制造站/贸易站按产品类别区分（写入计划 JSON 的 product 字段，MAA 据此匹配
游戏内对应设施并设置配方）：
    制造站：Pure Gold（赤金）/ Originium Shard（原石碎片）/ Battle Record（作战记录）
    贸易站：LMD（赤金订单）/ Orundum（原石碎片订单）
批次：随 config.schedule.times 动态生成——每个启用时间点一个批次，生效区间为
「该时间点 → 下一个时间点」，末批跨零点收尾。例如 8点/12点/24点：
    8点批  period = 08:00 - 11:59
    12点批 period = 12:00 - 23:59
    24点批 period = 00:00 - 07:59
MAA 的 PlanSelect=-1 时，GUI 会按当前时间落在哪个 period 区间自动选计划。

无人机：由 base_schedule.drones 配置（目标设施 manufacture/trading、站号 1 起、
是否启用、时机 pre/post），写入每个批次计划的 drones 字段，MAA 在自定义模式下
按该字段在换班前/后投放无人机（GUI 的「无人机用途」下拉只对 MAA 自带 Rotation
模式生效，自定义模式必须写在计划 JSON 里）。

宿舍：填入的干员放入对应宿舍，剩余空位由 MAA 自动补满（autofill=true，全留空
也自动安排）。与 MAA 官方排班一致：指定了干员的房间 sort=true（保证进驻顺序、
避免暖机技能重排），全空房间 sort=false。MAA 按宿舍 1→4 顺序处理且 autofill
会先消耗可用干员，因此生成计划时会把指定了干员的宿舍排在前面、空宿舍排在后面，
避免前面的空宿舍提前把后面指定宿舍要用的干员选走。

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
MANUFACTURE_PRODUCTS = ["Pure Gold", "Originium Shard", "Battle Record"]
TRADING_PRODUCTS = ["LMD", "Orundum"]
DEFAULT_MANUFACTURE_PRODUCT = "Pure Gold"
DEFAULT_TRADING_PRODUCT = "LMD"


def batch_name(time):
    """HH:MM → 班次名：04:00 → 4点，00:00 → 24点。"""
    m = re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(time))
    if not m:
        return str(time)
    hour = int(m.group(1))
    return "%d点" % (24 if hour == 0 else hour)


def schedule_entries(cfg):
    """config.schedule.times 中启用的时间点（升序、去重）。"""
    sched = cfg.get("schedule") if isinstance(cfg, dict) else {}
    times = sched.get("times") if isinstance(sched.get("times"), list) else []
    out, seen = [], set()
    for it in times:
        if not isinstance(it, dict) or not it.get("enabled", True):
            continue
        t = str(it.get("time", "")).strip()
        if t in seen or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
            continue
        seen.add(t)
        out.append({"time": t, "shutdown": bool(it.get("shutdown", False))})
    out.sort(key=lambda e: e["time"])
    return out


def periods_for_times(entries):
    """每个批次生效区间：从该时间点到下一个时间点；末批跨零点收尾。

    与 MAA 自定义基建 period 语义一致：[[start,end], ...] 中任一区间命中即生效。
    """
    if not entries:
        entries = [{"time": "04:00"}, {"time": "16:00"}]
    times = [e["time"] for e in entries]

    def minus1(t):
        total = (int(t[:2]) * 60 + int(t[3:]) - 1) % 1440
        return "%02d:%02d" % (total // 60, total % 60)

    n = len(times)
    out = []
    for i, t in enumerate(times):
        if i < n - 1:
            out.append([[t, minus1(times[i + 1])]])
        else:
            segs = [[t, "23:59"]]
            if times[0] != "00:00":
                segs.append(["00:00", minus1(times[0])])
            out.append(segs)
    return out


def schedule_spec(cfg):
    """当前启用时间点 → (entries, 批次名列表, 各批次生效区间)；无配置回退 4点/16点。"""
    entries = schedule_entries(cfg)
    if not entries:
        entries = [{"time": "04:00"}, {"time": "16:00"}]
    names = [batch_name(e["time"]) for e in entries]
    return entries, names, periods_for_times(entries)


def default_batch(layout="333"):
    """一个批次的空模板（与界面/配置共用同一种结构）。"""
    m = 4 if layout in ("423", "243") else 3
    t = 2 if layout in ("423", "243") else 3
    return {
        "control": [""] * 5,
        "meeting": [""] * 2,
        "manufacture": [
            {"product": DEFAULT_MANUFACTURE_PRODUCT, "operators": [""] * 3}
            for _ in range(m)],
        "trading": [
            {"product": DEFAULT_TRADING_PRODUCT, "operators": [""] * 3}
            for _ in range(t)],
        "power": [[""] for _ in range(3)],
        "dormitory": [[""] * 5 for _ in range(4)],
        "office": [""],
        "processing": [""],
    }


def default_base_schedule(layout="333"):
    """账号配置里的 base_schedule 默认结构。"""
    return {
        "enabled": False,
        "layout": layout,
        "drones": {
            "room": "manufacture",
            "index": 1,
            "enable": False,
            "order": "pre",
        },
        "batches": {b: default_batch(layout) for b in BATCHES},
    }


def normalize(bs, batches=None):
    """补齐/修正 base_schedule，保证生成器永远拿到完整结构。

    batches: 本次要使用的批次名（缺省 BATCHES 兜底）；配置里已有的其它批次一并
    保留，避免调整启动时间后旧批次数据丢失。
    """
    if not isinstance(bs, dict):
        bs = {}
    # 423 已改名为 243（数量不变：贸易2台/制造4台），兼容旧键
    layout = "243" if bs.get("layout") in ("423", "243") else "333"
    m = 4 if layout == "243" else 3
    t = 2 if layout == "243" else 3
    batches_src = bs.get("batches") if isinstance(bs.get("batches"), dict) else {}
    out = {"enabled": bool(bs.get("enabled")), "layout": layout, "batches": {}}

    # 无人机（全局，作用于每个批次）：目标制造站/贸易站、站号（1 起）、启用、时机
    drones = bs.get("drones")
    if not isinstance(drones, dict):
        drones = {}
    room = str(drones.get("room") or "manufacture")
    if room not in ("manufacture", "trading"):
        room = "manufacture"
    order = str(drones.get("order") or "pre")
    if order not in ("pre", "post"):
        order = "pre"
    try:
        index = int(drones.get("index") or 1)
    except (TypeError, ValueError):
        index = 1
    if index < 1:
        index = 1
    out["drones"] = {
        "room": room,
        "index": index,
        "enable": bool(drones.get("enable")),
        "order": order,
    }

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

    def stations(src, key, n, inner, default_product):
        """制造站/贸易站：每台 {product, operators}；兼容旧版纯数组格式。"""
        v = src.get(key)
        if not isinstance(v, list):
            v = []
        rows = []
        for i in range(n):
            item = v[i] if i < len(v) else {}
            if isinstance(item, list):  # 旧格式：[[干员...], ...] → 默认产品
                item = {"product": default_product, "operators": item}
            if not isinstance(item, dict):
                item = {}
            ops = item.get("operators")
            if not isinstance(ops, list):
                ops = []
            ops = [str(x) for x in ops]
            rows.append({
                "product": str(item.get("product") or default_product),
                "operators": (ops + [""] * inner)[:inner],
            })
        return rows

    names = list(batches if batches else BATCHES)
    for b in batches_src:
        if b not in names:
            names.append(b)
    for b in names:
        src = batches_src.get(b)
        if not isinstance(src, dict):
            src = {}
        out["batches"][b] = {
            "control": single(src, "control", 5),
            "meeting": single(src, "meeting", 2),
            "manufacture": stations(src, "manufacture", m, 3,
                                    DEFAULT_MANUFACTURE_PRODUCT),
            "trading": stations(src, "trading", t, 3, DEFAULT_TRADING_PRODUCT),
            "power": multi(src, "power", 3, 1),
            "dormitory": multi(src, "dormitory", 4, 5),
            "office": single(src, "office", 1),
            "processing": single(src, "processing", 1),
        }
    return out


def _entry(ops):
    """单个设施槽位条目。留空 → autofill，交给 MAA 默认算法补齐。"""
    ops = [o for o in ops if o and o.strip()]
    return {"skip": False, "operators": ops, "sort": True, "autofill": not ops}


def _room(ops, product=None):
    """制造站/贸易站条目：额外带 product（赤金/原石碎片/作战记录等），
    MAA 据此匹配游戏内对应设施并设置配方。"""
    entry = _entry(ops)
    if product:
        entry["product"] = product
    return entry


def _dorm(ops):
    """干员休整（宿舍）条目：填入的干员放入，剩余空位（含全留空）由 MAA 自动补满。

    MAA 核心只有宿舍任务消费 autofill 字段（InfrastDormTask 在 autofill=true
    时调用 fill_dorm_slots 补满空位）；生产设施指定干员后走自定义名单路径，
    autofill 不生效，剩余位置保持空着。

    与 MAA 官方排班一致：指定了干员的房间用 sort=true（保证进驻顺序，避免暖机
    技能重排），全空房间用 sort=false。MAA 按宿舍 1→4 顺序处理且 autofill 会
    先消耗可用干员，所以指定干员的宿舍要排在空宿舍前面（build_plan_document
    里已重排），否则前面的空宿舍可能提前把指定干员选走，导致宿舍补不满。
    """
    ops = [o for o in ops if o and o.strip()]
    return {"skip": False, "operators": ops, "sort": bool(ops), "autofill": True}


def build_plan_document(bs, entries=None, title="自定义基建",
                        description="由 MAA 挂机控制台生成"):
    """把账号的 base_schedule 转成 MAA 自定义计划 JSON 内容。

    entries: 启用的启动时间点（升序），缺省 04:00/16:00；批次名与生效区间
    按时间点自动推导，MAA 按当前时间自动选计划。
    """
    if not entries:
        entries = [{"time": "04:00"}, {"time": "16:00"}]
    names = [batch_name(e["time"]) for e in entries]
    periods = periods_for_times(entries)
    bs = normalize(bs, names)
    layout = bs["layout"]
    m = 4 if layout == "243" else 3
    t = 2 if layout == "243" else 3
    plans = []
    for name, period in zip(names, periods):
        b = bs["batches"][name]
        dorms = [_dorm(room) for room in b["dormitory"]]
        # MAA 按宿舍 1→4 顺序处理：指定了干员的宿舍放前面、空宿舍放后面，
        # 避免前面的空宿舍自动补位时提前选走后面指定宿舍要用的干员。
        dorms.sort(key=lambda d: 0 if d["operators"] else 1)
        plan = {
            "name": name + "批",
            "period": period,
            "rooms": {
                "control": [_entry(b["control"])],
                "meeting": [_entry(b["meeting"])],
                "manufacture": [
                    _room(st["operators"], st["product"]) for st in b["manufacture"]],
                "trading": [
                    _room(st["operators"], st["product"]) for st in b["trading"]],
                "power": [_entry(row) for row in b["power"]],
                "hire": [_entry(b["office"])],
                # 加工站：留空时 MAA 会跳过，不会自动派干员（自定义模式限制）
                "processing": [_entry(b["processing"])],
                # 干员休整（宿舍）：可指定干员，空位自动补；全留空则 MAA 默认算法安排
                "dormitory": dorms,
            },
        }
        drones = bs.get("drones") or {}
        if drones.get("enable"):
            plan["drones"] = {
                "room": drones.get("room") or "manufacture",
                "index": int(drones.get("index") or 1),
                "enable": True,
                "order": drones.get("order") or "pre",
            }
        plans.append(plan)
    return {
        "author": "MAA 挂机控制台",
        "title": title,
        "description": description,
        "planTimes": "%d班" % len(names),
        "plans": plans,
        "scheduleType": {
            "planTimes": len(names),
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
    entries, names, _periods = schedule_spec(cfg)
    bs = normalize((acc or {}).get("base_schedule"), names)
    doc = build_plan_document(
        bs, entries,
        title=(acc or {}).get("label") or "自定义基建",
        description="由 MAA 挂机控制台生成（%s）" % "、".join(names),
    )
    path = plan_path_for_slot((acc or {}).get("slot", (acc or {}).get("id", "account")))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def current_batch(now=None, entries=None):
    """按当前时间返回最近一个启用时间点对应的批次名（与 MAA period 判断一致）。"""
    if entries is None:
        entries = [{"time": "04:00"}, {"time": "16:00"}]
    if now is None:
        now = datetime.now()
    cur = now.strftime("%H:%M")
    pick = entries[-1]
    for e in entries:
        if e["time"] <= cur:
            pick = e
        else:
            break
    return batch_name(pick["time"])


def _atomic_json_write(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def apply_maa_config(maa_dir, plan_path, plan_index=None, log=print):
    """把某套 MAA 当前配置的基建任务切到 Custom(plan) 或恢复 Rotation。

    修改 gui.new.json 的 InfrastTask（Mode/Filename/PlanSelect）与
    gui.json 的 Infrast.InfrastMode。返回改动的文件名列表。
    plan_index：自定义模式下固定使用第几个计划（0=第一个批次，按时间升序）；
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
            queue = section.get("TaskQueue")
            if isinstance(queue, list):
                infrast = [t for t in queue
                           if isinstance(t, dict) and t.get("$type") == "InfrastTask"]
                if infrast:
                    # 撤除「一键休整」：只保留一个基建任务（清理旧版双任务残留）
                    for extra in infrast[1:]:
                        queue.remove(extra)
                    first = infrast[0]
                    # 恢复宿舍管理：设施列表补回 Dorm（一键休整版本移除过）
                    rooms = first.get("RoomList")
                    if isinstance(rooms, list):
                        enabled_names = [r.get("Room") for r in rooms
                                         if isinstance(r, dict)
                                         and r.get("IsEnabled", True)]
                        if "Dorm" not in enabled_names:
                            # 先移除可能存在的禁用 Dorm 条目（MAA GUI 会自动补禁用的缺失设施）
                            rooms[:] = [r for r in rooms
                                        if not (isinstance(r, dict)
                                                and r.get("Room") == "Dorm")]
                            pos = len(rooms)
                            for i, r in enumerate(rooms):
                                if isinstance(r, dict) and r.get("Room") == "Office":
                                    pos = i + 1
                                    break
                            rooms.insert(pos, {"Room": "Dorm"})
                    first["Mode"] = mode
                    first["Filename"] = filename
                    first["PlanSelect"] = plan_select
                    found = True
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
    entries, names, _periods = schedule_spec(cfg)
    bs = normalize(acc.get("base_schedule"), names)
    enabled = bool(bs.get("enabled")) and not args.disable
    label = acc.get("label") or args.account

    batch = args.batch
    if batch in (None, "auto"):
        batch = current_batch(entries=entries)
    if batch not in names:
        # 时间点刚改过 / 批次名对不上 → 按当前时间重新判定，保证计划索引正确
        batch = current_batch(entries=entries)

    plan_path = None
    plan_index = None
    if enabled:
        plan_path = regenerate_for_account(cfg, acc)
        plan_index = names.index(batch)
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
    a.add_argument("--batch", default="auto",
                   help="本次运行使用哪个批次（默认按当前时间自动判断，如 4点/8点/16点）")
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
