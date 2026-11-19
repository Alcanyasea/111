"""账号详情的遮罩、分组和单号操作只使用模拟账号。"""
import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from tests.test_support import appconfig
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QWidget
from sheets import MessageBox
from pages.accounts import AccountDetailDialog


class AccountDetailTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.save = self.enterContext(patch.object(appconfig, "save"))
        self.enterContext(patch("pages.accounts.slot_uid", return_value=None))
        self.owner = QWidget()
        self.owner.resize(1000, 560)
        self.owner.show()
        self.addCleanup(self.owner.close)
        self.account = {"label": "测试账号", "server": "official", "slot": "test"}
        self.page = Mock()
        self.page.export_busy.return_value = False
        self.cfg = {"accounts": [self.account, {"label": "第二账号"}]}
        self.sheet = AccountDetailDialog(self.cfg, self.account, self.page, self.owner)
        self.sheet.show()
        self.app.processEvents()
        self.addCleanup(self.sheet.close)

    def button(self, name):
        return next(b for b in self.sheet.sections.currentWidget().findChildren(QPushButton)
                    if (b.accessibleName() or b.text()) == name)

    def test_only_outside_click_dismisses_without_running_an_action(self):
        QTest.mouseClick(self.sheet.panel, Qt.LeftButton, pos=QPoint(5, 5))
        self.assertTrue(self.sheet.isVisible())
        QTest.mouseClick(self.sheet.tabs, Qt.LeftButton, pos=self.sheet.tabs.tabRect(1).center())
        self.assertEqual(self.sheet.sections.currentIndex(), 1)
        QTest.mouseClick(self.sheet, Qt.LeftButton, pos=QPoint(8, 8))
        self.assertFalse(self.sheet.isVisible())
        self.assertFalse(any((self.sheet.deleted, self.sheet.move_delta, self.sheet.export_wanted,
                              self.sheet.switch_wanted)))
        self.save.assert_not_called()

    def test_child_editor_can_close_and_return_to_the_sheet(self):
        visible = []
        def open_editor(cfg, account, parent):
            self.assertIs(account, self.account)
            child = QDialog(parent)
            def dismiss_editor():
                parent._app_state_changed(Qt.ApplicationInactive)
                visible.append(parent.isVisible())
                QTest.keyClick(child, Qt.Key_Escape)
            QTimer.singleShot(0, dismiss_editor)
            child.exec()
        with patch("pages.accounts.show_stage_plan_dialog", side_effect=open_editor) as editor:
            self.button("第二理智候选关卡").click()
            editor.assert_called_once()
        self.assertEqual(visible, [True])
        self.assertTrue(self.sheet.isVisible())
        QTest.keyClick(self.sheet, Qt.Key_Escape)
        self.assertFalse(self.sheet.isVisible())
        self.save.assert_not_called()

    def test_management_preserves_order_boundaries_and_delete_confirmation(self):
        self.sheet.tabs.setCurrentIndex(2)
        self.assertFalse(self.button("上移").isEnabled())
        def cancel_delete():
            self.sheet._app_state_changed(Qt.ApplicationInactive)
            self.sheet.findChild(MessageBox).cancelButton.click()
        QTimer.singleShot(250, cancel_delete)
        self.button("从运行列表移除").click()
        self.assertTrue(self.sheet.isVisible())
        self.assertEqual(len(self.cfg["accounts"]), 2)
        self.button("下移").click()
        self.assertEqual(self.sheet.move_delta, 1)
        self.assertFalse(self.sheet.isVisible())
        self.save.assert_not_called()

    def test_leaving_application_dismisses_detail(self):
        self.sheet._app_state_changed(Qt.ApplicationInactive)
        self.assertFalse(self.sheet.isVisible())
        self.save.assert_not_called()
