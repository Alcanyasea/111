# -*- coding: utf-8 -*-
# ============================================================
# 逐账号 MAA 干员识别导出（OperBox）
# 对 config.json 中每个启用的账号：
# 槽位切号 → 游戏更新等待 → 登录校验 →
# 用该服务器对应 MAA 的 MaaCore 跑「干员识别」任务，识别结果存成 JSON。
# 输出：D:\1\exports\<slot>.json（每号一份）+ exports\summary.json（汇总，
# 另在 exports\history\ 按时间戳归档一份）+
# 原始回调留档：scripts\debug\operbox\<slot>.callbacks.json（排查用）
#
# 与挂机互斥：master.lock 存在且是活着的 powershell 时拒绝运行；
# MAA.exe 正在运行（手动打开或控制台更新中）时同样拒绝，绝不抢杀。
#
# 用法（任一 Python 均可，仅用标准库）：
#   python export_operbox.py                     # 导出全部启用的账号
#   python export_operbox.py --only official_1   # 只导指定槽位（无视启用开关）
#   python export_operbox.py --account official_1 --no-switch   # 跳过切号，识别当前登录账号
# 退出码：0 全部成功；1 有账号失败
# ============================================================
import argparse
import ctypes
import json
import os
import pathlib
import re
import subprocess
import sys
import time
from datetime import datetime

BASE = pathlib.Path(__file__).resolve().parent          # D:\1\scripts
ROOT = BASE.parent                                       # D:\1
CONFIG_PATH = ROOT / "config.json"
DEFAULT_OUT_DIR = ROOT / "exports"
DEBUG_DIR = BASE / "debug" / "operbox"
LOG_PATH = BASE / "operbox_export.log"
MASTER_LOCK = BASE / "master.lock"

SERVER_TO_CLIENT = {"official": "Official", "bilibili": "Bilibili"}

# ---- 进程检查（ctypes Toolhelp，与 gui/core/proc.py 同机制，不派生子进程）----
_TH32CS_SNAPPROCESS = 0x00000002


class _ProcessEntry32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_ulong),
        ("cntUsage", ctypes.c_ulong),
        ("th32ProcessID", ctypes.c_ulong),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", ctypes.c_ulong),
        ("cntThreads", ctypes.c_ulong),
        ("th32ParentProcessID", ctypes.c_ulong),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_ulong),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def _snap_processes():
    """[(pid, exe名小写), ...]；快照失败返回 None（按检查不过处理，宁可不跑）。"""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    snap = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    invalid = ctypes.c_void_p(-1).value
    if not snap or snap == invalid:
        return None
    out = []
    entry = _ProcessEntry32W()
    entry.dwSize = ctypes.sizeof(_ProcessEntry32W)
    ok = k32.Process32FirstW(snap, ctypes.byref(entry))
    while ok:
        out.append((entry.th32ProcessID, str(entry.szExeFile).lower()))
        ok = k32.Process32NextW(snap, ctypes.byref(entry))
    k32.CloseHandle(snap)
    return out


def master_running():
    """master.ps1（挂机）是否在运行：锁文件里的 PID 是活着的 powershell。"""
    try:
        pid = int(MASTER_LOCK.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return False
    procs = _snap_processes()
    if procs is None:
        return True   # 查不到进程表时宁可误判在跑，不与挂机抢模拟器
    for p, name in procs:
        if p == pid and name in ("powershell.exe", "pwsh.exe"):
            return True
    return False


def maa_running():
    """MAA.exe 是否在运行（手动打开或控制台更新中）。"""
    procs = _snap_processes()
    if procs is None:
        return True
    return any(name == "maa.exe" for _p, name in procs)


def log(msg):
    line = "[{}] {}".format(datetime.now().strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def run_ps1(name, args, timeout=3600):
    """调用 scripts 下 PowerShell 子脚本，返回 (退出码, 合并输出文本)。"""
    cmd = [
        "pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(BASE / name),
    ] + [str(a) for a in args]
    try:
        r = subprocess.run(
            cmd, cwd=str(BASE), timeout=timeout,
            capture_output=True,
        )
        out = (r.stdout or b"") + (r.stderr or b"")
        try:
            text = out.decode("utf-8")
        except UnicodeDecodeError:
            text = out.decode("gbk", errors="replace")
        return r.returncode, text
    except subprocess.TimeoutExpired:
        return 124, "{} timed out after {}s".format(name, timeout)


def start_mumu(cfg):
    """复用 master.ps1 的启动逻辑：先连 ADB，连不上才拉起模拟器。"""
    adb = cfg["paths"]["adb"]
    device = cfg["paths"]["device"]
    cli = cfg["paths"].get("cli", "")
    subprocess.run([adb, "kill-server"], capture_output=True)
    time.sleep(1)
    r = subprocess.run([adb, "connect", device], capture_output=True, text=True)
    if re.search(r"connected|already", (r.stdout or "") + (r.stderr or "")):
        log("MuMu 已在运行，ADB 已连接")
        return True
    log("模拟器未运行，正在启动 MuMu...")
    if cli:
        subprocess.run([cli, "control", "-v", "0", "launch"], capture_output=True)
        time.sleep(5)
    deadline = time.time() + 120
    while time.time() < deadline:
        r = subprocess.run([adb, "connect", device], capture_output=True, text=True)
        if re.search(r"connected|already", (r.stdout or "") + (r.stderr or "")):
            log("ADB 已连接，等 15 秒让模拟器就绪")
            time.sleep(15)
            return True
        time.sleep(3)
    log("ERROR: MuMu 启动超时")
    return False


def get_game_package(maa_dir, server):
    """从该 MAA 的 resource/config.json 读游戏包名（official→Official, bilibili→Bilibili）。"""
    try:
        with open(pathlib.Path(maa_dir) / "resource" / "config.json", encoding="utf-8") as f:
            return json.load(f)["packageName"][SERVER_TO_CLIENT[server]]
    except Exception:
        return "com.hypergryph.arknights" if server == "official" \
            else "com.hypergryph.arknights.bilibili"


def extract_operbox(events):
    """取最后一个 OperBoxRecognitionTask 快照（MAA 逐步回报，最后一份最全）：
    all_opers = 全 roster（含 own 标记）；own_opers = 已拥有（含 elite/level/potential）。"""
    final = None
    for e in events:
        if e.get("name") != "SubTaskExtraInfo":
            continue
        det = e.get("details") or {}
        d = det.get("details")
        if isinstance(d, dict) and "all_opers" in d and "own_opers" in d:
            final = d
    return final


def recognize(maa_dir, adb, address, client_type, out_path, callbacks_path,
              touch="maatouch", maa_timeout=900, connect_config="General"):
    """加载 MaaCore 跑 StartUp + OperBox，把干员识别结果写到 out_path。"""
    maa_dir = str(maa_dir)
    sys.path.insert(0, str(pathlib.Path(maa_dir) / "Python"))
    from asst.asst import Asst                       # noqa: E402
    from asst.utils import Message, InstanceOptionType  # noqa: E402

    events = []

    def cb(msg, details, arg):
        try:
            d = json.loads(details.decode("utf-8"))
        except Exception:
            d = {"raw": details.decode("utf-8", errors="replace")}
        try:
            name = Message(msg).name
        except ValueError:
            name = str(msg)
        events.append({"msg": int(msg), "name": name, "details": d})

    user_dir = DEBUG_DIR / pathlib.Path(out_path).stem
    user_dir.mkdir(parents=True, exist_ok=True)

    if not Asst.load(path=maa_dir, user_dir=str(user_dir)):
        return False, "Asst.load 失败（MaaCore/资源加载失败）", 0, 0

    # 绑定的 AsstCreateEx 声明为 c_void_p，回调必须先包成 CFUNCTYPE 指针
    cb_c = Asst.CallBackType(cb)
    asst = Asst(callback=cb_c)
    version = asst.get_version()
    asst.set_instance_option(InstanceOptionType.touch_type, touch)
    if not asst.connect(adb, address, connect_config):
        return False, "MAA 连接模拟器失败（adb: {} {}）".format(adb, address), 0, 0

    # 不用 MAA 的 CloseDown 冷启动（实测 StartUp 拉起不稳）：需要干净状态时
    # 由脚本用 adb 重启游戏（与 slot_switch 同机制），再走更新等待+登录校验，
    # 复现「新启动游戏 → StartUp 导航主界面 → OperBox」的成功路径
    asst.append_task("StartUp", {
        "client_type": client_type,
        "start_game": {"enable": True},
    })
    asst.append_task("OperBox", {"enable": True})
    asst.start()

    t0 = time.time()
    timed_out = False
    while asst.running():
        if time.time() - t0 > maa_timeout:
            timed_out = True
            asst.stop()
            break
        time.sleep(1)
    time.sleep(2)

    with open(callbacks_path, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=1)

    errors = [e for e in events if e["name"] in ("TaskChainError", "InternalError", "InitFailed")]
    if timed_out:
        return False, "MAA 干员识别超时（{} 秒）".format(maa_timeout), 0, 0
    if errors:
        return False, "MAA 任务链报错: {}".format(errors[-1]["details"]), 0, 0
    snap = extract_operbox(events)
    if not snap or not snap.get("all_opers"):
        return False, "未从回调中捕获到干员数据（详见 {}）".format(callbacks_path), 0, 0
    if snap.get("done") is False:
        return False, "MAA 识别提前结束（done=false），结果不完整（详见 {}）".format(callbacks_path), 0, 0

    # 已拥有条目带 elite/level/potential，按 id 并进全名单
    own_detail = {o.get("id"): o for o in snap.get("own_opers", [])
                  if isinstance(o, dict)}
    opers = []
    for o in snap["all_opers"]:
        if not isinstance(o, dict):
            continue
        merged = dict(o)
        d = own_detail.get(o.get("id"))
        if d:
            for k in ("elite", "level", "potential"):
                if k in d:
                    merged[k] = d[k]
        opers.append(merged)
    own_n = sum(1 for o in opers if o.get("own"))

    result = {
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "maa_dir": maa_dir,
        "maa_version": version,
        "own_count": own_n,
        "roster_count": len(opers),
        "operators": opers,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    return True, "ok (MAA {})".format(version), own_n, len(opers)


def process_account(cfg, acc, out_dir, no_switch=False, touch="maatouch",
                    maa_timeout=900):
    """单账号全流程：切号 → 更新等待 → 登录校验 → 干员识别。返回结果 dict。"""
    slot = acc.get("slot") or ""
    label = acc.get("label") or slot or acc.get("id") or "?"
    server = acc.get("server") or "official"
    res = {"slot": slot, "label": label, "server": server, "ok": False,
           "own_count": 0, "roster_count": 0, "error": ""}

    if server not in SERVER_TO_CLIENT:
        res["error"] = "未知服务器类型: {}".format(server)
        return res

    maa_dir = (cfg["paths"].get("maa_official_dir") if server == "official"
               else cfg["paths"].get("maa_bilibili_dir"))
    if not maa_dir or not pathlib.Path(maa_dir).is_dir():
        res["error"] = "MAA 目录不存在: {}".format(maa_dir)
        return res

    slot_path = BASE / "accounts" / slot
    if not no_switch:
        # 槽位缺失：官服拒绝（防跑错号）；B服仅切客户端
        if slot and slot_path.is_dir():
            rc, out = run_ps1("slot_switch.ps1", ["-Server", server, "-Slot", slot])
            if rc != 0:
                res["error"] = "槽位切号失败（slot_switch 退出码 {}）".format(rc)
                return res
        elif server == "bilibili":
            log("  [WARN] 槽位 {} 不存在，仅切换 B 服客户端".format(slot))
            rc, out = run_ps1("switch_to_B服.ps1", [])
            if rc != 0:
                res["error"] = "切换 B 服客户端失败"
                return res
        else:
            res["error"] = "官服槽位 {} 不存在，拒绝运行（防跑错号）".format(slot)
            return res

        # 更新等待已内置于 login_check（v1.3.2 起合并），不再单独跑
        # game_update_wait.ps1：冷启动可能闪过「获取更新配置」后停在标题画面，
        # 独立等待只会干等到超时，标题/更新/公告都由 login_check 处理
    else:
        # --no-switch：不切号，但把游戏重启到干净状态（识别不能从残留页面开始）
        pkg = get_game_package(maa_dir, server)
        adb = cfg["paths"]["adb"]
        device = cfg["paths"]["device"]
        log("  重启游戏（{}）以获得干净状态...".format(pkg))
        subprocess.run([adb, "-s", device, "shell", "am", "force-stop", pkg],
                       capture_output=True)
        time.sleep(2)
        subprocess.run([adb, "-s", device, "shell", "monkey", "-p", pkg,
                        "-c", "android.intent.category.LAUNCHER", "1"],
                       capture_output=True)
        time.sleep(5)

    # 登录校验：按服务器分派（B服 有独立流程：开屏剧情 START 跳过、无标题画面、
    # 登录界面快速失败——此前对 B服 也跑官服式校验，开屏剧情页会盲点空转到超时）
    login_script = "login_check_bilibili.ps1" if server == "bilibili" else "login_check.ps1"
    rc, out = run_ps1(login_script,
                      ["-Server", server, "-Slot", slot,
                       "-ScreenTimeoutSec", 300])
    if rc != 0:
        res["error"] = "登录校验失败（{} 退出码 {}），请重新捕获该账号".format(login_script, rc)
        return res

    out_path = out_dir / "{}.json".format(slot or "no-slot")
    callbacks_path = DEBUG_DIR / "{}.callbacks.json".format(slot or "no-slot")
    ok, msg, own_n, roster_n = recognize(
        maa_dir=maa_dir,
        adb=cfg["paths"]["adb"],
        address=cfg["paths"]["device"],
        client_type=SERVER_TO_CLIENT[server],
        out_path=str(out_path),
        callbacks_path=str(callbacks_path),
        touch=touch,
        maa_timeout=maa_timeout,
    )
    res["ok"], res["error"] = ok, ("" if ok else msg)
    res["own_count"], res["roster_count"] = own_n, roster_n
    return res


def run_child(cmd, env, timeout):
    """跑单账号子进程，超时时杀整棵进程树。

    subprocess.run 的超时只会杀直接子进程（python），它派生的 PowerShell
    和 MaaCore 会全部存活继续占着 ADB/游戏，所以这里用 taskkill /T 兜底。
    返回 (returncode|None, stdout, stderr)，None = 超时被杀。
    """
    try:
        p = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
            cwd=str(BASE),
        )
    except OSError as exc:
        return -1, b"", "启动子进程失败: {}".format(exc).encode("utf-8")
    try:
        out_b, err_b = p.communicate(timeout=timeout)
        return p.returncode, out_b or b"", err_b or b""
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                       capture_output=True)
        try:
            p.wait(timeout=15)
        except subprocess.TimeoutExpired:
            p.kill()
        return None, b"", b""


def main():
    ap = argparse.ArgumentParser(description="逐账号 MAA 干员识别导出")
    ap.add_argument("--only", help="只处理该槽位（如 official_1），走完整流程")
    ap.add_argument("--account", help=argparse.SUPPRESS)  # 单账号子进程模式
    ap.add_argument("--no-switch", action="store_true",
                    help="跳过切号（识别当前登录账号）；仍会重启游戏并做登录校验，保证识别从干净状态开始")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--touch", default="maatouch",
                    help="MAA 触控模式: maatouch/minitouch/seminitouch")
    ap.add_argument("--maa-timeout", type=int, default=900,
                    help="单号 MAA 识别超时（秒）")
    args = ap.parse_args()

    cfg = load_config()
    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    # ---- 子进程模式：真正跑单账号 ----
    if args.account:
        acc = next((a for a in cfg["accounts"] if a.get("slot") == args.account), None)
        if acc is None:
            print("RESULT_JSON:" + json.dumps({"ok": False, "error": "config 中无该槽位: " + args.account}))
            return 1
        res = process_account(cfg, acc, out_dir, no_switch=args.no_switch,
                              touch=args.touch, maa_timeout=args.maa_timeout)
        print("RESULT_JSON:" + json.dumps(res, ensure_ascii=False))
        return 0 if res["ok"] else 1

    # ---- 编排模式：逐个账号起独立子进程（MaaCore 每号干净加载/卸载）----
    # 互斥检查：挂机（master.ps1）或 MAA 正在运行时拒绝，绝不抢模拟器
    if master_running():
        log("ERROR: 挂机流程正在运行（master.lock），与导出互斥；请停止挂机后再导出")
        return 1
    if maa_running():
        log("ERROR: MAA.exe 正在运行（手动打开或控制台更新中），与导出互斥；请先关闭 MAA")
        return 1

    accounts = [a for a in cfg["accounts"] if a.get("enabled", True)]
    if args.only:
        # 显式指定槽位 = 用户明确意图，无视 enabled 开关
        accounts = [a for a in cfg["accounts"] if a.get("slot") == args.only]
        if not accounts:
            log("ERROR: config 中没有槽位 {}".format(args.only))
            return 1
    elif not accounts:
        log("ERROR: 没有启用的账号（在控制台账号管理页启用账号，"
            "或用 --only 指定槽位）")
        return 1
    log("=== 干员资料导出开始：{} 个账号 → {} ===".format(len(accounts), out_dir))

    if not start_mumu(cfg):
        return 1

    # 单号子进程超时与配置联动：更新等待 + 登录校验 + MAA 识别 + 余量，
    # 避免配置调大后先撞到硬上限、报出笼统的「子进程异常退出」
    update_min = int(cfg.get("timeouts", {}).get("game_update_min", 90))
    child_timeout = max(3600, update_min * 60 + args.maa_timeout + 900)

    results = []
    for i, acc in enumerate(accounts, 1):
        slot = acc.get("slot") or "?"
        label = acc.get("label") or slot
        log("---- [{}/{}] {} ({}) ----".format(i, len(accounts), label, slot))
        cmd = [sys.executable, str(pathlib.Path(__file__).resolve()),
               "--account", slot, "--out-dir", str(out_dir),
               "--touch", args.touch, "--maa-timeout", str(args.maa_timeout)]
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        t0 = time.time()
        code, out_b, err_b = run_child(cmd, env, timeout=child_timeout)
        out = out_b.decode("utf-8", errors="replace")
        err = err_b.decode("utf-8", errors="replace")
        m = re.search(r"RESULT_JSON:(\{.*\})", out)
        if m:
            res = json.loads(m.group(1))
        else:
            tail = out.strip().splitlines()[-3:] if out.strip() else ["(无输出)"]
            if err.strip():
                tail += err.strip().splitlines()[-3:]
            if code is None:
                tail.append("子进程超时（>{} 秒），已强制结束进程树".format(child_timeout))
            elif code == -1:
                tail.append(err.strip() or "启动失败")
            else:
                tail.append("exit code: {}".format(code))
            res = {"slot": slot, "label": label, "server": acc.get("server"),
                   "ok": False, "own_count": 0, "roster_count": 0,
                   "error": "子进程异常退出 | " + " | ".join(tail)}
        res["duration_min"] = round((time.time() - t0) / 60, 1)
        results.append(res)
        log("  {} {}：{}{}".format(
            "OK  " if res["ok"] else "FAIL",
            label,
            "拥有 {}/{} 干员, {:.1f} 分钟".format(res["own_count"], res["roster_count"], res["duration_min"]) if res["ok"] else res["error"],
            "",
        ))

    summary_path = out_dir / "summary.json"
    summary = {
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "results": results,
    }
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    # 每次运行另存一份带时间戳的归档，方便对比练度变化（summary.json 始终是最新）
    try:
        history_dir = out_dir / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        with open(history_dir / "summary_{}.json".format(stamp), "w",
                  encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=1)
    except OSError as exc:
        log("WARN: 归档 summary 失败: {}".format(exc))

    ok_n = sum(1 for r in results if r["ok"])
    log("=== 完成：{}/{} 成功，汇总 {} ===".format(ok_n, len(results), summary_path))
    return 0 if ok_n == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
