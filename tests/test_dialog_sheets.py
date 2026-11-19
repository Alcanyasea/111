"""浮层行为使用独立的内存窗口，不启动控制台服务。"""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from tests.test_support import appconfig
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QWidget
from sheets import MessageBox, SheetDialog, _dismiss_active_sheet


class SheetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.enterContext(patch.object(appconfig, "save"))
        self.window = QWidget()
        self.window.resize(1000, 560)
        self.window.show()
        self.addCleanup(self.window.close)
        self.editor = SheetDialog(self.window, title="设置", size=(1060, 650))
        self.editor.show()
        self.addCleanup(self.editor.close)
        self.app.processEvents()

    def open_confirmation(self, message="是否保存？"):
        box = MessageBox("保存预设", message, self.editor)
        box.show()
        self.app.processEvents()
        self.addCleanup(box.close)
        return box

    def test_only_top_sheet_cancels_and_click_does_not_accept(self):
        box = self.open_confirmation()
        QTest.mouseClick(box.panel, Qt.LeftButton, pos=QPoint(10, 10))
        self.assertTrue(box.isVisible())
        QTest.mouseClick(box, Qt.LeftButton, pos=QPoint(4, 4))
        self.assertEqual(box.result(), QDialog.Rejected)
        self.assertFalse(box.isVisible())
        self.assertIs(self.app.activeModalWidget(), self.editor)
        box.show()
        box.yesButton.click()
        self.assertEqual(box.result(), QDialog.Accepted)
        self.assertTrue(self.editor.isVisible())

    def test_resize_and_deactivation_at_three_levels(self):
        middle = self.open_confirmation()
        top = MessageBox("第三层", "覆盖文件？", middle)
        top.show()
        self.addCleanup(top.close)
        self.window.resize(1040, 610)
        self.app.processEvents()
        self.assertEqual(top.size(), self.window.size())
        for sheet in (self.editor, middle, top):
            self.assertGreaterEqual(sheet.panel.x(), 15)
            self.assertGreaterEqual(sheet.panel.y(), 15)
        _dismiss_active_sheet(Qt.ApplicationInactive)
        self.assertFalse(top.isVisible())
        self.assertTrue(middle.isVisible())
        self.assertTrue(self.editor.isVisible())

    def test_popup_menu_and_drag_out_do_not_cancel_editor(self):
        combo = QComboBox()
        combo.addItems(["一", "二"])
        self.editor.body.addWidget(combo)
        combo.showPopup()
        _dismiss_active_sheet(Qt.ApplicationInactive)
        self.assertTrue(self.editor.isVisible())
        combo.hidePopup()
        QTest.mousePress(self.editor, Qt.LeftButton, pos=self.editor.panel.geometry().center())
        QTest.mouseRelease(self.editor, Qt.LeftButton, pos=QPoint(4, 4))
        self.assertTrue(self.editor.isVisible())
