# -*- coding: utf-8 -*-
"""日志：深色视图 + 工具栏（刷新 / 打开 / 清空 / 自动滚动），运行中自动 tail。"""
import os
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from qfluentwidgets import (BodyLabel, MessageBox, PushButton, SwitchButton,
                            TextEdit)

import theme
from core import logparse, runner

MAX_LINES = 3000


class LogsPage(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self._last_size = -1
        self._last_mtime = -1

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 16, 0, 16)
        root.setSpacing(10)

        # 工具栏
        bar = QHBoxLayout()
        bar.setSpacing(10)
        refresh_btn = PushButton("刷新")
        refresh_btn.clicked.connect(self.refresh)
        open_btn = PushButton("打开日志文件")
        open_btn.clicked.connect(self._open_file)
        clear_btn = PushButton("清空")
        clear_btn.setStyleSheet(
            "PushButton { color: %s; border: 1px solid #e5b7b1; background: %s; }"
            "PushButton:hover { background: %s; }"
            % (theme.ERR, theme.CARD, theme.ERR_TINT))
        clear_btn.clicked.connect(self._clear)
        bar.addWidget(refresh_btn)
        bar.addWidget(open_btn)
        bar.addWidget(clear_btn)
        bar.addStretch(1)
        autoscroll_label = BodyLabel("自动滚动")
        autoscroll_label.setStyleSheet("color: %s; font-size: 12.5px;" % theme.TEXT_2)
        self.autoscroll = SwitchButton()
        self.autoscroll.setChecked(True)
        bar.addWidget(autoscroll_label)
        bar.addWidget(self.autoscroll)
        root.addLayout(bar)

        # 日志视图
        self.view = TextEdit()
        self.view.setReadOnly(True)
        self.view.setStyleSheet(
            "TextEdit { background: %s; color: %s;"
            " font-family: Consolas, 'Cascadia Mono', monospace;"
            " font-size: 12px; border-radius: 10px; padding: 12px 14px;"
            " border: none; }" % (theme.LOG_BG, theme.LOG_FG))
        root.addWidget(self.view, 1)

        self._body = ""  # 当前已展示的日志文本（避免无变化时重刷）
        self.refresh()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(3000)

    def _log_path(self):
        return Path(self.cfg["paths"]["log_file"])

    def _read(self):
        path = self._log_path()
        if not path.exists():
            return ""
        try:
            data = path.read_bytes()
            text = data.decode("utf-8", errors="ignore")
        except OSError:
            return ""
        # 只保留最后 MAX_LINES 行，避免超长日志卡 UI
        return "\n".join(text.splitlines()[-MAX_LINES:])

    def _changed(self):
        path = self._log_path()
        try:
            st = path.stat()
        except OSError:
            return False
        return (st.st_size, st.st_mtime) != (self._last_size, self._last_mtime)

    def refresh(self):
        text = self._read()
        path = self._log_path()
        try:
            st = path.stat()
            self._last_size, self._last_mtime = st.st_size, st.st_mtime
        except OSError:
            self._last_size = self._last_mtime = -1
        if text == self._body:
            return
        self._body = text
        self._render(text)

    def _render(self, text):
        html = ("<html><head><style>%s</style></head><body>%s</body></html>"
                % (logparse.log_css(), logparse.to_html(text, MAX_LINES)))
        scroll_to_end = self.autoscroll.isChecked()
        self.view.setHtml(html)
        if scroll_to_end:
            self.view.moveCursor(QTextCursor.MoveOperation.End)

    def _poll(self):
        if self._changed():
            self.refresh()

    def _open_file(self):
        path = self._log_path()
        if not path.exists():
            return
        os.startfile(str(path))

    def _clear(self):
        box = MessageBox("清空日志", "确定清空 master_log.txt 吗？此操作不可恢复。", self.window())
        box.yesButton.setText("清空")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        try:
            self._log_path().write_text("", encoding="utf-8")
        except OSError:
            return
        self._body = ""
        self._last_size = self._last_mtime = -1
        self.refresh()
