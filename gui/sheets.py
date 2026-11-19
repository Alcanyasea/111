"""控制台浮层：每层拥有独立的对话框结果，遮罩只取消最上层。"""
from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout,
                               QPushButton, QScrollArea, QVBoxLayout)

import theme
from widgets import label, style_button, style_primary_button


def _dismiss_active_sheet(state):
    # 只查询一次活动模态框，避免关闭子层后同一次失焦又传给父层。
    active = QApplication.activeModalWidget()
    if isinstance(active, SheetDialog):
        active._app_state_changed(state)


class SheetDialog(QDialog):
    def __init__(self, parent=None, *, title="", subtitle="", size=(720, 480)):
        owner = parent.window() if parent is not None else None
        super().__init__(owner, Qt.Dialog | Qt.FramelessWindowHint)
        self._preferred = QSize(*size)
        self._pressed_outside = False
        self.setWindowTitle(title)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.panel = QFrame(self)
        self.panel.setObjectName("sheetPanel")
        theme.bind(self.panel, lambda: "QFrame#sheetPanel {background:%s; border:1px solid %s;"
                   "border-radius:20px;}" % (theme.CARD, theme.BORDER))
        column = QVBoxLayout(self.panel)
        column.setSpacing(0)
        column.setContentsMargins(0, 0, 0, 0)
        if title or subtitle:
            heading = QVBoxLayout()
            heading.setContentsMargins(24, 18, 24, 0)
            heading.setSpacing(3)
            for value, size_px, weight, color in ((title, 22, "700", "TEXT"),
                                                  (subtitle, 13, "400", "TEXT_2")):
                if value:
                    text = label(value, size_px, weight, color=color, transparent=True)
                    text.setTextFormat(Qt.PlainText)
                    text.setWordWrap(True)
                    heading.addWidget(text)
            column.addLayout(heading)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(24, 14, 24, 20)
        self.body.setSpacing(10)
        column.addLayout(self.body, 1)
        app = QApplication.instance()
        if not app.property("sheetDismissalConnected"):
            app.applicationStateChanged.connect(_dismiss_active_sheet)
            app.setProperty("sheetDismissalConnected", True)

    @property
    def _owner(self):
        # Qt 已管理父子生命周期；子层析构时不应再释放父窗口的最后一个 Python 强引用。
        return self.parentWidget()

    def _position_panel(self):
        size = self._preferred.boundedTo(QSize(max(1, self.width() - 32),
                                                max(1, self.height() - 32)))
        self.panel.resize(size)
        self.panel.move((self.width() - self.panel.width()) // 2,
                        (self.height() - self.panel.height()) // 2)

    def _follow_owner(self):
        if self._owner is not None:
            self.setGeometry(QRect(self._owner.mapToGlobal(QPoint()), self._owner.size()))
        else:
            screen = self.screen().availableGeometry()
            size = (self._preferred + QSize(80, 80)).boundedTo(screen.size())
            bounds = QRect(QPoint(), size)
            bounds.moveCenter(screen.center())
            self.setGeometry(bounds)
        self._position_panel()

    def showEvent(self, event):
        self._follow_owner()
        if self._owner is not None:
            self._owner.installEventFilter(self)
        super().showEvent(event)

    def hideEvent(self, event):
        self._pressed_outside = False
        if self._owner is not None:
            self._owner.removeEventFilter(self)
        super().hideEvent(event)

    def eventFilter(self, obj, event):
        if obj is self._owner and event.type() in (QEvent.Move, QEvent.Resize):
            self._follow_owner()
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_panel()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(18, 20, 32, 72 if isinstance(self._owner, SheetDialog) else 90))

    def mousePressEvent(self, event):
        self._pressed_outside = event.button() == Qt.LeftButton and not self.panel.geometry().contains(
            event.position().toPoint())
        event.accept()

    def mouseReleaseEvent(self, event):
        outside = self._pressed_outside and not self.panel.geometry().contains(event.position().toPoint())
        self._pressed_outside = False
        if outside and event.button() == Qt.LeftButton and QApplication.activeModalWidget() is self:
            self.reject()
        event.accept()

    def _app_state_changed(self, state):
        if (state == Qt.ApplicationInactive and self.isVisible()
                and QApplication.activeModalWidget() is self and not QApplication.activePopupWidget()):
            self.reject()


class MessageBoxBase(SheetDialog):
    """输入与确认共用底部操作区，只有明确确认才提交。"""
    def __init__(self, parent=None, *, title="", size=(460, 250)):
        super().__init__(parent, title=title, size=size)
        self.widget = self.panel
        self.viewLayout = QVBoxLayout()
        self.body.addLayout(self.viewLayout, 1)
        actions = QHBoxLayout()
        actions.addStretch()
        self.cancelButton = style_button(QPushButton("取消"))
        self.yesButton = style_primary_button(QPushButton("确定"))
        for button in (self.cancelButton, self.yesButton):
            button.setAutoDefault(False)
            actions.addWidget(button)
        self.body.addLayout(actions)
        self.cancelButton.clicked.connect(self.reject)
        self.yesButton.clicked.connect(self._confirm)

    def validate(self):
        return True

    def _confirm(self):
        if self.validate():
            self.accept()


class MessageBox(MessageBoxBase):
    def __init__(self, title, content, parent=None):
        super().__init__(parent, title=title, size=(470, 230))
        self.contentLabel = label(content, 15, color="TEXT", transparent=True)
        self.contentLabel.setTextFormat(Qt.PlainText)
        self.contentLabel.setWordWrap(True)
        self.contentLabel.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.contentLabel.setTextInteractionFlags(Qt.TextSelectableByMouse)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget {background:transparent;}")
        scroll.setWidget(self.contentLabel)
        self.viewLayout.addWidget(scroll)
        self._preferred.setHeight(max(178, min(440, self.contentLabel.heightForWidth(412) + 132)))
