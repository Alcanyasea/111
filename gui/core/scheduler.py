# -*- coding: utf-8 -*-
"""计划任务管理：MAA_明日方舟自动挂机（单个任务，内含 04:00 / 16:00 两个每日触发）。

原任务由管理员 schtasks 创建；查询无需管理员，修改（Set/Register/Enable/Disable）
需要管理员权限——失败时返回可读错误，由界面弹 InfoBar 提示。
"""
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000

TASK_NAME = "MAA_明日方舟自动挂机"
RUN_CMD = r"powershell.exe -ExecutionPolicy Bypass -WindowStyle Minimized -File D:\1\scripts\master.ps1"

_PS_DATE_RE = re.compile(r"/Date\((\d+)\)/")
_HHMM_RE = re.compile(r"T(\d{2}:\d{2})")


def _ps(script, timeout=40):
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW,
        )
        return r.returncode, r.stdout, r.stderr
    except (subprocess.TimeoutExpired, OSError):
        return -1, b"", b""


def _dec(b):
    """PS 5.1 重定向输出可能是 GBK 或 UTF-8，都试一下。"""
    for enc in ("utf-8", "gbk"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode(errors="replace")


def _parse_date(value):
    """PS 5.1 ConvertTo-Json 的日期格式：/Date(1756224000000)/，也可能是 ISO。"""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000)
    if isinstance(value, str):
        m = _PS_DATE_RE.search(value)
        if m:
            return datetime.fromtimestamp(int(m.group(1)) / 1000)
    return None


def query():
    """查询任务状态。返回:
    {"exists": bool, "enabled": bool, "times": ["HH:MM", ...],
     "next_run": datetime|None, "last_run": datetime|None, "last_result": int|None}
    """
    empty = {"exists": False, "enabled": False, "times": [],
             "next_run": None, "last_run": None, "last_result": None}
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$t = Get-ScheduledTask -TaskName '%s';"
        'if (-not $t) { \'[{"exists":false}]\' } else {'
        "  $i = Get-ScheduledTaskInfo -TaskName $t.TaskName;"
        "  [pscustomobject]@{ exists=$true; enabled=($t.State -ne 'Disabled');"
        "    times=@($t.Triggers | Where-Object { $_.Enabled } |"
        "      ForEach-Object { $_.StartBoundary });"
        "    next=$i.NextRunTime; last=$i.LastRunTime; lastResult=$i.LastTaskResult"
        "  } | ConvertTo-Json -Compress }"
    ) % TASK_NAME
    code, out, err = _ps(script)
    if code != 0:
        return empty
    try:
        data = json.loads(_dec(out))
        if isinstance(data, list):
            data = data[0]
    except (json.JSONDecodeError, IndexError, TypeError):
        return empty
    times = []
    for t in data.get("times") or []:
        m = _HHMM_RE.search(str(t))
        if m:
            times.append(m.group(1))
    result = {
        "exists": bool(data.get("exists")),
        "enabled": bool(data.get("enabled")),
        "times": sorted(set(times)),
        "next_run": _parse_date(data.get("next")),
        "last_run": _parse_date(data.get("last")),
        "last_result": data.get("lastResult"),
    }
    if not result["exists"]:
        return empty
    return result


def apply(cfg):
    """按 config 的 schedule 更新任务触发与启停。返回 (ok, message)。"""
    sched = cfg.get("schedule", {})
    times = [v["time"] for v in sched.values() if v.get("enabled")]
    times = [t for t in times if re.match(r"^([01]\d|2[0-3]):([0-5]\d)$", t)]
    enabled = bool(times)

    times_ps = "@(" + ",".join("'%s'" % t for t in times) + ")"
    en_ps = "$true" if enabled else "$false"
    script = (
        "$ErrorActionPreference='Stop';"
        "$name = '%s';"
        "$action = New-ScheduledTaskAction -Execute 'powershell.exe'"
        "  -Argument '-ExecutionPolicy Bypass -WindowStyle Minimized -File D:\\1\\scripts\\master.ps1';"
        "$triggers = @(%s | ForEach-Object {"
        "  New-ScheduledTaskTrigger -Daily -At ([datetime]::Parse($_)) });"
        "$t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue;"
        "if ($t) { Set-ScheduledTask -TaskName $name -Action $action -Trigger $triggers }"
        "else { Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers };"
        "if (%s) { Enable-ScheduledTask -TaskName $name }"
        "else { Disable-ScheduledTask -TaskName $name };"
        "'APPLIED'"
    ) % (TASK_NAME, times_ps, en_ps)
    code, out, err = _ps(script)
    err_text = _dec(err).strip()
    if code != 0 or "APPLIED" not in _dec(out):
        if re.search(r"denied|拒绝访问|权限", err_text, re.IGNORECASE):
            return False, "更新计划任务需要管理员权限：请以管理员身份运行本程序"
        return False, err_text[-300:] or "计划任务更新失败"
    return True, ""


def next_run_text(info):
    """「下次运行」的人类可读文本。"""
    if not info.get("exists"):
        return "未创建"
    if not info.get("enabled"):
        return "已禁用"
    nxt = info.get("next_run")
    if not nxt:
        return "暂无"
    today = datetime.now().date()
    if nxt.date() == today:
        return "今天 %s" % nxt.strftime("%H:%M")
    if (nxt.date() - today).days == 1:
        return "明天 %s" % nxt.strftime("%H:%M")
    return nxt.strftime("%m-%d %H:%M")
