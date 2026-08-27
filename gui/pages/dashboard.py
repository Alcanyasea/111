# -*- coding: utf-8 -*-
"""仪表盘：3 账号卡片 + 班次计划 + 最近一次运行 + 连接状态。"""
import re
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from qfluentwidgets import (BodyLabel, InfoBar, InfoBarPosition, LineEdit,
                            ScrollArea, SwitchButton)

import config as appconfig
import theme
from core import adb, logparse, runner, scheduler
from widgets import Card, IconBadge, Pill, big_number, kv_row

LEGACY_LOG_NAMES = {"official1": "Official 1", "official2": "Official 2",
                    "bilibili": "Bilibili"}


def account_specs(cfg):
    """从 cfg 账号数组生成仪表盘卡片规格（顺序 = 运行顺序）。"""
    specs = []
    for i, a in enumerate(cfg.get("accounts", [])):
        server = a.get("server", "official")
        label = a.get("label") or ("账号 %d" % (i + 1))
        colors = (theme.ACCENT_B if server == "bilibili"
                  else (theme.ACCENT_O1 if i % 2 == 0 else theme.ACCENT_O2))
        legacy = LEGACY_LOG_NAMES.get(a.get("id", ""))
        specs.append({
            "key": a.get("id") or label,
            "name": label,
            "log_names": {label, legacy} if legacy else {label},
            "meta": ("MAA B服 · Bilibili 客户端" if server == "bilibili"
                     else "MAA 官服 · 槽位切号"),
            "char": str(i + 1),
            "colors": colors,
        })
    return specs

TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def fmt_ts(ts):
    """时间戳 → 「今天 04:02」/「08-25 16:00」。"""
    try:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ts
    if dt.date() == datetime.now().date():
        return "今天 " + dt.strftime("%H:%M")
    return dt.strftime("%m-%d %H:%M")


def _label(text, size="12.5px", weight="400", color=theme.TEXT):
    lab = QLabel(text)
    lab.setStyleSheet("font-size: %s; font-weight: %s; color: %s;" % (size, weight, color))
    return lab


def _set_big_num(big_widget, num):
    """更新 big_number 组件的数字（数字 label 是第一个子控件）。"""
    big_widget.findChild(QLabel).setText(str(num))


class AccountCard(Card):
    """单个账号状态卡片。"""

    def __init__(self, acc):
        super().__init__()
        self.acc = acc
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(IconBadge(acc["char"], acc["colors"]))
        name_box = QVBoxLayout()
        name_box.setSpacing(1)
        name_box.addWidget(_label(acc["name"], size="14.5px", weight="600"))
        name_box.addWidget(_label(acc["meta"], size="12px", color=theme.TEXT_2))
        head.addLayout(name_box)
        head.addStretch(1)
        self.pill = Pill()
        head.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignTop)
        self.vbox.addLayout(head)
        self.vbox.addSpacing(8)

        # kv1：今日耗时（大数字）或当前进度（文本）
        self.kv1_key = _label("今日耗时", color=theme.TEXT_2)
        self.kv1_val = _label("—")
        self.kv1_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.vbox.addWidget(kv_row(self.kv1_key, self.kv1_val))
        self.vbox.addSpacing(4)
        self.kv2_key = _label("最近运行", color=theme.TEXT_2)
        self.kv2_val = _label("—")
        self.kv2_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.vbox.addWidget(kv_row(self.kv2_key, self.kv2_val))

    def _set_kv1_big(self, num):
        self.kv1_val.setText(
            '<b style="font-size:20px;color:%s">%s</b>'
            ' <span style="font-size:12px;color:%s">分钟</span>'
            % (theme.TEXT, num, theme.TEXT_2)
        )

    def refresh(self, run, stage, enabled):
        """run: logparse.last_run() 结果；stage: current_stage() 结果（仅运行时非空）。"""
        if not enabled:
            self.pill.set_state("wait", "已禁用")
            self.kv1_key.setText("今日耗时")
            self.kv1_val.setText("—")
            self.kv2_key.setText("最近运行")
            self.kv2_val.setText("—")
            return

        if stage is not None and stage["account"] in self.acc["log_names"]:
            # 正在跑这个号
            self.pill.set_state("run", "运行中")
            self.kv1_key.setText("当前进度")
            self.kv1_val.setText(stage["stage"])
            self.kv2_key.setText("已用时间")
            elapsed = stage.get("elapsed_min")
            self.kv2_val.setText("%s 分钟" % elapsed if elapsed is not None else "—")
            return

        if stage is not None:
            # 别的号在跑：等待中
            self.pill.set_state("wait", "等待中")

        # 展示最近一次已完成的运行结果
        result = next((a for a in (run or {}).get("accounts", [])
                       if a["key"] == self.acc["key"]
                       or a["name"] == self.acc["name"]), None)
        self.kv1_key.setText("今日耗时")
        self.kv2_key.setText("最近运行")
        if result is None:
            self.pill.set_state("wait", "未运行")
            self.kv1_val.setText("—")
            self.kv2_val.setText("暂无记录")
            return
        if result.get("skipped"):
            self.pill.set_state("wait", "已跳过")
            self.kv1_val.setText("—")
            self.kv2_val.setText("已禁用")
            return
        today = (run["start"] or "").startswith(datetime.now().strftime("%Y-%m-%d"))
        if result["ok"]:
            self.pill.set_state("ok", "今日已完成" if today else "上次完成")
        else:
            self.pill.set_state("fail", "失败")
        if today:
            self._set_kv1_big(str(result["dur"]))
        else:
            self.kv1_val.setText("—")
        self.kv2_val.setText(fmt_ts(run["start"]) + (" ✓" if result["ok"] else " ✗"))


class ScheduleCard(Card):
    """班次计划：早晚班时间 + 开关，修改即保存并更新计划任务。"""

    def __init__(self, cfg):
        super().__init__("班次计划")
        self.cfg = cfg

        def make_row(label_text):
            lab = BodyLabel(label_text)
            lab.setStyleSheet("color: %s; font-size: 13px;" % theme.TEXT_2)
            lab.setFixedWidth(60)
            edit = LineEdit()
            edit.setFixedWidth(84)
            edit.setClearButtonEnabled(False)
            sw = SwitchButton()
            hint = _label("", size="12px", color=theme.TEXT_3)
            row = QHBoxLayout()
            row.setSpacing(10)
            row.addWidget(lab)
            row.addWidget(edit)
            row.addWidget(sw)
            row.addWidget(hint)
            row.addStretch(1)
            self.vbox.addLayout(row)
            self.vbox.addSpacing(10)
            return edit, sw, hint

        self.morning_edit, self.morning_sw, self.morning_hint = make_row("早班")
        self.evening_edit, self.evening_sw, self.evening_hint = make_row("晚班")
        self.vbox.addSpacing(2)

        self.next_val = _label("—")
        self.next_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.vbox.addWidget(kv_row("下次运行", self.next_val))
        self.vbox.addSpacing(4)
        self.task_val = _label("—")
        self.task_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.vbox.addWidget(kv_row("计划任务", self.task_val))

        self.refresh_from_cfg()
        self.morning_edit.editingFinished.connect(lambda: self._on_time("morning"))
        self.evening_edit.editingFinished.connect(lambda: self._on_time("evening"))
        self.morning_sw.checkedChanged.connect(self._on_switch)
        self.evening_sw.checkedChanged.connect(self._on_switch)

    def refresh_from_cfg(self):
        """从 cfg 回填控件（不改动文件）。"""
        for key, edit, sw, hint, hint_text in (
            ("morning", self.morning_edit, self.morning_sw, self.morning_hint, "完成后自动关机"),
            ("evening", self.evening_edit, self.evening_sw, self.evening_hint, "只关模拟器，不关机"),
        ):
            item = self.cfg["schedule"][key]
            edit.blockSignals(True)
            edit.setText(item["time"])
            edit.blockSignals(False)
            sw.blockSignals(True)
            sw.setChecked(item["enabled"])
            sw.blockSignals(False)
            hint.setText(hint_text + (" ✓" if item["enabled"] else ""))

    def refresh_scheduler(self, info):
        self.next_val.setText(scheduler.next_run_text(info))
        if not info.get("exists"):
            self.task_val.setText("未创建")
        elif not info.get("enabled"):
            self.task_val.setText("已禁用")
        else:
            self.task_val.setText("1 个任务 · %d 个触发 ✓" % len(info.get("times", [])))

    def _apply(self):
        appconfig.save(self.cfg)
        ok, msg = scheduler.apply(self.cfg)
        self.refresh_scheduler(scheduler.query())
        if ok:
            InfoBar.success("已更新计划任务", "", parent=self.window(),
                            position=InfoBarPosition.TOP_RIGHT, duration=2500)
        else:
            InfoBar.warning("配置已保存，但计划任务未更新", msg, parent=self.window(),
                            position=InfoBarPosition.TOP_RIGHT, duration=6000)

    def _on_time(self, key):
        edit = self.morning_edit if key == "morning" else self.evening_edit
        text = edit.text().strip()
        if not TIME_RE.match(text):
            InfoBar.warning("时间格式应为 HH:MM",
                            "已还原为 %s" % self.cfg["schedule"][key]["time"],
                            parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                            duration=3000)
            edit.blockSignals(True)
            edit.setText(self.cfg["schedule"][key]["time"])
            edit.blockSignals(False)
            return
        if text == self.cfg["schedule"][key]["time"]:
            return
        self.cfg["schedule"][key]["time"] = text
        self._apply()

    def _on_switch(self, checked):
        for key, sw in (("morning", self.morning_sw), ("evening", self.evening_sw)):
            if sw is self.sender():
                self.cfg["schedule"][key]["enabled"] = bool(checked)
                break
        self.refresh_from_cfg()
        self._apply()


class LastRunCard(Card):
    """最近一次运行汇总。"""

    def __init__(self, specs):
        super().__init__("最近一次运行", "")
        self.specs = specs
        self.account_rows = {}
        for acc in specs:
            pill = Pill("未运行")
            self.vbox.addWidget(kv_row(acc["name"], pill))
            self.vbox.addSpacing(6)
            self.account_rows[acc["key"]] = pill
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("border: none; border-top: 1px dashed %s;" % theme.BORDER)
        self.vbox.addWidget(line)
        self.vbox.addSpacing(10)
        self.total_num = big_number("—", "分钟")
        self.vbox.addWidget(kv_row("总耗时", self.total_num))
        self.vbox.addSpacing(4)
        self.emu_pill = Pill("—")
        self.vbox.addWidget(kv_row("模拟器", self.emu_pill))

    def refresh(self, run):
        if self.hint_label is not None:
            self.hint_label.setText("")
        if run is None:
            for pill in self.account_rows.values():
                pill.set_state("wait", "未运行")
            _set_big_num(self.total_num, "—")
            self.emu_pill.set_state("wait", "—")
            return
        if self.hint_label is not None:
            self.hint_label.setText(fmt_ts(run["start"]))
        for acc in self.specs:
            pill = self.account_rows[acc["key"]]
            result = next((a for a in run.get("accounts", [])
                           if a["key"] == acc["key"] or a["name"] == acc["name"]), None)
            if result is None:
                pill.set_state("wait", "未运行")
            elif result.get("skipped"):
                pill.set_state("wait", "已跳过")
            elif result["ok"]:
                pill.set_state("ok", "成功 · %s 分" % result["dur"])
            else:
                pill.set_state("fail", "超时 · %s 分" % result["dur"])
        total = run.get("total_min")
        _set_big_num(self.total_num, str(total) if total is not None else "—")
        if run.get("emulator_closed"):
            self.emu_pill.set_state("ok", "已自动关闭 ✓")
        elif run.get("final") is not None:
            self.emu_pill.set_state("wait", "未关闭")
        else:
            self.emu_pill.set_state("wait", "—")


class ConnectionCard(Card):
    """连接状态：模拟器 / ADB / MAA 进程 / RunDirectly 检查。"""

    def __init__(self):
        super().__init__("连接状态")
        self.mumu_pill = Pill("未启动")
        self.vbox.addWidget(kv_row("MuMu 模拟器", self.mumu_pill))
        self.vbox.addSpacing(6)
        self.adb_pill = Pill("未连接")
        self.vbox.addWidget(kv_row("ADB", self.adb_pill))
        self.vbox.addSpacing(6)
        self.maa_pill = Pill("未运行")
        self.vbox.addWidget(kv_row("MAA 进程", self.maa_pill))
        self.vbox.addSpacing(6)
        self.rd_val = _label("—")
        self.rd_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.rd_val.setTextFormat(Qt.TextFormat.RichText)
        self.vbox.addWidget(kv_row("MAA 自动运行配置", self.rd_val))

    def refresh(self, cfg):
        if adb.emulator_running():
            self.mumu_pill.set_state("ok", "已启动")
        else:
            self.mumu_pill.set_state("wait", "未启动")
        if adb.is_connected(cfg):
            self.adb_pill.set_state("ok", "可连接")
        else:
            self.adb_pill.set_state("wait", "未连接")
        if adb.maa_running():
            self.maa_pill.set_state("run", "运行中")
        else:
            self.maa_pill.set_state("wait", "未运行")

        rd = runner.check_run_directly(cfg)
        parts = []
        for label, key in (("官服", "official"), ("B服", "bilibili")):
            v = rd.get(key)
            if v is True:
                parts.append('<span style="color:%s">%s ✓</span>' % (theme.OK, label))
            elif v is False:
                parts.append('<span style="color:%s">%s ✗ RunDirectly 已关闭</span>' % (theme.ERR, label))
            else:
                parts.append('<span style="color:%s">%s 配置缺失</span>' % (theme.WARN, label))
        self.rd_val.setText(" · ".join(parts))


class DashboardPage(ScrollArea):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.view = QWidget()
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        root = QVBoxLayout(self.view)
        root.setContentsMargins(0, 16, 0, 16)
        root.setSpacing(16)

        self.acc_row = QHBoxLayout()
        self.acc_row.setSpacing(16)
        root.addLayout(self.acc_row)

        row = QHBoxLayout()
        row.setSpacing(16)
        self.schedule_card = ScheduleCard(cfg)
        row.addWidget(self.schedule_card, 1)
        self.last_card = LastRunCard(account_specs(cfg))
        row.addWidget(self.last_card, 1)
        root.addLayout(row)

        self.conn_card = ConnectionCard()
        root.addWidget(self.conn_card)
        root.addStretch(1)

        self.acc_cards = []
        self.acc_sig = None
        self.tick = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(5000)
        self.refresh()

    def _acc_sig(self):
        return tuple((a.get("id"), a.get("label"), bool(a.get("enabled")),
                      a.get("server")) for a in self.cfg.get("accounts", []))

    def _rebuild_accounts(self):
        """账号列表变化时重建卡片行（顺序/增删/改名）。"""
        specs = account_specs(self.cfg)
        while self.acc_row.count():
            item = self.acc_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.acc_cards = []
        for acc in specs:
            card = AccountCard(acc)
            self.acc_row.addWidget(card, 1)
            self.acc_cards.append(card)

    def refresh(self):
        self.tick += 1
        # 账号结构变化 → 重建卡片与「最近运行」行
        sig = self._acc_sig()
        if sig != self.acc_sig:
            self.acc_sig = sig
            self._rebuild_accounts()
            self.last_card.specs = account_specs(self.cfg)
        log_path = self.cfg["paths"]["log_file"]
        text = logparse.read_text(log_path)
        run = logparse.last_run(text)
        stage = logparse.current_stage(text) if runner.is_running() else None
        enabled_map = {a.get("id"): bool(a.get("enabled", True))
                       for a in self.cfg.get("accounts", [])}
        for card in self.acc_cards:
            card.refresh(run, stage, enabled_map.get(card.acc["key"], True))
        self.last_card.refresh(run)
        self.conn_card.refresh(self.cfg)
        # 计划任务状态 30 秒查一次（每次查询要拉起一个 powershell）
        if self.tick % 6 == 1:
            self.schedule_card.refresh_scheduler(scheduler.query())
        # 残留锁清理（上次运行中断）
        if runner.stale_lock() is not None:
            try:
                runner.LOCK_FILE.unlink()
            except OSError:
                pass
            InfoBar.warning("已清理残留锁文件", "上次运行可能被中断，本次可正常启动",
                            parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                            duration=5000)
