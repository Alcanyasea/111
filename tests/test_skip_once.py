"""一次跳过：仅使用临时状态文件，PowerShell 入口的正常路径用哨兵隔离。"""
import json
import copy
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.test_support import appconfig
from tests.ps_harness import PWSH, ROOT, run_ps
from core import scheduler


def task_info(target=datetime(2026, 11, 8, 16)):
    return dict(exists=True, enabled=True, times=["04:00", "16:00"],
                next_run=target)


class SkipOnceTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / "skip_once.json"
        p = patch.object(scheduler, "SKIP_ONCE_PATH", self.path, create=True)
        p.start()
        self.addCleanup(p.stop)
        self.now = datetime(2026, 11, 8, 12)

    def test_persist_specific_shift_across_reload_and_expire(self):
        scheduler.skip_next_run(task_info(), now=self.now)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data, {"skip_at": "2026-11-08T16:00:00",
                                "resume_at": "2026-11-09T04:00:00"})
        self.assertEqual(scheduler.pending_skip(now=self.now), datetime(2026, 11, 8, 16))
        self.assertIsNone(scheduler.pending_skip(now=datetime(2026, 11, 9, 4)))

    def test_cancel_preserves_backup_and_restores_normal_schedule(self):
        scheduler.skip_next_run(task_info(), now=self.now)
        scheduler.cancel_skip()
        self.assertIsNone(scheduler.pending_skip(now=self.now))
        self.assertTrue(self.path.with_suffix(".json.bak").exists())

    def test_disabled_missing_or_stale_task_cannot_be_skipped(self):
        for info in ({}, task_info(self.now), dict(task_info(), enabled=False)):
            with self.subTest(info=info), self.assertRaises(ValueError):
                scheduler.skip_next_run(info, now=self.now)
        self.assertFalse(self.path.exists())

    def test_single_daily_shift_resumes_next_day(self):
        info = dict(task_info(datetime(2026, 11, 9, 4)), times=["04:00"])
        scheduler.skip_next_run(info, now=self.now)
        self.assertEqual(json.loads(self.path.read_text())["resume_at"],
                         "2026-11-10T04:00:00")

    def test_task_date_with_timezone_can_be_used_for_skip(self):
        target = datetime(2026, 11, 8, 16)
        parsed = scheduler._parse_date(target.astimezone().isoformat())
        self.assertEqual(parsed, target)
        scheduler.skip_next_run(task_info(parsed), now=self.now)
        self.assertEqual(scheduler.pending_skip(now=self.now), target)

    def test_failed_atomic_write_keeps_previous_state(self):
        scheduler.skip_next_run(task_info(), now=self.now)
        original = self.path.read_bytes()
        with patch("os.replace", side_effect=PermissionError("busy")):
            with self.assertRaises(OSError):
                scheduler.skip_next_run(task_info(datetime(2026, 11, 9, 4)), now=self.now)
        self.assertEqual(self.path.read_bytes(), original)

    def test_corrupt_state_is_ignored(self):
        for data in ("not json", "[]", '{"skip_at": "bad"}'):
            self.path.write_text(data)
            self.assertIsNone(scheduler.pending_skip(now=self.now))

    @unittest.skipUnless(PWSH, "PowerShell 7 required")
    def test_powershell_skips_only_target_window(self):
        self.path.write_text(json.dumps({"skip_at": "2026-11-08T16:00:00",
                                        "resume_at": "2026-11-09T04:00:00"}))
        result = run_ps(self, self.root, ["scripts/master.ps1"], r'''
$path = Join-Path $root 'skip_once.json'
@('2026-11-08T15:59:59', '2026-11-08T16:00:00',
  '2026-11-08T16:02:00', '2026-11-09T04:00:00', '2026-11-09T16:00:00') |
    ForEach-Object { Test-SkipScheduledRun -StatePath $path -Now ([datetime]$_) } |
    ConvertTo-Json -Compress
''')
        self.assertEqual(result, [False, True, True, False, False])

    @unittest.skipUnless(PWSH, "PowerShell 7 required")
    def test_entry_exits_before_side_effects_and_manual_collect_bypass(self):
        shutil.copyfile(ROOT / "scripts" / "master.ps1", self.root / "master.ps1")
        # 正常入口一进入库就退出；绝不加载真实配置、日志、模拟器或 MAA。
        (self.root / "master_lib.ps1").write_text(
            "[Console]::WriteLine('NORMAL'); [Environment]::Exit(0)")
        now = datetime.now()
        self.path.write_text(json.dumps({
            "skip_at": (now - timedelta(minutes=1)).isoformat(timespec="seconds"),
            "resume_at": (now + timedelta(hours=1)).isoformat(timespec="seconds")}))
        for args, normal in (([], False), (["-NoShutdown"], True),
                             (["-InfrastCollect"], True), (["-SkipMAA"], True),
                             (["-SwitchTo", "fake"], True)):
            with self.subTest(args=args):
                result = subprocess.run(
                    [PWSH, "-NoProfile", "-File", str(self.root / "master.ps1"), *args],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual("NORMAL" in result.stdout, normal, result.stdout)
        self.assertTrue(self.path.exists(), "重复触发也应保持跳过该时间段")


class SkipOnceUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from pages.dashboard import ScheduleCard
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "skip_once.json"
        for p in (patch.object(scheduler, "SKIP_ONCE_PATH", self.path, create=True),
                  patch.object(appconfig, "save"),
                  patch("pages.dashboard.InfoBar.success"),
                  patch("pages.dashboard.InfoBar.warning")):
            p.start()
            self.addCleanup(p.stop)
        self.cfg = {"schedule": {"times": [{"time": "04:00"}, {"time": "16:00"}]}}
        self.card = ScheduleCard(self.cfg)
        self.addCleanup(self.card.deleteLater)
        self.target = (datetime.now() + timedelta(days=1)).replace(hour=4, minute=0, second=0, microsecond=0)
        self.info = task_info(self.target)

    def test_button_sets_visible_persistent_state_then_cancels(self):
        self.card.refresh_scheduler(self.info)
        self.card.skip_btn.click()
        self.assertEqual(scheduler.pending_skip(), self.target)
        self.assertIn(self.target.strftime("%m-%d %H:%M"), self.card.skip_val.text())
        self.assertEqual(self.card.skip_btn.text(), "取消跳过")
        self.card.skip_btn.click()
        self.assertIsNone(scheduler.pending_skip())
        self.assertEqual(self.card.skip_btn.text(), "跳过一次运行")

    def test_restart_restores_state_and_missing_task_disables_button(self):
        scheduler.skip_next_run(self.info)
        self.card.refresh_scheduler(self.info)
        self.assertEqual(self.card.skip_btn.text(), "取消跳过")
        scheduler.cancel_skip()
        self.card.refresh_scheduler({"exists": False})
        self.assertFalse(self.card.skip_btn.isEnabled())

    def test_write_error_does_not_report_success(self):
        self.card.refresh_scheduler(self.info)
        with patch.object(scheduler, "skip_next_run", side_effect=OSError("disk full")):
            self.card.skip_btn.click()
        self.assertIsNone(scheduler.pending_skip())
        self.assertEqual(self.card.skip_btn.text(), "跳过一次运行")

    def test_changing_triggers_cancels_old_skip_window(self):
        for operation in ("add", "delete", "time", "enabled"):
            with self.subTest(operation=operation):
                self.cfg["schedule"]["times"] = [{"time": "04:00"}, {"time": "16:00"}]
                self.card.refresh_from_cfg()
                scheduler.skip_next_run(self.info)
                self.card.refresh_scheduler(self.info)
                entry = self.card._entries()[1]
                with patch.object(self.card, "_apply"):
                    if operation == "add":
                        self.card._on_add()
                    elif operation == "delete":
                        self.card._on_delete(entry)
                    elif operation == "enabled":
                        self.card._on_enabled(entry, False)
                    else:
                        edit = self.card._edits[id(entry)]
                        edit.setText("20:00")
                        self.card._on_time(entry, edit)
                self.assertIsNone(scheduler.pending_skip())
                self.assertEqual(self.card.skip_btn.text(), "跳过一次运行")

    def test_cancel_failure_keeps_trigger_configuration(self):
        scheduler.skip_next_run(self.info)
        original = copy.deepcopy(self.cfg)
        with patch.object(scheduler, "cancel_skip", side_effect=OSError("busy")), \
                patch.object(self.card, "_apply"):
            self.card._on_add()
        self.assertEqual(self.cfg, original)
        self.assertEqual(scheduler.pending_skip(), self.target)


if __name__ == "__main__":
    unittest.main()
