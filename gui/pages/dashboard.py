# -*- coding: utf-8 -*-
"""仪表盘：3 账号卡片 + 班次计划 + 最近一次运行 + 连接状态。"""
import re
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QVBoxLayout, QWidget)

from qfluentwidgets import (BodyLabel, InfoBar, InfoBarPosition, LineEdit,
                            PushButton, ScrollArea, SwitchButton)

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
    """班次计划：启动时间列表（可增删改），每项可启用/关机，修改即保存并更新计划任务。"""

    def __init__(self, cfg):
        super().__init__("班次计划")
        self.cfg = cfg
        self._row_widgets = []
        self._edits = {}

        # 列标题：与下方每行控件同宽对齐（44 班次 / 68 时间 / 75 启用 / 75 关机 / 56 操作）
        head = QHBoxLayout()
        head.setSpacing(6)
        for text, w in (("班次", 44), ("时间", 68), ("启用", 75), ("关机", 75), ("操作", 56)):
            hlab = _label(text, size="12px", color=theme.TEXT_3)
            hlab.setFixedWidth(w)
            head.addWidget(hlab)
        head.addStretch(1)
        self.vbox.addLayout(head)
        self.vbox.addSpacing(4)

        self.rows_host = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_host)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(10)
        self.vbox.addWidget(self.rows_host)
        self.vbox.addSpacing(8)

        self.next_val = _label("—")
        self.next_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.vbox.addWidget(kv_row("下次运行", self.next_val))
        self.vbox.addSpacing(4)
        self.task_val = _label("—")
        self.task_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.vbox.addWidget(kv_row("计划任务", self.task_val))
        self.vbox.addSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.add_btn = PushButton("添加时间")
        self.add_btn.setToolTip(
            "新增一个启动时间点，加完后可修改为任意 HH:MM（如 08:00 / 00:00）。\n"
            "新时间立即写入计划任务。")
        self.add_btn.clicked.connect(self._on_add)
        bar.addWidget(self.add_btn)
        hint = _label("格式 HH:MM（00:00 即 24点）；增删改立即生效",
                      size="12px", color=theme.TEXT_3)
        bar.addWidget(hint)
        bar.addStretch(1)
        self.vbox.addLayout(bar)

        self.refresh_from_cfg()

    # ---------- 行构建 ----------

    def _entries(self):
        """schedule.times 列表（不存在则创建）。"""
        sched = self.cfg.setdefault("schedule", {})
        times = sched.get("times")
        if not isinstance(times, list):
            times = []
            sched["times"] = times
        return times

    def _clear_rows(self):
        for w in self._row_widgets:
            self.rows_layout.removeWidget(w)
            w.deleteLater()
        self._row_widgets = []
        self._edits = {}

    @staticmethod
    def _update_hint(entry, hint):
        parts = ["✓ 启用" if entry.get("enabled", True) else "停用"]
        parts.append("关机" if entry.get("shutdown", False) else "不关机")
        hint.setText(" · ".join(parts))

    def _make_row(self, entry):
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        lab = BodyLabel(appconfig.batch_name(entry["time"]))
        lab.setStyleSheet("color: %s; font-size: 13px;" % theme.TEXT_2)
        lab.setFixedWidth(44)
        edit = LineEdit()
        edit.setFixedWidth(68)
        edit.setClearButtonEnabled(False)
        edit.setText(entry["time"])
        edit.setToolTip("启动时间，HH:MM；00:00 显示为 24点")
        sw = SwitchButton()
        sw.setText("启用")
        sw.setFixedWidth(75)
        sw.setChecked(bool(entry.get("enabled", True)))
        sw.setToolTip(
            "开启：该时间点写入计划任务，到点自动开始挂机。\n"
            "关闭：该时间点不触发（保留在列表，随时可重新打开）。\n"
            "全部关闭时计划任务整体禁用。")
        shutdown_sw = SwitchButton()
        shutdown_sw.setText("关机")
        shutdown_sw.setFixedWidth(75)
        shutdown_sw.setChecked(bool(entry.get("shutdown", False)))
        shutdown_sw.setToolTip(
            "开启：该时间点运行成功后 60 秒自动关机（无需确认）。\n"
            "关闭：跑完保持开机。失败时一律不关机，只弹窗提示。\n"
            "手动点「立即运行」不受此开关影响，永不关机。")
        del_btn = PushButton("删除")
        del_btn.setFixedWidth(56)
        del_btn.setToolTip(
            "删除该时间点，计划任务中的对应触发立即移除。")
        del_btn.setStyleSheet(
            "PushButton { color: %s; border: 1px solid #e5b7b1; }" % theme.ERR)

        row.addWidget(lab)
        row.addWidget(edit)
        row.addWidget(sw)
        row.addWidget(shutdown_sw)
        row.addWidget(del_btn)
        row.addStretch(1)
        self.rows_layout.addWidget(row_widget)
        self._row_widgets.append(row_widget)
        self._edits[id(entry)] = edit

        edit.editingFinished.connect(lambda e=entry, ed=edit: self._on_time(e, ed))
        sw.checkedChanged.connect(lambda c, e=entry: self._on_enabled(e, c))
        shutdown_sw.checkedChanged.connect(lambda c, e=entry: self._on_shutdown(e, c))
        del_btn.clicked.connect(lambda _=False, e=entry: self._on_delete(e))
        return edit

    def refresh_from_cfg(self):
        """从 cfg 重建时间行（不改动文件）。"""
        self._clear_rows()
        for entry in self._entries():
            self._make_row(entry)

    # ---------- 交互 ----------

    def _sort_entries(self):
        self._entries().sort(key=lambda e: e.get("time", ""))

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

    def refresh_scheduler(self, info):
        self.next_val.setText(scheduler.next_run_text(info))
        if not info.get("exists"):
            self.task_val.setText("未创建")
        elif not info.get("enabled"):
            self.task_val.setText("已禁用")
        else:
            self.task_val.setText("1 个任务 · %d 个触发 ✓" % len(info.get("times", [])))

    def _on_time(self, entry, edit):
        text = edit.text().strip()
        if not TIME_RE.match(text):
            InfoBar.warning("时间格式应为 HH:MM",
                            "已还原为 %s" % entry["time"],
                            parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                            duration=3000)
            edit.blockSignals(True)
            edit.setText(entry["time"])
            edit.blockSignals(False)
            return
        if text == entry["time"]:
            return
        if any(e is not entry and e.get("time") == text for e in self._entries()):
            InfoBar.warning("时间重复", "已还原为 %s" % entry["time"],
                            parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                            duration=3000)
            edit.blockSignals(True)
            edit.setText(entry["time"])
            edit.blockSignals(False)
            return
        entry["time"] = text
        self._sort_entries()
        self._apply()
        self.refresh_from_cfg()

    def _on_enabled(self, entry, checked):
        entry["enabled"] = bool(checked)
        self._apply()  # 触发列表变化，需同步计划任务
        self.refresh_from_cfg()

    def _on_shutdown(self, entry, checked):
        """某时间点「关机」开关：立即保存（无需同步计划任务）。"""
        entry["shutdown"] = bool(checked)
        appconfig.save(self.cfg)
        self.refresh_from_cfg()

    def _on_delete(self, entry):
        times = self._entries()
        if entry in times:
            times.remove(entry)
        self._apply()
        self.refresh_from_cfg()

    def _on_add(self):
        times = self._entries()
        used = {e.get("time") for e in times}
        default = None
        for mins in range(8 * 60, 8 * 60 + 24 * 60, 30):
            hh, mm = divmod(mins % 1440, 60)
            cand = "%02d:%02d" % (hh, mm)
            if cand not in used:
                default = cand
                break
        if default is None:
            default = "08:00"
        entry = {"time": default, "enabled": True, "shutdown": False}
        times.append(entry)
        self._sort_entries()
        self._apply()
        self.refresh_from_cfg()
        new_edit = self._edits.get(id(entry))
        if new_edit is not None:
            new_edit.setFocus()
            new_edit.selectAll()


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

    def refresh(self, cfg, adb_ok):
        if adb.emulator_running():
            self.mumu_pill.set_state("ok", "已启动")
        else:
            self.mumu_pill.set_state("wait", "未启动")
        if adb_ok is None:
            self.adb_pill.set_state("wait", "检查中…")
        elif adb_ok:
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

        self.acc_grid = QGridLayout()
        self.acc_grid.setSpacing(16)
        root.addLayout(self.acc_grid)

        # 中间两个卡片：宽窗口左右并排，窄窗口上下堆叠
        self.mid_vbox = QVBoxLayout()
        self.mid_vbox.setSpacing(16)
        root.addLayout(self.mid_vbox)
        self.mid_hbox = QHBoxLayout()
        self.mid_hbox.setSpacing(16)
        self.mid_vbox.addLayout(self.mid_hbox)
        self.schedule_card = ScheduleCard(cfg)
        self.mid_hbox.addWidget(self.schedule_card, 1)
        self.last_card = LastRunCard(account_specs(cfg))
        self.mid_hbox.addWidget(self.last_card, 1)
        self._mid_stacked = False

        self.conn_card = ConnectionCard()
        root.addWidget(self.conn_card)
        root.addStretch(1)

        self.acc_cards = []
        self.acc_sig = None
        self._acc_cols = 2
        self.tick = 0
        self.adb_ok = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(5000)
        self.refresh()

    def set_adb_state(self, ok):
        """后台线程回报的 ADB 在线状态。"""
        self.adb_ok = ok

    def _acc_sig(self):
        return tuple((a.get("id"), a.get("label"), bool(a.get("enabled")),
                      a.get("server")) for a in self.cfg.get("accounts", []))

    def _rebuild_accounts(self):
        """账号列表变化时重建卡片网格（每行 2 个，顺序/增删/改名）。"""
        specs = account_specs(self.cfg)
        while self.acc_grid.count():
            item = self.acc_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.acc_cards = []
        for i, acc in enumerate(specs):
            card = AccountCard(acc)
            row, col = divmod(i, self._acc_cols)
            span = (self._acc_cols - col) if (self._acc_cols > 1
                                              and len(specs) % 2
                                              and i == len(specs) - 1) else 1
            self.acc_grid.addWidget(card, row, col, 1, span)
            for c in range(self._acc_cols):
                self.acc_grid.setColumnStretch(c, 1)
            self.acc_cards.append(card)

    @staticmethod
    def _detach_widget(layout, widget):
        for i in reversed(range(layout.count())):
            if layout.itemAt(i).widget() is widget:
                layout.takeAt(i)

    def _apply_mid_stack(self, stack):
        """宽窗口「班次计划 + 最近运行」并排；窄窗口改上下堆叠。"""
        if stack == self._mid_stacked:
            return
        self._mid_stacked = stack
        cards = (self.schedule_card, self.last_card)
        for w in cards:
            self._detach_widget(self.mid_hbox, w)
            self._detach_widget(self.mid_vbox, w)
        if stack:
            for w in cards:
                self.mid_vbox.addWidget(w)
        else:
            for w in cards:
                self.mid_hbox.addWidget(w, 1)

    def resizeEvent(self, event):
        # 太窄时账号卡改一列、中间卡片上下堆叠，避免横向截断
        cols = 2 if self.viewport().width() >= 720 else 1
        if cols != self._acc_cols:
            self._acc_cols = cols
            self._rebuild_accounts()
        self._apply_mid_stack(self.viewport().width() < 820)
        super().resizeEvent(event)

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
        self.conn_card.refresh(self.cfg, self.adb_ok)
        # 残留锁清理（上次运行中断）
        if runner.stale_lock() is not None:
            try:
                runner.LOCK_FILE.unlink()
            except OSError:
                pass
            InfoBar.warning("已清理残留锁文件", "上次运行可能被中断，本次可正常启动",
                            parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                            duration=5000)
