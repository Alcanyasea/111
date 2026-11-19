"""文件面板只打开临时目录中的模拟文件。"""
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from tests import test_support  # 将 gui 加入模块路径，并隔离配置导入
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QDialogButtonBox, QFileDialog,
                               QLineEdit, QToolButton, QTreeView, QWidget)

from sheet_files import FileDialog
from sheets import MessageBox, MessageBoxBase, SheetDialog


class FileSheetTest(unittest.TestCase):
    filters = "JSON 文件 (*.json);;所有文件 (*)"

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.owner = QWidget()
        self.owner.resize(1080, 700)
        self.owner.show()
        self.addCleanup(self.owner.close)

    def run_picker(self, method, interact, initial=None, **kwargs):
        errors = []
        driver = QTimer(self.owner)

        def drive():
            sheet = self.app.activeModalWidget()
            if not isinstance(sheet, SheetDialog) or not sheet.isVisible():
                return
            driver.stop()
            try:
                browser = sheet.findChild(QFileDialog)
                interact(sheet, browser, browser.findChild(QLineEdit, "fileNameEdit"))
            except Exception as exc:
                errors.append(exc)
            finally:
                if sheet.isVisible():
                    sheet.reject()

        driver.timeout.connect(drive)
        driver.start(10)
        result = method(self.owner, "模拟文件", str(initial or self.directory),
                        self.filters, **kwargs)
        if errors:
            raise errors[0]
        return result

    def test_open_typed_unicode_path_after_directory_navigation(self):
        subdir = self.directory / "模拟文件夹"
        subdir.mkdir()
        target = subdir / "模拟排班.json"
        target.touch()

        def open_file(sheet, browser, edit):
            edit.setText(str(subdir))
            browser.accept()
            self.assertTrue(sheet.isVisible())
            self.assertEqual(Path(browser.directory().absolutePath()), subdir)
            edit.setText(target.name)
            browser.accept()

        name, selected_filter = self.run_picker(FileDialog.getOpenFileName, open_file)
        self.assertEqual(Path(name), target)
        self.assertEqual(selected_filter, "JSON 文件 (*.json)")

    def test_save_adds_suffix_chosen_by_filter(self):
        def save_file(sheet, browser, edit):
            self.assertEqual(edit.text(), "初始.json")
            edit.setText("新排班")
            browser.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()

        name, selected_filter = self.run_picker(FileDialog.getSaveFileName, save_file,
                                               initial=self.directory / "初始.json")
        self.assertEqual(Path(name), self.directory / "新排班.json")
        self.assertEqual(selected_filter, "JSON 文件 (*.json)")

    def test_invalid_windows_filename_stays_open_for_correction(self):
        def correct_file(sheet, browser, edit):
            edit.setText("不合法?.json")
            browser.accept()
            self.assertTrue(sheet.isVisible())
            edit.setText("更正.json")
            browser.accept()

        name, _ = self.run_picker(FileDialog.getSaveFileName, correct_file)
        self.assertEqual(Path(name), self.directory / "更正.json")

    def test_double_click_file_in_filtered_list_opens_it(self):
        target = self.directory / "列表.json"
        target.touch()
        (self.directory / "隐藏.txt").touch()

        def double_click(sheet, browser, edit):
            browser.setViewMode(QFileDialog.Detail)
            view = browser.findChild(QTreeView, "treeView")
            model, root = view.model(), view.rootIndex()
            for _ in range(100):
                if model.rowCount(root):
                    break
                QTest.qWait(10)
            names = [model.index(row, 0, root).data() for row in range(model.rowCount(root))]
            self.assertEqual(names, [target.name])
            point = view.visualRect(model.index(0, 0, root)).center()
            QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=point)
            QTest.mouseDClick(view.viewport(), Qt.LeftButton, pos=point)

        name, _ = self.run_picker(FileDialog.getOpenFileName, double_click)
        self.assertEqual(Path(name), target)

    def test_create_folder_retries_duplicate_and_saves_into_new_directory(self):
        (self.directory / "已有").mkdir()

        def create_folder(sheet, browser, edit):
            button = next(b for b in browser.findChildren(QToolButton)
                          if b.accessibleName() == "新建文件夹")
            attempted = []

            def fill():
                box = sheet.findChild(MessageBoxBase)
                if not box or not box.isVisible():
                    return
                driver.stop()
                folder = box.findChild(QLineEdit)
                folder.setText("已有")
                box.yesButton.click()
                attempted.append(box.isVisible())
                folder.setText("空班次")
                box.yesButton.click()
                if box.isVisible():
                    box.reject()

            driver = QTimer(sheet)
            driver.timeout.connect(fill)
            driver.start(10)
            button.click()
            self.assertEqual(attempted, [True])
            self.assertEqual(Path(browser.directory().absolutePath()), self.directory / "空班次")
            edit.setText("排班.json")
            QTest.keyClick(edit, Qt.Key_Return)

        name, _ = self.run_picker(FileDialog.getSaveFileName, create_folder)
        self.assertEqual(Path(name), self.directory / "空班次" / "排班.json")

    def test_outside_overwrite_then_outside_picker_cancels_each_layer(self):
        target = self.directory / "旧排班.json"
        target.write_text("原排班", encoding="utf-8")

        def cancel_save(sheet, browser, edit):
            edit.setText("旧排班.txt")
            dismissed = []

            def dismiss_overwrite():
                box = sheet.findChild(MessageBox)
                if box and box.isVisible():
                    driver.stop()
                    QTest.mouseClick(box, Qt.LeftButton, pos=QPoint(2, 2))
                    dismissed.append(not box.isVisible())
                    if box.isVisible():
                        box.reject()

            driver = QTimer(sheet)
            driver.timeout.connect(dismiss_overwrite)
            driver.start(10)
            browser.accept()
            self.assertEqual(dismissed, [True])
            self.assertTrue(sheet.isVisible())
            QTest.mouseClick(sheet, Qt.LeftButton, pos=QPoint(2, 2))
            self.assertFalse(sheet.isVisible())

        self.assertEqual(self.run_picker(FileDialog.getSaveFileName, cancel_save,
                                         required_suffix=".json"), ("", ""))
        self.assertEqual(target.read_text(encoding="utf-8"), "原排班")
