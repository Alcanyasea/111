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

from widgets import BusyStrip, style_button, style_primary_button

import config as appconfig
import theme
from core import cleanup, logparse, poller, runner, scheduler
from pages.accounts import AccountsPage
from pages.dashboard import DashboardPage
from pages.history import HistoryPage
from pages.logs import LogsPage
from pages.settings import SettingsPage
from widgets import Pill

PAGE_TITLES = ["仪表盘", "账号管理", "运行设置", "运行历史", "日志"]

# 当前主窗口引用（主题切换时会整窗重建，运行设置页通过 swap_window 换窗）
_win = None

# 关窗时仍在运行的后台线程引用（防 GC），线程随进程退出而非随窗口析构
_detached_threads = []


def _export_threads_alive():
    """是否有已脱离旧窗口、仍在跑的导出线程（主题换窗时 closeEvent 转出的）。"""
    return any(w.isRunning() for w in _detached_threads)


def swap_window():
    """用当前配置重建主窗口（主题切换用）：先建新窗再关旧窗，桌面不留空。

    旧窗的位置/尺寸/最大化状态与当前所在页面会带到新窗：先落几何、
    落页面再 show，重建后窗口不跳回默认大小，也不闪回仪表盘。
    """
    global _win
    old = _win
    geo = old.geometry() if old is not None else None
    maximized = old.isMaximized() if old is not None else False
    page_index = old.stackedWidget.currentIndex() if old is not None else 0
    _win = MainWindow()
    if not maximized and geo is not None:
        _win.setGeometry(geo)
    # 先把 _prev_page 拨到目标页再 switchTo：currentChanged 触发
    # _on_page_changed 时因「页码未变」直接返回，重建落页不播页面过渡
    _win._prev_page = page_index
    _win.switchTo(_win.stackedWidget.widget(page_index))
    _win.show()
    if maximized:
        # 无边框窗口必须先 show 再最大化，直接 showMaximized 不会生效
        _win.showMaximized()
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
        # 外层纵向布局：上行是标题/按钮行，底边压一条运行光带（空闲时隐藏）
        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 6, 2, 0)
        outer.setSpacing(5)
        lay = QHBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
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
        self.collect_btn = style_button(PushButton("基建收菜"))
        self.collect_btn.setToolTip(
            "对所有已启用账号只收基建：制造站产物 + 贸易站订单。\n"
            "全部房间 skip：不更换干员、不换班，不动班次计划。\n"
            "不跑理智/招募等任务，结束后自动恢复 MAA 配置。\n"
            "与挂机互斥，运行中不可用。")
        self.run_btn = style_primary_button(PrimaryPushButton("立即运行"))
        self.run_btn.setToolTip(
            "手动运行一次完整挂机流程（启动模拟器 → 切号 → 跑 MAA → 关模拟器）。\n"
            "手动运行即使成功也不会自动关机。")
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

    def closeEvent(self, event):
        """关窗前收尾：更新线程先恢复配置再退，轮询线程停净，导出转后台。"""
        if not self._rebuilding and self.dash.update_running():
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
        names = "、".join(str(a.get("label") or a.get("id")) for a in enabled)
        box = MessageBox(
            "基建收菜",
            "将依次对 %d 个已启用账号（%s）只收取基建产物：\n\n"
            "· 切号 → 登录检查 → MAA 只进制造站/贸易站收产物与订单\n"
            "· 不换班：所有房间 skip，完全不更换干员，也不动班次计划\n"
            "· 不跑理智/招募/信用等任务\n"
            "· 结束后自动恢复 MAA 配置，模拟器照常关闭\n\n"
            "确定开始吗？" % (len(enabled), names),
            self)
        box.yesButton.setText("开始收菜")
        box.cancelButton.setText("取消")
        if not box.exec():
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
