# -*- coding: utf-8 -*-
"""master.lock 解析与 PID 复用防护测试（gui/core/runner.py + proc.py）。

全部用临时锁文件，绝不触碰真实的 scripts\\master.lock。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gui"))

from core import proc, runner  # noqa: E402


def _pwsh():
    from core.scheduler import pwsh_exe
    return pwsh_exe()


def _win_only(cls):
    return unittest.skipUnless(sys.platform == "win32", "Windows 专属")(cls)


@_win_only
class ProcessStartTicksTest(unittest.TestCase):
    def test_matches_powershell_starttime(self):
        """跨语言核对：Python 读到的启动时间（.NET UTC Ticks 口径）必须与
        PowerShell Get-Process 的 StartTime.Ticks 一致——验证 FILETIME→
        .NET 纪元换算常数正确（锁比对两端各用一种语言实现）。"""
        pwsh = _pwsh()
        if shutil.which(pwsh) is None and not Path(pwsh).is_file():
            self.skipTest("pwsh 不可用")
        p = subprocess.Popen(
            [pwsh, "-NoProfile", "-Command",
             "$t = (Get-Process -Id $PID).StartTime.ToUniversalTime().Ticks;"
             "Start-Sleep 6; $t"],
            stdout=subprocess.PIPE, text=True)
        try:
            py_ticks = proc.process_start_ticks(p.pid)
            out, _ = p.communicate(timeout=60)
            self.assertIsNotNone(py_ticks)
            self.assertEqual(int(out.strip()), py_ticks)
        finally:
            if p.poll() is None:
                p.kill()

    def test_dead_pid_returns_none(self):
        self.assertIsNone(proc.process_start_ticks(999999))


@_win_only
class LockDetailsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old_lock = runner.LOCK_FILE
        runner.LOCK_FILE = Path(self.tmp) / "master.lock"

    def tearDown(self):
        runner.LOCK_FILE = self._old_lock
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, content):
        runner.LOCK_FILE.write_text(content, encoding="ascii")

    def test_missing_lock(self):
        self.assertEqual(runner.lock_details(), (None, None))
        self.assertIsNone(runner.lock_pid())
        self.assertFalse(runner.is_running())
        self.assertIsNone(runner.stale_lock())

    def test_corrupt_lock(self):
        self._write("not-a-pid")
        self.assertEqual(runner.lock_details(), (None, None))
        self.assertFalse(runner.is_running())

    def test_new_format_ticks_mismatch_is_stale(self):
        """PID 复用核心场景：同 PID 同进程名但启动时间对不上 → 锁判定为陈旧。"""
        p = subprocess.Popen([_pwsh(), "-NoProfile", "-Command", "Start-Sleep 8"])
        try:
            ticks = proc.process_start_ticks(p.pid)
            self.assertIsNotNone(ticks)
            self._write("%d|%d" % (p.pid, ticks))
            self.assertTrue(runner.is_running())          # 名称+启动时间都匹配
            self.assertIsNone(runner.stale_lock())
            self._write("%d|%d" % (p.pid, ticks + 1))
            self.assertFalse(runner.is_running())         # PID 复用 → 按陈旧处理
            self.assertEqual(runner.stale_lock(), p.pid)
            self.assertEqual(runner.clear_stale_lock(), p.pid)
            self.assertFalse(runner.LOCK_FILE.exists())   # 陈旧锁被清理
        finally:
            if p.poll() is None:
                p.kill()

    def test_old_format_name_only(self):
        """旧版锁（纯 PID 无启动时间）：退回仅进程名判断，口径不变。"""
        p = subprocess.Popen([_pwsh(), "-NoProfile", "-Command", "Start-Sleep 8"])
        try:
            self._write(str(p.pid))
            self.assertTrue(runner.is_running())
            self.assertIsNone(runner.stale_lock())
        finally:
            if p.poll() is None:
                p.kill()

    def test_non_pwsh_pid_is_stale(self):
        self._write("%d|123" % os.getpid())   # 本测试进程是 python，不是 pwsh
        self.assertFalse(runner.is_running())
        self.assertEqual(runner.stale_lock(), os.getpid())


if __name__ == "__main__":
    unittest.main()
