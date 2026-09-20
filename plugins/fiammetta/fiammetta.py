# -*- coding: utf-8 -*-
r"""菲亚梅塔心情恢复插件（换班前先恢复目标干员心情）。

设置保存在控制台「精确基建」配置里，且**按批次**独立：
    accounts[].base_schedule.batches[<批次>].fiammetta = {"enable", "target"}
（账号管理 → 点账号卡片 → 精确基建配置 → 顶部「菲亚梅塔」一行，跟着「批次」
下拉切换；或用「导入排班文件」从排班表 JSON 各计划的 plan["Fiammetta"] 导入，
有的批次开、有的批次不开都照原样落在对应批次上。）

MAA 有两套入口，按基建模式二选一，两者都是「先恢复心情，再换班」：

1. 自定义模式（账号开着「精确基建」）
   计划 JSON 里的 plan["Fiammetta"] = {enable, target, order: "pre"}，
   由 base_schedule 生成计划时按该设置写入（直接写进每个批次）。
   MaaCore 解析计划时会把
   「目标单独进一次宿舍 → 目标+菲亚梅塔进一次宿舍（强制换位触发技能）」
   插到子任务列表最前面（InfrastTask.cpp 的 parse_and_set_custom_config），
   所以整套换班开始前就把目标干员的心情换满了。

2. 常规模式（未启用精确基建）
   MAA 只读基建任务参数 fiammetta_recovery_enabled / fiammetta_targets，
   对应源码里 mode == Mode::Default 才生效的「宿舍前置轮」
   （FacilityStep::DormPrepare），同样排在换班设施之前。本插件负责把账号
   设置写进该服 MAA 的 config/gui.new.json（当前配置的 InfrastTask）。

两套同时写不冲突：MaaCore 在自定义模式下不读任务级参数、常规模式下不读计划级
Fiammetta，互斥生效。

用法（master.ps1 启动 MAA 前按账号调用）：
    python fiammetta.py apply --config D:\1\config.json --account official1
"""

import argparse
import json
import sys
from pathlib import Path

DEFAULT_CONFIG = Path(r"D:\1\config.json")

# 插件公共工具（plugins\common.py）——此前本文件与其它四个插件各复制一份
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import (atomic_json_write as _atomic_json_write, find_account,
                    load_config, log_file, maa_dir_for as _maa_dir)

# 复用精确基建插件的班次判断（当前时间落在哪个班次），避免两边逻辑漂移
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "base_schedule"))
try:
    import base_schedule as bsplugin
except Exception:   # noqa: BLE001 - 插件缺失时退回「取第一个开启的批次」
    bsplugin = None


def _log_file(cfg, msg):
    log_file(cfg, "菲亚梅塔", msg)


def active_batch(cfg):
    """当前时间落在哪个班次 → (批次名, 按时间升序的批次名列表)。"""
    if bsplugin is None:
        return None, []
    try:
        entries, names, _periods = bsplugin.schedule_spec(cfg)
        return bsplugin.current_batch(entries=entries), names
    except Exception:   # noqa: BLE001 - 配置异常时退回方案 B
        return None, []


def maa_settings(cfg, acc):
    """当前班次在精确基建里的菲亚梅塔设置 → (enabled, target, batch)。"""
    bs = acc.get("base_schedule") if isinstance(acc, dict) else None
    if not isinstance(bs, dict):
        return False, "", None
    batches = bs.get("batches") if isinstance(bs.get("batches"), dict) else {}
    global_fia = bs.get("fiammetta") if isinstance(bs.get("fiammetta"), dict) else None
    name, names = active_batch(cfg)

    def batch_fia(b):
        v = batches.get(b)
        v = v.get("fiammetta") if isinstance(v, dict) else None
        # 该批次还没单独设置时，沿用早期写在精确基建全局的那一份
        return v if isinstance(v, dict) else global_fia

    fia = batch_fia(name) if name else None
    if not isinstance(fia, dict):
        # 班次判断不出来 / 批次名对不上（没配启动时间等）：取第一个开启的批次
        order = list(batches) + [n for n in names if n not in batches]
        for b in order:
            cand = batch_fia(b)
            if (isinstance(cand, dict) and cand.get("enable")
                    and str(cand.get("target") or "").strip()):
                fia, name = cand, b
                break
    if not isinstance(fia, dict):
        return False, "", name
    target = str(fia.get("target") or "").strip()
    # 兼容旧字段名：批次级用 enable，早期全局/账号级写法用 enabled
    enabled = bool(fia.get("enable", fia.get("enabled", False)))
    return (enabled and bool(target)), target, name


def find_infrast_task(maa_dir):
    """返回 (data, task, path)：MAA config/gui.new.json 当前配置的 InfrastTask。"""
    path = Path(maa_dir) / "config" / "gui.new.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    cur = data.get("Current") or "Default"
    section = (data.get("Configurations") or {}).get(cur)
    queue = section.get("TaskQueue") if isinstance(section, dict) else None
    if not isinstance(queue, list):
        return None
    for task in queue:
        if isinstance(task, dict) and task.get("$type") == "InfrastTask":
            return data, task, path
    return None


def apply_maa_config(maa_dir, enabled, target, log=print):
    """把菲亚梅塔设置写进 MAA 基建任务（常规模式生效）。

    启用时只保留用户选的那一个目标（FiammettaTarget2/3 清空），避免 MAA
    界面上残留的默认目标（清流/可露希尔/但书）被一并恢复；
    未启用时只关开关，不动目标。
    """
    found = find_infrast_task(maa_dir)
    if not found:
        log("WARN 未在 %s 找到 MAA 基建任务（config/gui.new.json），"
            "跳过菲亚梅塔设置" % maa_dir)
        return []
    data, task, path = found
    changed = []
    if bool(task.get("FiammettaRecoveryEnabled")) != bool(enabled):
        task["FiammettaRecoveryEnabled"] = bool(enabled)
        changed.append("FiammettaRecoveryEnabled")
    if enabled:
        for key in ("FiammettaTarget1", "FiammettaTarget2", "FiammettaTarget3"):
            want = target if key == "FiammettaTarget1" else ""
            if str(task.get(key) or "") != want:
                task[key] = want
                changed.append(key)
    if changed:
        _atomic_json_write(path, data)
    return changed


def cmd_apply(args):
    cfg = load_config(args.config)
    acc = find_account(cfg, args.account)
    if acc is None:
        print("ERROR account not found: %s" % args.account)
        return 2

    server = args.server or acc.get("server") or "official"
    label = acc.get("label") or args.account
    bs = acc.get("base_schedule") if isinstance(acc.get("base_schedule"), dict) else {}
    batches = bs.get("batches") if isinstance(bs.get("batches"), dict) else {}
    has_per_batch = any(isinstance(v.get("fiammetta"), dict)
                        for v in batches.values() if isinstance(v, dict))
    if not has_per_batch and not isinstance(bs.get("fiammetta"), dict):
        # 老配置里还没有这个字段：不碰 MAA 设置，避免覆盖用户手工开的开关
        print("SKIP fiammetta not configured")
        return 0
    enabled, target, batch = maa_settings(cfg, acc)
    batch_lab = ("%s批" % batch) if batch else "当前班次"
    if enabled and not target:
        _log_file(cfg, "账号「%s」的 %s 开着菲亚梅塔恢复但没选目标，本次不恢复"
                  % (label, batch_lab))
        print("WARN enabled but no target, skip")
        enabled = False

    maa_dir = _maa_dir(cfg, server)
    if not maa_dir or not maa_dir.exists():
        _log_file(cfg, "WARN 未找到 %s 的 MAA 目录：%s，跳过菲亚梅塔设置"
                  % (server, maa_dir))
        print("WARN no maa dir")
        return 0

    changed = apply_maa_config(
        maa_dir, enabled, target, log=lambda m: _log_file(cfg, m))
    if enabled:
        _log_file(cfg, "账号「%s」%s 菲亚梅塔恢复已写入：目标 %s（换班前恢复；"
                  "常规模式参数 %s）"
                  % (label, batch_lab, target, ", ".join(changed) or "无改动"))
        print("OK fiammetta %s (%s)" % (target, batch_lab))
    else:
        _log_file(cfg, "账号「%s」%s 未启用菲亚梅塔恢复（常规模式参数已关闭，%s）"
                  % (label, batch_lab, ", ".join(changed) or "无改动"))
        print("OK fiammetta off")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="菲亚梅塔心情恢复插件")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("apply", help="按账号把菲亚梅塔设置写进 MAA 基建任务")
    a.add_argument("--config", default=str(DEFAULT_CONFIG))
    a.add_argument("--account", required=True)
    a.add_argument("--server", choices=["official", "bilibili"], default=None)
    a.set_defaults(func=cmd_apply)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
