# -*- coding: utf-8 -*-
"""日志页测试（offscreen，日志用临时文件，不碰真实 master_log.txt）。

覆盖：尾部读取只装载最近 MAX_LINES 行、过滤输入 250ms 防抖后整体重载。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gui"))

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pages.logs import MAX_LINES, LogsPage  # noqa: E402


class LogsPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.log = self.root / "master_log.txt"
        self.log.write_text(
            "\n".join("line-%04d" % i for i in range(1, 4001)) + "\n",
            encoding="utf-8")
        self.cfg = {"paths": {"log_file": str(self.log)}}
        self.page = LogsPage(self.cfg)

    def tearDown(self):
        self.page.deleteLater()
        self.app.processEvents()

    def test_reads_only_tail_lines(self):
        text = self.page.view.toPlainText()
        self.assertIn("line-4000", text)
        self.assertNotIn("line-%04d" % (4000 - MAX_LINES), text)

    def test_filter_is_debounced(self):
        self.page.filter_edit.setText("line-4000")
        # 输入后未满 250ms：视图仍是旧内容（没有每敲一个字就全量重载）
        self.assertIn("line-3999", self.page.view.toPlainText())
        QTest.qWait(400)
        text = self.page.view.toPlainText()
        self.assertIn("line-4000", text)
        self.assertNotIn("line-3999", text)


if __name__ == "__main__":
    unittest.main()
