# -*- coding: utf-8 -*-
"""MAA 挂机控制台 — 主窗口。

PySide6 + PyQt-Fluent-Widgets 实现的桌面 GUI。
「脚本当引擎，界面当控制台」：master.ps1 / slot_switch.ps1 仍是执行主体，
计划任务照常直接调用，GUI 关闭不影响 4:00 / 16:00 自动挂机。

GUI 手动「立即运行」时传 -NoShutdown：手动运行不自动关机（无论成败）。
"""
import atexit
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QMenu,
                               QSystemTrayIcon, QVBoxLayout)
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from qfluentwidgets import (FluentWidget, InfoBar, InfoBarIcon, InfoBarManager,
                            InfoBarPosition, PushButton, Theme,
                            setTheme, setThemeColor)

from sheets import MessageBox
from widgets import label, refresh_card_shadows, style_button
from motion import PageStack

import config as appconfig
import theme
from core import cleanup, logparse, poller, proc, runner
from core import maa_update
from pages.accounts import AccountsPage
from pages.dashboard import AccountStatusPage, CollectScheduleCard, ScheduleCard
from pages.home import HomePage, SectionPage, Sidebar, next_plan, scroll_cards
from pages.history import HistoryPage
from pages.logs import LogsPage
from pages.settings import SettingsPage

PAGE_TITLES = ("主页", "账号", "计划", "记录", "设置")

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


class MainWindow(FluentWidget):
    def __init__(self):
        super().__init__()
        self.cfg = appconfig.load()
        self._adb_ok = None

        # 主题：明暗（明亮/暗夜）× 配色方案（暖雾灰/暖沙/青瓷/雾蓝/藕荷），读自 config.json
        appearance = self.cfg.get("appearance") or {}
        theme.apply(appearance.get("theme", "light"),
                    appearance.get("palette", theme.DEFAULT_PALETTE))
        setTheme(Theme.DARK if theme.is_dark() else Theme.LIGHT)
        setThemeColor(theme.ACCENT)
        # 控制台整体底色：关掉 Win11 默认 Mica 背景后，窗口（含标题栏/侧栏区域）
        # 统一刷成主题 BG 色；明暗两套一次写全，切换主题时 qfluentwidgets 自选
        self.setMicaEffectEnabled(False)
        self.setCustomBackgroundColor(theme.BG_LIGHT, theme.BG_DARK)
        self._app_icon = make_icon()
        self.setWindowIcon(self._app_icon)
        self._init_tray()
        self.setWindowTitle("明日方舟控制台")
        self._apply_close_btn_colors()
        self.resize(1180, 780)
        self.setMinimumSize(1000, 560)

        self.home = HomePage()
        self.accounts_p = AccountsPage(self.cfg)
        self.account_status = AccountStatusPage(self.cfg)
        self.accounts = SectionPage([("账号管理", self.accounts_p),
                                     ("运行状态", self.account_status)])
        self.schedule_card = ScheduleCard(self.cfg)
        self.collect_card = CollectScheduleCard(self.cfg)
        self.plans = SectionPage([("日常挂机", scroll_cards(self.schedule_card)),
                                  ("基建收菜", scroll_cards(self.collect_card))])
        self.history_p = HistoryPage(self.cfg)
        self.logs_p = LogsPage(self.cfg)
        self.records = SectionPage([("运行历史", self.history_p), ("实时日志", self.logs_p)])
        self.settings_p = SettingsPage(self.cfg, on_theme_change=self._on_theme_change)
        self.stack = PageStack()
        for page in (self.home, self.accounts, self.plans, self.records, self.settings_p):
            self.stack.addWidget(page)

        self.nav = Sidebar(PAGE_TITLES)
        self.nav.currentChanged.connect(self.stack.setCurrentIndex)
        self.chip = label("", 14, color="TEXT_2", transparent=True)
        self.stop_btn = style_button(PushButton("停止"), small=True)
        self.stop_btn.setToolTip("停止当前流程并取消已排定的自动关机")
        self.stop_btn.clicked.connect(self.on_stop)
        title = self.titleBar
        title.setObjectName("consoleTitle")
        title.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        theme.bind(title, lambda: "#consoleTitle {background:%s; border-bottom:1px solid %s;}"
                   % (theme.CARD, theme.BORDER))
        title.setFixedHeight(52)
        title.hBoxLayout.setContentsMargins(20, 0, 0, 0)
        title.iconLabel.setFixedSize(26, 26)
        title.iconLabel.setPixmap(self._app_icon.pixmap(26, 26))
        theme.bind(title.titleLabel, lambda: "font-size:16px; font-weight:600;"
                   " color:%s; padding-left:10px; background:transparent;" % theme.TEXT)
        title.hBoxLayout.insertWidget(3, self.chip)
        title.hBoxLayout.insertWidget(4, self.stop_btn)
        title.hBoxLayout.insertSpacing(5, 12)

        rail = QFrame()
        rail.setObjectName("sectionRail")
        theme.bind(rail, lambda: "QFrame#sectionRail {background:%s; border-right:1px solid %s;}"
                   % (theme.CARD, theme.BORDER))
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(0, 14, 0, 0)
        rail_layout.addWidget(self.nav, 0, Qt.AlignTop)
        rail_layout.addStretch()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, title.height(), 16, 0)
        layout.setSpacing(16)
        layout.addWidget(rail)
        layout.addWidget(self.stack, 1)
        title.raise_()
        self.home.run_btn.clicked.connect(self.on_run)
        self.home.collect_btn.clicked.connect(self.on_collect)
        self.home.accounts_btn.clicked.connect(self._open_accounts)
        self.home.plan.link.clicked.connect(self._open_plan)
        self.home.history.link.clicked.connect(self._open_last_record)
        self.home.progress_btn.clicked.connect(self._open_logs)
        self.accounts_p.export_requested.connect(self.on_export_account)
        self.accounts_p.switch_requested.connect(self.on_switch_account)
        self.accounts_p.start_requested.connect(self.on_start_account)
        self.accounts_p.task_requested.connect(self.on_account_task)
        self.account_status.export_done.connect(self._on_export_done)
        self.history_p.refreshed.connect(self._update_history_summary)
        self._update_history_summary()
        self.stack.currentChanged.connect(self._refresh_visible_page)

        # 状态轮询
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_status)
        self.timer.start(2000)
        # 后台轮询：计划任务 / ADB 检查不走界面线程，避免点击卡顿
        self.poller = poller.SchedulerPoller(self)
        self.poller.result.connect(self.schedule_card.refresh_scheduler)
        self.poller.collect_result.connect(self.collect_card.refresh_scheduler)
        self.adb_poller = poller.AdbPoller(self.cfg, self)
        self.adb_poller.result.connect(self._set_adb_state)
        self.poller.start()
        self.adb_poller.start()
        self.refresh_status()

    def _show_section(self, page, tab):
        page.tabs.setCurrentIndex(tab)
        self.nav.setCurrentIndex(self.stack.indexOf(page))

    def _open_accounts(self):
        self._show_section(self.accounts, 0)
        if not self.cfg.get("accounts"):
            self.accounts_p.add_btn.click()
        self.refresh_status()

    def _open_plan(self):
        self._show_section(self.plans, self._next_plan_index)

    def _open_last_record(self):
        self._show_section(self.records, 0)
        self.history_p.select_record(self._last_file)

    def _open_logs(self):
        self._show_section(self.records, 1)

    def _update_history_summary(self):
        self._last_file, when, text = self.history_p.latest_summary()
        self.home.history.time.setText(when)
        self.home.history.detail.setText(text)
        self.home.history.link.setEnabled(bool(self._last_file))

    def _update_plan_summary(self):
        tasks = (self.schedule_card, self.collect_card)
        next_task = next_plan(*(card.scheduler_info for card in tasks))
        self._next_plan_index = 0
        if next_task:
            self._next_plan_index, _ = next_task
            card = tasks[self._next_plan_index]
            when = card.next_val.text()
            detail = self.plans.tabs.tabText(self._next_plan_index)
        else:
            when = "查询中…" if any(card.scheduler_info is None for card in tasks) else "暂无班次"
            detail = ""
        self.home.plan.time.setText(when)
        self.home.plan.detail.setText(detail)

    def _refresh_visible_page(self, _index):
        if self.stack.currentWidget() is self.home:
            self.refresh_status()
        elif self.stack.currentWidget() is self.settings_p:
            self._refresh_connections()

    def _set_adb_state(self, ok):
        self._adb_ok = ok
        if self.stack.currentWidget() is self.settings_p:
            self._refresh_connections()

    def _refresh_connections(self):
        card = self.settings_p.status_card
        card.refresh(self.cfg, self._adb_ok)
        card.refresh_versions(self.cfg)

    def _on_theme_change(self, name, palette_name=None):
        """运行设置页切换外观（明暗主题 × 配色方案）：保存配置后就地换肤。"""
        appearance = self.cfg.setdefault("appearance", {})
        appearance["theme"] = name
        if palette_name is not None:
            appearance["palette"] = palette_name
        appconfig.save(self.cfg)
        # 等下拉框动画走完
        QTimer.singleShot(250, lambda: self._switch_theme(
            name, palette_name or appearance.get("palette")))
        return True

    def _switch_theme(self, name, palette_name=None):
        """就地换肤：先让 qfluentwidgets 换掉它自己的配色，再重套自定义样式。

        顺序很重要：setTheme 会让 qfluentwidgets 对其控件重新套用自带样式
        （会覆盖 style_button 等写上去的自定义样式），所以 bind() 配方的
        重套必须放在 theme.apply() 里、走在 setTheme 之后，自定义样式才能
        最终生效。paintEvent 里实时读色的自绘控件（Card 描边 / BusyStrip /
        token 标红描边）靠全量 update() 触发重画；富文本里的主题色（耗时
        数字、RunDirectly 状态、结果圆点、历史摘要）靠页面刷新重新生成。
        """
        setTheme(Theme.DARK if name == "dark" else Theme.LIGHT)
        setThemeColor(theme.accent_of(name, palette_name))
        theme.apply(name, palette_name or theme.palette_name())
        self.setCustomBackgroundColor(theme.BG_LIGHT, theme.BG_DARK)
        self._apply_close_btn_colors()
        # 投影色随背景色相派生：换配色后重建全部卡片特效
        refresh_card_shadows()
        # qfluentwidgets 卡片（CardWidget 等）的背景色缓存在 backgroundColor
        # 属性里，且只在 themeChanged 时重读——setTheme 那一刻调色板还是旧值，
        # 所以 apply 之后要把这批控件的背景色再刷一次，否则慢一拍
        for w in QApplication.allWidgets():
            if hasattr(w, "_updateBackgroundColor"):
                w._updateBackgroundColor()
            w.update()
        self.account_status.refresh()
        self.history_p.refresh()
        self._refresh_connections()

    def _apply_close_btn_colors(self):
        """标题栏关闭按钮悬停/按下配色：明暗两套观感（替代 qfluentwidgets 默认红）。"""
        if theme.is_dark():
            hover_bg, press_bg = QColor("#3a4048"), QColor("#464d56")
        else:
            hover_bg, press_bg = QColor("#22252a"), QColor("#3a3f46")
        self.titleBar.closeBtn.setHoverBackgroundColor(hover_bg)
        self.titleBar.closeBtn.setPressedBackgroundColor(press_bg)
        self.titleBar.closeBtn.setHoverColor(QColor("#ffffff"))
        self.titleBar.closeBtn.setPressedColor(QColor("#ffffff"))

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
            try:
                freed, ok_count, _ = cleanup.perform(items, self.cfg)
            except cleanup.CleanupBlockedError:
                return  # 不记清理时间，等下次空闲时重试。
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
                detail = "启动中，可查看实时日志"
        else:
            detail = ""
        self.home.set_run_state(self.cfg.get("accounts", []), running, detail)
        self.chip.setText('<span style="color:%s">●</span>  %s' %
                          (theme.RUN if running else theme.OK, "运行中" if running else "空闲"))
        self.chip.setToolTip(detail)
        self.stop_btn.setVisible(running)
        self._update_plan_summary()
        self._tray.setToolTip("MAA 挂机控制台 · " + ("运行中" if running else "空闲"))

    def closeEvent(self, event):
        """关窗前收尾：轮询线程停净，导出与 token 自检转后台。"""
        for p in (self.poller, self.adb_poller):
            p.stop()
            # 最多等 2 秒：底层查询虽已压到 12 秒，但网络/adb 卡死时不能
            # 让用户关不掉窗口；未退出的线程转后台（模块级列表保证不被
            # 析构，避免 QThread 运行中被销毁直接 abort）
            if not p.wait(2000):
                _detached_threads.append(p)
        # 导出子进程独立于控制台存活：断开工作线程父级，关窗不中断导出，
        # 结果仍写入 exports\（实时日志窗口随主窗口关闭）
        detached = self.account_status.detach_export_worker()
        if detached is not None:
            _detached_threads.append(detached)
        # token 自检线程（HTTP 探测）先等收尾；极端网络卡死时转后台，
        # 结果仍会写入槽位状态文件，下次打开控制台照常显示
        self.account_status.wait_token_check(12000)
        token_w = self.account_status.detach_token_worker()
        if token_w is not None:
            _detached_threads.append(token_w)
        event.accept()

    def on_run(self):
        if runner.is_running():
            return
        if self.account_status.export_running() or _export_threads_alive():
            InfoBar.warning("干员导出进行中", "导出正在占用模拟器，请等导出结束后再运行挂机",
                            parent=self, position=InfoBarPosition.TOP_RIGHT,
                            duration=5000)
            return
        runner.clear_stale_lock()
        runner.start(self.cfg)
        InfoBar.success("已启动挂机流程", "可在「记录 → 实时日志」查看进度",
                        parent=self, position=InfoBarPosition.TOP_RIGHT, duration=4000)
        self.refresh_status()

    def on_collect(self):
        """基建收菜：逐个已启用账号收制造站/贸易站产物并自动换休宿舍，不碰班次计划。"""
        if runner.is_running():
            return
        if self.account_status.export_running() or _export_threads_alive():
            InfoBar.warning("干员导出进行中", "导出正在占用模拟器，请等导出结束后再收菜",
                            parent=self, position=InfoBarPosition.TOP_RIGHT,
                            duration=5000)
            return
        enabled = [a for a in self.cfg.get("accounts", []) if a.get("enabled", True)]
        if not enabled:
            InfoBar.warning("没有可收菜的账号", "所有账号均已停用；请在「账号 → 账号管理」启用至少一个",
                            parent=self, position=InfoBarPosition.TOP_RIGHT,
                            duration=5000)
            return
        runner.clear_stale_lock()
        runner.start_collect(self.cfg)
        InfoBar.success("已启动基建收菜", "可在「记录 → 实时日志」查看进度",
                        parent=self, position=InfoBarPosition.TOP_RIGHT, duration=4000)
        self.refresh_status()

    def on_account_task(self, acc, collect):
        """卡片操作带稳定账号 ID，无需修改自动班次或启用状态。"""
        action = "收菜" if collect else "运行"
        name = acc.get("label") or acc.get("id") or "未命名账号"
        title = "无法为「%s」%s" % (name, action)
        if runner.is_running() or self.account_status.export_running() or _export_threads_alive():
            reason = "模拟器正被其他流程占用，请在当前流程结束后重试。"
        elif not acc.get("slot"):
            reason = "该账号没有登录槽位，请先捕获登录数据。"
        else:
            try:
                runner.start_account(self.cfg, acc.get("id"), collect=collect)
            except (ValueError, OSError) as exc:
                reason = str(exc)
            else:
                InfoBar.info("已提交「%s」的%s任务" % (name, action), "可在「记录 → 实时日志」查看进度",
                             parent=self, position=InfoBarPosition.TOP_RIGHT, duration=4000)
                self.refresh_status()
                return
        InfoBar.warning(title, reason, parent=self, position=InfoBarPosition.TOP_RIGHT,
                        duration=5000)

    def on_export_account(self, acc):
        """账号详情里点了「导出干员资料」：互斥检查 + 确认后只导这一个号。"""
        if runner.is_running():
            InfoBar.warning("挂机运行中", "请先停止挂机再导出干员资料",
                            parent=self, position=InfoBarPosition.TOP_RIGHT,
                            duration=4000)
            return
        if self.account_status.export_running() or _export_threads_alive():
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
        self.account_status.start_export(acc)

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
        if self.account_status.export_running() or _export_threads_alive():
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
        if self.account_status.export_running() or _export_threads_alive():
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

    图标文件缺失时退回程序绘制的「M」渐变徽标。
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
    global _win, _single_server
    _single_server = QLocalServer()
    _single_server.listen(_SINGLE_KEY)
    _single_server.newConnection.connect(_on_second_instance)
    try:
        _win = MainWindow()
    except appconfig.ConfigLoadError as exc:
        notice = MessageBox("无法安全读取配置", str(exc))
        notice.yesButton.setText("关闭")
        notice.cancelButton.hide()
        notice.exec()
        _single_server.close()
        _release_single_instance()
        return 1
    _win.show()
    # MAA 更新途中控制台被强杀/断电时，临时配置修改（RunDirectly=false 等）
    # 不会被 finally 恢复，之后每轮挂机会等完成信号全部超时。启动时按恢复
    # 标记里的原值快照自动复原，事件写入 master_log（日志页可见）
    try:
        maa_update.recover_all(_win.cfg)
    except Exception:
        pass
    # 当天首次启动把配置滚动备份一份（config.json.bak，只留最近一份完好
    # 完整记录；凌晨计划任务路径由插件读配置时触发同一函数）。完整性校验、
    # 原子替换都在 backup_daily 里，任何异常都不阻塞启动
    try:
        _plugins_dir = Path(__file__).resolve().parents[1] / "plugins"
        if str(_plugins_dir) not in sys.path:
            sys.path.insert(0, str(_plugins_dir))
        from common import backup_daily
        backup_daily(appconfig.CONFIG_PATH)
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
