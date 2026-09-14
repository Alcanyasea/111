# -*- coding: utf-8 -*-
"""通用小组件：状态徽章 Pill、键值行 KV、带标题卡片 Card、圆角数字徽标 IconBadge。

macOS 风格约定（配色不变，只管形状与排版）：
- 卡片 12px 圆角 + 发丝描边（Apple 卡片那种若隐若现的轮廓）
- 按钮 8px 圆角、ghost 描边式；破坏性操作用黑字细描边，不用彩色
- 徽章 Pill 全圆角、中等字重
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
                               QVBoxLayout, QWidget, QSizePolicy)

from qfluentwidgets import BodyLabel, CardWidget, SubtitleLabel

import theme


class Pill(QLabel):
    """全圆角状态徽章：ok / run / fail / wait 四种配色，可带圆点。

    配色在每次 set_state 时从 theme 读取，主题切换（重建窗口）后自动生效。
    """

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._kind = "wait"
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.set_state("wait", text)

    @staticmethod
    def _styles():
        return {
            "ok": (theme.OK, theme.OK_TINT),
            "run": (theme.RUN, theme.RUN_TINT),
            "fail": (theme.PILL_FAIL_FG, theme.ERR),
            "wait": (theme.WAIT, theme.WAIT_TINT),
            # token 失效提醒：唯一的彩色徽章（用户要求「标红」提醒）
            "alert": (theme.PILL_ALERT_FG, theme.ALERT),
        }

    def set_state(self, kind, text):
        self._kind = kind
        self._apply_style()
        self.setText(text)

    def _apply_style(self):
        fg, bg = self._styles().get(self._kind, self._styles()["wait"])
        self.setStyleSheet(
            "QLabel { background: %s; color: %s; border-radius: 99px;"
            " padding: 2.5px 10px; %s font-family: %s; }"
            % (bg, fg, theme.font_stack("11px", "600"), theme.FONT_FAMILY)
        )


def dark_log_qss(selector="QPlainTextEdit"):
    """深色日志框统一样式：账号捕获 / MAA 更新 / 导出等实时输出框共用。"""
    return (
        "%s { background: %s; color: %s; font-family: %s;"
        " font-size: 12px; border: none; border-radius: %dpx;"
        " padding: 12px 14px; }"
        % (selector, theme.LOG_BG, theme.LOG_FG, theme.FONT_MONO,
           theme.RADIUS_CARD)
    )


def kv_row(key_text, value_widget, value_min_width=0, refs=False):
    """一行「键 … 值」，值右对齐。value_widget 可为 QWidget 或纯文本 str。

    refs=True 时返回 (row, key_label, value_label)，运行时要改文案的场景用；
    注意 key_text 不要传 QLabel：qfluentwidgets 会把非 str 参数当 parent 重载，
    键名会凭空消失。
    """
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    key = BodyLabel(key_text)
    key.setStyleSheet("color: %s; %s background: transparent;"
                      % (theme.TEXT_2, theme.font_stack(12.5)))
    lay.addWidget(key)
    lay.addStretch(1)
    if isinstance(value_widget, str):
        value_widget = BodyLabel(value_widget)
        value_widget.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    lay.addWidget(value_widget, 0, Qt.AlignmentFlag.AlignRight)
    if value_min_width:
        value_widget.setMinimumWidth(value_min_width)
    return (row, key, value_widget) if refs else row


def big_number(num_text, unit_text):
    """「22 分钟」样式的数字 + 单位。"""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    num = QLabel(num_text)
    num.setStyleSheet("font-family: %s; %s color: %s;"
                      % (theme.FONT_FAMILY, theme.font_stack(22, "700"), theme.TEXT))
    unit = QLabel(unit_text)
    unit.setStyleSheet("font-family: %s; %s color: %s;"
                       % (theme.FONT_FAMILY, theme.font_stack(12), theme.TEXT_2))
    unit.setAlignment(Qt.AlignmentFlag.AlignBottom)
    lay.addWidget(num)
    lay.addWidget(unit, 0, Qt.AlignmentFlag.AlignBottom)
    return w


class Card(CardWidget):
    """主题色卡片：可选标题 + 提示文字，内容用 add_widget 逐行加入。

    背景取 theme.CARD，12px 圆角 + 发丝描边（macOS 卡片质感），
    描边颜色每次绘制时读取，双主题下都保持若隐若现的轮廓。
    """

    def _normalBackgroundColor(self):
        return QColor(theme.CARD)

    def _hoverBackgroundColor(self):
        return QColor(theme.CARD)

    def _pressedBackgroundColor(self):
        return QColor(theme.CARD)

    def __init__(self, title=None, hint=None, parent=None):
        super().__init__(parent)
        self.setBorderRadius(theme.RADIUS_CARD)
        self.apply_shadow()
        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(theme.CARD_PAD, theme.CARD_PAD - 2,
                                     theme.CARD_PAD, theme.CARD_PAD)
        self.vbox.setSpacing(0)
        self.title_label = None
        self.hint_label = None
        if title:
            head = QHBoxLayout()
            head.setSpacing(8)
            t = SubtitleLabel(title)
            t.setStyleSheet("font-family: %s; %s color: %s; background: transparent;"
                            % (theme.FONT_FAMILY, theme.font_stack(15, "600"), theme.TEXT))
            _label_transparent(t)
            self.title_label = t
            head.addWidget(t)
            if hint:
                h = BodyLabel(hint)
                h.setStyleSheet("font-family: %s; %s color: %s;"
                                % (theme.FONT_FAMILY, theme.font_stack(12), theme.TEXT_3))
                self.hint_label = h
                head.addWidget(h, 0, Qt.AlignmentFlag.AlignBottom)
                head.addStretch(1)
            self.vbox.addLayout(head)
            self.vbox.addSpacing(14)

    def add_widget(self, w, spacing=8):
        self.vbox.addWidget(w)
        self.vbox.addSpacing(spacing)

    def apply_shadow(self):
        """iOS 式柔和投影：大模糊、低不透明度、向下偏移，让卡片从灰底上浮起。

        setGraphicsEffect 会替换（并删除）旧特效，账号拖动排序借用了
        透明度特效，落位后需要重新调用本方法恢复投影。
        """
        eff = QGraphicsDropShadowEffect(self)
        eff.setBlurRadius(30)
        eff.setOffset(0, 9)
        eff.setColor(QColor(0, 0, 0, 42))
        self.setGraphicsEffect(eff)

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(*theme.HAIRLINE), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1),
                          theme.RADIUS_CARD, theme.RADIUS_CARD)


class IconBadge(QLabel):
    """账号卡片左上角的圆角数字图标：主题灰阶渐变底、白色数字（squircle 感）。"""

    def __init__(self, char, parent=None):
        super().__init__(char, parent)
        self.setFixedSize(38, 38)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            "QLabel { background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            " stop:0 %s, stop:1 %s); color: #ffffff;"
            " font-family: %s; %s border-radius: 12px; }"
            % (theme.BADGE_TOP, theme.BADGE_BOTTOM,
               theme.FONT_FAMILY, theme.font_stack(17, "700"))
        )


def style_scroll_area(scroll):
    """滚动区统一样式：透明底 + 浅色细滚动条。

    两层处理：
    1. qfluentwidgets ScrollArea 的滚动条是自绘的 SmoothScrollBar
       （3px 常显悬浮条，不吃 QSS），默认黑色 45% 透明度，正好压在
       卡片右缘上像一条黑边 —— 这里把滑块改成低对比浅灰；
    2. 经典 QScrollBar 的 QSS 一并给出（弹窗里的普通滚动区会用到）。
    页面内容右侧再让出 12px（各页 root margins），滑块落在留白里。
    """
    delegate = getattr(scroll, "scrollDelagate", None)
    for name in ("vScrollBar", "hScrollBar"):
        bar = getattr(delegate, name, None) if delegate is not None else None
        handle = getattr(bar, "handle", None) if bar is not None else None
        if handle is not None:
            color = QColor(*theme.SCROLL_HANDLE_COLOR)
            handle.setLightColor(color)
            handle.setDarkColor(color)
    scroll.setStyleSheet(
        "QScrollArea { border: none; background: transparent; }"
        "QScrollArea > QWidget > QWidget { background: transparent; }"
        "QScrollBar:vertical { background: transparent; width: 8px;"
        " margin: 4px 2px 4px 0; }"
        "QScrollBar::handle:vertical { background: %s;"
        " border-radius: 3px; min-height: 40px; }"
        "QScrollBar::handle:vertical:hover { background: %s; }"
        "QScrollBar::sub-line:vertical, QScrollBar::add-line:vertical"
        " { height: 0; width: 0; }"
        "QScrollBar::sub-page:vertical, QScrollBar::add-page:vertical"
        " { background: transparent; }"
        % (theme.SCROLLBAR_HANDLE, theme.SCROLLBAR_HANDLE_HOVER)
    )
    scroll.viewport().setStyleSheet("background: transparent;")
    return scroll


def hline():
    """1px 发丝分隔线（弹窗、卡片内分区用）。"""
    line = QWidget()
    line.setFixedHeight(1)
    line.setStyleSheet("background: %s;" % theme.SEP)
    return line


def inset_row():
    """iOS 设置列表式内嵌行容器：深半档底色 + 6px 圆角。"""
    w = QWidget()
    w.setStyleSheet(
        "QWidget { background: %s; border-radius: %dpx; }"
        % (theme.ROW_INSET, theme.RADIUS_ROW))
    return w


def style_button(btn, kind="ghost", small=False):
    """统一按钮质感：8px 圆角、半透明底描边式（macOS push button 变体）。

    kind: ghost（常规）/ danger（破坏性：ERR 色文字 + 重描边）
    small: 日志工具栏等次级操作用小一号规格。
    颜色全部取自 theme 令牌，明亮/暗夜各自成套。
    """
    h = theme.BTN_H_SM if small else theme.BTN_H
    pad = "4px 12px" if small else "6px 16px"
    danger_border = ("rgba(255, 255, 255, 0.22)" if theme.is_dark()
                     else "rgba(0, 0, 0, 0.18)")
    border = danger_border if kind == "danger" else theme.BTN_BORDER
    color = theme.ERR if kind == "danger" else theme.BTN_FG
    weight = "600" if kind == "danger" else "500"
    btn.setStyleSheet(
        "PushButton { background: %s; color: %s;"
        " border: 1px solid %s; border-radius: %dpx; padding: %s;"
        " font-family: %s; %s }"
        "PushButton:hover { background: %s; }"
        "PushButton:pressed { background: %s; }"
        "PushButton:disabled { color: %s;"
        " border-color: %s; background: %s; }"
        % (theme.BTN_BG, color, border, theme.RADIUS_BTN, pad,
           theme.FONT_FAMILY, theme.font_stack(12.5 if small else 13, weight),
           theme.BTN_BG_HOVER, theme.BTN_BG_PRESSED,
           theme.BTN_DISABLED_FG, theme.BTN_DISABLED_BORDER, theme.BTN_DISABLED_BG)
    )
    btn.setMinimumHeight(h)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def style_primary_button(btn):
    """主按钮：主题强调色实底（macOS Accent button 的柔和灰阶版）。

    注意：实例 setStyleSheet 会整体替换 qfluentwidgets 的按钮 qss，
    所以这里必须写全背景/悬停/按下/禁用各态，不能只写增量。
    """
    btn.setStyleSheet(
        "PrimaryPushButton { background: %s; color: %s;"
        " border: none; border-radius: %dpx; padding: 6px 18px;"
        " font-family: %s; %s }"
        "PrimaryPushButton:hover { background: %s; }"
        "PrimaryPushButton:pressed { background: %s; }"
        "PrimaryPushButton:disabled { background: %s; color: %s; }"
        % (theme.PRIMARY_BG, theme.PRIMARY_FG, theme.RADIUS_BTN,
           theme.FONT_FAMILY, theme.font_stack(13, "600"),
           theme.PRIMARY_HOVER, theme.PRIMARY_PRESSED,
           theme.PRIMARY_DISABLED_BG, theme.PRIMARY_DISABLED_FG)
    )
    btn.setMinimumHeight(theme.BTN_H)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def _label_transparent(widget):
    """让 Fluent 文本标签透明，避免在灰卡片上画出底色方块。

    qfluentwidgets 的 FluentLabelBase 样式本身不设背景，但在带背景色
    样式表的父级里会把调色板 Window 色画出来，补一条 background 规则覆盖。
    """
    old = widget.styleSheet() or ""
    if "background: transparent" not in old:
        widget.setStyleSheet(old + "\nFluentLabelBase { background: transparent; }")


def set_switch_checked_gray(switch, text=None):
    """开关开启态从主题青色改为深灰，与灰底卡片风格统一。

    text 不为空时同步 on/off 文案：qfluentwidgets 的 SwitchButton 在切换
    状态时会用 onText/offText（默认 On/Off）覆盖 setText 的内容，只调
    setText 的话一开一关文字就会变回 On/Off。
    """
    switch.setCheckedIndicatorColor(theme.SWITCH_ON, theme.SWITCH_ON_DARK)
    if text is not None:
        switch.setOnText(text)
        switch.setOffText(text)
        switch.setText(text)
    return switch
