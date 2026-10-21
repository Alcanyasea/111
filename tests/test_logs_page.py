# -*- coding: utf-8 -*-
"""日志页测试（offscreen，日志用临时文件，不碰真实 master_log.txt）。

覆盖：尾部读取只装载最近 MAX_LINES 行、过滤输入 250ms 防抖后整体重载。
"""
import os
import sys
import tempfile
import time
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
        """两次输入间隔远小于防抖窗口：合并为一次重载，按最终关键字过滤。

        不赌墙钟时序（CI 慢机器上 250ms 内外断言会抖）：改为给定时器
        timeout 挂计数器，断言两次 textChanged 只触发一次、最终视图正确。
        """
        fired = []
        self.page._filter_timer.timeout.connect(lambda: fired.append(None))
        self.page.filter_edit.setText("line-40")
        self.page.filter_edit.setText("line-4000")
        deadline = time.time() + 5
        while not fired and time.time() < deadline:
            QTest.qWait(50)
        self.assertEqual(len(fired), 1)   # 单次定时器：两次输入只重载一次
        text = self.page.view.toPlainText()
        self.assertIn("line-4000", text)
        self.assertNotIn("line-3999", text)


if __name__ == "__main__":
    unittest.main()
