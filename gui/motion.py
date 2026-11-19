"""短距离页面过渡和按钮按压反馈，共用系统动画偏好。"""
import ctypes
import sys

from PySide6.QtCore import (QEasingCurve, QEvent, QPoint, QPropertyAnimation,
                            QRectF, Qt, QVariantAnimation)
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QStackedWidget, QTabBar, QWidget

import theme


def motion_enabled():
    if sys.platform == "win32":
        enabled = ctypes.c_int()
        # SPI_GETCLIENTAREAANIMATION，跟随 Windows 辅助功能中的动画效果。
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
            return bool(enabled.value)
    return QApplication.isEffectEnabled(Qt.UIEffect.UI_General)


class _DepartingPage(QWidget):
    """只绘制离场页，鼠标和键盘始终交给当前页。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()
        self.snapshot = QPixmap()
        self.progress = 0.0
        self.distance = 0

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.translate(-round(self.distance * self.progress), 0)
        painter.fillRect(self.rect(), QColor(theme.BG))
        painter.drawPixmap(0, 0, self.snapshot)


class PageStack(QStackedWidget):
    """两页共用一条移动边缘，滑动期间文字始终清楚。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._slide = QPropertyAnimation(self)
        self._slide.setPropertyName(b"pos")
        self._slide.setDuration(560)
        self._slide.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._departing = _DepartingPage(self)
        self._slide.valueChanged.connect(self._advance_departure)
        self._slide.finished.connect(self._settle)

    def _advance_departure(self, pos):
        if self._departing.distance:
            self._departing.progress = 1 - (pos.x() - self.contentsRect().x()) / self._departing.distance
            self._departing.update()

    def _settle(self):
        self._slide.stop()
        self._departing.hide()
        self._departing.snapshot = QPixmap()
        self._departing.distance = 0
        target = self._slide.targetObject()
        if target is not None:
            target.move(self.contentsRect().topLeft())
            self._slide.setTargetObject(None)

    def setCurrentIndex(self, index):
        previous = self.currentIndex()
        if previous == index or not 0 <= index < self.count():
            return
        self._settle()
        animate = previous >= 0 and self.isVisible() and motion_enabled()
        if animate:
            page = self.currentWidget()
            ratio = page.devicePixelRatioF()
            snapshot = QPixmap(page.size() * ratio)
            snapshot.setDevicePixelRatio(ratio)
            snapshot.fill(QColor(theme.BG))
            page.render(snapshot, renderFlags=QWidget.RenderFlag.DrawChildren)
            self._departing.snapshot = snapshot
        super().setCurrentIndex(index)
        if self.currentIndex() != index or not animate:
            return
        origin = self.contentsRect().topLeft()
        distance = self.contentsRect().width() * (1 if index > previous else -1)
        self._departing.setGeometry(self.contentsRect())
        self._departing.distance = distance
        self._departing.progress = 0.0
        self._departing.show()
        self._departing.raise_()
        self._slide.setTargetObject(self.currentWidget())
        self._slide.setStartValue(origin + QPoint(distance, 0))
        self._slide.setEndValue(origin)
        self._slide.start()

    def resizeEvent(self, event):
        self._settle()
        super().resizeEvent(event)

    def hideEvent(self, event):
        self._settle()
        super().hideEvent(event)


class NavigationTabs(QTabBar):
    """选中下划线从当前位置滑到目标标签，文字高亮立即更新。"""

    def __init__(self):
        super().__init__()
        self._indicator = QVariantAnimation(self)
        self._indicator.setDuration(170)
        self._indicator.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._indicator.valueChanged.connect(lambda _: self.update())
        self._last_index = -1
        self.currentChanged.connect(self._move_indicator)

    def _line_rect(self, index):
        rect = self.tabRect(index)
        return QRectF(rect.center().x() - 7, rect.bottom() - 6, 14, 2)

    def _move_indicator(self, index):
        old = self._last_index
        self._last_index = index
        start = (self._indicator.currentValue() if self._indicator.state() ==
                 QVariantAnimation.State.Running else self._line_rect(old))
        self._indicator.stop()
        if old >= 0 and index >= 0 and self.isVisible() and motion_enabled():
            self._indicator.setStartValue(start)
            self._indicator.setEndValue(self._line_rect(index))
            self._indicator.start()
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.currentIndex() < 0:
            return
        rect = (self._indicator.currentValue() if self._indicator.state() ==
                QVariantAnimation.State.Running else self._line_rect(self.currentIndex()))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.PRIMARY_FG))
        painter.drawRoundedRect(rect, 1.5, 1.5)


class PressFeedback(QWidget):
    """按钮内的轻微明暗变化，松开后淡出；保留原按钮的布局、焦点和信号。"""

    def __init__(self, button):
        super().__init__(button)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()
        self._release = QVariantAnimation(self)
        self._release.setDuration(130)
        self._release.setStartValue(1.0)
        self._release.setEndValue(0.0)
        self._release.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._release.valueChanged.connect(lambda _: self.update())
        self._release.finished.connect(self.hide)
        button.pressed.connect(self._press)
        button.released.connect(self._lift)
        button.installEventFilter(self)

    def _press(self):
        self._release.stop()
        self._release.setCurrentTime(0)
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()

    def _lift(self):
        if self.isVisible() and motion_enabled():
            self._release.start()
        else:
            self.hide()

    def eventFilter(self, button, event):
        kind = event.type()
        if kind == QEvent.Type.Hide or (kind == QEvent.Type.EnabledChange and not button.isEnabled()):
            self._release.stop()
            self.hide()
        elif kind == QEvent.Type.Resize and self.isVisible():
            self.setGeometry(button.rect())
        return False

    def paintEvent(self, event):
        tint = QColor("#ffffff" if theme.is_dark() else "#152431")
        tint.setAlphaF(0.22 * self._release.currentValue())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(tint)
        radius = self.parentWidget().property("pressRadius") or 12
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), radius, radius)


def press_feedback(button):
    if not button.findChild(PressFeedback, options=Qt.FindChildOption.FindDirectChildrenOnly):
        PressFeedback(button)
    return button
