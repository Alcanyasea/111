"""控制台主页与栏目导航。主页只展示运行状态，操作由主窗口接线。"""
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QTabBar, QVBoxLayout, QWidget)
from qfluentwidgets import FluentIcon, ScrollArea

import theme
from motion import NavigationTabs, PageStack, press_feedback
from widgets import label, style_scroll_area


def next_plan(*tasks):
    """同一时间优先显示日常班次，与计划栏的排列顺序一致。"""
    now = datetime.now()
    choices = [(info["next_run"], index) for index, info in enumerate(tasks)
               if info and info.get("exists") and info.get("enabled")
               and isinstance(info.get("next_run"), datetime) and info["next_run"] >= now]
    if choices:
        when, index = min(choices)
        return index, when
    return None


def navigation(titles, *, compact=False):
    tabs = NavigationTabs()
    tabs.setExpanding(False)
    tabs.setDrawBase(False)
    tabs.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    for title in titles:
        tabs.addTab(title)
    theme.bind(tabs, lambda: (
        "QTabBar {background:%s; border-radius:16px;} QTabBar::tab {background:transparent;"
        "color:%s; font-family:%s; font-size:%dpx; padding:%dpx %dpx;"
        "border-radius:13px; margin:3px;} QTabBar::tab:hover {background:%s;}"
        "QTabBar::tab:selected {background:%s; color:%s; font-weight:600;}"
        % (theme.mix(theme.CARD, theme.ACCENT, 0.08), theme.TEXT_2, theme.FONT_FAMILY,
           13 if compact else 15, 6 if compact else 8, 12 if compact else 17,
           theme.CARD, theme.PRIMARY_BG, theme.PRIMARY_FG)))
    return tabs


class Sidebar(QTabBar):
    """纵向栏目栏，用原生标签处理键盘、焦点和无障碍名称。"""

    def __init__(self, titles):
        super().__init__()
        self.setShape(QTabBar.Shape.RoundedWest)
        self.setExpanding(False)
        self.setDrawBase(False)
        self.setFixedWidth(90)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        for title in titles:
            self.addTab(title)
        icons = (FluentIcon.HOME, FluentIcon.PEOPLE, FluentIcon.CALENDAR,
                 FluentIcon.HISTORY, FluentIcon.SETTING)
        def colors():
            self.icons = [icon.icon(color=theme.TEXT_2) for icon in icons]
            self.active_icons = [icon.icon(color=theme.ACCENT) for icon in icons]
            self.update()
            return "QTabBar {background:transparent;}"
        theme.bind(self, colors)

    def tabSizeHint(self, index):
        return QSize(90, 72)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hover = self.tabAt(self.mapFromGlobal(self.cursor().pos())) if self.underMouse() else -1
        for i in range(self.count()):
            rect = self.tabRect(i).adjusted(9, 3, -9, -3)
            selected = i == self.currentIndex()
            if selected or i == hover:
                painter.setBrush(QColor(theme.mix(theme.CARD, theme.ACCENT, .14 if selected else .06)))
                painter.setPen(QPen(QColor(theme.ACCENT)) if selected and self.hasFocus() else Qt.NoPen)
                painter.drawRoundedRect(rect, 15, 15)
            icons = self.active_icons if selected else self.icons
            icons[i].paint(painter, QRect(rect.center().x()-10, rect.y()+10, 21, 21))
            painter.setPen(QColor(theme.ACCENT if selected else theme.TEXT_2))
            font = self.font()
            font.setPixelSize(13)
            font.setBold(selected)
            painter.setFont(font)
            painter.drawText(rect.adjusted(0, 33, 0, -5), Qt.AlignCenter, self.tabText(i))


class SectionPage(QWidget):
    def __init__(self, entries):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 14, 0, 0)
        self.tabs = navigation([title for title, _ in entries], compact=True)
        layout.addWidget(self.tabs, 0, Qt.AlignmentFlag.AlignLeft)
        self.stack = PageStack()
        for _, page in entries:
            self.stack.addWidget(page)
        self.tabs.currentChanged.connect(self.stack.setCurrentIndex)
        layout.addWidget(self.stack, 1)


def scroll_cards(*cards):
    scroll = ScrollArea()
    scroll.setWidgetResizable(True)
    style_scroll_area(scroll)
    body = QWidget()
    layout = QVBoxLayout(body)
    layout.setContentsMargins(12, 16, 12, 16)
    for card in cards:
        layout.addWidget(card)
    layout.addStretch()
    scroll.setWidget(body)
    return scroll


class ActivityCard(QFrame):
    def __init__(self, icon, title, action):
        super().__init__()
        self.setObjectName("activityCard")
        theme.bind(self, lambda: "QFrame#activityCard {background:%s; border-radius:18px; "
                   "border:1px solid %s;}" % (theme.CARD, theme.BORDER))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 13, 16, 14)
        layout.setSpacing(3)
        top = QHBoxLayout()
        mark = QLabel()
        def mark_color():
            mark.setPixmap(icon.icon(color=theme.ACCENT).pixmap(17, 17))
            return "background:transparent;"
        theme.bind(mark, mark_color)
        top.addWidget(mark)
        top.addWidget(label(title, 14, "600", color="TEXT_2"))
        top.addStretch()
        self.link = press_feedback(QPushButton(action + "  ›"))
        self.link.setProperty("pressRadius", 9)
        self.link.setCursor(Qt.PointingHandCursor)
        theme.bind(self.link, lambda: "QPushButton {color:%s; background:transparent; border:0;"
                   "border-radius:9px; padding:6px 8px;} QPushButton:hover,QPushButton:focus "
                   "{background:%s;} QPushButton:disabled {color:%s;}"
                   % (theme.ACCENT, theme.ROW_INSET, theme.TEXT_3))
        top.addWidget(self.link)
        layout.addLayout(top)
        self.time = label("—", 22, "600")
        self.detail = label("", 13, color="TEXT_2")
        self.detail.setTextFormat(Qt.PlainText)
        self.detail.setWordWrap(True)
        layout.addWidget(self.time)
        layout.addWidget(self.detail)


class ShortcutButton(QPushButton):
    """整块快捷指令就是按钮，绘制时只缩放表面，点击范围稳定。"""

    def __init__(self, text, hint, icon, art=None):
        super().__init__(text)
        self.hint = hint
        self.symbol = icon
        self.art = art
        self.setMinimumHeight(146)
        self.setMaximumHeight(205)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleDescription(hint)
        self.setProperty("pressRadius", 22)
        press_feedback(self)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        inset = 4 if self.isDown() else 1
        face = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        clip = QPainterPath()
        clip.addRoundedRect(face, 22, 22)
        painter.setClipPath(clip)
        if self.art is None:
            base = theme.mix(theme.ACCENT, "#222650", .43)
            end = theme.mix(base, "#858eff", .46)
            fg = "#ffffff"
        else:
            base, end, fg = (("#483a32", "#63503b", "#fff4e0") if theme.is_dark()
                             else ("#f5e1bb", "#eed1a2", "#47361e"))
        gradient = QLinearGradient(face.topLeft(), face.bottomRight())
        gradient.setColorAt(0, QColor(base))
        gradient.setColorAt(1, QColor(end))
        painter.fillPath(clip, gradient)
        if self.underMouse() and self.isEnabled():
            painter.fillPath(clip, QColor(255, 255, 255, 16))
        if not self.isEnabled():
            painter.fillPath(clip, QColor(96, 96, 106, 100))
        pad = inset + 22
        # 右侧插画或播放唱片始终给左侧文字留足位置。
        if self.art is not None:
            width = min(int(face.width() * .50), int(face.height() * 1.45))
            height = round(width * self.art.height() / max(1, self.art.width()))
            painter.drawPixmap(QRect(self.width()-width-6, (self.height()-height)//2, width, height),
                               self.art)
        else:
            r = min(face.height() * .60, face.width() * .24)
            cx, cy = face.right() - r * .8, face.center().y()
            painter.setPen(Qt.NoPen)
            for scale, alpha in ((1.18, 14), (.93, 17), (.68, 22), (.42, 42)):
                painter.setBrush(QColor(255, 255, 255, alpha))
                painter.drawEllipse(QRectF(cx-r*scale, cy-r*scale, 2*r*scale, 2*r*scale))
            self.symbol.icon(color=fg).paint(painter, QRect(int(cx)-12, int(cy)-12, 25, 25))
        if self.art is not None:
            self.symbol.icon(color=fg).paint(painter, QRect(pad, pad, 22, 22))
        painter.setPen(QColor(fg))
        font = self.font()
        font.setPixelSize(26)
        font.setWeight(QFont.DemiBold)
        painter.setFont(font)
        baseline = int(face.bottom())-45
        painter.drawText(pad, baseline, self.text())
        font.setPixelSize(14)
        font.setWeight(QFont.Normal)
        painter.setFont(font)
        painter.drawText(pad, baseline+23, self.hint)
        if self.hasFocus():
            painter.setPen(QPen(QColor(fg), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(face.adjusted(4, 4, -4, -4), 18, 18)


class HomePage(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 16)
        layout.setSpacing(0)
        self.heading = label("日常挂机", 38, "700")
        layout.addWidget(self.heading)
        self.description = label("", 16, color="TEXT_2")
        self.description.setTextFormat(Qt.PlainText)
        self.description.setWordWrap(True)
        layout.addSpacing(5)
        layout.addWidget(self.description)
        layout.addSpacing(20)
        buttons = QHBoxLayout()
        buttons.setSpacing(16)
        self.run_btn = ShortcutButton("立即运行", "按账号顺序执行日常任务", FluentIcon.PLAY_SOLID)
        self.run_btn.setToolTip("按账号顺序运行日常。手动运行结束后不自动关机。")
        blueprint = QPixmap(str(Path(__file__).resolve().parents[1] / "base-blueprint.png"))
        self.collect_btn = ShortcutButton("基建收菜", "制造站产物与贸易站订单", FluentIcon.LEAF, blueprint)
        self.collect_btn.setToolTip("对已启用的账号收取制造站产物和贸易站订单。")
        self.accounts_btn = ShortcutButton("添加第一个账号", "前往账号管理", FluentIcon.PEOPLE)
        self.progress_btn = ShortcutButton("查看进度", "打开本次运行日志", FluentIcon.DOCUMENT)
        for btn in (self.run_btn, self.collect_btn, self.accounts_btn, self.progress_btn):
            buttons.addWidget(btn, 1)
        layout.addLayout(buttons, 3)
        layout.addSpacing(22)
        activity = QHBoxLayout()
        activity.setSpacing(16)
        self.plan = ActivityCard(FluentIcon.CALENDAR, "下一班", "查看计划")
        self.history = ActivityCard(FluentIcon.HISTORY, "上一次", "查看详情")
        activity.addWidget(self.plan, 1)
        activity.addWidget(self.history, 1)
        layout.addLayout(activity)
        layout.addStretch(2)
        self.set_run_state([], False, "")

    def set_run_state(self, accounts, running, detail):
        enabled = sum(bool(a.get("enabled", True)) for a in accounts)
        self.heading.setText("正在运行" if running else "日常挂机")
        self.run_btn.setVisible(bool(enabled) and not running)
        self.collect_btn.setVisible(bool(enabled) and not running)
        self.progress_btn.setVisible(running)
        self.accounts_btn.setVisible(not running and not enabled)
        self.accounts_btn.setText("管理账号" if accounts else "添加第一个账号")
        if running:
            text = detail
        elif enabled:
            text = "已启用 %d 个账号，将按账号顺序运行。" % enabled
        elif accounts:
            text = "所有账号均已停用。启用账号后即可运行。"
        else:
            text = "添加游戏账号后，即可开始日常挂机。"
        self.description.setText(text)
