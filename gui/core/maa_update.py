# -*- coding: utf-8 -*-
"""一键更新 MAA（版本更新 + 资源更新），两套 MAA 依次执行。

原理：MAA 启动时按 gui.new.json 顶层 Update 块自动检查并安装更新
（CheckOnStartup / AutoDownloadUpdatePackage / AutoInstallUpdatePackage），
版本更新与资源更新都由 MAA 自己完成：原地升级、目录名不变、装完自动重启。
MAA 是单实例程序（互斥锁），两套必须串行，不能同时开。

GitHub 直连不稳，更新前先启动 Clash（Clash Verge Rev，配置在 config.json
的 maa_update 节），并临时把 MAA 的 Update.Proxy 指向 Clash 混合端口，确保
下载走代理；同时临时把 StartUpSettings.RunDirectly 置 false——否则 MAA 启动
会直接连模拟器跑任务（RunDirectly 必须 true 是挂机流程的要求，更新完恢复）。
全部结束后恢复 MAA 原配置。注意 MAA 退出时会回写 gui.new.json，所以恢复必须
在 MAA 进程完全退出之后进行。

若更新前 Clash 已在运行则复用，结束时不主动关闭；若是本次启动的，结束时
关闭 Clash 并清理系统代理残留（Clash 被强杀后 ProxyEnable 可能残留，
残留的失效代理会导致普通网页打不开）。
"""
import ctypes
import json
import os
import shutil
import socket
import subprocess
import time
import winreg
from ctypes import wintypes
from pathlib import Path

from core import proc, runner

CREATE_NO_WINDOW = 0x08000000

MAA_EXE = "MAA.exe"
UPDATER_EXE = "MAA.Updater.exe"
# Clash Verge Rev 的 mihomo 内核进程，结束 Clash 时一并清理
CLASH_CORE_EXES = ("verge-mihomo.exe", "verge-mihomo-alpha.exe")
CLASH_START_WAIT_SEC = 90   # Clash 启动后等代理端口就绪的上限
INSTALL_SETTLE_SEC = 60     # 版本装完后等 MAA 重启 + 资源更新的宽限


def _run(args, timeout=15):
    """一次性命令（同 core/runner._run）：返回 (code, stdout, stderr)。"""
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout,
                           creationflags=CREATE_NO_WINDOW)
        return r.returncode, r.stdout, r.stderr
    except (subprocess.TimeoutExpired, OSError):
        return -1, b"", b""


def _wait_until(fn, timeout):
    """每 2 秒轮询 fn 直到为真或超时，返回是否等到。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(2)
    return False


# ---------- 版本号 ----------

_version_dll = ctypes.WinDLL("version", use_last_error=True)


def file_version(exe_path):
    """读取 exe 文件版本号（如 "6.17.1.0"）；读不到返回 None。"""
    path = str(exe_path)
    try:
        size = _version_dll.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        data = ctypes.create_string_buffer(size)
        if not _version_dll.GetFileVersionInfoW(path, 0, size, data):
            return None
        ptr = ctypes.c_void_p()
        blen = wintypes.UINT()
        if not _version_dll.VerQueryValueW(data, "\\", ctypes.byref(ptr),
                                           ctypes.byref(blen)):
            return None
        # VS_FIXEDFILEINFO：签名/结构版本之后是版本号高低两个 DWORD
        fixed = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint32 * 4)).contents
        ms, ls = fixed[2], fixed[3]
        return "%d.%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except OSError:
        return None


def _ver_tuple(v):
    try:
        return tuple(int(x) for x in str(v).lstrip("vV").split("."))
    except ValueError:
        return None


def _ver_gt(a, b):
    """a 的版本号是否比 b 新（位数不同按 0 补齐比较；带不带 v 前缀均可）。"""
    ta, tb = _ver_tuple(a), _ver_tuple(b)
    if ta is None or tb is None:
        return False
    n = max(len(ta), len(tb))
    ta += (0,) * (n - len(ta))
    tb += (0,) * (n - len(tb))
    return ta > tb


def has_update(cur, latest):
    """最新版本是否比当前新（任一未知返回 False）。"""
    return bool(cur and latest and _ver_gt(latest, cur))


def fmt_version(v):
    """展示用版本号：去掉 v 前缀和末尾多余的 .0（"v6.17.1" / "6.11.1.0" → "6.17.1"）。"""
    t = _ver_tuple(v)
    if t is None:
        return "未知"
    while len(t) > 3 and t[-1] == 0:
        t = t[:-1]
    return ".".join(str(x) for x in t) or "未知"


def _maa_targets(cfg):
    """[(key, 名称, 目录), ...]，目录可能为 None（未配置）。"""
    paths = cfg.get("paths") or {}
    return (
        ("official", "官服", paths.get("maa_official_dir")),
        ("bilibili", "B服", paths.get("maa_bilibili_dir")),
    )


def latest_version(maa_dir):
    """MAA 缓存的最新版本号（cache\\version\\stable.json 的 version 字段）。"""
    try:
        data = json.loads((Path(maa_dir) / "cache" / "version" / "stable.json")
                          .read_text(encoding="utf-8"))
        v = str(data.get("version") or data.get("tag_name") or "").strip()
        return v or None
    except (OSError, json.JSONDecodeError):
        return None


def version_rows(cfg):
    """仪表盘展示用：[(名称, 当前版本, 最新版本), ...]，缺失为 None。"""
    rows = []
    for _key, name, d in _maa_targets(cfg):
        if not d or not Path(d).is_dir():
            rows.append((name, None, None))
            continue
        exe = Path(d) / MAA_EXE
        rows.append((name, file_version(exe) if exe.is_file() else None,
                     latest_version(d)))
    return rows


# ---------- Clash（VPN）----------

def vpn_ready(port):
    """Clash 混合端口是否就绪（TCP 可连即视为内核已起）。"""
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=1):
            return True
    except OSError:
        return False


def vpn_start(cfg, log):
    """启动 Clash 并等代理端口就绪。

    返回 (started, ready)：started = 是否本次新启动（结束时需要关闭），
    ready = 代理端口是否就绪。已在运行则复用，结束后不关它。
    """
    mu = cfg.get("maa_update") or {}
    exe = str(mu.get("vpn_exe", "")).strip()
    port = int(mu.get("proxy_port", 7897))
    if not exe or not Path(exe).is_file():
        log("!! 找不到 Clash 程序：%s（请在「运行设置 → MAA 更新」里配置）"
            % (exe or "未配置"))
        return False, False
    if proc.process_running(Path(exe).name):
        log("Clash 已在运行，复用现有连接（更新结束后不会关闭它）")
        if not _wait_until(lambda: vpn_ready(port), 20):
            log("!! 代理端口 %d 未就绪，MAA 将尝试直连下载" % port)
        return False, vpn_ready(port)
    log("启动 Clash：%s" % exe)
    try:
        subprocess.Popen([exe], cwd=str(Path(exe).parent),
                         creationflags=CREATE_NO_WINDOW)
    except OSError as exc:
        log("!! Clash 启动失败：%s" % exc)
        return False, False
    if not _wait_until(lambda: vpn_ready(port), CLASH_START_WAIT_SEC):
        log("!! 等待 %d 秒后代理端口 %d 仍未就绪" % (CLASH_START_WAIT_SEC, port))
        return True, False
    log("Clash 代理端口 %d 已就绪" % port)
    return True, True


def vpn_stop(cfg, log):
    """结束 Clash（GUI + mihomo 内核）并清理系统代理残留。"""
    mu = cfg.get("maa_update") or {}
    exe = str(mu.get("vpn_exe", "")).strip()
    names = [Path(exe).name] if exe else []
    names.extend(n for n in CLASH_CORE_EXES if n not in names)
    log("关闭 Clash…")
    for n in names:
        _run(["taskkill", "/IM", n, "/F"])
    # 等进程真正退出（内核还在监听时清系统代理会误伤）
    _wait_until(lambda: not any(proc.process_running(n) for n in names), 10)
    _clear_system_proxy(log)


def _clear_system_proxy(log):
    """关掉 Windows 系统代理（ProxyEnable=0）并通知系统刷新。

    Clash 被强杀后系统代理设置可能残留，指向已关闭的 127.0.0.1 端口，
    会导致关闭 VPN 后上不了网，这里兜底清理。
    """
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0, winreg.KEY_SET_VALUE)
        try:
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        finally:
            key.Close()
        wininet = ctypes.WinDLL("wininet")
        wininet.InternetSetOptionW(None, 39, None, 0)  # SETTINGS_CHANGED
        wininet.InternetSetOptionW(None, 37, None, 0)  # REFRESH
        log("已清理系统代理残留")
    except OSError as exc:
        log("!! 清理系统代理失败：%s（如无法上网请到 系统设置→网络→代理 手动关闭）"
            % exc)


# ---------- MAA 配置临时修改 ----------

def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path, data):
    tmp = Path(path).with_suffix(Path(path).suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _backup(path):
    try:
        shutil.copy2(path, Path(path).with_suffix(Path(path).suffix + ".bak"))
    except OSError:
        pass


def _apply_update_config(maa_dir, proxy_url):
    """临时改 gui.new.json：下载走代理、强制自动更新、暂停直接运行。

    返回原值 dict（供结束后恢复）。更新前备份 .bak。
    """
    gui_new = Path(maa_dir) / "config" / "gui.new.json"
    data = _read_json(gui_new)
    _backup(gui_new)
    update = data.setdefault("Update", {})
    saved = {
        "Proxy": update.get("Proxy", ""),
        "ProxyType": update.get("ProxyType", "Http"),
        "CheckOnStartup": update.get("CheckOnStartup", False),
        "AutoDownloadUpdatePackage": update.get("AutoDownloadUpdatePackage", False),
        "AutoInstallUpdatePackage": update.get("AutoInstallUpdatePackage", False),
    }
    update["Proxy"] = proxy_url
    update["ProxyType"] = "Http"
    update["CheckOnStartup"] = True
    update["AutoDownloadUpdatePackage"] = True
    update["AutoInstallUpdatePackage"] = True
    conf = (data.get("Configurations") or {}).get(data.get("Current") or "Default")
    if isinstance(conf, dict):
        su = conf.setdefault("Gui", {}).setdefault("StartUpSettings", {})
        saved["RunDirectly"] = su.get("RunDirectly", True)
        su["RunDirectly"] = False   # 更新期间不要连模拟器跑任务
    _write_json(gui_new, data)
    return saved


def _restore_update_config(maa_dir, saved):
    """恢复 gui.new.json。必须在 MAA 进程退出后调用（否则会被回写覆盖）。"""
    gui_new = Path(maa_dir) / "config" / "gui.new.json"
    try:
        data = _read_json(gui_new)
    except (OSError, json.JSONDecodeError):
        return
    update = data.setdefault("Update", {})
    update["Proxy"] = saved.get("Proxy", "")
    update["ProxyType"] = saved.get("ProxyType", "Http")
    update["CheckOnStartup"] = saved.get("CheckOnStartup", False)
    update["AutoDownloadUpdatePackage"] = saved.get("AutoDownloadUpdatePackage", False)
    update["AutoInstallUpdatePackage"] = saved.get("AutoInstallUpdatePackage", False)
    conf = (data.get("Configurations") or {}).get(data.get("Current") or "Default")
    if isinstance(conf, dict) and "RunDirectly" in saved:
        conf.setdefault("Gui", {}).setdefault("StartUpSettings", {})[
            "RunDirectly"] = saved["RunDirectly"]
    try:
        _write_json(gui_new, data)
    except OSError:
        pass


# ---------- 更新流程 ----------

GUI_LOG = Path("debug") / "gui.log"
# gui.log 里的关键节点（MAA 更新流程：启动检查 → 后台下载 → 下次启动安装）
MARK_SUMMARY = "api/version/summary.json"      # 版本检查完成（无论有无更新都会拉）
MARK_DOWNLOAD = "Start to download file from"  # 发现新版本，开始下载更新包
MARK_DOWNLOADED = "Remove download temp file"  # 下载结束（成功或失败都清临时文件）
MARK_PENDING = "Pending update package detected"  # 启动时检测到待安装更新包
NO_UPDATE_GRACE_SEC = 30     # 版本检查完成且无下载动作后，再等的确认时间


def _read_new_log_lines(path, offset):
    """从 offset 起读取 gui.log 新增的完整行，返回 (新offset, 行列表)。

    文件被轮转/重写（体积小于 offset，MAA 每次启动会把旧日志挪到 .bak）时从头读。
    """
    try:
        if path.stat().st_size < offset:
            offset = 0
    except OSError:
        return offset, []
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return offset, []
    if not data:
        return offset, []
    cut = data.rfind(b"\n")   # 只消费完整行，半行留给下次
    if cut == -1:
        return offset, []
    return offset + cut + 1, data[:cut].decode("utf-8", errors="replace").splitlines()


def _kill_maa(log):
    """结束 MAA 主程序与更新器进程（强杀，不等待用户操作）。"""
    if proc.process_running(MAA_EXE):
        log("结束 MAA 进程…")
        _run(["taskkill", "/IM", MAA_EXE, "/F"])
    if proc.process_running(UPDATER_EXE):
        _run(["taskkill", "/IM", UPDATER_EXE, "/F"])
    _wait_until(lambda: not proc.process_running(MAA_EXE), 15)


def _start_maa(exe, maa_dir, log):
    try:
        subprocess.Popen([str(exe)], cwd=str(maa_dir),
                         creationflags=CREATE_NO_WINDOW)
        return True
    except OSError as exc:
        log("!! 启动 MAA 失败：%s" % exc)
        return False


def _watch_maa(exe, maa_dir, old_ver, deadline, log, expect_download=False):
    """启动 MAA 并监控，直到得出结论。

    返回 (new_ver, updater_seen, outcome)：
    outcome = "updated"（版本已变） | "latest"（检查完无更新）
            | "pending"（更新包已下载，需重启 MAA 安装） | "exited"（MAA 退出了）
    expect_download=True 时（第二阶段安装）不做 "latest" 判断，只等版本变化。
    """
    maa_dir = Path(maa_dir)
    log_path = maa_dir / GUI_LOG
    try:
        offset = log_path.stat().st_size
    except OSError:
        offset = 0
    started = time.time()
    updater_seen = False
    saw_summary = saw_download = downloaded = False
    summary_at = 0.0
    last_note = started
    time.sleep(3)   # 等 MAA 进程真正起来，避免误判「提前退出」
    while time.time() < deadline:
        if not updater_seen and proc.process_running(UPDATER_EXE):
            updater_seen = True
            log("MAA.Updater 正在安装更新…")
        v = file_version(exe)
        if v and (old_ver is None or _ver_gt(v, old_ver)):
            return v, updater_seen, "updated"
        maa_up = proc.process_running(MAA_EXE)
        upd_up = proc.process_running(UPDATER_EXE)
        if not maa_up and not upd_up:
            if downloaded:
                return None, updater_seen, "pending"   # 下完还没装就退了
            return None, updater_seen, "exited"
        offset, lines = _read_new_log_lines(log_path, offset)
        for line in lines:
            if MARK_SUMMARY in line and not saw_summary:
                saw_summary = True
                summary_at = time.time()
            elif MARK_DOWNLOAD in line and not saw_download:
                saw_download = True
                log("发现新版本，开始下载更新包…")
            elif MARK_DOWNLOADED in line and saw_download and not downloaded:
                downloaded = True
                log("更新包下载完成")
            elif MARK_PENDING in line and not downloaded:
                downloaded = True   # 启动即安装残留包的场景
        if saw_summary and not saw_download and not expect_download \
                and time.time() - summary_at > NO_UPDATE_GRACE_SEC:
            return None, updater_seen, "latest"
        if saw_download and downloaded:
            return None, updater_seen, "pending"
        if time.time() - last_note >= 60:
            last_note = time.time()
            log("更新进行中…（已等待 %d 分钟）" % int((time.time() - started) // 60))
        time.sleep(3)
    return None, updater_seen, "timeout"


def update_one(name, maa_dir, proxy_url, timeout_min, log):
    """更新一套 MAA。返回 (ok: bool, message: str)。

    MAA 的版本更新分两跳：启动时检查并后台下载更新包（不立即安装），下次
    启动检测到待安装包才交给 MAA.Updater 原地安装并自动重启。因此下载完成后
    要把 MAA 重启一次触发安装。资源更新由 MAA 启动时自行检查（不自动下载，
    只在 MAA 界面提示，见 run_full_update 说明）。
    """
    maa_dir = Path(maa_dir)
    exe = maa_dir / MAA_EXE
    if not exe.is_file():
        return False, "找不到 %s" % exe
    old_ver = file_version(exe)
    latest = latest_version(maa_dir)
    log("当前版本：%s；缓存最新版本：%s" % (old_ver or "未知", latest or "未知"))
    if old_ver and latest and not _ver_gt(latest, old_ver):
        log("版本已是最新，仍启动一次检查版本与资源更新")

    try:
        saved = _apply_update_config(maa_dir, proxy_url)
    except (OSError, json.JSONDecodeError) as exc:
        return False, "修改 gui.new.json 失败：%s" % exc

    ok, msg = False, ""
    try:
        deadline = time.time() + timeout_min * 60
        log("启动 MAA 检查更新…")
        if not _start_maa(exe, maa_dir, log):
            return False, "启动 MAA 失败"
        new_ver, updater_seen, outcome = _watch_maa(
            exe, maa_dir, old_ver, deadline, log)

        if outcome == "pending":
            # 下载完成但没装（MAA 装更新靠下次启动），重启触发安装
            _kill_maa(log)
            log("重启 MAA 安装已下载的更新…")
            if not _start_maa(exe, maa_dir, log):
                return False, "重启 MAA 安装更新失败"
            new_ver, updater_seen, outcome = _watch_maa(
                exe, maa_dir, old_ver, deadline, log, expect_download=True)

        if new_ver:
            log("版本已更新到 %s，等待 MAA 自动重启与启动自检…" % new_ver)
            _wait_until(lambda: not proc.process_running(UPDATER_EXE), 180)
            _wait_until(lambda: proc.process_running(MAA_EXE), 120)
            time.sleep(INSTALL_SETTLE_SEC)
            ok = True
            msg = "版本 %s → %s" % (old_ver or "?", new_ver)
        elif outcome == "latest":
            ok = True
            msg = "已是最新（版本 %s）" % (file_version(exe) or old_ver or "?")
        elif outcome == "exited":
            ok = False
            msg = "MAA 提前退出，未执行更新（请手动打开 MAA 看是否弹窗报错）"
        elif updater_seen:
            # 装过更新包但版本号没变化（如资源增量包）
            _wait_until(lambda: not proc.process_running(UPDATER_EXE), 180)
            ok = True
            msg = "更新流程完成（版本 %s）" % (file_version(exe) or old_ver or "?")
        else:
            ok = False
            msg = "更新超时（%d 分钟），请稍后重试或手动打开 MAA 更新" % timeout_min
    finally:
        _kill_maa(log)
        _restore_update_config(maa_dir, saved)
    return ok, msg


def run_full_update(cfg, log):
    """一键更新两套 MAA（串行）。返回 (ok: bool, summary: str)，log 为逐行回调。"""
    if runner.is_running():
        return False, "挂机正在运行，请先停止挂机再更新 MAA"
    if proc.process_running(MAA_EXE):
        return False, "MAA 正在打开，请先关闭 MAA 窗口再更新"

    mu = cfg.get("maa_update") or {}
    timeout_min = int(mu.get("timeout_min", 15))
    port = int(mu.get("proxy_port", 7897))
    proxy_url = "http://127.0.0.1:%d" % port
    use_vpn = bool(mu.get("use_vpn", True))
    started_vpn = False

    results = []
    ok_all = True
    try:
        if use_vpn:
            started_vpn, ready = vpn_start(cfg, log)
            if started_vpn and not ready:
                return False, "Clash 启动后代理端口未就绪，已取消更新"
            if not ready:
                log("!! 无可用代理，MAA 将直连下载（可能较慢或失败）")
        for _key, name, mdir in _maa_targets(cfg):
            if not mdir or not Path(mdir).is_dir():
                results.append("%s：MAA 目录未配置或不存在" % name)
                ok_all = False
                continue
            log("")
            log("======== %s MAA ========" % name)
            try:
                ok, msg = update_one(name, Path(mdir), proxy_url, timeout_min, log)
            except Exception as exc:  # 单套异常不影响另一套与收尾
                ok, msg = False, "更新过程异常：%s" % exc
            log("结果：%s" % msg)
            results.append("%s %s" % (name, msg))
            ok_all = ok_all and ok
    finally:
        if started_vpn:
            vpn_stop(cfg, log)
    return ok_all, "；".join(results)
