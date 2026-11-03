# -*- coding: utf-8 -*-
"""运行历史页的「失败现场」入口（offscreen，目录用临时数据）。

覆盖：新记录优先使用 stamp，旧记录从 start 推导；仅打开存在的失败现场目录。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.ps_harness import run_ps

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gui"))

from PySide6.QtWidgets import QApplication  # noqa: E402
from qfluentwidgets import PushButton  # noqa: E402

from pages.history import HistoryPage  # noqa: E402


class HistoryEvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "run_history").mkdir()
        self.cfg = {"paths": {"script_dir": str(self.root)},
                    "schedule": {"times": [], "collect_times": []}}
        self.page = HistoryPage(self.cfg)

    def tearDown(self):
        self.page.timer.stop()
        self.page.deleteLater()
        self.app.processEvents()

    def test_evidence_dir_follows_start_timestamp(self):
        expected = self.root / "run_history" / "20261102_220002"
        expected.mkdir()
        rec = {"start": "2026-11-02 22:00:02"}
        self.assertEqual(self.page._evidence_dir(rec), expected)

    def test_missing_evidence_dir_returns_none(self):
        # 全部成功的一轮不会生成证据目录，此时不能显示入口
        rec = {"start": "2026-11-02 22:00:02"}
        self.assertIsNone(self.page._evidence_dir(rec))

    def test_unparseable_start_is_ignored(self):
        self.assertIsNone(self.page._evidence_dir({"start": ""}))
        self.assertIsNone(self.page._evidence_dir({}))
        self.assertIsNone(self.page._evidence_dir({"start": "2026/11/02 22:00"}))

    def test_explicit_stamp_is_preferred_for_new_records(self):
        expected = self.root / "run_history" / "20261102_220003"
        expected.mkdir()
        rec = {"start": "not parseable", "stamp": "20261102_220003"}
        self.assertEqual(self.page._evidence_dir(rec), expected)

    def test_invalid_stamp_cannot_select_a_directory_outside_history(self):
        expected = self.root / "run_history" / "20261102_220002"
        expected.mkdir()
        for invalid in ("..", str(self.root), "../outside", [], 42, None):
            with self.subTest(stamp=invalid):
                self.assertIsNone(self.page._evidence_dir({"stamp": invalid}))
                self.assertEqual(self.page._evidence_dir({
                    "stamp": invalid, "start": "2026-11-02 22:00:02"}), expected)

    def test_missing_explicit_directory_does_not_open_another_runs_evidence(self):
        (self.root / "run_history" / "20261102_220002").mkdir()
        self.assertIsNone(self.page._evidence_dir({
            "stamp": "20261102_220003", "start": "2026-11-02 22:00:02"}))

    def test_written_history_opens_the_archived_evidence_in_gui(self):
        # Run the real PS writer and archive function on synthetic files, then
        # load the resulting record and click the real GUI button.
        r = run_ps(self, self.root,
                   ["scripts/master_history.ps1", "scripts/master_pipeline.ps1"], r'''
$runHistoryDir = Join-Path $scriptDir 'run_history'
$runStartTs = '2026-11-02 22:00:02'
$runStamp = '20261102_220003'
$runMode = 'collect'
$bsBatch = ''
$totalSw = [Diagnostics.Stopwatch]::StartNew()
$results = @([pscustomobject]@{Account='fixture'; OK=$false; Minutes=1; Reason='fixture failure'; Step='login'; Retried=$false})
$debug = Join-Path $scriptDir 'debug'
$null = New-Item -ItemType Directory -Path $debug
[IO.File]::WriteAllText((Join-Path $debug 'fixture.txt'), 'synthetic diagnostic')
Save-FailureEvidence 'fixture' 'official' 'login'
Save-RunHistory ''
@{written=(Test-Path -LiteralPath (Join-Path $runHistoryDir 'run_20261102_220003.json'))} | ConvertTo-Json
''')
        self.assertTrue(r["written"])
        self.page.refresh()
        self.page.list.setCurrentRow(0)
        expected = self.root / "run_history" / "20261102_220003"
        self.assertEqual((expected / "fixture" / "fixture.txt").read_text(),
                         "synthetic diagnostic")
        buttons = [b for b in self.page.findChildren(PushButton)
                   if b.text() == "打开失败现场"]
        self.assertEqual(len(buttons), 1)
        with patch("pages.history.os.startfile") as open_folder:
            buttons[0].click()
        open_folder.assert_called_once_with(str(expected))


if __name__ == "__main__":
    unittest.main()
