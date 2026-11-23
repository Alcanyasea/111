# -*- coding: utf-8 -*-
"""计划任务管理：两个 Windows 计划任务，由控制台统一创建/同步。

- MAA_明日方舟自动挂机：config.schedule.times 全部每日触发，直跑 master.ps1
  （按班次计划挂机，成败都按班次关机开关收尾）；
- MAA_基建收菜：config.schedule.collect_times 全部每日触发，跑
  master.ps1 -InfrastCollect（定时收菜只收产物/订单 + 宿舍自动换休，跑全部
  已启用账号）。收菜结束后是否关机不由任务动作决定——一个任务只有一份动作、
  无法按触发器携带参数，与挂机班次同款：master.ps1 启动时按「最近一个已到的
  启用收菜时间点」的 shutdown 字段判定（GUI 手动收菜带 -NoShutdown 永不关机）。

任务由本模块注册（Register/Set-ScheduledTask 需要管理员权限，失败时返回可读
错误，由界面弹 InfoBar 提示）；查询无需管理员。
"""
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from core.util import decode_console, pwsh_exe, run as _run

TASK_NAME = "MAA_明日方舟自动挂机"
COLLECT_TASK_NAME = "MAA_基建收菜"

# master.ps1 路径按本文件位置推导（gui\core\scheduler.py → 项目根\scripts），
# 不再写死 D:\1：项目挪目录/装到别的盘后计划任务仍指向正确脚本
MASTER_PS1 = str(Path(__file__).resolve().parents[2] / "scripts" / "master.ps1")
SKIP_ONCE_PATH = Path(MASTER_PS1).with_name("skip_once.json")

# 计划任务从 pythonw 进入，再由 util.headless_main 以 CREATE_NO_WINDOW 拉起
# pwsh；单独使用 -WindowStyle Hidden 在 Windows Terminal 默认终端下仍会弹窗。
# GUI 正式入口始终使用同目录的 pythonw（开发 venv / 安装包便携运行时）。
PYTHONW_EXE = str(Path(sys.executable).with_name("pythonw.exe"))
HEADLESS_LAUNCHER = str(Path(__file__).resolve().with_name("util.py"))


_PS_DATE_RE = re.compile(r"/Date\((\d+)\)/")
_HHMM_RE = re.compile(r"T(\d{2}:\d{2})")

# 查询超时：主窗口关闭时要 wait 轮询线程，查询必须等得起（应用 40s 的
# 是 Register/Set-ScheduledTask，只有 apply 需要）
QUERY_TIMEOUT = 12
APPLY_TIMEOUT = 40


def pending_skip(now=None):
    """读取一次跳过的具体班次。到下一个班次自动失效，不修改用户配置。"""
    now = now or datetime.now()
    try:
        state = json.loads(SKIP_ONCE_PATH.read_text(encoding="utf-8"))
        target = datetime.strptime(state["skip_at"], "%Y-%m-%dT%H:%M:%S")
        resume = datetime.strptime(state["resume_at"], "%Y-%m-%dT%H:%M:%S")
        if target < resume <= target + timedelta(days=1) and now < resume:
            return target
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        pass
    return None


def skip_next_run(info, now=None):
    """绑定已注册任务的下一班；过期查询拒绝写入，避免跳错日期。

    状态用原子替换保存，重启控制台仍有效。旧文件先备份；主脚本只读，
    用有效期防止重复触发漏跑或残留标记误跳次日班次。
    """
    now = now or datetime.now()
    target = info.get("next_run")
    times = _enabled_times([{"time": t} for t in info.get("times", [])])
    if (not info.get("exists") or not info.get("enabled")
            or not isinstance(target, datetime) or target <= now
            or target.strftime("%H:%M") not in times):
        raise ValueError("没有可跳过的下次班次，请等待计划任务状态刷新后重试")
    following = []
    for value in times:
        hour, minute = map(int, value.split(":"))
        candidate = target.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= target:
            candidate += timedelta(days=1)
        following.append(candidate)
    state = {"skip_at": target.strftime("%Y-%m-%dT%H:%M:%S"),
             "resume_at": min(following).strftime("%Y-%m-%dT%H:%M:%S")}
    if SKIP_ONCE_PATH.exists():
        shutil.copyfile(SKIP_ONCE_PATH, SKIP_ONCE_PATH.with_suffix(".json.bak"))
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False,
                                         dir=SKIP_ONCE_PATH.parent,
                                         prefix="skip_once.", suffix=".tmp") as stream:
            tmp_path = Path(stream.name)
            json.dump(state, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_path, SKIP_ONCE_PATH)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
    return target


def cancel_skip():
    """撤销前保留状态备份，绝不改动班次配置或计划任务。"""
    if SKIP_ONCE_PATH.exists():
        shutil.copyfile(SKIP_ONCE_PATH, SKIP_ONCE_PATH.with_suffix(".json.bak"))
        SKIP_ONCE_PATH.unlink()


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
            parsed = datetime.fromisoformat(value)
            # pwsh 可能返回带偏移的日期；计划任务和 GUI 均使用本地墙钟时间。
            return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed
        except ValueError:
            pass
    return None


def query(name=TASK_NAME):
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
    ) % name
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


def _enabled_times(entries):
    """entries（schedule 时间项列表）→ 校验过的启用时间列表（HH:MM）。

    enabled 缺失视为启用，与 config.py / 插件 / master.ps1 同口径。
    """
    times = [e.get("time") for e in entries
             if isinstance(e, dict) and e.get("enabled", True)]
    return [t for t in times
            if isinstance(t, str) and re.match(r"^([01][0-9]|2[0-3]):([0-5][0-9])$", t)]


def _apply_task(name, times, master_args):
    """注册/更新单个计划任务：pythonw 无窗口启动 pwsh + master.ps1。

    times 为已校验的 HH:MM 列表；全部被禁用/无时间时不创建任务，任务已存在
    则只禁用（触发保留但灰置，重新启用时由下一次 apply 按最新配置重写）——
    避免注册无触发任务这种任务计划程序不保证接受的形态。
    """
    times = [t for t in times if re.match(r"^([01][0-9]|2[0-3]):([0-5][0-9])$", t)]
    enabled = bool(times)

    extra = (" " + " ".join(master_args)) if master_args else ""
    times_ps = "@(" + ",".join("'%s'" % t for t in times) + ")"
    en_ps = "$true" if enabled else "$false"
    script = (
        "$ErrorActionPreference='Stop';"
        "$name = '%s';"
        "$action = New-ScheduledTaskAction -Execute '%s'"
        # pythonw 执行 util.py，再由后者创建无窗口 pwsh；所有路径均加双引号。
        "  -Argument '\"%s\" \"%s\" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"%s\"%s';"
        "$triggers = @(%s | ForEach-Object {"
        "  New-ScheduledTaskTrigger -Daily -At ([datetime]::Parse($_)) });"
        # Do not stop a run when AC power briefly disconnects.  The 16:00
        # failure was exactly this setting: MAA had finished and written its
        # signal, then Task Scheduler terminated the master during cleanup.
        "$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
        "  -DontStopIfGoingOnBatteries -Hidden -StartWhenAvailable "
        "  -MultipleInstances IgnoreNew -ExecutionTimeLimit ([timespan]::Zero);"
        "$t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue;"
        "if (%s) {"
        "  if ($t) { Set-ScheduledTask -TaskName $name -Action $action -Trigger $triggers -Settings $settings }"
        "  else { Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers -Settings $settings };"
        "  Enable-ScheduledTask -TaskName $name"
        "} elseif ($t) { Disable-ScheduledTask -TaskName $name };"
        "'APPLIED'"
    ) % (name, PYTHONW_EXE, HEADLESS_LAUNCHER, pwsh_exe(), MASTER_PS1,
         extra, times_ps, en_ps)
    code, out, err = _ps(script, timeout=APPLY_TIMEOUT)
    err_text = decode_console(err).strip()
    if code != 0 or "APPLIED" not in decode_console(out):
        if re.search(r"denied|拒绝访问|权限", err_text, re.IGNORECASE):
            return False, "更新计划任务需要管理员权限：请以管理员身份运行本程序"
        return False, err_text[-300:] or "计划任务更新失败"
    return True, ""


def apply(cfg):
    """按 config 的 schedule 更新挂机任务触发与启停。返回 (ok, message)。"""
    sched = cfg.get("schedule", {})
    if isinstance(sched, dict) and isinstance(sched.get("times"), list):
        entries = sched["times"]
    else:
        # 旧版 {morning: {...}, evening: {...}} 兜底
        entries = [v for v in sched.values() if isinstance(v, dict)]
    times = _enabled_times(entries)
    return _apply_task(TASK_NAME, times, ())


def query_collect():
    """查询收菜计划任务状态（返回结构与 query 一致）。"""
    return query(COLLECT_TASK_NAME)


def apply_collect(cfg):
    """按 config 的 schedule.collect_times 更新收菜任务触发与启停。

    动作只带 -InfrastCollect（不带 -NoShutdown）：任务计划程序无法按触发器
    区分动作参数，「是否关机」由 master.ps1 启动时按最近一个已到的启用收菜
    时间点的 shutdown 字段判定（与挂机班次同一套机制）；GUI 手动收菜走
    runner.start_collect 的 -NoShutdown，永不关机。
    """
    sched = cfg.get("schedule", {})
    entries = sched.get("collect_times") if isinstance(sched, dict) else None
    if not isinstance(entries, list):
        entries = []
    return _apply_task(COLLECT_TASK_NAME, _enabled_times(entries),
                       ("-InfrastCollect",))


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
