# -*- coding: utf-8 -*-
"""干员资料导出：驱动 scripts/export_operbox.py 逐账号跑 MAA 干员识别。

与挂机互斥由两层保证：
- 脚本自己检查 master.lock（供命令行单独运行时也生效）；
- GUI 侧启动前检查 runner.is_running()，挂机中按钮直接拒绝。
导出子进程独立于控制台存活：关窗后导出继续，结果仍写入 exports\\。
"""
import os
import subprocess
import sys

from PySide6.QtCore import QThread, Signal

from core.util import CREATE_NO_WINDOW, decode_console


class ExportWorker(QThread):
    """后台跑 export_operbox.py 编排模式：逐行转发输出，结束发 done(ok, summary)。"""

    line = Signal(str)
    done = Signal(bool, str)

    def __init__(self, script_path, out_dir, parent=None):
        super().__init__(parent)
        self.script_path = str(script_path)
        self.out_dir = str(out_dir)
        self.proc = None

    def run(self):
        cmd = [sys.executable, self.script_path, "--out-dir", self.out_dir]
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        try:
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW, env=env,
                cwd=os.path.dirname(self.script_path))
        except OSError as e:
            self.line.emit("!! 启动导出脚本失败：%s" % e)
            self.done.emit(False, "启动导出脚本失败：%s" % e)
            return
        try:
            for raw in iter(self.proc.stdout.readline, b""):
                self.line.emit(decode_console(raw).rstrip())
        finally:
            try:
                self.proc.stdout.close()
            except OSError:
                pass
            code = self.proc.wait()
            ok = code == 0
            self.done.emit(ok, "导出完成" if ok
                           else "导出未全部成功（退出码 %d），详见日志" % code)

    def kill(self):
        """中止导出：杀掉脚本进程树（含其派生的 PowerShell/MAA 子进程）。"""
        if self.proc is not None and self.proc.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                capture_output=True, creationflags=CREATE_NO_WINDOW)
