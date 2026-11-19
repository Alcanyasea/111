"""在统一面板中使用 Qt 的文件列表、目录导航和文件类型筛选。"""
import os
import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialogButtonBox, QFileDialog, QLabel, QLineEdit, QToolButton
from qfluentwidgets import LineEdit

import theme
from sheets import MessageBox, MessageBoxBase, SheetDialog
from widgets import style_button, style_primary_button


class _NewFolderBox(MessageBoxBase):
    def __init__(self, directory, parent):
        super().__init__(parent, title="新建文件夹", size=(440, 215))
        self.directory = directory
        self.created = None
        self.name = LineEdit()
        self.name.setPlaceholderText("文件夹名称")
        self.name.setAccessibleName("文件夹名称")
        self.viewLayout.addWidget(self.name)
        self.error = QLabel()
        self.error.setWordWrap(True)
        theme.bind(self.error, lambda: f"color: {theme.TEXT_2};")
        self.viewLayout.addWidget(self.error)
        self.name.textChanged.connect(self.error.clear)
        self.yesButton.setText("创建")
        self.name.returnPressed.connect(self.yesButton.click)
        self.name.setFocus()

    def validate(self):
        name = self.name.text().strip()
        if (not name or name.endswith(".") or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
                or Path(name).is_reserved()):
            self.error.setText("请输入有效的文件夹名称。")
            return False
        path = self.directory / name
        try:
            path.mkdir()
        except FileExistsError:
            self.error.setText("当前目录已存在同名项目，请更换名称。")
            return False
        except OSError as exc:
            self.error.setText(str(exc))
            return False
        self.created = path
        return True


class _FileBrowser(QFileDialog):
    def __init__(self, sheet, directory, name_filter, saving, required_suffix):
        super().__init__(sheet.panel, "", directory, name_filter,
                         options=QFileDialog.Option.DontUseNativeDialog)
        self.setWindowFlags(Qt.WindowType.Widget)
        self.saving = saving
        self.required_suffix = required_suffix
        self.chosen_path = ""
        self.setOption(QFileDialog.Option.ReadOnly)  # 选择文件时不移动、删除或重命名文件
        self.setOption(QFileDialog.Option.DontConfirmOverwrite)
        self.setAcceptMode(QFileDialog.AcceptSave if saving else QFileDialog.AcceptOpen)
        self.setFileMode(QFileDialog.AnyFile if saving else QFileDialog.ExistingFile)
        labels = QFileDialog.DialogLabel
        self.setLabelText(labels.LookIn, "所在目录")
        self.setLabelText(labels.FileName, "文件名")
        self.setLabelText(labels.FileType, "文件类型")
        self.setLabelText(labels.Accept, "保存" if saving else "打开")
        self.setLabelText(labels.Reject, "取消")
        self.layout().setContentsMargins(0, 0, 0, 0)
        builtin = self.findChild(QToolButton, "newFolderButton")
        new_folder = QToolButton(self)
        new_folder.setIcon(builtin.icon())
        new_folder.setAccessibleName("新建文件夹")
        new_folder.setToolTip("新建文件夹")
        new_folder.setAutoRaise(True)
        self.layout().replaceWidget(builtin, new_folder)
        builtin.hide()
        new_folder.clicked.connect(self._create_folder)
        theme.bind(self, lambda: f"""
            QLabel {{ color: {theme.TEXT_2}; }}
            QAbstractItemView, QLineEdit, QComboBox {{
                color: {theme.TEXT}; background: {theme.CARD}; border: 1px solid {theme.BORDER};
                border-radius: 6px; selection-background-color: {theme.PRIMARY_BG};
                selection-color: {theme.PRIMARY_FG};
            }}
            QLineEdit, QComboBox {{ min-height: 26px; padding: 1px 6px; }}
            QHeaderView::section {{ background: {theme.ROW_INSET}; color: {theme.TEXT_2};
                                    padding: 4px; border: none; }}
        """)
        buttons = self.findChild(QDialogButtonBox)
        for button in buttons.buttons():
            if buttons.buttonRole(button) == QDialogButtonBox.AcceptRole:
                style_primary_button(button, small=True)
            else:
                style_button(button, small=True)
        self.name_edit = self.findChild(QLineEdit, "fileNameEdit")
        self.error = QLabel(sheet.panel)
        self.error.setWordWrap(True)
        theme.bind(self.error, lambda: f"color: {theme.TEXT_2};")
        self.error.hide()
        self.name_edit.textChanged.connect(self.error.hide)
        self.directoryEntered.connect(self.error.hide)
        self.rejected.connect(sheet.reject)

    def _create_folder(self):
        box = _NewFolderBox(Path(self.directory().absolutePath()), self.window())
        if box.exec():
            self.setDirectory(str(box.created))
        box.deleteLater()

    def _invalid(self, message):
        self.error.setText(message)
        self.error.show()
        self.name_edit.setFocus()

    def accept(self):
        name = self.name_edit.text().strip('"')
        if not name:
            return
        # Qt 切换目录后，selectedFiles() 有时会同时返回上个选中项和新输入；
        # 单文件选择以界面上显示的文件名为准（列表选择也会回填此处）。
        path = Path(self.directory().absolutePath()) / os.path.expanduser(name)
        if path.is_dir():
            self.setDirectory(str(path))
            self.name_edit.clear()
            return
        if self.saving:
            if (path.name.endswith((" ", ".")) or path.is_reserved()
                    or re.search(r'[<>:"/\\|?*\x00-\x1f]', path.name)):
                self._invalid("请输入有效的文件名。")
                return
            if self.required_suffix and path.suffix.lower() != self.required_suffix.lower():
                path = path.with_suffix(self.required_suffix)
            elif not path.suffix:
                suffix = re.search(r"\*\.([\w.-]+)(?=[ )]|$)", self.selectedNameFilter())
                if suffix:
                    path = path.with_suffix("." + suffix.group(1))
            if not path.parent.is_dir():
                self._invalid("所在目录不存在，请选择已有目录。")
                return
            if path.is_dir():
                self._invalid("此名称已用于文件夹，请更换文件名。")
                return
            if path.exists():
                box = MessageBox("覆盖现有文件？", str(path), self.window())
                box.yesButton.setText("覆盖")
                box.cancelButton.setText("取消")
                overwrite = box.exec()
                box.deleteLater()
                if not overwrite:
                    return
        elif not path.is_file():
            self._invalid("找不到该文件，请检查路径或重新选择。")
            return
        self.chosen_path = str(path)
        self.window().accept()


class FileDialog:
    @staticmethod
    def _choose(parent, caption, directory, name_filter, saving, required_suffix=""):
        sheet = SheetDialog(parent, title=caption or ("保存文件" if saving else "打开文件"),
                            size=(760, 510))
        browser = _FileBrowser(sheet, directory, name_filter, saving, required_suffix)
        sheet.body.addWidget(browser, 1)
        sheet.body.addWidget(browser.error)
        try:
            if sheet.exec():
                return browser.chosen_path, browser.selectedNameFilter()
            return "", ""
        finally:
            sheet.deleteLater()

    @staticmethod
    def getOpenFileName(parent=None, caption="", dir="", filter=""):
        return FileDialog._choose(parent, caption, dir, filter, False)

    @staticmethod
    def getSaveFileName(parent=None, caption="", dir="", filter="", *, required_suffix=""):
        return FileDialog._choose(parent, caption, dir, filter, True, required_suffix)
