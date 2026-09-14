# -*- coding: utf-8 -*-
"""通知推送（GUI 侧）：调 plugins/notify/notify.py 发送测试消息。

发送逻辑只有一份（插件里）：master.ps1 走 --config 读已保存的配置，
控制台「发送测试」走 --provider/--key 直接传界面当前值（不必先保存）。
HTTP 最长十几秒，调用方（运行设置页）须放在工作线程里。
"""
import subprocess
import sys
from pathlib import Path

from core.util import CREATE_NO_WINDOW, decode_console

PLUGIN = Path(r"D:\1\plugins\notify\notify.py")

# 推送渠道：下拉框顺序即此元组顺序（settings 页共用）
PROVIDERS = ("serverchan", "pushplus", "wecom")


def send_test(provider, key):
    """发一条测试消息，返回 (ok, 插件输出摘要)。"""
    if not PLUGIN.exists():
        return False, "未找到通知插件：%s" % PLUGIN
    cmd = [
        sys.executable, str(PLUGIN), "send",
        "--provider", provider, "--key", key,
        "--title", "MAA 控制台测试推送",
        "--body", "收到这条消息说明推送渠道和密钥配置正确。",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=30,
                           creationflags=CREATE_NO_WINDOW)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, "调用通知插件失败：%s" % exc
    out = decode_console(r.stdout).strip()
    err = decode_console(r.stderr).strip()
    text = out or err or ("退出码 %d" % r.returncode)
    # 摘要只留最后一行（插件的单行结论），traceback 等长输出进不了提示条
    summary = text.splitlines()[-1] if text else ""
    return r.returncode == 0, summary
