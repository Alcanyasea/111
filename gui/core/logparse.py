# -*- coding: utf-8 -*-
"""解析 master_log.txt（每行 `yyyy-MM-dd HH:mm:ss - message`，见 master.ps1 Log()）。

注意 PS 5.1 的坑（见探索记录）：`=== Run MAA [`、`MAA finished! ` 等行被截断，
账号名要从 `********** [n/3] Label **********` 横幅取，不能从 MAA 行取。
"""
import re
from datetime import datetime
from html import escape

TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) - (.*)$")
ACC_RE = re.compile(r"^  \[(OK|FAIL)\] (.+?) - ([\d.]+ min|skip)$")
TOTAL_RE = re.compile(r"^Total: ([\d.]+) min \| Passed: (\d+) \| Failed: (\d+)$")
BANNER_RE = re.compile(r"^\*{10} \[(\d+)/(\d+)\] (.+?) \*{10}$")

ACCOUNT_KEYS = {"Official 1": "official1", "Official 2": "official2", "Bilibili": "bilibili"}


def read_text(path, max_lines=3000):
    """读日志文件（容忍编码损坏），返回最近 max_lines 行的完整文本。"""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        return "".join(lines[-max_lines:])
    except OSError:
        return ""


def _matched_lines(text):
    """返回 [(时间戳, 消息)] 列表，忽略格式不符的行。"""
    out = []
    for line in text.splitlines():
        m = TS_RE.match(line)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def last_run(text):
    """解析最后一次运行的摘要。

    返回 {"start": ts, "accounts": [{"key","name","ok","dur"}], "total_min",
          "passed", "failed", "final": "success"/"fail", "emulator_closed": bool}
    日志里没有 SUMMARY 时返回 None。
    """
    lines = _matched_lines(text)
    if not lines:
        return None
    # 从后往前找 SUMMARY 横幅
    summary_i = None
    for i in range(len(lines) - 1, -1, -1):
        if lines[i][1].strip() == "SUMMARY":
            summary_i = i
            break
    if summary_i is None:
        return None

    accounts = []
    total_min = passed = failed = None
    final = None
    for _, msg in lines[summary_i + 1:]:
        am = ACC_RE.match(msg)
        if am:
            dur_str = am.group(3)
            skipped = dur_str == "skip"
            accounts.append({
                "key": ACCOUNT_KEYS.get(am.group(2)) or am.group(2),
                "name": am.group(2),
                "ok": am.group(1) == "OK",
                "dur": None if skipped else float(dur_str[:-4]),
                "skipped": skipped,
            })
            continue
        tm = TOTAL_RE.match(msg)
        if tm:
            total_min = float(tm.group(1))
            passed = int(tm.group(2))
            failed = int(tm.group(3))
            continue
        if "ALL ACCOUNTS COMPLETED" in msg:
            final = "success"
        elif "WARNING:" in msg and "FAILED" in msg:
            final = "fail"

    # 运行起始时间：往前找本次运行的 banner
    start = lines[summary_i][0]
    emulator_closed = False
    for i in range(summary_i - 1, -1, -1):
        ts, msg = lines[i]
        if "MAA Auto Farm" in msg:
            start = ts
            break
        if "Emulator closed" in msg:
            emulator_closed = True

    return {
        "start": start,
        "accounts": accounts,
        "total_min": total_min,
        "passed": passed,
        "failed": failed,
        "final": final,
        "emulator_closed": emulator_closed,
    }


def current_stage(text, now=None):
    """当前运行阶段：最后一次 banner 之后的状态。

    返回 {"account": 账号名, "stage": 阶段文本, "elapsed_min": 距 MAA 启动的分钟数}
    不在运行中（没有未完成的 banner 段）时返回 None。
    """
    lines = _matched_lines(text)
    if not lines:
        return None
    # 找最后一次 banner
    banner_i = None
    for i in range(len(lines) - 1, -1, -1):
        m = BANNER_RE.match(lines[i][1])
        if m:
            banner_i = i
            banner = m
            break
    if banner_i is None:
        return None
    # banner 之后是否已有 SUMMARY（说明本轮已结束）
    for _, msg in lines[banner_i + 1:]:
        if msg.strip() == "SUMMARY":
            return None

    section = lines[banner_i:]
    stage = "启动中"
    maa_start_ts = None
    for ts, msg in section:
        if "=== Run MAA" in msg or "Launching MAA" in msg:
            stage = "启动 MAA"
            maa_start_ts = ts
        elif "Waiting for MAA tasks" in msg:
            stage = "运行 MAA"
            if maa_start_ts is None:
                maa_start_ts = ts
        elif "Running:" in msg and ".ps1" in msg:
            stage = "切号中"
        elif "MAA finished!" in msg:
            stage = "MAA 完成"
        elif "MAA" in msg and "completed successfully" in msg:
            stage = "完成"

    elapsed_min = None
    if maa_start_ts:
        try:
            t0 = datetime.strptime(maa_start_ts, "%Y-%m-%d %H:%M:%S")
            t1 = now or datetime.now()
            elapsed_min = max(0.0, round((t1 - t0).total_seconds() / 60.0, 1))
        except ValueError:
            pass
    return {"account": banner.group(2), "stage": stage, "elapsed_min": elapsed_min}


def classify(msg):
    """日志消息着色分类：err / head / ok / 空。"""
    if re.search(r"ERROR|FAIL|WARNING", msg):
        return "err"
    stripped = msg.strip()
    if stripped == "SUMMARY" or msg.startswith("====") or msg.startswith("****") \
            or msg.startswith("----"):
        return "head"
    if re.search(r"\bOK\b|finished|SUCCESS|Done in", msg):
        return "ok"
    return ""


def to_html(text, max_lines=3000):
    """日志文本转 HTML（配色同 mockup .log-view），只取最近 max_lines 行。"""
    lines = text.splitlines()[-max_lines:]
    out = []
    for line in lines:
        m = TS_RE.match(line)
        if m:
            ts, msg = m.group(1), escape(m.group(2))
            cls = classify(m.group(2))
            if cls:
                msg = '<span class="%s">%s</span>' % (cls, msg)
            out.append('<span class="t">%s</span> - %s' % (ts, msg))
        else:
            out.append(escape(line))
    return "<br>".join(out)


def log_css():
    """日志视图的 CSS 片段。"""
    return (
        "span.t { color: %s; }"
        "span.ok { color: %s; }"
        "span.err { color: %s; }"
        "span.head { color: %s; font-weight: 600; }"
        % (theme.LOG_TS, theme.LOG_OK, theme.LOG_ERR, theme.LOG_HEAD)
    )


import theme  # noqa: E402  （末尾导入避免循环）
