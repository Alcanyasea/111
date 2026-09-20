# -*- coding: utf-8 -*-
"""计划任务管理：MAA_明日方舟自动挂机（单个任务，内含 config.schedule.times 全部每日触发）。

原任务由管理员 schtasks 创建；查询无需管理员，修改（Set/Register/Enable/Disable）
需要管理员权限——失败时返回可读错误，由界面弹 InfoBar 提示。
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path

from core.util import decode_console, run as _run

TASK_NAME = "MAA_明日方舟自动挂机"

# 计划任务必须用 pwsh 绝对路径：任务计划程序不按用户 PATH 解析裸 pwsh.exe，
# 商店版（MSIX）的执行别名直接 0x80070002 起不来；MSI 版 Program Files 优先。
_PWSH_CANDIDATES = (
    r"C:\Program Files\PowerShell\7\pwsh.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe"),
)

# master.ps1 路径按本文件位置推导（gui\core\scheduler.py → 项目根\scripts），
# 不再写死 D:\1：项目挪目录/装到别的盘后计划任务仍指向正确脚本
MASTER_PS1 = str(Path(__file__).resolve().parents[2] / "scripts" / "master.ps1")


def pwsh_exe():
    for p in _PWSH_CANDIDATES:
        if os.path.isfile(p):
            return p
    return "pwsh.exe"

_PS_DATE_RE = re.compile(r"/Date\((\d+)\)/")
_HHMM_RE = re.compile(r"T(\d{2}:\d{2})")

# 查询超时：主窗口关闭时要 wait 轮询线程，查询必须等得起（应用 40s 的
# 是 Register/Set-ScheduledTask，只有 apply 需要）
QUERY_TIMEOUT = 12
APPLY_TIMEOUT = 40


def _ps(script, timeout=QUERY_TIMEOUT):
    # 用 pwsh 绝对路径：GUI 由 pythonw 拉起时 PATH 不保证含 pwsh
    return _run([pwsh_exe(), "-NoProfile", "-Command", script], timeout=timeout)


def _parse_date(value):
    """计划任务时间解析：5.1 是 /Date(毫秒)/，pwsh 7 是 ISO 8601 字符串。"""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000)
    if isinstance(value, str):
        m = _PS_DATE_RE.search(value)
        if m:
            return datetime.fromtimestamp(int(m.group(1)) / 1000)
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
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
        data = json.loads(decode_console(out))
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
    if isinstance(sched, dict) and isinstance(sched.get("times"), list):
        entries = sched["times"]
    else:
        # 旧版 {morning: {...}, evening: {...}} 兜底
        entries = [v for v in sched.values() if isinstance(v, dict)]
    times = [e["time"] for e in entries if isinstance(e, dict) and e.get("enabled")]
    times = [t for t in times if re.match(r"^([01]\d|2[0-3]):([0-5]\d)$", t)]
    enabled = bool(times)

    times_ps = "@(" + ",".join("'%s'" % t for t in times) + ")"
    en_ps = "$true" if enabled else "$false"
    script = (
        "$ErrorActionPreference='Stop';"
        "$name = '%s';"
        "$action = New-ScheduledTaskAction -Execute '%s'"
        # -File 路径加双引号：项目目录含空格时任务计划程序照样能启动
        "  -Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File \"%s\"';"
        "$triggers = @(%s | ForEach-Object {"
        "  New-ScheduledTaskTrigger -Daily -At ([datetime]::Parse($_)) });"
        "$t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue;"
        "if ($t) { Set-ScheduledTask -TaskName $name -Action $action -Trigger $triggers }"
        "else { Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers };"
        "if (%s) { Enable-ScheduledTask -TaskName $name }"
        "else { Disable-ScheduledTask -TaskName $name };"
        "'APPLIED'"
    ) % (TASK_NAME, pwsh_exe(), MASTER_PS1, times_ps, en_ps)
    code, out, err = _ps(script, timeout=APPLY_TIMEOUT)
    err_text = decode_console(err).strip()
    if code != 0 or "APPLIED" not in decode_console(out):
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
