# -*- coding: utf-8 -*-
"""运行设置：程序路径 / 连接与超时 / 行为开关。保存 → config.json + 计划任务同步。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from qfluentwidgets import (BodyLabel, InfoBar, InfoBarPosition, LineEdit,
                            PrimaryPushButton, PushButton, ScrollArea, SpinBox,
                            SwitchButton)

import config as appconfig
import theme
from core import scheduler
from widgets import Card

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

        # ---- 连接与超时 / 行为开关 ----
        row = QHBoxLayout()
        row.setSpacing(16)

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
        row.addWidget(self.conn_card, 1)

        self.behavior_card = Card("行为开关")
        self.close_emu_sw = SwitchButton()
        self._card_row(self.behavior_card, "完成后关模拟器", self.close_emu_sw)
        self.shutdown_sw = SwitchButton()
        self._card_row(self.behavior_card, "早班成功后关机", self.shutdown_sw, "60 秒倒计时")
        acc_hint = BodyLabel("账号增删 / 启用 / 捕获请到「账号管理」页")
        acc_hint.setStyleSheet("color: %s; font-size: 12px;" % theme.TEXT_3)
        self.behavior_card.vbox.addWidget(acc_hint)
        row.addWidget(self.behavior_card, 1)
        root.addLayout(row)

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
        self.close_emu_sw.setChecked(bool(source["behavior"].get("close_emulator", True)))
        self.shutdown_sw.setChecked(bool(source["behavior"].get("morning_shutdown", True)))

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
        self.cfg["behavior"]["close_emulator"] = self.close_emu_sw.isChecked()
        self.cfg["behavior"]["morning_shutdown"] = self.shutdown_sw.isChecked()

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

    def _browse(self, key):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择程序", "", "程序 (*.exe);;所有文件 (*.*)")
        if path:
            self.path_edits[key].setText(path)
