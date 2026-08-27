# -*- coding: utf-8 -*-
"""数据清理：清除挂机过程产生的临时 / 调试数据。

分类（kind）：
  debug     scripts/debug/ 下的登录校验截屏与捕获日志（login_check_*.png、capture_*.log）
  tmp       挂机残留临时文件（switch_output.tmp、master.lock.tmp、maa_done.signal、陈旧 master.lock）
  leftover  测试遗留文件（scripts/_t1.xml / _t2.xml / _t3.bin，登录缓存 dump，含 token）
  backup    旧配置备份（D:/1/config.json.bak）
  log       master_log.txt 超限截断（默认 >1MB 保留末尾 512KB，日志页只读尾部不受影响）

安全规则：
  - 挂机运行中（runner.is_running()）必须拒绝清理，防止删掉活动的锁文件 / 正在追加的日志
  - 绝不触碰 scripts\accounts\（登录数据目录，清理会跑错号）
"""
from datetime import datetime
from pathlib import Path

from core import runner

SCRIPT_DIR = Path(r"D:\1\scripts")
DEBUG_DIR = SCRIPT_DIR / "debug"
LOG_FILE = SCRIPT_DIR / "master_log.txt"
LOCK_FILE = SCRIPT_DIR / "master.lock"

# 日志截断阈值：超过 max_size 时保留末尾 keep_size 字节
LOG_MAX_SIZE = 1_000_000
LOG_KEEP_SIZE = 512_000

# 测试遗留文件（调试时 dump 到 scripts\ 根目录的登录缓存，含 token，不再需要）
LEFTOVERS = ("_t1.xml", "_t2.xml", "_t3.bin")
# 挂机结束应自删的临时文件（异常中断会残留）
TMP_FILES = ("switch_output.tmp", "master.lock.tmp", "maa_done.signal")
# 旧配置备份
BACKUPS = (r"D:\1\config.json.bak",)

KIND_NAMES = {
    "debug": "调试截屏 / 捕获日志",
    "tmp": "挂机残留临时文件",
    "leftover": "测试遗留数据（含登录缓存）",
    "backup": "旧配置备份",
    "log": "master_log.txt 截断",
}


def format_size(n):
    if n < 1024:
        return "%d B" % n
    if n < 1024 * 1024:
        return "%.1f KB" % (n / 1024.0)
    return "%.1f MB" % (n / 1048576.0)


def _item(path, kind):
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError:
        return None
    return {"path": str(p), "kind": kind, "size": size}


def scan(cfg=None):
    """扫描可清理数据，返回 item 列表（不执行删除）。

    cfg 仅用于定位 script_dir（沿用 config.json 的 paths.script_dir）。
    """
    script_dir = SCRIPT_DIR
    if cfg:
        script_dir = Path(cfg.get("paths", {}).get("script_dir") or SCRIPT_DIR)
    debug_dir = script_dir / "debug"
    log_file = script_dir / "master_log.txt"

    items = []

    # debug 目录：整目录内容都可删（master.ps1 每次运行开头也会清 *.png）
    if debug_dir.is_dir():
        try:
            for f in sorted(debug_dir.iterdir()):
                if f.is_file():
                    it = _item(f, "debug")
                    if it:
                        items.append(it)
        except OSError:
            pass

    # 残留临时文件
    for name in TMP_FILES:
        it = _item(script_dir / name, "tmp")
        if it:
            items.append(it)
    # 陈旧的 master.lock（调用方保证当前未运行）
    it = _item(LOCK_FILE, "tmp")
    if it:
        items.append(it)

    # 测试遗留文件
    for name in LEFTOVERS:
        it = _item(script_dir / name, "leftover")
        if it:
            items.append(it)

    # 旧配置备份
    for p in BACKUPS:
        it = _item(p, "backup")
        if it:
            items.append(it)

    # 日志截断：size 记多余部分（估算，实际截断后微差可忽略）
    try:
        size = log_file.stat().st_size
    except OSError:
        size = 0
    if size > LOG_MAX_SIZE:
        items.append({"path": str(log_file), "kind": "log", "size": size - LOG_KEEP_SIZE})

    return items


def _trim_log(path):
    """master_log.txt 超限截断：保留末尾 LOG_KEEP_SIZE 字节并对齐行首，
    开头补一行清理说明（格式同 master.ps1 的 Log()，日志页可正常显示）。
    返回实际释放字节数。"""
    try:
        size = Path(path).stat().st_size
    except OSError:
        return 0
    if size <= LOG_MAX_SIZE:
        return 0
    try:
        with open(path, "r+b") as f:
            f.seek(size - LOG_KEEP_SIZE)
            tail = f.read()
            nl = tail.find(b"\n")
            if nl >= 0:
                tail = tail[nl + 1:]
            note = ("%s - [清理] 旧日志已截断（原 %d KB），仅保留最近部分\n"
                    % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), size // 1024)
                    ).encode("utf-8")
            f.seek(0)
            f.truncate()
            f.write(note + tail)
        return size - len(note) - len(tail)
    except OSError:
        return 0


def perform(items):
    """执行清理，返回 (释放字节数, 成功项数, 失败项数)。"""
    freed = 0
    ok = 0
    fail = 0
    for it in items:
        if it["kind"] == "log":
            n = _trim_log(it["path"])
        else:
            n = it["size"]
            try:
                Path(it["path"]).unlink()
            except OSError:
                n = 0
        if n > 0:
            freed += n
            ok += 1
        elif it["kind"] != "log":
            fail += 1
    return freed, ok, fail


def is_due(cfg):
    """自动清理到期判断：距上次清理 >= interval_days；无记录视为到期。"""
    c = cfg.get("cleanup") or {}
    last = c.get("last_run", "")
    if not last:
        return True
    try:
        last_dt = datetime.strptime(last, "%Y-%m-%d %H:%M")
    except ValueError:
        return True
    days = int(c.get("interval_days", 7))
    return (datetime.now() - last_dt).total_seconds() >= days * 86400


def last_run_text(cfg):
    """「上次清理」提示文本。"""
    last = (cfg.get("cleanup") or {}).get("last_run", "")
    if not last:
        return "尚未清理过"
    return "上次清理：%s" % last
