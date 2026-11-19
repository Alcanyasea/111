"""主页导航回归。使用合成账号和计划，不启动后台任务。"""
import copy
import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from tests.test_support import appconfig
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pages.home import HomePage, next_plan


class HomeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        save = patch.object(appconfig, "save")
        self.save = save.start()
        self.addCleanup(save.stop)

    def test_first_account_disabled_accounts_and_running(self):
        home = HomePage()
        home.set_run_state([], False, "")
        self.assertFalse(home.accounts_btn.isHidden())
        self.assertTrue(home.run_btn.isHidden())
        home.set_run_state([{"enabled": False}], False, "")
        self.assertEqual(home.accounts_btn.text(), "管理账号")
        home.set_run_state([{}], False, "")
        self.assertFalse(home.run_btn.isHidden())
        self.assertIn("1 个账号", home.description.text())
        home.set_run_state([{}], True, "当前：测试号 · 基建")
        self.assertTrue(home.run_btn.isHidden())
        self.assertFalse(home.progress_btn.isHidden())
        self.assertIn("测试号", home.description.text())
        self.save.assert_not_called()

    def test_next_plan_compares_enabled_tasks(self):
        when = datetime.now() + timedelta(hours=2)
        farm = {"enabled": True, "exists": True, "next_run": when}
        collect = dict(farm, next_run=when - timedelta(hours=1))
        self.assertEqual(next_plan(farm, collect)[0], 1)
        self.assertEqual(next_plan(farm, dict(collect, enabled=False))[0], 0)
        self.assertIsNone(next_plan(None, {}))

    def test_main_window_links_to_active_plan_record_and_log(self):
        from main import MainWindow
        with tempfile.TemporaryDirectory() as td, ExitStack() as mocks:
            root = Path(td)
            cfg = copy.deepcopy(appconfig.DEFAULTS)
            cfg["paths"] = {key: str(root / key) for key in cfg["paths"]}
            cfg["accounts"] = [{"id": "first", "label": "测试号", "server": "official"},
                               {"id": "second", "label": "测试号", "server": "bilibili",
                                "slot": "slot two", "enabled": False}]
            cfg["cleanup"]["auto"] = False
            history = root / "script_dir" / "run_history"
            history.mkdir(parents=True)
            (root / "script_dir" / "run_account.ps1").touch()
            name = "run_20260101_084200.json"
            (history / name).write_text(json.dumps({"start": "2026-01-01 08:42:00",
                "accounts": [{"name": "测试号", "ok": True}]}), encoding="utf-8")
            mocks.enter_context(patch.object(appconfig, "load", return_value=cfg))
            mocks.enter_context(patch.object(MainWindow, "_init_tray",
                lambda window: setattr(window, "_tray", Mock())))
            for target in ("main.poller.SchedulerPoller.start", "main.poller.AdbPoller.start",
                           "main.runner.clear_stale_lock", "core.scheduler.pending_skip",
                           "core.adb.emulator_running", "core.adb.maa_running",
                           "pages.dashboard.AccountStatusPage._start_token_check"):
                mocks.enter_context(patch(target, return_value=None))
            running = mocks.enter_context(patch("main.runner.is_running", return_value=False))
            stop = mocks.enter_context(patch.object(MainWindow, "on_stop"))
            launch = mocks.enter_context(patch("main.runner._launch_master"))
            mocks.enter_context(patch("main.InfoBar.info"))
            mocks.enter_context(patch("main.InfoBar.warning"))
            window = MainWindow()
            try:
                window.show()
                self.app.processEvents()
                self.assertIs(window.stack.currentWidget(), window.home)
                self.assertEqual(window.nav.count(), 5)
                window.nav.setCurrentIndex(1)
                card = window.accounts_p.cards[1]
                for button, flags in ((card.collect_btn, ["-InfrastCollect"]), (card.run_btn, [])):
                    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                    launch.assert_called_once_with(cfg, ["-AccountId", "second", *flags],
                                                   "run_account.ps1")
                    launch.reset_mock()
                running.return_value = True
                card.run_btn.click()
                running.return_value = False
                launch.assert_not_called()
                for index in (1, 4, 0):
                    QTest.mouseClick(window.nav, Qt.MouseButton.LeftButton,
                                     pos=window.nav.tabRect(index).center())
                    self.assertEqual(window.stack.currentIndex(), index)
                when = datetime.now() + timedelta(hours=1)
                window.collect_card.refresh_scheduler({"exists": True, "enabled": True,
                                                       "next_run": when})
                window.refresh_status()
                window.home.plan.link.click()
                self.assertIs(window.stack.currentWidget(), window.plans)
                self.assertEqual(window.plans.tabs.currentIndex(), 1)
                window.nav.setCurrentIndex(0)
                window.home.history.link.click()
                self.assertEqual(window.history_p._selected_file, name)
                running.return_value = True
                window.nav.setCurrentIndex(0)
                window.home.progress_btn.click()
                self.assertIs(window.records.stack.currentWidget(), window.logs_p)
                self.assertTrue(window.stop_btn.isVisible())
                window.stop_btn.click()
                stop.assert_called_once()
                self.save.assert_not_called()
            finally:
                window.close()
                window.deleteLater()
                QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
