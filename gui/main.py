# -*- coding: utf-8 -*-
"""MAA 挂机控制台 — 主窗口。

PySide6 + PyQt-Fluent-Widgets 实现的桌面 GUI，设计见 mockup.html。
「脚本当引擎，界面当控制台」：master.ps1 / slot_switch.ps1 仍是执行主体，
计划任务照常直接调用，GUI 关闭不影响 4:00 / 16:00 自动挂机。

GUI 手动「立即运行」时传 -NoShutdown：手动运行即使全部成功也不自动关机。
"""
import sys
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from qfluentwidgets import (FluentIcon, FluentWindow, InfoBar, InfoBarManager,
                            InfoBarPosition, MessageBox, PrimaryPushButton,
                            PushButton, Theme, setTheme)

import config as appconfig
import theme
from core import cleanup, logparse, poller, runner, scheduler
from pages.accounts import AccountsPage
from pages.dashboard import DashboardPage
from pages.logs import LogsPage
from pages.settings import SettingsPage
from widgets import Pill

PAGE_TITLES = ["仪表盘", "账号管理", "运行设置", "日志"]

# 右上角提示默认贴窗口顶边（y=24），会盖住标题栏关闭按钮，导致「点了没反应」；
# 把提示下移到顶部控制栏下方，不再遮挡任何按钮。
_top_right_mgr = InfoBarManager.managers.get(InfoBarPosition.TOP_RIGHT)
if _top_right_mgr is not None:
    _top_right_mgr.margin = 126


class HeaderBar(QWidget):
    """运行控制栏：页标题 + 状态提示 + 停止/立即运行，独立白卡样式，
    与窗口标题栏和内容区都有清晰间隔，不再贴边。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("controlBar")
        self.setStyleSheet(
            "QWidget#controlBar { background: %s; border: 1px solid %s;"
            " border-radius: 10px; }" % (theme.CARD, theme.BORDER))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 11, 16, 11)
        lay.setSpacing(12)
        self.title = QLabel("仪表盘")
        self.title.setStyleSheet(
            "font-size: 17px; font-weight: 600; color: %s; background: transparent;"
            % theme.TEXT)
        lay.addWidget(self.title)
        self.chip = Pill("空闲")
        lay.addWidget(self.chip)
        self.detail = QLabel("")
        self.detail.setStyleSheet(
            "font-size: 12.5px; color: %s; background: transparent;" % theme.TEXT_2)
        lay.addWidget(self.detail)
        lay.addStretch(1)
        self.stop_btn = PushButton("✕ 停止")
        self.stop_btn.setStyleSheet(
            "PushButton { color: %s; border: 1px solid #e5b7b1; background: #fff; }"
            "PushButton:hover { background: %s; }" % (theme.ERR, theme.ERR_TINT))
        self.run_btn = PrimaryPushButton("▶ 立即运行")
        lay.addWidget(self.stop_btn)
        lay.addWidget(self.run_btn)

    def update_state(self, running, detail):
        self.chip.set_state("run" if running else "ok", "运行中" if running else "空闲")
        self.detail.setText(detail)
        self.stop_btn.setEnabled(running)
        self.run_btn.setEnabled(not running)

    def mousePressEvent(self, event):
        """按住控制栏空白处（除按钮外）即可拖动整个窗口。"""
        if event.button() == Qt.MouseButton.LeftButton:
            wnd = self.window().windowHandle()
            if wnd is not None:
                wnd.startSystemMove()
            event.accept()
            return
        super().mousePressEvent(event)


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.cfg = appconfig.load()
        self._task_info = None

        setTheme(Theme.LIGHT)
        self.setWindowIcon(make_icon())
        self.setWindowTitle("MAA 挂机控制台")
        self.titleBar.setTitle("MAA 挂机控制台")
        self.resize(1180, 780)
        # 默认展开侧边栏（qfluentwidgets 初始会收起成 48px 图标模式）
        self.navigationInterface.expand(useAni=False)

        # 四个页面（objectName 是 addSubInterface 的路由键，不能为空）
        self.dash = DashboardPage(self.cfg)
        self.accounts_p = AccountsPage(self.cfg)
        self.settings_p = SettingsPage(self.cfg)
        self.logs_p = LogsPage(self.cfg)
        # 仪表盘班次卡片与运行设置页互相同步「关机」开关
        self.dash.schedule_card.settings_page = self.settings_p
        self.settings_p.dash_schedule = self.dash.schedule_card
        self.dash.setObjectName("dashboard")
        self.accounts_p.setObjectName("accounts")
        self.settings_p.setObjectName("settings")
        self.logs_p.setObjectName("logs")
        self.addSubInterface(self.dash, FluentIcon.HOME, "仪表盘")
        self.addSubInterface(self.accounts_p, FluentIcon.PEOPLE, "账号管理")
        self.addSubInterface(self.settings_p, FluentIcon.SETTING, "运行设置")
        self.addSubInterface(self.logs_p, FluentIcon.DOCUMENT, "日志")

        # 在内容区顶部插入运行控制栏（独立白卡，与标题栏/窗口边缘留间距）。
        # 注意：标题栏是悬浮在窗口顶部的覆盖层，内容区必须留出 48px 上边距
        # 让位，否则控制栏会钻到标题栏/关闭按钮下面（qfluentwidgets 布局特性）
        # stackedWidget 原本在 widgetLayout 里，先摘出来再包进新容器。
        self.widgetLayout.removeWidget(self.stackedWidget)
        self.header = HeaderBar()
        right = QWidget()
        v = QVBoxLayout(right)
        v.setContentsMargins(22, 60, 24, 0)
        v.setSpacing(0)
        v.addWidget(self.header)
        v.addWidget(self.stackedWidget)
        self.hBoxLayout.addWidget(right, 1)
        # 原内容区布局已空，移除避免占位；新容器后加入会盖住标题栏，必须重新置顶
        self.hBoxLayout.removeItem(self.widgetLayout)
        self.titleBar.raise_()
        self.header.run_btn.clicked.connect(self.on_run)
        self.header.stop_btn.clicked.connect(self.on_stop)
        self.stackedWidget.currentChanged.connect(self._on_page_changed)

        # 状态轮询
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_status)
        self.timer.start(2000)
        # 后台轮询：计划任务 / ADB 检查不走界面线程，避免点击卡顿
        self.poller = poller.SchedulerPoller(self)
        self.poller.result.connect(self._on_task_info)
        self.poller.result.connect(self.dash.schedule_card.refresh_scheduler)
        self.adb_poller = poller.AdbPoller(self.cfg, self)
        self.adb_poller.result.connect(self.dash.set_adb_state)
        self.poller.start()
        self.adb_poller.start()
        self.refresh_status()

    def _on_page_changed(self, index):
        if 0 <= index < len(PAGE_TITLES):
            self.header.title.setText(PAGE_TITLES[index])

    def _on_task_info(self, info):
        """后台线程返回的计划任务信息。"""
        self._task_info = info

    def _maybe_auto_clean(self):
        """自动清理到期检查（每 2 秒轮询中顺带执行，判断本身是纯字符串比较）。

        只在空闲时清理（挂机中删除锁文件/截断日志会干扰运行）；
        清理后立刻更新 last_run，避免本次会话内重复触发。
        """
        c = self.cfg.get("cleanup") or {}
        if not c.get("auto", True):
            return
        if runner.is_running() or not cleanup.is_due(self.cfg):
            return
        items = cleanup.scan(self.cfg)
        if items:
            freed, ok_count, _ = cleanup.perform(items)
            if ok_count:
                InfoBar.info("已自动清理", "清除 %d 项，释放 %s"
                             % (ok_count, cleanup.format_size(freed)),
                             parent=self, position=InfoBarPosition.TOP_RIGHT,
                             duration=4000)
        c["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        appconfig.save(self.cfg)

    def refresh_status(self):
        self._maybe_auto_clean()
        running = runner.is_running()
        if running:
            # 当前跑到哪个号、什么阶段（日志解析）
            text = logparse.read_text(self.cfg["paths"]["log_file"])
            stage = logparse.current_stage(text)
            if stage:
                detail = "当前：%s · %s" % (stage["account"], stage["stage"])
                if stage.get("elapsed_min") is not None:
                    detail += " · 已 %s 分钟" % stage["elapsed_min"]
            else:
                detail = "启动中，日志页查看实时进度"
        else:
            if self._task_info is None:
                detail = "系统空闲 · 正在查询计划任务…"
            else:
                detail = "系统空闲 · 下次 %s" % scheduler.next_run_text(self._task_info)
        self.header.update_state(running, detail)

    def closeEvent(self, event):
        """关窗前停掉后台轮询线程，避免 QThread 泄漏告警。"""
        for p in (self.poller, self.adb_poller):
            p.stop()
            p.wait(3000)
        event.accept()

    def on_run(self):
        if runner.is_running():
            return
        if runner.stale_lock() is not None:
            try:
                runner.LOCK_FILE.unlink()
            except OSError:
                pass
        runner.start(self.cfg)
        InfoBar.success("已启动挂机流程", "日志页可查看实时进度",
                        parent=self, position=InfoBarPosition.TOP_RIGHT, duration=4000)
        self.refresh_status()

    def on_stop(self):
        box = MessageBox(
            "停止运行",
            "确定要停止当前挂机流程吗？\n\n"
            "将结束 master.ps1 与 MAA 进程，并取消可能排定的自动关机。",
            self)
        box.yesButton.setText("停止")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        runner.stop()
        InfoBar.warning("已停止运行", "已取消排定的自动关机（如有）",
                        parent=self, position=InfoBarPosition.TOP_RIGHT, duration=4000)
        self.refresh_status()


def make_icon():
    """绘制「M」渐变徽标作为窗口图标（同 mockup .logo-badge）。"""
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    grad = QLinearGradient(0, 0, 64, 64)
    grad.setColorAt(0, QColor("#2f7fd1"))
    grad.setColorAt(1, QColor("#1e5fa8"))
    p.setBrush(grad)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(0, 0, 64, 64, 14, 14)
    p.setPen(QColor("#ffffff"))
    f = QFont("Segoe UI", 30)
    f.setBold(True)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "M")
    p.end()
    return QIcon(pm)


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    win = MainWindow()
    win.show()
    if "--smoke" in sys.argv:
        # 自检模式：加载所有页面后自动退出，供无交互验证
        QTimer.singleShot(1500, app.quit)
        app.exec()
        print("SMOKE OK")
        return 0
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
