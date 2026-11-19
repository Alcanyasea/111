"""Result classification uses temporary history and synthetic process/log data."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from tests.test_support import appconfig
from tests.ps_harness import run_ps
from PySide6.QtWidgets import QApplication, QLabel
from core import logparse
from pages.history import HistoryPage
from pages.dashboard import LastRunStrip


class RunOutcomeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_log_summary_preserves_warning_unknown_and_legacy_results(self):
        lines = [('2026-11-18 04:00:00', s) for s in (
            'SUMMARY', '  [WARN] one - 2 min', '  [UNKNOWN] two - 3 min',
            '  [FAIL] three - 1 min', '  [OK] four - 1 min',
            'Total: 7 min | Passed: 2 | Failed: 1')]
        run = logparse._last_run_from_lines(lines)
        self.assertEqual(len(run['accounts']), 4)
        self.assertEqual([a['status'] for a in run['accounts']],
                         ['warning', 'unknown', 'failed', 'success'])
        self.assertTrue(run['accounts'][0]['ok'])
        self.assertFalse(run['accounts'][1]['ok'])
        self.assertEqual(run['unknown'], 1)
        self.assertEqual(run['warned'], 1)

    def test_dashboard_summary_does_not_label_unknown_as_failed(self):
        card = LastRunStrip()
        try:
            card._refresh_dots({'accounts': [
                {'name': 'one', 'ok': True, 'status': 'warning'},
                {'name': 'two', 'ok': False, 'status': 'unknown'}]})
            self.assertIn('收尾异常', card.passed_val.text())
            self.assertIn('待确认', card.passed_val.text())
            self.assertNotIn('失败', card.passed_val.text())
        finally:
            card.deleteLater()
            self.app.processEvents()

    def test_history_shows_warning_and_unknown_in_summary_and_rows(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(appconfig, 'save') as save:
            page = HistoryPage({'paths': {'script_dir': tmp}})
            try:
                rec = {'accounts': [
                    {'name': 'one', 'ok': True, 'status': 'warning', 'warning': '槽位回拉失败'},
                    {'name': 'two', 'ok': False, 'status': 'unknown', 'reason': '日志不可读'}]}
                text = page._plain_summary(rec)
                self.assertIn('收尾异常', text)
                self.assertIn('待确认', text)
                self.assertNotIn('失败', text)
                page._show_detail(rec)
                detail = ' '.join(w.text() for w in page.detail_card.findChildren(QLabel))
                self.assertNotIn('全部成功', detail)
                self.assertIn('成功但收尾异常', detail)
                self.assertIn('结果待确认', detail)
                self.assertIn('槽位回拉失败', detail)
                self.assertIn('日志不可读', detail)
                save.assert_not_called()
            finally:
                page.timer.stop()
                page.deleteLater()
                self.app.processEvents()

    def test_serialized_history_preserves_distinct_outcomes(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_ps(self, Path(tmp), ['scripts/master_pipeline.ps1', 'scripts/master_history.ps1'], r'''
$runHistoryDir = Join-Path $scriptDir 'history'
$runStamp = '20261118_040000'; $runStartTs = '2026-11-18 04:00:00'
$totalSw = [Diagnostics.Stopwatch]::StartNew()
$results = @(
    (New-AccountResult 'one' $totalSw $true -Warning '槽位回拉失败'),
    (New-AccountResult 'two' $totalSw $false 'MAA 运行' '日志不可读' -Unknown)
)
Save-RunHistory $null
@{ok=$true} | ConvertTo-Json
''')
            data = json.loads((Path(tmp) / 'history/run_20261118_040000.json').read_text(encoding='utf-8'))
            self.assertEqual((data['passed'], data['failed'], data['warned'], data['unknown']), (1, 0, 1, 1))
            self.assertEqual(data['accounts'][0]['status'], 'warning')
            self.assertEqual(data['accounts'][1]['status'], 'unknown')
            self.assertFalse(any(a['retry_allowed'] for a in data['accounts']))
