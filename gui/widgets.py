# -*- coding: utf-8 -*-
"""通用小组件：状态徽章 Pill、键值行 KV、带标题卡片 Card、渐变图标徽标 IconBadge。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget, QSizePolicy

from qfluentwidgets import BodyLabel, CardWidget, SubtitleLabel

import theme


class Pill(QLabel):
    """小圆角状态徽章：ok / run / fail / wait 四种配色，可带圆点。"""

    _STYLES = {
        "ok": (theme.OK, theme.OK_TINT),
        "run": (theme.RUN, theme.RUN_TINT),
        "fail": (theme.ERR, theme.ERR_TINT),
        "wait": (theme.WAIT, theme.WAIT_TINT),
    }

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.set_state("wait", text)

    def set_state(self, kind, text):
        fg, bg = self._STYLES.get(kind, self._STYLES["wait"])
        self.setStyleSheet(
            "QLabel { background: %s; color: %s; border-radius: 99px;"
            " padding: 3px 10px; font-size: 11.5px; font-weight: 600; }" % (bg, fg)
        )
        self.setText(text)


def kv_row(key_text, value_widget, value_min_width=0):
    """一行「键 … 值」，值右对齐。value_widget 可为 QWidget 或纯文本 str。"""
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    key = BodyLabel(key_text)
    key.setStyleSheet("color: %s; font-size: 12.5px;" % theme.TEXT_2)
    lay.addWidget(key)
    lay.addStretch(1)
    if isinstance(value_widget, str):
        value_widget = BodyLabel(value_widget)
        value_widget.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    lay.addWidget(value_widget, 0, Qt.AlignmentFlag.AlignRight)
    if value_min_width:
        value_widget.setMinimumWidth(value_min_width)
    return row


def big_number(num_text, unit_text):
    """「22 分钟」样式的数字 + 单位。"""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    num = QLabel(num_text)
    num.setStyleSheet("font-size: 20px; font-weight: 700; color: %s;" % theme.TEXT)
    unit = QLabel(unit_text)
    unit.setStyleSheet("font-size: 12px; color: %s;" % theme.TEXT_2)
    unit.setAlignment(Qt.AlignmentFlag.AlignBottom)
    lay.addWidget(num)
    lay.addWidget(unit, 0, Qt.AlignmentFlag.AlignBottom)
    return w


class Card(CardWidget):
    """白底卡片：可选标题 + 提示文字，内容用 add_widget 逐行加入。"""

    def __init__(self, title=None, hint=None, parent=None):
        super().__init__(parent)
        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(18, 18, 20, 18)
        self.vbox.setSpacing(0)
        self.title_label = None
        self.hint_label = None
        if title:
            head = QHBoxLayout()
            head.setSpacing(8)
            t = SubtitleLabel(title)
            self.title_label = t
            head.addWidget(t)
            if hint:
                h = BodyLabel(hint)
                h.setStyleSheet("color: %s; font-size: 12px;" % theme.TEXT_3)
                self.hint_label = h
                head.addWidget(h, 0, Qt.AlignmentFlag.AlignBottom)
                head.addStretch(1)
            self.vbox.addLayout(head)
            self.vbox.addSpacing(12)

    def add_widget(self, w, spacing=8):
        self.vbox.addWidget(w)
        self.vbox.addSpacing(spacing)


class IconBadge(QLabel):
    """账号卡片左上角的渐变圆角图标（字符 + 渐变底色）。"""

    def __init__(self, char, colors, parent=None):
        super().__init__(char, parent)
        self.setFixedSize(38, 38)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            "QLabel { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            " stop:0 %s, stop:1 %s); color: #fff; font-size: 18px;"
            " font-weight: 700; border-radius: 10px; }" % colors
        )
