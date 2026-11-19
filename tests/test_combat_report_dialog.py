import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_support import appconfig
from PySide6.QtWidgets import QApplication
from pages.combat_report_dialog import CombatReportDialog


class CombatReportDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_account_day_filters_and_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(appconfig, 'save') as save:
            folder = Path(tmp) / 'combat_report'
            folder.mkdir()
            row = dict(id='one', game_day='2026-11-13', stage='1-7', times=3, rounds=1,
                       sanity=18, medicinesUsed=0, expiringMedicinesUsed=1, stonesUsed=0,
                       drops={'30012': 4}, incomplete=True)
            report = dict(version=1, account='A', server='official', status='available',
                          captured_at='2026-11-13T00:00:00Z', records=[row], item_names={'30012': '固源岩'})
            (folder / 'fixture.json').write_text(json.dumps(report), encoding='utf-8')
            report = dict(report, account='B', records=[], status='unavailable')
            (folder / 'unavailable.json').write_text(json.dumps(report), encoding='utf-8')
            (folder / 'broken.json').write_text('{', encoding='utf-8')
            dialog = CombatReportDialog({'accounts': [{'id': 'A', 'label': '已改名账号'}]}, tmp)
            try:
                self.assertEqual(1, dialog.table.rowCount())
                self.assertIn('场次 3', dialog.summary.text())
                self.assertIn('固源岩', dialog.summary.text())
                self.assertIn('已改名账号', dialog.table.item(0, 0).text())
                self.assertIn('官服', dialog.table.item(0, 0).text())
                dialog.account_filter.setCurrentIndex(dialog.account_filter.findData('official:B'))
                self.assertEqual(0, dialog.table.rowCount())
                self.assertIn('数据不可用', dialog.summary.text())
                dialog.account_filter.setCurrentIndex(dialog.account_filter.findData('official:A'))
                dialog.day_filter.setCurrentIndex(dialog.day_filter.findData('2026-11-13'))
                self.assertEqual(1, dialog.table.rowCount())
                self.assertIn('未完整结束', dialog.table.item(0, 7).text())
                (folder / 'fixture.json').unlink()
                dialog.reload()
                self.assertEqual(0, dialog.table.rowCount())
                self.assertIn('数据不可用', dialog.summary.text())
                save.assert_not_called()
            finally:
                dialog.deleteLater()
                self.app.processEvents()
