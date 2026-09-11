# -*- coding: utf-8 -*-
"""MAA 挂机控制台 — 主窗口。

PySide6 + PyQt-Fluent-Widgets 实现的桌面 GUI，设计见 mockup.html。
「脚本当引擎，界面当控制台」：master.ps1 / slot_switch.ps1 仍是执行主体，
计划任务照常直接调用，GUI 关闭不影响 4:00 / 16:00 自动挂机。

GUI 手动「立即运行」时传 -NoShutdown：手动运行即使全部成功也不自动关机。
"""
import sys
from datetime import datetime

from PySide6.QtCore import (QEasingCurve, QPoint, QParallelAnimationGroup,
                            QPropertyAnimation, Qt, QTimer)
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QGraphicsOpacityEffect,
                               QHBoxLayout, QLabel, QVBoxLayout, QWidget)

from qfluentwidgets import (FluentIcon, FluentWindow, InfoBar, InfoBarIcon,
                            InfoBarManager, InfoBarPosition, MessageBox,
                            PrimaryPushButton, PushButton, Theme, setTheme,
                            setThemeColor)

from widgets import style_button, style_primary_button

import config as appconfig
import theme
from core import cleanup, logparse, poller, runner, scheduler
from pages.accounts import AccountsPage
from pages.dashboard import DashboardPage
from pages.logs import LogsPage
from pages.settings import SettingsPage
from widgets import Pill

PAGE_TITLES = ["仪表盘", "账号管理", "运行设置", "日志"]

# 当前主窗口引用（主题切换时会整窗重建，运行设置页通过 swap_window 换窗）
_win = None


def swap_window():
    """用当前配置重建主窗口（主题切换用）：先建新窗再关旧窗，桌面不留空。"""
    global _win
    old = _win
    _win = MainWindow()
    _win.show()
    if old is not None:
        old._rebuilding = True   # 让 closeEvent 跳过「更新进行中」确认
        old.close()
        old.deleteLater()

# 右上角提示默认贴窗口顶边（y=24），会盖住标题栏关闭按钮，导致「点了没反应」；
# 把提示下移到顶部控制栏下方，不再遮挡任何按钮。
_top_right_mgr = InfoBarManager.managers.get(InfoBarPosition.TOP_RIGHT)
if _top_right_mgr is not None:
    _top_right_mgr.margin = 126


class HeaderBar(QWidget):
    """运行控制栏：macOS「大标题」式页头 —— 不再用卡片包框，
    大号页标题 + 状态徽章 + 次要说明靠左，停止/立即运行按钮靠右，
    与下方内容区之间留白分隔，整体像系统设置页的顶部。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("headerBar")
        self.setStyleSheet("QWidget#headerBar { background: transparent; }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 6, 2, 4)
        lay.setSpacing(12)
        self.title = QLabel("仪表盘")
        self.title.setStyleSheet(
            "font-family: %s; %s color: %s; background: transparent;"
            % (theme.FONT_FAMILY, theme.font_stack(24, "700"), theme.TEXT))
        lay.addWidget(self.title)
        self.chip = Pill("空闲")
        lay.addWidget(self.chip, 0, Qt.AlignmentFlag.AlignVCenter)
        self.detail = QLabel("")
        self.detail.setStyleSheet(
            "font-family: %s; %s color: %s; background: transparent;"
            % (theme.FONT_FAMILY, theme.font_stack(12.5), theme.TEXT_2))
        lay.addWidget(self.detail, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addStretch(1)
        self.stop_btn = style_button(PushButton("停止"))
        self.stop_btn.setToolTip(
            "停止当前挂机（结束 master.ps1 与 MAA 进程），\n"
            "并取消已排定的自动关机。空闲时不可用。")
        self.run_btn = style_primary_button(PrimaryPushButton("立即运行"))
        self.run_btn.setToolTip(
            "手动运行一次完整挂机流程（启动模拟器 → 切号 → 跑 MAA → 关模拟器）。\n"
            "手动运行即使成功也不会自动关机。")
        lay.addWidget(self.stop_btn)
        lay.addWidget(self.run_btn)

    def update_state(self, running, detail):
        self.chip.set_state("run" if running else "ok", "运行中" if running else "空闲")
        self.detail.setText(detail)
        self.stop_btn.setEnabled(running)
        self.run_btn.setEnabled(not running)

    def mousePressEvent(self, event):
        """按住页头空白处（除按钮外）即可拖动整个窗口。"""
        if event.button() == Qt.MouseButton.LeftButton:
            wnd = self.window().windowHandle()
            if wnd is not None:
                wnd.startSystemMove()
            event.accept()
            return
        super().mousePressEvent(event)

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
        self._rebuilding = False

        # 主题：明亮（暖雾灰）/ 暗夜（暮色深灰），读自 config.json
        theme_name = (self.cfg.get("appearance") or {}).get("theme", "light")
        theme.apply(theme_name)
        setTheme(Theme.DARK if theme.is_dark() else Theme.LIGHT)
        setThemeColor(theme.ACCENT)
        # 控制台整体底色：关掉 Win11 默认 Mica 背景后，窗口（含标题栏/侧栏区域）
        # 统一刷成主题 BG 色；Mica 开启时 setCustomBackgroundColor 不生效
        self.setMicaEffectEnabled(False)
        self.setCustomBackgroundColor(theme.BG, theme.BG)
        self.setWindowIcon(make_icon())
        self.setWindowTitle("MAA 挂机控制台")
        self.titleBar.setTitle("MAA 挂机控制台")
        # 标题栏关闭按钮默认悬停是红色，统一改成黑/灰系
        self.titleBar.closeBtn.setHoverBackgroundColor(QColor("#22252a"))
        self.titleBar.closeBtn.setPressedBackgroundColor(QColor("#3a3f46"))
        self.titleBar.closeBtn.setHoverColor(QColor("#ffffff"))
        self.titleBar.closeBtn.setPressedColor(QColor("#ffffff"))
        self.resize(1180, 780)
        # 侧边栏收窄：能完整显示「仪表盘/账号管理/运行设置/日志」即可
        # （必须先设宽度再 expand，expand 时按 expandWidth 定宽）
        self.navigationInterface.setExpandWidth(200)
        self.navigationInterface.expand(useAni=False)

        # 四个页面（objectName 是 addSubInterface 的路由键，不能为空）
        self.dash = DashboardPage(self.cfg)
        self.accounts_p = AccountsPage(self.cfg)
        self.settings_p = SettingsPage(self.cfg,
                                       on_theme_change=self._on_theme_change)
        self.logs_p = LogsPage(self.cfg)
        self.dash.setObjectName("dashboard")
        self.accounts_p.setObjectName("accounts")
        self.settings_p.setObjectName("settings")
        self.logs_p.setObjectName("logs")
        self.addSubInterface(self.dash, FluentIcon.HOME, "仪表盘")
        self.addSubInterface(self.accounts_p, FluentIcon.PEOPLE, "账号管理")
        self.addSubInterface(self.settings_p, FluentIcon.SETTING, "运行设置")
        self.addSubInterface(self.logs_p, FluentIcon.DOCUMENT, "日志")

        # 在内容区顶部插入大标题页头（透明背景，与内容区留白分隔）。
        # 注意：标题栏是悬浮在窗口顶部的覆盖层，内容区必须留出 48px 上边距
        # 让位，否则页头会钻到标题栏/关闭按钮下面（qfluentwidgets 布局特性）
        # stackedWidget 原本在 widgetLayout 里，先摘出来再包进新容器。
        self.widgetLayout.removeWidget(self.stackedWidget)
        self.header = HeaderBar()
        right = QWidget()
        v = QVBoxLayout(right)
        # 左右 44px 留白：卡片不贴窗口边，右侧还留出悬浮滚动条的位置
        v.setContentsMargins(44, 62, 44, 12)
        v.setSpacing(6)
        v.addWidget(self.header)
        v.addWidget(self.stackedWidget)
        self.hBoxLayout.addWidget(right, 1)
        # 原内容区布局已空，移除避免占位；新容器后加入会盖住标题栏，必须重新置顶
        self.hBoxLayout.removeItem(self.widgetLayout)
        self.titleBar.raise_()
        self.header.run_btn.clicked.connect(self.on_run)
        self.header.stop_btn.clicked.connect(self.on_stop)
        # 关闭 qfluentwidgets 自带的「向上弹出」切换动画，改用 iOS 式
        # 推入/推出过渡（_on_page_changed 里按切换方向滑入滑出）
        self.stackedWidget.setAnimationEnabled(False)
        self._prev_page = 0
        self._page_ani = None
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
        if index == self._prev_page:
            return
        self._ios_switch(self._prev_page, index)
        self._prev_page = index

    def _ios_switch(self, old_index, new_index):
        """iOS 式页面切换：新页按方向滑入并淡入，旧页反向视差滑出并淡出。

        参考 UINavigationController push/pop：前进时新页从右推入、旧页向左
        退后半屏并变暗；后退时镜像反向。曲线 OutCubic、330ms，接近 iOS
        手势转场的手感。快速连点时先立即收尾上一次过渡再开始新的。
        """
        sw = self.stackedWidget
        old_w, new_w = sw.widget(old_index), sw.widget(new_index)
        if old_w is None or new_w is None or old_w is new_w:
            return
        if self._page_ani is not None:
            self._page_ani.stop()      # stop 会触发 finished → 立即收尾
            self._page_ani = None

        rect = sw.rect()
        w, h = rect.width(), rect.height()
        if w <= 0 or h <= 0:
            return
        sign = 1 if new_index > old_index else -1

        old_w.setGeometry(rect)
        old_w.show()
        old_w.raise_()
        new_w.setGeometry(rect.translated(sign * w, 0))
        new_w.show()
        new_w.raise_()

        old_eff = QGraphicsOpacityEffect(old_w)
        old_w.setGraphicsEffect(old_eff)
        new_eff = QGraphicsOpacityEffect(new_w)
        new_w.setGraphicsEffect(new_eff)

        group = QParallelAnimationGroup(self)

        def _slide(target, start, end):
            ani = QPropertyAnimation(target, b"pos", group)
            ani.setStartValue(QPoint(start, 0))
            ani.setEndValue(QPoint(end, 0))
            return ani

        def _fade(eff, start, end):
            ani = QPropertyAnimation(eff, b"opacity", group)
            ani.setStartValue(start)
            ani.setEndValue(end)
            return ani

        slide_new = _slide(new_w, sign * w, 0)
        fade_new = _fade(new_eff, 0.30, 1.0)
        # 旧页只退 22%（视差），方向与新页相反；整体淡出让灰底透出
        slide_old = _slide(old_w, 0, int(-sign * w * 0.22))
        fade_old = _fade(old_eff, 1.0, 0.0)
        for ani in (slide_new, fade_new, slide_old, fade_old):
            ani.setDuration(330)
            ani.setEasingCurve(QEasingCurve.Type.OutCubic)
            group.addAnimation(ani)

        group.finished.connect(lambda: self._finish_switch(old_w, new_w))
        self._page_ani = group
        group.start()

    def _finish_switch(self, old_w, new_w):
        """过渡收尾：移除透明度特效、隐藏旧页并把两页几何归位。"""
        self._page_ani = None
        sw = self.stackedWidget
        for w in (old_w, new_w):
            try:
                w.setGraphicsEffect(None)
            except RuntimeError:
                return                 # 窗口/页面已销毁
        if sw.currentWidget() is not old_w:
            old_w.hide()
        rect = sw.rect()
        old_w.setGeometry(rect)
        new_w.setGeometry(rect)

    def _on_task_info(self, info):
        """后台线程返回的计划任务信息。"""
        self._task_info = info

    def _on_theme_change(self, name):
        """运行设置页切换外观：保存配置并整窗重建。返回 False 表示拒绝。"""
        if self.dash.update_running():
            InfoBar.warning("MAA 更新进行中", "请等更新结束后再切换外观",
                            parent=self, position=InfoBarPosition.TOP_RIGHT,
                            duration=4000)
            return False
        self.cfg.setdefault("appearance", {})["theme"] = name
        appconfig.save(self.cfg)
        QTimer.singleShot(250, swap_window)   # 等下拉框动画走完再换窗
        return True

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
        if not self._rebuilding and self.dash.update_running():
            box = MessageBox(
                "MAA 更新进行中",
                "MAA 正在后台更新，现在退出会中断更新：\n"
                "Clash 可能保持开启、MAA 代理配置可能未恢复。\n\n确定退出吗？",
                self)
            box.yesButton.setText("仍要退出")
            box.cancelButton.setText("继续更新")
            if not box.exec():
                event.ignore()
                return
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
    grad.setColorAt(0, QColor("#3f454d"))
    grad.setColorAt(1, QColor("#171a1f"))
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


def _patch_gray_info_bar():
    """通知条配色跟主题走（图标统一用 INFORMATION，不引入彩色）。"""
    orig_new = InfoBar.new.__func__

    def _new(cls, icon, title, content, orient=Qt.Horizontal, isClosable=True,
             duration=1000, position=InfoBarPosition.TOP_RIGHT, parent=None):
        bar = orig_new(cls, InfoBarIcon.INFORMATION, title, content, orient,
                       isClosable, duration, position, parent)
        bar.setCustomBackgroundColor(theme.INFOBAR_BG, theme.INFOBAR_BG)
        return bar

    InfoBar.new = classmethod(_new)


def _patch_gray_message_box():
    """MessageBox 统一成 macOS 式圆角浮窗：主题底色、发丝描边、按钮区同底色。"""
    orig_init = MessageBox.__init__

    def _init(self, title, content, parent=None):
        orig_init(self, title, content, parent)
        self.widget.setStyleSheet(
            "QFrame#centerWidget { background: %s; border: 1px solid %s;"
            " border-radius: 12px; }"
            "QFrame#buttonGroup { background: transparent; border: none;"
            " border-bottom-left-radius: 12px; border-bottom-right-radius: 12px; }"
            "QPushButton { border-radius: 6px; }"
            % (theme.CARD, theme.POP_BORDER))

    MessageBox.__init__ = _init


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    # 主题（明亮/暗夜）在 MainWindow.__init__ 里按配置应用
    _patch_gray_info_bar()
    _patch_gray_message_box()
    global _win
    _win = MainWindow()
    _win.show()
    if "--smoke" in sys.argv:
        # 自检模式：加载所有页面后自动退出，供无交互验证
        QTimer.singleShot(1500, app.quit)
        app.exec()
        print("SMOKE OK")
        return 0
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
