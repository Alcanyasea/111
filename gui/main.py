# -*- coding: utf-8 -*-
"""MAA 挂机控制台 — 主窗口。

PySide6 + PyQt-Fluent-Widgets 实现的桌面 GUI，设计见 mockup.html。
「脚本当引擎，界面当控制台」：master.ps1 / slot_switch.ps1 仍是执行主体，
计划任务照常直接调用，GUI 关闭不影响 4:00 / 16:00 自动挂机。

GUI 手动「立即运行」时传 -NoShutdown：手动运行不自动关机（无论成败）。
"""
import atexit
import os
import sys
import time
from datetime import datetime

from PySide6.QtCore import (QEasingCurve, QPoint, QParallelAnimationGroup,
                            QPropertyAnimation, Qt, QTimer)
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QGraphicsOpacityEffect,
                               QHBoxLayout, QLabel, QMenu, QSystemTrayIcon,
                               QVBoxLayout, QWidget)
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from qfluentwidgets import (FluentIcon, FluentWindow, InfoBar, InfoBarIcon,
                            InfoBarManager, InfoBarPosition, MessageBox,
                            PrimaryPushButton, PushButton, Theme, setTheme,
                            setThemeColor)

from widgets import BusyStrip, style_button, style_primary_button

import config as appconfig
import theme
from core import cleanup, logparse, poller, proc, runner, scheduler
from core import maa_update
from pages.accounts import AccountsPage
from pages.dashboard import DashboardPage
from pages.history import HistoryPage
from pages.logs import LogsPage
from pages.settings import SettingsPage
from widgets import Pill

PAGE_TITLES = ["仪表盘", "账号管理", "运行设置", "运行历史", "日志"]

# 当前主窗口引用（main() 里创建；主题切换就地换肤，不再重建窗口）
_win = None

# 关窗时仍在运行的后台线程引用（防 GC），线程随进程退出而非随窗口析构
_detached_threads = []

# 单实例保护：控制台同时跑两份时，各自持有加载时的配置快照，任何一次保存
# （切主题/自动清理记账/改班次……都会触发）都会把另一份实例刚保存的设置
# （推送密钥、账号改动等）整份覆盖回旧值。
# 机制：gui\console.lock 以 O_EXCL 原子创建 + PID 存活校验（与 master.ps1 的
# master.lock 同思路，无竞态；崩溃残留的锁因持有进程已死会被自动清掉）；
# 命名管道只负责「唤起已有实例的窗口」。--smoke 自检模式不受此限制。
_SINGLE_KEY = "MAA-Console-SingleInstance"
_LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "console.lock")
_single_server = None


def _read_lock_pid():
    try:
        with open(_LOCK_PATH, "r") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 0


def _raise_via_pipe():
    """尽力唤起已有实例的窗口（旧版本实例没有管道监听，连不上就静默忽略）。"""
    probe = QLocalSocket()
    probe.connectToServer(_SINGLE_KEY)
    if probe.waitForConnected(300):
        probe.disconnectFromServer()  # 已有实例收到新连接会自己 raise


def _acquire_single_instance():
    """返回 True = 本进程成为唯一实例；False = 已有实例在跑（已尽力唤起其窗口）。"""
    deadline = time.monotonic() + 6
    while True:
        try:
            fd = os.open(_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            return True
        except FileExistsError:
            pid = _read_lock_pid()
            name = (proc.process_name(pid) or "") if pid else ""
            if pid and name.startswith("python"):
                _raise_via_pipe()
                return False
            # 残留锁（持有进程已死 / PID 被无关进程复用 / 文件损坏）→ 清掉重抢
            try:
                os.remove(_LOCK_PATH)
            except OSError:
                pass
            if time.monotonic() >= deadline:
                return True  # 极端情况清不掉：放行，两份实例总好过控制台打不开
            time.sleep(0.25)
        except OSError:
            return True  # 锁文件系统级错误：不因单实例保护挡住控制台本身


def _release_single_instance():
    """退出时删掉自己的锁；锁里不是本进程 PID（已被他人持有）则不动。"""
    if _read_lock_pid() == os.getpid():
        try:
            os.remove(_LOCK_PATH)
        except OSError:
            pass


def _on_second_instance():
    """重复启动：管道有新连接 = 又有人启动了一次控制台，把主窗口带到前台。"""
    if _single_server is None:
        return
    while _single_server.hasPendingConnections():
        sock = _single_server.nextPendingConnection()
        if sock is not None:
            sock.disconnectFromServer()
    if _win is not None:
        _win.setWindowState((_win.windowState() & ~Qt.WindowState.WindowMinimized)
                            | Qt.WindowState.WindowActive)
        _win.showNormal()
        _win.raise_()
        _win.activateWindow()


def _export_threads_alive():
    """是否有已脱离窗口、仍在跑的导出线程（关窗时 closeEvent 转出的）。"""
    return any(w.isRunning() for w in _detached_threads)

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
        # 外层纵向布局：上行是标题/按钮行，底边压一条运行光带（空闲时隐藏）
        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 6, 2, 0)
        outer.setSpacing(5)
        lay = QHBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        self.title = QLabel("仪表盘")
        theme.bind(self.title, lambda: "font-family: %s; %s color: %s;"
                   " background: transparent;"
                   % (theme.FONT_FAMILY, theme.font_stack(24, "700"), theme.TEXT))
        lay.addWidget(self.title)
        self.chip = Pill("空闲")
        lay.addWidget(self.chip, 0, Qt.AlignmentFlag.AlignVCenter)
        self.detail = QLabel("")
        theme.bind(self.detail, lambda: "font-family: %s; %s color: %s;"
                   " background: transparent;"
                   % (theme.FONT_FAMILY, theme.font_stack(12.5), theme.TEXT_2))
        lay.addWidget(self.detail, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addStretch(1)
        self.stop_btn = style_button(PushButton("停止"))
        self.stop_btn.setToolTip(
            "停止当前挂机（结束 master.ps1 与 MAA 进程），\n"
            "并取消已排定的自动关机。空闲时不可用。")
        self.collect_btn = style_button(PushButton("基建收菜"))
        self.collect_btn.setToolTip(
            "对所有已启用账号只收基建：制造站产物 + 贸易站订单。\n"
            "全部房间 skip：不更换干员、不换班，不动班次计划。\n"
            "不跑理智/招募等任务，结束后自动恢复 MAA 配置。\n"
            "与挂机互斥，运行中不可用。")
        self.run_btn = style_primary_button(PrimaryPushButton("立即运行"))
        self.run_btn.setToolTip(
            "手动运行一次完整挂机流程（启动模拟器 → 切号 → 跑 MAA → 关模拟器）。\n"
            "手动运行不会自动关机（无论成败）。")
        lay.addWidget(self.stop_btn)
        lay.addWidget(self.collect_btn)
        lay.addWidget(self.run_btn)
        outer.addLayout(lay)
        # 运行中的动态光带：通栏贴在页头底边，空闲时隐藏不占空间
        self.busy = BusyStrip(self)
        outer.addWidget(self.busy)

    def update_state(self, running, detail):
        self.chip.set_state("run" if running else "ok", "运行中" if running else "空闲")
        self.detail.setText(detail)
        self.stop_btn.setEnabled(running)
        self.collect_btn.setEnabled(not running)
        self.run_btn.setEnabled(not running)
        if running:
            self.busy.start()
        else:
            self.busy.stop()


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.cfg = appconfig.load()
        self._task_info = None

        # 主题：明亮（暖雾灰）/ 暗夜（暮色深灰），读自 config.json
        theme_name = (self.cfg.get("appearance") or {}).get("theme", "light")
        theme.apply(theme_name)
        setTheme(Theme.DARK if theme.is_dark() else Theme.LIGHT)
        setThemeColor(theme.ACCENT)
        # 控制台整体底色：关掉 Win11 默认 Mica 背景后，窗口（含标题栏/侧栏区域）
        # 统一刷成主题 BG 色；明暗两套一次写全，切换主题时 qfluentwidgets 自选
        self.setMicaEffectEnabled(False)
        self.setCustomBackgroundColor(theme.BG_LIGHT, theme.BG_DARK)
        self._app_icon = make_icon()
        self.setWindowIcon(self._app_icon)
        self._init_tray()
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
        self.history_p = HistoryPage(self.cfg)
        self.logs_p = LogsPage(self.cfg)
        self.dash.setObjectName("dashboard")
        self.accounts_p.setObjectName("accounts")
        self.settings_p.setObjectName("settings")
        self.history_p.setObjectName("history")
        self.logs_p.setObjectName("logs")
        self.addSubInterface(self.dash, FluentIcon.HOME, "仪表盘")
        self.addSubInterface(self.accounts_p, FluentIcon.PEOPLE, "账号管理")
        self.addSubInterface(self.settings_p, FluentIcon.SETTING, "运行设置")
        self.addSubInterface(self.history_p, FluentIcon.HISTORY, "运行历史")
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
        self.header.collect_btn.clicked.connect(self.on_collect)
        self.header.stop_btn.clicked.connect(self.on_stop)
        self.accounts_p.export_requested.connect(self.on_export_account)
        self.accounts_p.switch_requested.connect(self.on_switch_account)
        self.accounts_p.start_requested.connect(self.on_start_account)
        self.dash.export_done.connect(self._on_export_done)
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
        """运行设置页切换外观：保存配置后就地换肤（窗口不重建、不重启）。"""
        if self.dash.update_running():
            InfoBar.warning("MAA 更新进行中", "请等更新结束后再切换外观",
                            parent=self, position=InfoBarPosition.TOP_RIGHT,
                            duration=4000)
            return False
        self.cfg.setdefault("appearance", {})["theme"] = name
        appconfig.save(self.cfg)
        QTimer.singleShot(250, lambda: self._switch_theme(name))   # 等下拉框动画走完
        return True

    def _switch_theme(self, name):
        """就地换肤：先让 qfluentwidgets 换掉它自己的配色，再重套自定义样式。

        顺序很重要：setTheme 会让 qfluentwidgets 对其控件重新套用自带样式
        （会覆盖 style_button 等写上去的自定义样式），所以 bind() 配方的
        重套必须放在 theme.apply() 里、走在 setTheme 之后，自定义样式才能
        最终生效。paintEvent 里实时读色的自绘控件（Card 描边 / BusyStrip /
        token 标红描边）靠全量 update() 触发重画；富文本里的主题色（耗时
        数字、RunDirectly 状态、结果圆点、历史摘要）靠页面刷新重新生成。
        """
        setTheme(Theme.DARK if name == "dark" else Theme.LIGHT)
        setThemeColor(theme.accent_of(name))
        theme.apply(name)          # 更新调色板 + 重套全部 bind() 配方
        # qfluentwidgets 卡片（CardWidget 等）的背景色缓存在 backgroundColor
        # 属性里，且只在 themeChanged 时重读——setTheme 那一刻调色板还是旧值，
        # 所以 apply 之后要把这批控件的背景色再刷一次，否则慢一拍
        for w in QApplication.allWidgets():
            if hasattr(w, "_updateBackgroundColor"):
                w._updateBackgroundColor()
            w.update()
        self.dash.refresh()
        self.history_p.refresh()

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

    def _init_tray(self):
        """托盘图标：单击/双击唤起主窗口，右键菜单「打开控制台 / 退出」。

        退出必须走 self.close()（触发 closeEvent 收尾清理），
        不能直接 QApplication.quit()——那会跳过关窗收尾。
        """
        self._tray = QSystemTrayIcon(self._app_icon, self)
        self._tray.setToolTip("MAA 挂机控制台")
        menu = QMenu()
        act_open = menu.addAction("打开控制台")
        act_open.triggered.connect(self._tray_raise)
        menu.addSeparator()
        act_quit = menu.addAction("退出")
        act_quit.triggered.connect(self.close)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _tray_raise(self):
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._tray_raise()

    def refresh_status(self):
        self._maybe_auto_clean()
        running = runner.is_running()
        if running:
            # 当前跑到哪个号、什么阶段（日志解析，内部带变更缓存）
            stage = logparse.snapshot(self.cfg["paths"]["log_file"])["stage"]
            if stage:
                detail = "当前：%s · %s" % (stage["account"], stage["stage"])
                if stage.get("elapsed_min") is not None:
                    detail += " · 已 %s 分钟" % stage["elapsed_min"]
                idx, total = stage.get("index"), stage.get("total")
                if idx and total and total > 1:
                    detail += " · 第 %d/%d 个" % (idx, total)
            else:
                detail = "启动中，日志页查看实时进度"
        else:
            if self._task_info is None:
                detail = "系统空闲 · 正在查询计划任务…"
            else:
                detail = "系统空闲 · 下次 %s" % scheduler.next_run_text(self._task_info)
        self.header.update_state(running, detail)
        self._tray.setToolTip("MAA 挂机控制台 · " + ("运行中" if running else "空闲"))

    def closeEvent(self, event):
        """关窗前收尾：更新线程先恢复配置再退，轮询线程停净，导出转后台。"""
        if self.dash.update_running():
            box = MessageBox(
                "MAA 更新进行中",
                "MAA 正在后台更新，现在退出会中止更新。\n\n"
                "退出前会等更新流程恢复 MAA 配置并关闭 Clash（最多约半分钟），"
                "确定退出吗？",
                self)
            box.yesButton.setText("仍要退出")
            box.cancelButton.setText("继续更新")
            if not box.exec():
                event.ignore()
                return
            self.dash.request_update_stop()
            self.dash.wait_update_worker(30000)
        for p in (self.poller, self.adb_poller):
            p.stop()
            # 不带超时：底层查询超时已压到 12 秒，等到位再退，
            # 避免 QThread 运行中被析构直接 abort
            p.wait()
        # 导出子进程独立于控制台存活：断开工作线程父级，关窗不中断导出，
        # 结果仍写入 exports\（实时日志窗口随主窗口关闭）
        detached = self.dash.detach_export_worker()
        if detached is not None:
            _detached_threads.append(detached)
        # token 自检线程（HTTP 探测）先等收尾；极端网络卡死时转后台，
        # 结果仍会写入槽位状态文件，下次打开控制台照常显示
        self.dash.wait_token_check(12000)
        token_w = self.dash.detach_token_worker()
        if token_w is not None:
            _detached_threads.append(token_w)
        event.accept()

    def on_run(self):
        if runner.is_running():
            return
        if self.dash.export_running() or _export_threads_alive():
            InfoBar.warning("干员导出进行中", "导出正在占用模拟器，请等导出结束后再运行挂机",
                            parent=self, position=InfoBarPosition.TOP_RIGHT,
                            duration=5000)
            return
        runner.clear_stale_lock()
        runner.start(self.cfg)
        InfoBar.success("已启动挂机流程", "日志页可查看实时进度",
                        parent=self, position=InfoBarPosition.TOP_RIGHT, duration=4000)
        self.refresh_status()

    def on_collect(self):
        """基建收菜：逐个已启用账号只收制造站/贸易站，不碰班次计划。"""
        if runner.is_running():
            return
        if self.dash.update_running():
            InfoBar.warning("MAA 更新进行中", "请等更新结束后再收菜",
                            parent=self, position=InfoBarPosition.TOP_RIGHT,
                            duration=4000)
            return
        if self.dash.export_running() or _export_threads_alive():
            InfoBar.warning("干员导出进行中", "导出正在占用模拟器，请等导出结束后再收菜",
                            parent=self, position=InfoBarPosition.TOP_RIGHT,
                            duration=5000)
            return
        enabled = [a for a in self.cfg.get("accounts", []) if a.get("enabled", True)]
        if not enabled:
            InfoBar.warning("没有可收菜的账号", "所有账号均已停用；请在「账号管理」启用至少一个",
                            parent=self, position=InfoBarPosition.TOP_RIGHT,
                            duration=5000)
            return
        runner.clear_stale_lock()
        runner.start_collect(self.cfg)
        InfoBar.success("已启动基建收菜", "日志页可查看实时进度",
                        parent=self, position=InfoBarPosition.TOP_RIGHT, duration=4000)
        self.refresh_status()

    def on_export_account(self, acc):
        """账号详情里点了「导出干员资料」：互斥检查 + 确认后只导这一个号。"""
        if runner.is_running():
            InfoBar.warning("挂机运行中", "请先停止挂机再导出干员资料",
                            parent=self, position=InfoBarPosition.TOP_RIGHT,
                            duration=4000)
            return
        if self.dash.update_running():
            InfoBar.warning("MAA 更新进行中", "更新会占用 MAA，请等更新结束后再导出",
                            parent=self, position=InfoBarPosition.TOP_RIGHT,
                            duration=4000)
            return
        if self.dash.export_running() or _export_threads_alive():
            InfoBar.info("导出进行中", "干员导出正在后台执行，请稍候",
                         parent=self, position=InfoBarPosition.TOP_RIGHT,
                         duration=4000)
            return
        name = acc.get("label") or acc.get("slot") or "?"
        if not acc.get("slot"):
            InfoBar.warning("无法导出「%s」" % name,
                            "该账号没有登录数据槽位，请先重新捕获登录数据",
                            parent=self, position=InfoBarPosition.TOP_RIGHT,
                            duration=5000)
            return
        box = MessageBox(
            "导出干员资料",
            "将为「%s」运行 MAA 干员识别（约 3 分钟）：\n\n"
            "切号 → 更新等待 → 登录校验 → 识别，结果写入 exports\\。\n"
            "期间会占用模拟器，不能同时运行挂机。\n确定开始吗？" % name, self)
        box.yesButton.setText("开始导出")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        self.accounts_p.set_export_busy(True)
        self.dash.start_export(acc)

    def on_switch_account(self, acc):
        """账号详情里点了「切换到此账号」：互斥检查 + 确认后只切号不跑日常。

        master.ps1 -SwitchTo <slot>：切号 + 登录校验完成即停，模拟器保持运行，
        不跑 MAA、不关机。与挂机/MAA 更新/干员导出互斥（都占用模拟器）。
        """
        if runner.is_running():
            InfoBar.warning("挂机运行中", "请先停止当前流程再切换账号",
                            parent=self, position=InfoBarPosition.TOP_RIGHT,
                            duration=4000)
            return
        if self.dash.update_running():
            InfoBar.warning("MAA 更新进行中", "请等更新结束后再切换账号",
                            parent=self, position=InfoBarPosition.TOP_RIGHT,
                            duration=4000)
            return
        if self.dash.export_running() or _export_threads_alive():
            InfoBar.warning("干员导出进行中", "导出正在占用模拟器，请等导出结束后再切换",
                            parent=self, position=InfoBarPosition.TOP_RIGHT,
                            duration=5000)
            return
        name = acc.get("label") or acc.get("slot") or "?"
        slot = acc.get("slot") or ""
        if not slot:
            InfoBar.warning("无法切换「%s」" % name,
                            "该账号没有登录数据槽位，请先捕获登录数据",
                            parent=self, position=InfoBarPosition.TOP_RIGHT,
                            duration=5000)
            return
        box = MessageBox(
            "切换到此账号",
            "将启动模拟器（如未运行）并切换到「%s」，完成登录校验后停下：\n\n"
            "· 不跑日常任务，结束后模拟器保持运行，可直接手动游戏\n"
            "· 期间占用模拟器，不能同时挂机/收菜/导出\n\n"
            "确定开始吗？" % name, self)
        box.yesButton.setText("开始切换")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        runner.clear_stale_lock()
        runner.start_switch(self.cfg, slot)
        InfoBar.success("已开始切换", "切到「%s」后模拟器保持运行；日志页可查看进度" % name,
                        parent=self, position=InfoBarPosition.TOP_RIGHT, duration=4000)
        self.refresh_status()

    def on_start_account(self, acc):
        """账号卡片上点了「快速启动」：互斥检查 + 确认后只推 token 不校验。

        master.ps1 -SwitchTo <slot> -NoLoginCheck：槽位登录数据（token）推入
        游戏并启动即停，跳过更新等待与登录校验（两者都在 login_check 内），
        模拟器保持运行，不跑 MAA、不关机不推送。互斥面与切换账号相同。
        """
        if runner.is_running():
            InfoBar.warning("流程运行中", "请先停止当前流程再快速启动账号",
                            parent=self, position=InfoBarPosition.TOP_RIGHT,
                            duration=4000)
            return
        if self.dash.update_running():
            InfoBar.warning("MAA 更新进行中", "请等更新结束后再快速启动账号",
                            parent=self, position=InfoBarPosition.TOP_RIGHT,
                            duration=4000)
            return
        if self.dash.export_running() or _export_threads_alive():
            InfoBar.warning("干员导出进行中", "导出正在占用模拟器，请等导出结束后再启动",
                            parent=self, position=InfoBarPosition.TOP_RIGHT,
                            duration=5000)
            return
        name = acc.get("label") or acc.get("slot") or "?"
        slot = acc.get("slot") or ""
        if not slot:
            InfoBar.warning("无法快速启动「%s」" % name,
                            "该账号没有登录数据槽位，请先捕获登录数据",
                            parent=self, position=InfoBarPosition.TOP_RIGHT,
                            duration=5000)
            return
        runner.clear_stale_lock()
        runner.start_switch_fast(self.cfg, slot)
        InfoBar.success("已开始快速启动", "「%s」登录数据推入后即可手动游戏；日志页可查看进度" % name,
                        parent=self, position=InfoBarPosition.TOP_RIGHT, duration=4000)
        self.refresh_status()

    def _on_export_done(self, ok, summary):
        self.accounts_p.set_export_busy(False)
        if ok:
            InfoBar.success("干员资料导出完成", summary,
                            parent=self, position=InfoBarPosition.TOP_RIGHT,
                            duration=8000)
        else:
            InfoBar.warning("干员资料导出未全部成功", summary,
                            parent=self, position=InfoBarPosition.TOP_RIGHT,
                            duration=10000)

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
    """窗口/任务栏图标：gui/app.ico（罗德岛徽记，白底黑三角白棋）。

    图标文件缺失时退回程序绘制的「M」渐变徽标（同 mockup .logo-badge）。
    """
    ico = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.ico")
    if os.path.exists(ico):
        return QIcon(ico)
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
    """通知条配色跟主题走（图标统一用 INFORMATION，不引入彩色）。

    明暗两套底色一次写全，主题切换后新弹出的通知条自动用对配色。
    """
    orig_new = InfoBar.new.__func__

    def _new(cls, icon, title, content, orient=Qt.Horizontal, isClosable=True,
             duration=1000, position=InfoBarPosition.TOP_RIGHT, parent=None):
        bar = orig_new(cls, InfoBarIcon.INFORMATION, title, content, orient,
                       isClosable, duration, position, parent)
        bar.setCustomBackgroundColor(theme.INFOBAR_BG_LIGHT, theme.INFOBAR_BG_DARK)
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
    # 显式声明应用身份（AUMID）：pythonw 裸跑时任务栏按钮默认用 exe 的
    # Python 图标；声明后任务栏/托盘/通知都改用 setWindowIcon 的图标
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "MAA.HangConsole.Gui")
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    # 单实例：抢不到锁 = 已有控制台在跑（对方收到管道连接会自己把窗口带到前台），
    # 本进程直接退出。--smoke 自检不参与单实例。
    if "--smoke" not in sys.argv and not _acquire_single_instance():
        return 0
    atexit.register(_release_single_instance)
    # 主题（明亮/暗夜）在 MainWindow.__init__ 里按配置应用
    _patch_gray_info_bar()
    _patch_gray_message_box()
    global _win, _single_server
    _single_server = QLocalServer()
    _single_server.listen(_SINGLE_KEY)
    _single_server.newConnection.connect(_on_second_instance)
    _win = MainWindow()
    _win.show()
    # MAA 更新途中控制台被强杀/断电时，临时配置修改（RunDirectly=false 等）
    # 不会被 finally 恢复，之后每轮挂机会等完成信号全部超时。启动时按恢复
    # 标记里的原值快照自动复原，事件写入 master_log（日志页可见）
    try:
        maa_update.recover_all(_win.cfg)
    except Exception:
        pass
    if appconfig.LAST_LOAD_WARNING:
        # 配置损坏已备份：必须让用户知道，避免误以为账号还在列表里
        box = MessageBox("配置文件异常", appconfig.LAST_LOAD_WARNING, _win)
        box.yesButton.setText("知道了")
        box.cancelButton.hide()
        box.exec()
    if "--smoke" in sys.argv:
        # 自检模式：加载所有页面后自动退出，供无交互验证
        QTimer.singleShot(1500, app.quit)
        app.exec()
        print("SMOKE OK")
        return 0
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
