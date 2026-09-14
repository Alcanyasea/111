# -*- coding: utf-8 -*-
"""官服账号 token 自检：不点「开始唤醒」离屏校验槽位缓存的登录 token。

槽位 shared_prefs\\HypergryphSdkPreferences.xml 的 USER_CACHE 里缓存着
鹰角通行证 token（游戏点「开始唤醒」时向服务器校验的就是它）。这里拿
最新一次登录的 token 直接调官方校验接口：

    GET https://as.hypergryph.com/user/info/v1/basic?token=<token>
    HTTP 200 / status=0        → 有效
    HTTP 401 / status=3        → 「登录已过期，请重新登录」= 失效
    其他（超时/5xx/改版）      → error，不影响已有判定

2026-09-14 实测：前一天跑过的槽位返回 200，约两周没用过的槽位返回 401。
B 服走 B 站 SDK，槽位 SDK 配置里没有 USER_CACHE，自然跳过。

结果统一写入 scripts\\accounts\\<slot>\\token_status.json，仪表盘标红与
login_check.ps1 的快速失败共用同一份状态（PS 侧探测逻辑同源）。
"""
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

PROBE_URL = "https://as.hypergryph.com/user/info/v1/basic?token="
_UA = "okhttp/4.9.3"
_TIMEOUT = 15

# 启动补检的保鲜期：ok 状态在 6 小时内不重查（换主题重建窗口不重复探测；
# 过期/出错的状态每次启动都重查，便于捕获「用户已修复」的恢复）
FRESH_SEC = 6 * 3600

# read_all 的 mtime 缓存：仪表盘每 5 秒读一次状态文件，未变化时直接回内存
_status_cache = {}


def status_path(cfg, slot):
    return Path(cfg["paths"]["script_dir"]) / "accounts" / slot / "token_status.json"


def _sdk_prefs_path(cfg, slot):
    return Path(cfg["paths"]["script_dir"]) / "accounts" / slot / "shared_prefs" / "HypergryphSdkPreferences.xml"


def latest_token(cfg, slot):
    """槽位 SDK 配置里最近一次登录的 (token, lastLoginTime)；没有返回 None。

    USER_CACHE 是账号列表，取 lastLoginTime 最大的一条：每次成功登录/跑完
    挂机回刷槽位时，它对应的就是该槽位的账号。
    """
    path = _sdk_prefs_path(cfg, slot)
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r'name="USER_CACHE"[^>]*>([^<]*)<', raw)
    if not m:
        return None
    try:
        entries = json.loads(html.unescape(m.group(1)))
    except (ValueError, TypeError):
        return None
    best = None
    for e in entries if isinstance(entries, list) else []:
        tok = str((e or {}).get("token") or "")
        t = str((e or {}).get("lastLoginTime") or "")
        if tok and (best is None or t > best[1]):
            best = (tok, t)
    return best


def probe_token(token):
    """探测 token。返回 (status, detail)，status ∈ ok / expired / error。"""
    req = urllib.request.Request(
        PROBE_URL + urllib.parse.quote(token),
        headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        if str(data.get("status")) == "0":
            return "ok", "token 有效"
        return "expired", str(data.get("msg") or "服务端返回未登录")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            try:
                msg = str(json.loads(e.read().decode("utf-8", errors="replace")).get("msg") or "")
            except Exception:
                msg = ""
            return "expired", msg or "登录已过期"
        return "error", "HTTP %s" % e.code
    except Exception as exc:  # 超时 / DNS / 接口改版等：按 error 处理
        return "error", str(exc)


def _write_status(cfg, slot, status, detail):
    """写状态文件；error 不得覆盖已有 expired（网络抖动不能洗掉标红）。"""
    path = status_path(cfg, slot)
    if status == "error" and path.exists():
        old = read_status(cfg, slot)
        if old and old.get("status") == "expired":
            return old
    rec = {"status": status, "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "detail": detail}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)
            f.write("\n")
        tmp.replace(path)
    except OSError:
        return rec  # 写不进也要把结果带回给调用方
    _status_cache.pop(str(path), None)
    return rec


def read_status(cfg, slot):
    try:
        with open(status_path(cfg, slot), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) and data.get("status") else None
    except (OSError, ValueError):
        return None


def read_all(cfg):
    """{slot: 状态 dict}；只含有状态文件的槽位。带 mtime 缓存。"""
    out = {}
    for acc in cfg.get("accounts", []):
        slot = str(acc.get("slot") or "")
        if not slot:
            continue
        path = status_path(cfg, slot)
        try:
            key = (str(path), path.stat().st_mtime_ns)
        except OSError:
            continue
        hit = _status_cache.get(key[0])
        if hit and hit[0] == key[1]:
            out[slot] = hit[1]
            continue
        st = read_status(cfg, slot)
        _status_cache[key[0]] = (key[1], st)
        if st is not None:
            out[slot] = st
    return out


def check_slot(cfg, slot):
    """探测单个槽位并落盘。返回状态 dict；无 token 可查时返回 None。"""
    tok = latest_token(cfg, slot)
    if not tok:
        return None
    status, detail = probe_token(tok[0])
    if tok[1]:
        detail = "%s（上次登录 %s）" % (detail, tok[1])
    return _write_status(cfg, slot, status, detail)


def is_fresh_ok(st):
    """ok 且检查时间在保鲜期内。"""
    if not st or st.get("status") != "ok":
        return False
    try:
        dt = datetime.strptime(str(st.get("checked_at")), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False
    return (datetime.now() - dt).total_seconds() < FRESH_SEC


def check_all(cfg, force=False, only_slots=None):
    """遍历账号自检。返回摘要 {"checked", "ok", "expired", "error",
    "skipped", "expired_labels"}。

    force=False（启动补检）：ok 且保鲜期内跳过；force=True（每天 4 点 /
    重新捕获后）：全部重查。B 服与无 token 槽位自然跳过。
    """
    summary = {"checked": 0, "ok": 0, "expired": [], "error": [],
               "skipped": 0, "expired_labels": []}
    for acc in cfg.get("accounts", []):
        label = str(acc.get("label") or acc.get("id") or "?")
        slot = str(acc.get("slot") or "")
        if (acc.get("server") != "official" or not slot
                or not acc.get("enabled", True)
                or (only_slots is not None and slot not in only_slots)):
            summary["skipped"] += 1
            continue
        if not force and is_fresh_ok(read_status(cfg, slot)):
            summary["skipped"] += 1
            continue
        st = check_slot(cfg, slot)
        if st is None:
            summary["skipped"] += 1
            continue
        summary["checked"] += 1
        if st["status"] == "ok":
            summary["ok"] += 1
        elif st["status"] == "expired":
            summary["expired"].append(slot)
            summary["expired_labels"].append(label)
        else:
            summary["error"].append(label)
    return summary
