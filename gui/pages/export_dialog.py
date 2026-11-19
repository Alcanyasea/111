# -*- coding: utf-8 -*-
"""干员资料导出弹窗：列出本次要导的账号，实时显示 export_operbox.py 输出。

与 MAA 更新弹窗同款交互：点「后台运行」只是收起窗口，导出继续；
结果在右上角通知条提示。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit

from qfluentwidgets import PushButton

from sheets import SheetDialog
from widgets import dark_log_qss, label, style_button


class ExportDialog(SheetDialog):
    """导出实时日志窗口。关闭只是后台运行，不中断导出。"""

    def __init__(self, accounts, parent=None):
        super().__init__(parent, title="干员资料导出", size=(720, 452))
        names = "、".join(a.get("label") or a.get("slot") or "?" for a in accounts)
        head = label("导出账号（%d）   %s" % (len(accounts), names), 16, "600")
        head.setTextFormat(Qt.PlainText)
        head.setWordWrap(True)
        self.body.addWidget(head)
        info = label("MAA 会依次切号、校验登录并识别干员，结果保存在 exports 文件夹。约 3 分钟 / 账号。",
                     13, color="TEXT_2")
        info.setWordWrap(True)
        self.body.addWidget(info)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setAccessibleName("导出日志")
        self.log_view.setPlaceholderText("正在等待导出日志…")
        self.log_view.setStyleSheet(dark_log_qss())
        self.body.addWidget(self.log_view, 1)

        btns = QHBoxLayout()
        self.status_label = label("收起后继续导出，完成时会通知你。", 13, color="TEXT_2")
        self.close_btn = style_button(PushButton("后台运行"))
        self.close_btn.clicked.connect(self.reject)
        btns.addWidget(self.status_label)
        btns.addStretch(1)
        btns.addWidget(self.close_btn)
        self.body.addLayout(btns)

    def finish(self, ok, summary):
        status = "导出完成" if ok else "导出未全部成功"
        self.append("")
        self.append("==== %s ====" % status)
        self.append(summary)
        self.status_label.setText(status)
        self.close_btn.setText("关闭")

    def append(self, text):
        self.log_view.appendPlainText(text)
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())
