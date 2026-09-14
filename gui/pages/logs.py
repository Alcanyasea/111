# -*- coding: utf-8 -*-
"""日志：深色视图 + 工具栏（刷新 / 打开 / 清空 / 自动滚动），运行中自动 tail。

增量渲染：按 (size, mtime) 判变更后只追加新增的完整行，不再每 3 秒
setHtml 重渲染整个视图；文件被截断/清空时整体重载。
"""
import os
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (BodyLabel, InfoBar, InfoBarPosition, LineEdit,
                            MessageBox, PushButton, SwitchButton, TextEdit)

import theme
from core import logparse, runner
from widgets import set_switch_checked_gray, style_button

MAX_LINES = 3000


class LogsPage(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self._last_size = -1
        self._last_mtime = -1
        self._offset = 0        # 已渲染到的文件字节位置
        self._pending = b""     # 尾部不完整行（等下次凑齐再渲染）

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 16, 12, 16)
        root.setSpacing(10)

        # 工具栏
        bar = QHBoxLayout()
        bar.setSpacing(10)
        refresh_btn = style_button(PushButton("刷新"), small=True)
        refresh_btn.clicked.connect(self.refresh)
        open_btn = style_button(PushButton("打开日志文件"), small=True)
        open_btn.clicked.connect(self._open_file)
        clear_btn = style_button(PushButton("清空"), "danger", small=True)
        clear_btn.clicked.connect(self._clear)
        bar.addWidget(refresh_btn)
        bar.addWidget(open_btn)
        bar.addWidget(clear_btn)
        # 关键字过滤：只显示命中的行（整体重载 + 增量追加两条路径都过滤）
        self.filter_edit = LineEdit()
        self.filter_edit.setPlaceholderText("过滤关键字（留空显示全部）")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setFixedWidth(220)
        self.filter_edit.textChanged.connect(self.refresh)
        bar.addStretch(1)
        bar.addWidget(self.filter_edit)
        autoscroll_label = BodyLabel("自动滚动")
        autoscroll_label.setStyleSheet("color: %s; font-size: 12.5px;" % theme.TEXT_2)
        self.autoscroll = set_switch_checked_gray(SwitchButton())
        self.autoscroll.setChecked(True)
        bar.addWidget(autoscroll_label)
        bar.addWidget(self.autoscroll)
        root.addLayout(bar)

        # 日志视图
        self.view = TextEdit()
        self.view.setReadOnly(True)
        self.view.setStyleSheet(
            "TextEdit { background: %s; color: %s;"
            " font-family: %s; font-size: 12px; border-radius: %dpx;"
            " padding: 12px 14px; border: none; }"
            % (theme.LOG_BG, theme.LOG_FG, theme.FONT_MONO, theme.RADIUS_CARD))
        root.addWidget(self.view, 1)

        self.refresh()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(3000)

    def _log_path(self):
        return Path(self.cfg["paths"]["log_file"])

    def _read_all(self):
        """全量读尾部 MAX_LINES 行（初始化/截断后重载用），并记录渲染位置。"""
        path = self._log_path()
        try:
            with open(path, "rb") as f:
                data = f.read()
            self._offset = len(data)
            self._pending = b""
        except OSError:
            self._offset = 0
            self._pending = b""
            return ""
        text = data.decode("utf-8", errors="ignore")
        return "\n".join(text.splitlines()[-MAX_LINES:])

    def _changed(self):
        path = self._log_path()
        try:
            st = path.stat()
        except OSError:
            return False
        return (st.st_size, st.st_mtime) != (self._last_size, self._last_mtime)

    def _filtered(self, text):
        """按关键字（不区分大小写）过滤行；关键字为空时原样返回。"""
        kw = self.filter_edit.text().strip().lower()
        if not kw:
            return text
        return "\n".join(l for l in text.splitlines() if kw in l.lower())

    def refresh(self):
        """整体重载（初始化 / 点刷新 / 日志被截断 / 过滤关键字变化）。"""
        text = self._filtered(self._read_all())
        path = self._log_path()
        try:
            st = path.stat()
            self._last_size, self._last_mtime = st.st_size, st.st_mtime
        except OSError:
            self._last_size = self._last_mtime = -1
        html = ("<html><head><style>%s</style></head><body>%s</body></html>"
                % (logparse.log_css(), logparse.to_html(text, MAX_LINES)))
        self.view.setHtml(html)
        if self.autoscroll.isChecked():
            self.view.moveCursor(QTextCursor.MoveOperation.End)

    def _poll(self):
        if not self._changed():
            return
        path = self._log_path()
        try:
            st = path.stat()
            size, mtime = st.st_size, st.st_mtime
        except OSError:
            return
        if size < self._offset:
            # 日志被清空/截断（master.ps1 或清理）：回退到整体重载
            self._last_size, self._last_mtime = size, mtime
            self.refresh()
            return
        self._last_size, self._last_mtime = size, mtime
        try:
            with open(path, "rb") as f:
                f.seek(self._offset)
                data = f.read()
        except OSError:
            return
        if not data:
            return
        self._offset += len(data)
        buf = self._pending + data
        cut = buf.rfind(b"\n")
        if cut == -1:
            self._pending = buf      # 还没有完整行，全部留待下次
            return
        self._pending = buf[cut + 1:]
        lines = buf[:cut].decode("utf-8", errors="ignore").splitlines()
        cursor_at_end = self.view.textCursor().atEnd()
        kw = self.filter_edit.text().strip().lower()
        appended = False
        for line in lines:
            if kw and kw not in line.lower():
                continue
            self.view.append(logparse.line_html(line))
            appended = True
        if appended:
            self._trim_blocks()
            if self.autoscroll.isChecked() or cursor_at_end:
                self.view.moveCursor(QTextCursor.MoveOperation.End)

    def _trim_blocks(self):
        """只保留最近 MAX_LINES 行（删掉文档开头的多余块）。"""
        doc = self.view.document()
        extra = doc.blockCount() - (MAX_LINES + 1)
        if extra <= 0:
            return
        cursor = QTextCursor(doc)
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.movePosition(QTextCursor.MoveOperation.NextBlock,
                            QTextCursor.MoveMode.KeepAnchor, extra)
        cursor.removeSelectedText()

    def _open_file(self):
        path = self._log_path()
        if not path.exists():
            return
        try:
            os.startfile(str(path))
        except OSError as exc:
            InfoBar.warning("无法打开日志", str(exc), parent=self.window(),
                            position=InfoBarPosition.TOP_RIGHT, duration=4000)

    def _clear(self):
        if runner.is_running():
            InfoBar.warning("挂机运行中", "运行期间不能清空日志（master.ps1 正在写入），"
                            "请停止后再试",
                            parent=self.window(),
                            position=InfoBarPosition.TOP_RIGHT, duration=5000)
            return
        box = MessageBox("清空日志", "确定清空 master_log.txt 吗？此操作不可恢复。", self.window())
        box.yesButton.setText("清空")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        try:
            self._log_path().write_text("", encoding="utf-8")
        except OSError:
            return
        self._offset = 0
        self._pending = b""
        self._last_size = self._last_mtime = -1
        self.refresh()
