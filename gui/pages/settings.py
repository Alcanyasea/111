# -*- coding: utf-8 -*-
"""运行设置：程序路径 / 连接与超时 / 行为开关 / 数据清理。保存 → config.json + 计划任务同步。"""
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from qfluentwidgets import (BodyLabel, InfoBar, InfoBarPosition, LineEdit,
                            MessageBox, PrimaryPushButton, PushButton, ScrollArea,
                            SpinBox, SwitchButton)

import config as appconfig
import theme
from core import cleanup, maa_setup, runner, scheduler
from widgets import Card, set_switch_checked_gray

PATH_KEYS = (
    ("maa_official", "MAA 官服"),
    ("maa_bilibili", "MAA B 服"),
    ("adb", "ADB 程序"),
    ("cli", "MuMu CLI"),
)

def _row_label(text, width=96):
    lab = BodyLabel(text)
    lab.setStyleSheet("color: %s; font-size: 13px;" % theme.TEXT_2)
    lab.setFixedWidth(width)
    return lab


class SettingsPage(ScrollArea):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.view = QWidget()
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        # 视口透明化：露出的滚动区底色改用窗口灰底
        self.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.viewport().setStyleSheet("background: transparent;")
        root = QVBoxLayout(self.view)
        root.setContentsMargins(0, 16, 0, 16)
        root.setSpacing(16)

        # ---- 程序路径 ----
        self.path_card = Card("程序路径")
        self.path_edits = {}
        for key, label in PATH_KEYS:
            edit = LineEdit()
            edit.setClearButtonEnabled(False)
            browse = PushButton("浏览…")
            browse.setFixedWidth(76)
            browse.clicked.connect(lambda _=False, k=key: self._browse(k))
            row = QHBoxLayout()
            row.setSpacing(10)
            row.addWidget(_row_label(label))
            row.addWidget(edit, 1)
            row.addWidget(browse)
            self.path_card.vbox.addLayout(row)
            self.path_card.vbox.addSpacing(10)
            self.path_edits[key] = edit
        root.addWidget(self.path_card)

        # ---- 连接与超时 / 行为开关：固定上下两张通栏卡片，不随宽度重排 ----
        self.conn_card = Card("连接与超时")
        self.device_edit = LineEdit()
        self.device_edit.setClearButtonEnabled(False)
        self._card_row(self.conn_card, "ADB 地址", self.device_edit)
        self.maa_spin = SpinBox()
        self.maa_spin.setRange(1, 180)
        self._card_row(self.conn_card, "单号超时", self.maa_spin, "分钟（默认 30）")
        self.launch_spin = SpinBox()
        self.launch_spin.setRange(10, 600)
        self._card_row(self.conn_card, "启动等待", self.launch_spin, "秒（模拟器启动上限）")
        self.update_spin = SpinBox()
        self.update_spin.setRange(5, 360)
        self._card_row(self.conn_card, "更新等待上限", self.update_spin,
                       "分钟（游戏更新下载/安装最长等待，默认 90）")
        self.behavior_card = Card("行为开关")
        self.close_emu_sw = set_switch_checked_gray(SwitchButton())
        self._card_row(self.behavior_card, "完成后关模拟器", self.close_emu_sw)
        self.wait_update_sw = set_switch_checked_gray(SwitchButton())
        self._card_row(self.behavior_card, "游戏更新检测", self.wait_update_sw,
                       "检测到游戏更新时先等更新完成，再开始登录检测")
        shutdown_hint = BodyLabel(
            "每个启动时间的「关机」开关在仪表盘「班次计划」中设置（60 秒倒计时）")
        shutdown_hint.setStyleSheet("color: %s; font-size: 12px;" % theme.TEXT_3)
        shutdown_hint.setWordWrap(True)
        self.behavior_card.vbox.addWidget(shutdown_hint)
        self.behavior_card.vbox.addSpacing(10)
        acc_hint = BodyLabel("账号增删 / 启用 / 捕获请到「账号管理」页")
        acc_hint.setStyleSheet("color: %s; font-size: 12px;" % theme.TEXT_3)
        self.behavior_card.vbox.addWidget(acc_hint)
        root.addWidget(self.conn_card)
        root.addWidget(self.behavior_card)

        # ---- MAA 服务器配置 ----
        self.maa_setup_card = Card("MAA 服务器配置")
        hint = BodyLabel(
            "一键修正对应服务器 MAA 的关键配置（客户端类型 / ADB / 直接运行 / "
            "结束脚本 / 常用任务），目录缺失时自动从另一服复制一份。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: %s; font-size: 12px;" % theme.TEXT_3)
        self.maa_setup_card.vbox.addWidget(hint)
        self.maa_setup_card.vbox.addSpacing(10)
        setup_row = QHBoxLayout()
        setup_row.setSpacing(10)
        self.maa_official_btn = PushButton("配置官服 MAA")
        self.maa_official_btn.setToolTip("修正官服 MAA：客户端类型 Official、ADB、直接运行、结束脚本、常用任务")
        self.maa_official_btn.clicked.connect(lambda: self._on_maa_setup("official"))
        self.maa_bili_btn = PushButton("配置B服 MAA")
        self.maa_bili_btn.setToolTip("修正B服 MAA：客户端类型 Bilibili、ADB、直接运行、结束脚本、常用任务")
        self.maa_bili_btn.clicked.connect(lambda: self._on_maa_setup("bilibili"))
        setup_row.addWidget(self.maa_official_btn)
        setup_row.addWidget(self.maa_bili_btn)
        setup_row.addStretch(1)
        self.maa_setup_card.vbox.addLayout(setup_row)
        self.maa_setup_card.vbox.addSpacing(10)
        root.addWidget(self.maa_setup_card)

        # ---- MAA 更新（一键更新按钮在仪表盘；这里只放 Clash 代理配置）----
        self.upd_card = Card("MAA 更新")
        upd_hint = BodyLabel(
            "「仪表盘 → 一键更新」会依次更新两套 MAA（版本更新 + 资源更新，由 MAA "
            "启动时自动完成）。更新前自动启动 Clash 并把 MAA 下载代理指向它，"
            "全部结束后关闭 Clash 并恢复 MAA 原配置；更新前 Clash 已开着则复用，"
            "不会主动关闭。挂机运行或 MAA 正在打开时不能更新。")
        upd_hint.setWordWrap(True)
        upd_hint.setStyleSheet("color: %s; font-size: 12px;" % theme.TEXT_3)
        self.upd_card.vbox.addWidget(upd_hint)
        self.upd_card.vbox.addSpacing(10)
        self.use_vpn_sw = set_switch_checked_gray(SwitchButton())
        self._card_row(self.upd_card, "Clash 代理", self.use_vpn_sw,
                       "关闭后 MAA 更新直连下载（不推荐，GitHub 直连不稳）")
        self.vpn_edit = LineEdit()
        self.vpn_edit.setClearButtonEnabled(False)
        vpn_browse = PushButton("浏览…")
        vpn_browse.setFixedWidth(76)
        vpn_browse.clicked.connect(self._browse_vpn)
        vpn_row = QHBoxLayout()
        vpn_row.setSpacing(10)
        vpn_row.addWidget(_row_label("Clash 程序"))
        vpn_row.addWidget(self.vpn_edit, 1)
        vpn_row.addWidget(vpn_browse)
        self.upd_card.vbox.addLayout(vpn_row)
        self.upd_card.vbox.addSpacing(10)
        self.port_spin = SpinBox()
        self.port_spin.setRange(1024, 65535)
        self._card_row(self.upd_card, "代理端口", self.port_spin,
                       "Clash 混合端口（Clash Verge Rev 默认 7897）")
        self.upd_timeout = SpinBox()
        self.upd_timeout.setRange(5, 60)
        self._card_row(self.upd_card, "更新超时", self.upd_timeout,
                       "分钟（单套 MAA 下载+安装的等待上限）")
        root.addWidget(self.upd_card)

        # ---- 数据清理 ----
        self.clean_card = Card("数据清理")
        self.clean_auto_sw = set_switch_checked_gray(SwitchButton())
        self._card_row(self.clean_card, "自动清理", self.clean_auto_sw,
                       "控制台运行期间到期自动清理（挂机中不清理）")
        self.clean_interval = SpinBox()
        self.clean_interval.setRange(1, 30)
        self._card_row(self.clean_card, "清理间隔", self.clean_interval, "天（默认 7）")
        clean_row = QHBoxLayout()
        clean_row.setSpacing(10)
        clean_btn = PushButton("立即清理")
        clean_btn.clicked.connect(self.on_clean)
        clean_row.addWidget(_row_label("手动清理"))
        clean_row.addWidget(clean_btn)
        clean_row.addStretch(1)
        self.clean_card.vbox.addLayout(clean_row)
        self.clean_hint = BodyLabel("")
        self.clean_hint.setStyleSheet("color: %s; font-size: 12px;" % theme.TEXT_3)
        self.clean_card.vbox.addWidget(self.clean_hint)
        self.clean_card.vbox.addSpacing(10)
        root.addWidget(self.clean_card)

        # ---- 保存 ----
        self.action_card = Card()
        bar = QHBoxLayout()
        bar.setSpacing(10)
        save_btn = PrimaryPushButton("保存配置")
        save_btn.clicked.connect(self.on_save)
        reset_btn = PushButton("恢复默认")
        reset_btn.clicked.connect(self.on_reset)
        bar.addWidget(save_btn)
        bar.addWidget(reset_btn)
        bar.addStretch(1)
        hint = BodyLabel("保存后计划任务自动更新，无需重启")
        hint.setStyleSheet("color: %s; font-size: 12px;" % theme.TEXT_3)
        bar.addWidget(hint)
        self.action_card.vbox.addLayout(bar)
        root.addWidget(self.action_card)
        root.addStretch(1)

        self.load_from_cfg()

    def _card_row(self, card, label, widget, hint=None):
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(_row_label(label))
        row.addWidget(widget, 0)
        if hint:
            h = BodyLabel(hint)
            h.setStyleSheet("color: %s; font-size: 12px;" % theme.TEXT_3)
            row.addWidget(h)
        row.addStretch(1)
        card.vbox.addLayout(row)
        card.vbox.addSpacing(10)

    def _fill(self, source):
        for key, edit in self.path_edits.items():
            edit.setText(source["paths"].get(key, ""))
        self.device_edit.setText(source["paths"].get("device", ""))
        self.maa_spin.setValue(int(source["timeouts"].get("maa_min", 30)))
        self.launch_spin.setValue(int(source["timeouts"].get("launch_wait_sec", 120)))
        self.update_spin.setValue(int(source["timeouts"].get("game_update_min", 90)))
        self.close_emu_sw.setChecked(bool(source["behavior"].get("close_emulator", True)))
        self.wait_update_sw.setChecked(bool(source["behavior"].get("wait_game_update", True)))
        c = source.get("cleanup") or {}
        self.clean_auto_sw.setChecked(bool(c.get("auto", True)))
        self.clean_interval.setValue(int(c.get("interval_days", 7)))
        self._refresh_clean_hint()
        u = source.get("maa_update") or {}
        self.use_vpn_sw.setChecked(bool(u.get("use_vpn", True)))
        self.vpn_edit.setText(str(u.get("vpn_exe", "")))
        self.port_spin.setValue(int(u.get("proxy_port", 7897)))
        self.upd_timeout.setValue(int(u.get("timeout_min", 15)))

    def _refresh_clean_hint(self):
        self.clean_hint.setText(cleanup.last_run_text(self.cfg))

    def load_from_cfg(self):
        self._fill(self.cfg)

    def on_reset(self):
        self._fill(appconfig.DEFAULTS)
        InfoBar.info("已恢复默认值", "点击「保存配置」后生效",
                     parent=self.window(), position=InfoBarPosition.TOP_RIGHT, duration=3000)

    def on_save(self):
        for key, edit in self.path_edits.items():
            self.cfg["paths"][key] = edit.text().strip()
        self.cfg["paths"]["device"] = self.device_edit.text().strip()
        self.cfg["timeouts"]["maa_min"] = self.maa_spin.value()
        self.cfg["timeouts"]["launch_wait_sec"] = self.launch_spin.value()
        self.cfg["timeouts"]["game_update_min"] = self.update_spin.value()
        self.cfg["behavior"]["close_emulator"] = self.close_emu_sw.isChecked()
        self.cfg["behavior"]["wait_game_update"] = self.wait_update_sw.isChecked()
        c = self.cfg.setdefault("cleanup", {})
        c["auto"] = self.clean_auto_sw.isChecked()
        c["interval_days"] = self.clean_interval.value()
        u = self.cfg.setdefault("maa_update", {})
        u["use_vpn"] = self.use_vpn_sw.isChecked()
        u["vpn_exe"] = self.vpn_edit.text().strip()
        u["proxy_port"] = self.port_spin.value()
        u["timeout_min"] = self.upd_timeout.value()

        appconfig.save(self.cfg)
        ok, msg = scheduler.apply(self.cfg)
        if ok:
            InfoBar.success("配置已保存", "计划任务已同步更新",
                            parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                            duration=3000)
        else:
            InfoBar.warning("配置已保存，但计划任务未更新", msg,
                            parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                            duration=6000)

    def on_clean(self):
        if runner.is_running():
            InfoBar.warning("挂机运行中", "运行期间不能清理，请停止后再试",
                            parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                            duration=4000)
            return
        items = cleanup.scan(self.cfg)
        if not items:
            InfoBar.info("无需清理", "没有发现可清理的数据",
                         parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                         duration=3000)
            return
        total = sum(i["size"] for i in items)
        lines = []
        for it in items:
            lines.append("· %s（%s）" % (it["path"], cleanup.format_size(it["size"])))
        box = MessageBox(
            "清理数据",
            "将清理 %d 项，释放约 %s：\n\n%s\n\n"
            "不含账号登录数据（scripts\\accounts）。" % (
                len(items), cleanup.format_size(total), "\n".join(lines)),
            self)
        box.yesButton.setText("清理")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        freed, ok_count, fail_count = cleanup.perform(items)
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.cfg.setdefault("cleanup", {})["last_run"] = now
        appconfig.save(self.cfg)
        self._refresh_clean_hint()
        if fail_count:
            InfoBar.warning("清理完成", "释放 %s（%d 项），%d 项删除失败"
                            % (cleanup.format_size(freed), ok_count, fail_count),
                            parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                            duration=4000)
        else:
            InfoBar.success("清理完成", "释放 %s（%d 项）"
                            % (cleanup.format_size(freed), ok_count),
                            parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                            duration=4000)

    def _on_maa_setup(self, server):
        name = "官服" if server == "official" else "B服"
        client = "Official" if server == "official" else "Bilibili"
        box = MessageBox(
            "配置%s MAA" % name,
            "将检查并修正 %s MAA 的关键配置：\n\n"
            "· 客户端类型（%s）\n· ADB 路径与地址\n"
            "· RunDirectly（直接运行）\n· 结束脚本 signal_done.bat\n"
            "· 启用常用任务\n\n"
            "若该服 MAA 目录不存在，会自动从另一服复制一份再修正。\n"
            "修改前会先备份原配置文件。确定继续吗？" % (name, client),
            self.window())
        box.yesButton.setText("开始配置")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        ok, msg = maa_setup.apply_server_config(self.cfg, server)
        if ok:
            InfoBar.success("%s MAA 配置完成" % name, msg,
                            parent=self.window(),
                            position=InfoBarPosition.TOP_RIGHT, duration=6000)
        else:
            InfoBar.error("%s MAA 配置失败" % name, msg,
                          parent=self.window(),
                          position=InfoBarPosition.TOP_RIGHT, duration=8000)

    def _browse_vpn(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Clash 程序", "", "程序 (*.exe);;所有文件 (*.*)")
        if path:
            self.vpn_edit.setText(path)

    def _browse(self, key):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择程序", "", "程序 (*.exe);;所有文件 (*.*)")
        if path:
            self.path_edits[key].setText(path)
