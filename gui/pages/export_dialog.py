# -*- coding: utf-8 -*-
"""干员资料导出弹窗：列出本次要导的账号，实时显示 export_operbox.py 输出。

与 MAA 更新弹窗同款交互：点「后台运行」只是收起窗口，导出继续；
结果在右上角通知条提示。
"""
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPlainTextEdit,
                               QVBoxLayout)

from qfluentwidgets import BodyLabel, PushButton

import theme
from widgets import dark_log_qss, style_button


class ExportDialog(QDialog):
    """导出实时日志窗口。关闭只是后台运行，不中断导出。"""

    def __init__(self, accounts, parent=None):
        super().__init__(parent)
        self.setWindowTitle("干员资料导出")
        self.resize(640, 480)
        theme.bind(self, lambda: "QDialog { background: %s; }" % theme.BG)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)

        names = "、".join(a.get("label") or a.get("slot") or "?" for a in accounts)
        head = QLabel(
            "本次导出 %d 个账号：%s\n"
            "逐号切号 → 更新等待 → 登录校验 → MAA 干员识别，"
            "结果写入 exports\\（约 3 分钟/号）。" % (len(accounts), names))
        theme.bind(head, lambda: "font-family: %s; font-size: 12.5px; color: %s;"
                   % (theme.FONT_FAMILY, theme.TEXT_2))
        root.addWidget(head)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setStyleSheet(dark_log_qss())
        root.addWidget(self.log_view, 1)

        btns = QHBoxLayout()
        tip = BodyLabel("关闭窗口不会中断导出，完成后右上有提示")
        theme.bind(tip, lambda: "font-family: %s; font-size: 12px; color: %s;"
                   % (theme.FONT_FAMILY, theme.TEXT_3))
        close_btn = style_button(PushButton("后台运行"))
        close_btn.clicked.connect(self.accept)
        btns.addWidget(tip)
        btns.addStretch(1)
        btns.addWidget(close_btn)
        root.addLayout(btns)

    def append(self, text):
        self.log_view.appendPlainText(text)
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())
