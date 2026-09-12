# -*- coding: utf-8 -*-
"""仪表盘：3 账号卡片 + 上次运行汇总条 + 班次计划 + 状态与更新合并卡片（1×2）。"""
import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QPlainTextEdit, QVBoxLayout, QWidget)

from qfluentwidgets import (BodyLabel, InfoBar, InfoBarPosition, LineEdit,
                            MessageBox, PrimaryPushButton, PushButton,
                            ScrollArea, SwitchButton)

import config as appconfig
import theme
from core import (adb, export_runner, logparse, maa_update, poller, runner,
                  scheduler)
from pages.export_dialog import ExportDialog
from widgets import (Card, IconBadge, Pill, big_number, dark_log_qss, inset_row,
                     kv_row, set_switch_checked_gray, style_button,
                     style_primary_button, style_scroll_area)

LEGACY_LOG_NAMES = {"official1": "Official 1", "official2": "Official 2",
                    "bilibili": "Bilibili"}


def account_specs(cfg):
    """从 cfg 账号数组生成仪表盘卡片规格（顺序 = 运行顺序）。"""
    specs = []
    for i, a in enumerate(cfg.get("accounts", [])):
        server = a.get("server", "official")
        label = a.get("label") or ("账号 %d" % (i + 1))
        legacy = LEGACY_LOG_NAMES.get(a.get("id", ""))
        specs.append({
            "key": a.get("id") or label,
            "name": label,
            "log_names": {label, legacy} if legacy else {label},
            "meta": ("MAA B服 · Bilibili 客户端" if server == "bilibili"
                     else "MAA 官服 · 槽位切号"),
            "char": str(i + 1),
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


def _label(text, size="12.5px", weight="400", color=None):
    # color 缺省时在调用时读取主题色（不能写成默认参数 theme.TEXT，
    # 否则会在模块导入时固化，切换主题后仍旧值）
    lab = QLabel(text)
    lab.setStyleSheet(
        "font-family: %s; font-size: %s; font-weight: %s; color: %s;"
        % (theme.FONT_FAMILY, size, weight, color or theme.TEXT))
    return lab


def _set_big_num(big_widget, num):
    """更新 big_number 组件的数字（数字 label 是第一个子控件）。"""
    big_widget.findChild(QLabel).setText(str(num))


def kv_pair(key_text, value_text="—"):
    """带引用的键值行（widgets.kv_row 的 refs 版）：返回 (row, key_label, value_label)。

    注意不要把 QLabel 传给 widgets.kv_row 的 key_text：qfluentwidgets 会把
    非 str 参数当 parent 重载，键名会凭空消失。
    """
    return kv_row(key_text, value_text, refs=True)


class AccountCard(Card):
    """单个账号状态卡片。"""

    def __init__(self, acc):
        super().__init__()
        self.acc = acc
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(IconBadge(acc["char"]))
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

        # kv1：今日耗时（大数字）或当前进度（文本）；kv2：最近运行/已用时间
        row1, self.kv1_key, self.kv1_val = kv_pair("今日耗时")
        self.vbox.addWidget(row1)
        self.vbox.addSpacing(4)
        row2, self.kv2_key, self.kv2_val = kv_pair("最近运行")
        self.vbox.addWidget(row2)

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
        # 优先按 id（key）匹配；日志 SUMMARY 只有账号名，id 匹配不上再按名兜底
        results = (run or {}).get("accounts", [])
        result = next((a for a in results
                       if self.acc["key"] and a["key"] == self.acc["key"]), None)
        if result is None:
            result = next((a for a in results
                           if a["name"] == self.acc["name"]), None)
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
        self._apply_worker = None   # 计划任务同步的后台线程（改完行立即生效）

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
        self.add_btn = style_button(PushButton("添加时间"))
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
        row_widget = inset_row()   # iOS 设置列表式内嵌行：深半档底色 + 6px 圆角
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(10, 7, 10, 7)
        row.setSpacing(8)

        lab = BodyLabel(appconfig.batch_name(entry["time"]))
        lab.setStyleSheet("color: %s; font-size: 13px;" % theme.TEXT_2)
        lab.setFixedWidth(44)
        edit = LineEdit()
        edit.setFixedWidth(68)
        edit.setClearButtonEnabled(False)
        edit.setText(entry["time"])
        edit.setToolTip("启动时间，HH:MM；00:00 显示为 24点")
        sw = set_switch_checked_gray(SwitchButton(), "启用")
        sw.setFixedWidth(75)
        sw.setChecked(bool(entry.get("enabled", True)))
        sw.setToolTip(
            "开启：该时间点写入计划任务，到点自动开始挂机。\n"
            "关闭：该时间点不触发（保留在列表，随时可重新打开）。\n"
            "全部关闭时计划任务整体禁用。")
        shutdown_sw = set_switch_checked_gray(SwitchButton(), "关机")
        shutdown_sw.setFixedWidth(75)
        shutdown_sw.setChecked(bool(entry.get("shutdown", False)))
        shutdown_sw.setToolTip(
            "开启：该时间点运行成功后 60 秒自动关机（无需确认）。\n"
            "关闭：跑完保持开机。失败时一律不关机，只弹窗提示。\n"
            "手动点「立即运行」不受此开关影响，永不关机。")
        del_btn = style_button(PushButton("删除"), "danger", small=True)
        del_btn.setFixedWidth(56)
        del_btn.setToolTip(
            "删除该时间点，计划任务中的对应触发立即移除。")
        del_btn.setStyleSheet(
            "PushButton { color: %s; border: 1px solid #9aa1ab; }" % theme.ERR)

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
        # 计划任务同步（Register/Set-ScheduledTask）会卡 1~40 秒，必须后台跑：
        # 配置已保存，界面行不变，任务更新完成后回填提示与「下次运行」
        if self._apply_worker is not None and self._apply_worker.isRunning():
            return   # 上一次同步还没完：配置已落盘，由它按最新配置重试即可
        self.add_btn.setEnabled(False)
        self._apply_worker = poller.SchedulerApplyWorker(self.cfg, self)
        self._apply_worker.done.connect(self._on_apply_done)
        self._apply_worker.start()

    def _on_apply_done(self, ok, msg, info):
        self.add_btn.setEnabled(True)
        if info is not None:
            self.refresh_scheduler(info)
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


class LastRunStrip(Card):
    """上次运行汇总：并入账号卡片区（账号各卡片展示各自结果，
    这里只保留全局的总耗时 / 模拟器关闭情况，标题右侧为运行时间）。"""

    def __init__(self):
        super().__init__("上次运行", "")
        bar = QHBoxLayout()
        bar.setSpacing(28)
        self.total_num = big_number("—", "分钟")
        bar.addWidget(self.total_num)
        self.emu_pill = Pill("—")
        bar.addWidget(self.emu_pill, 0, Qt.AlignmentFlag.AlignBottom)
        bar.addStretch(1)
        self.vbox.addLayout(bar)

    def refresh(self, run):
        if self.hint_label is not None:
            self.hint_label.setText(fmt_ts(run["start"]) if run else "暂无记录")
        total = (run or {}).get("total_min")
        _set_big_num(self.total_num, str(total) if total is not None else "—")
        if run is None:
            self.emu_pill.set_state("wait", "—")
        elif run.get("emulator_closed"):
            self.emu_pill.set_state("ok", "模拟器已自动关闭 ✓")
        elif run.get("final") is not None:
            self.emu_pill.set_state("wait", "模拟器未关闭")
        else:
            self.emu_pill.set_state("wait", "—")


class StatusCard(Card):
    """连接状态 + MAA 更新 合并卡片：内部左右两列（1×2）。

    更新逻辑在 core/maa_update：开 Clash → 两套 MAA 依次自更新（版本更新）
    → 恢复配置 → 关 Clash。配置（Clash 路径/端口）在「运行设置 → MAA 更新」。
    """

    def __init__(self):
        super().__init__("连接状态与 MAA 更新")
        self._last_rd = {}

        cols = QHBoxLayout()
        cols.setSpacing(16)
        self.vbox.addLayout(cols)

        # 左列：连接状态
        left = QVBoxLayout()
        left.setSpacing(6)
        left.addWidget(self._section_label("连接状态"))
        left.addSpacing(4)
        self.mumu_pill = Pill("未启动")
        left.addWidget(self._kv("MuMu 模拟器", self.mumu_pill))
        self.adb_pill = Pill("未连接")
        left.addWidget(self._kv("ADB", self.adb_pill))
        self.maa_pill = Pill("未运行")
        left.addWidget(self._kv("MAA 进程", self.maa_pill))
        self.rd_val = _label("—")
        self.rd_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.rd_val.setTextFormat(Qt.TextFormat.RichText)
        left.addWidget(self._kv("MAA 自动运行配置", self.rd_val))
        cols.addLayout(left, 1)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setStyleSheet("border: none; border-left: 1px dashed %s;" % theme.BORDER)
        cols.addWidget(divider)

        # 右列：MAA 更新
        right = QVBoxLayout()
        right.setSpacing(6)
        right.addWidget(self._section_label("MAA 更新"))
        right.addSpacing(4)
        self.pills = {}
        for name in ("官服", "B服"):
            pill = Pill("未知")
            self.pills[name] = pill
            right.addWidget(self._kv("%s MAA" % name, pill))
        self.hint = _label("版本更新一次完成（资源随版本包到位）；自动开关 Clash 代理，"
                           "全程几分钟到十几分钟，期间请勿关闭控制台",
                           size="12px", color=theme.TEXT_3)
        self.hint.setWordWrap(True)
        right.addWidget(self.hint)
        right.addSpacing(4)
        btn_row = QHBoxLayout()
        self.update_btn = style_primary_button(
            PrimaryPushButton("一键更新两套 MAA"))
        self.update_btn.setToolTip(
            "更新前自动启动 Clash 并把 MAA 下载代理指向它，全部结束后关闭；\n"
            "更新前 Clash 已开着则复用，不会主动关闭。挂机运行时不可用。")
        btn_row.addWidget(self.update_btn)
        btn_row.addStretch(1)
        right.addLayout(btn_row)
        cols.addLayout(right, 1)

    # ---------- 构建 ----------

    def _section_label(self, text):
        return _label(text, size="12px", weight="600", color=theme.TEXT_2)

    def _kv(self, key_text, value_widget):
        """同 widgets.kv_row 的键值行。"""
        return kv_row(key_text, value_widget)

    # ---------- 刷新 ----------

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

        self._last_rd = runner.check_run_directly(cfg)
        self._render_rd()

    def _render_rd(self):
        parts = []
        tips = []
        full = {"official": "官服", "bilibili": "B服"}
        for key, label in full.items():
            v = self._last_rd.get(key)
            tips.append("%s：%s" % (label, {
                True: "RunDirectly 已开启",
                False: "RunDirectly 已关闭",
            }.get(v, "配置缺失")))
            if v is True:
                parts.append('<span style="color:%s">%s ✓</span>' % (theme.OK, label))
            elif v is False:
                parts.append('<span style="color:%s">%s ✗ RunDirectly 已关闭</span>'
                             % (theme.ERR, label))
            else:
                parts.append('<span style="color:%s">%s 配置缺失</span>' % (theme.WARN, label))
        self.rd_val.setText(" · ".join(parts))
        self.rd_val.setToolTip("\n".join(tips))

    def refresh_versions(self, cfg):
        """刷新版本号显示（ctypes 读版本 + 读本地缓存文件，毫秒级，不派生子进程）。

        「最新版本」来自 MAA 本地缓存（MAA 自己检查后写入），可能过期：
        tooltip 标注缓存检查时间，超过 7 天在文案上提示缓存可能过期。
        """
        for name, cur, latest, checked in maa_update.version_rows(cfg):
            pill = self.pills[name]
            if checked:
                checked_dt = datetime.fromtimestamp(checked)
                age_days = int((datetime.now() - checked_dt).total_seconds()) // 86400
                tip = "缓存检查于 %s（MAA 上次启动检查，一键更新会刷新）" % checked_dt.strftime("%Y-%m-%d %H:%M")
            else:
                age_days = None
                tip = "MAA 尚未检查过版本（无缓存，一键更新后生成）"
            pill.setToolTip(tip)
            stale = "（缓存 %d 天前）" % age_days if age_days is not None and age_days >= 7 else ""
            if maa_update.has_update(cur, latest):
                pill.set_state("fail", "有更新 %s → %s"
                               % (maa_update.fmt_version(cur),
                                  maa_update.fmt_version(latest)))
            elif cur:
                pill.set_state("ok", "已最新 %s%s" % (maa_update.fmt_version(cur), stale))
            else:
                pill.set_state("wait", "未知")


class MaaUpdateWorker(QThread):
    """后台跑 run_full_update：逐行转发日志，结束发 done(ok, summary)。

    closeEvent 里「仍要退出」时调 request_stop()：更新循环会尽快收尾，
    finally 里的恢复逻辑（MAA 配置 / Clash / 系统代理）保证不被跳过。
    """

    line = Signal(str)
    done = Signal(bool, str)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._stop = False

    def request_stop(self):
        self._stop = True

    def run(self):
        try:
            ok, summary = maa_update.run_full_update(
                self.cfg, self.line.emit, cancel=lambda: self._stop)
        except Exception as exc:  # 兜底：更新流程异常不能让线程崩掉
            ok, summary = False, "更新过程异常：%s" % exc
        self.done.emit(ok, summary)


class UpdateLogDialog(QDialog):
    """MAA 更新实时日志窗口。关闭只是后台运行，不中断更新。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("MAA 更新")
        self.resize(620, 460)
        self.setStyleSheet("QDialog { background: %s; }" % theme.BG)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet(dark_log_qss())
        root.addWidget(self.log_view, 1)
        btns = QHBoxLayout()
        tip = BodyLabel("关闭窗口不会中断更新，完成后右上有提示")
        tip.setStyleSheet("font-family: %s; font-size: 12px; color: %s;"
                          % (theme.FONT_FAMILY, theme.TEXT_3))
        close_btn = style_button(PushButton("后台运行"))
        close_btn.clicked.connect(self.accept)
        btns.addWidget(tip)
        btns.addStretch(1)
        btns.addWidget(close_btn)
        root.addLayout(btns)

    def append(self, text):
        self.log_view.appendPlainText(text)


class DashboardPage(ScrollArea):
    """仪表盘。export_done = 干员导出结束（含后台运行的情况）回主窗口提示。"""

    export_done = Signal(bool, str)

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.view = QWidget()
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        # 视口透明化 + 浅色细滚动条（默认深色悬浮条会压在卡片右缘上）
        style_scroll_area(self)
        root = QVBoxLayout(self.view)
        # 左右对称 12px：卡片两侧都有留白，右侧同时让位给悬浮滚动条
        root.setContentsMargins(12, 16, 12, 16)
        root.setSpacing(16)

        self.acc_grid = QGridLayout()
        self.acc_grid.setSpacing(20)
        root.addLayout(self.acc_grid)

        # 上次运行汇总：紧贴账号卡片的同一区块（全局总耗时 + 模拟器状态）
        self.last_strip = LastRunStrip()
        root.addWidget(self.last_strip)

        self.schedule_card = ScheduleCard(cfg)
        root.addWidget(self.schedule_card)

        # 底部：连接状态 + MAA 更新 合并为一张 1×2 卡片
        self.status_card = StatusCard()
        self.status_card.update_btn.clicked.connect(self.on_maa_update)
        root.addWidget(self.status_card)
        self.status_card.refresh_versions(self.cfg)
        self._upd_worker = None
        self._upd_dialog = None
        # 干员资料导出（core/export_runner 驱动 scripts/export_operbox.py）
        self._export_worker = None
        self._export_dialog = None
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

    def update_running(self):
        """MAA 一键更新是否正在后台执行（主窗口关闭前检查）。"""
        w = self._upd_worker
        return w is not None and w.isRunning()

    def request_update_stop(self):
        """请更新线程尽快收尾：更新循环取消后 finally 会恢复 MAA 配置/关 Clash。"""
        w = self._upd_worker
        if w is not None:
            w.request_stop()

    def wait_update_worker(self, timeout_ms):
        """等更新线程退出（收尾含恢复配置与杀 MAA，给足超时）。"""
        w = self._upd_worker
        if w is not None and w.isRunning():
            w.wait(timeout_ms)

    def on_maa_update(self):
        if runner.is_running():
            InfoBar.warning("挂机运行中", "请先停止挂机再更新 MAA",
                            parent=self.window(),
                            position=InfoBarPosition.TOP_RIGHT, duration=4000)
            return
        if self.update_running():
            InfoBar.info("更新进行中", "MAA 更新正在后台执行，请稍候",
                         parent=self.window(),
                         position=InfoBarPosition.TOP_RIGHT, duration=4000)
            return
        mu = self.cfg.get("maa_update") or {}
        vpn_line = ("更新前自动启动 Clash，全部结束后关闭"
                    if mu.get("use_vpn", True) else "未启用 Clash，MAA 将直连下载")
        box = MessageBox(
            "一键更新 MAA",
            "将依次更新官服与B服两套 MAA（版本更新，资源随版本包一并更新）。\n\n"
            "· %s\n· 更新期间会临时修改 MAA 配置，结束后自动恢复\n"
            "· 全程可能需要几分钟到十几分钟，期间请勿关闭控制台\n\n"
            "确定开始吗？" % vpn_line,
            self.window())
        box.yesButton.setText("开始更新")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        self._upd_dialog = UpdateLogDialog(self.window())
        self._upd_worker = MaaUpdateWorker(self.cfg, self)
        self._upd_worker.line.connect(self._upd_dialog.append)
        self._upd_worker.done.connect(self._on_update_done)
        self.status_card.update_btn.setEnabled(False)
        self._upd_worker.start()
        self._upd_dialog.show()

    def _on_update_done(self, ok, summary):
        self.status_card.update_btn.setEnabled(True)
        self.status_card.refresh_versions(self.cfg)
        dlg = self._upd_dialog
        if dlg is not None:
            dlg.append("")
            dlg.append("==== %s ====" % ("更新完成" if ok else "更新未全部完成"))
            dlg.append(summary)
        if ok:
            InfoBar.success("MAA 更新完成", summary, parent=self.window(),
                            position=InfoBarPosition.TOP_RIGHT, duration=8000)
        else:
            InfoBar.warning("MAA 更新未全部完成", summary, parent=self.window(),
                            position=InfoBarPosition.TOP_RIGHT, duration=10000)

    # ---------- 干员资料导出 ----------

    def export_running(self):
        """干员导出是否正在后台执行（主窗口「立即运行」/关闭前检查）。"""
        w = self._export_worker
        return w is not None and w.isRunning()

    def export_accounts(self):
        """本次导出范围：启用且打开「导出」开关的账号。"""
        return [a for a in self.cfg.get("accounts", [])
                if a.get("enabled", True) and a.get("export_enabled", False)]

    def start_export(self):
        """启动导出（调用方已确认互斥与账号列表非空）。"""
        script = Path(self.cfg["paths"]["script_dir"]) / "export_operbox.py"
        out_dir = Path(self.cfg["paths"]["script_dir"]).parent / "exports"
        self._export_dialog = ExportDialog(self.export_accounts(),
                                           parent=self.window())
        self._export_worker = export_runner.ExportWorker(script, out_dir,
                                                         parent=self)
        self._export_worker.line.connect(self._export_dialog.append)
        self._export_worker.done.connect(self._on_export_done)
        self._export_worker.start()
        self._export_dialog.show()

    def _on_export_done(self, ok, summary):
        dlg = self._export_dialog
        if dlg is not None:
            dlg.append("")
            dlg.append("==== %s ====" % ("导出完成" if ok else "导出未全部成功"))
            dlg.append(summary)
        self.export_done.emit(ok, summary)

    def detach_export_worker(self):
        """主窗口关闭时调用：导出子进程独立于控制台存活，断开父级让线程自然收尾。

        返回被断开的工作线程（调用方持有引用防止 GC），没有导出在跑返回 None。
        """
        w = self._export_worker
        self._export_worker = None
        if w is not None and w.isRunning():
            w.setParent(None)
            return w
        return None

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

    def resizeEvent(self, event):
        """不做响应式重排：布局固定，窗口过窄时由滚动区横向滚动兜底。"""
        super().resizeEvent(event)

    def refresh(self):
        self.tick += 1
        # 账号结构变化 → 重建卡片与「最近运行」行
        sig = self._acc_sig()
        if sig != self.acc_sig:
            self.acc_sig = sig
            self._rebuild_accounts()
        log_path = self.cfg["paths"]["log_file"]
        # snapshot：一次读盘+一次行匹配同时取摘要与阶段（内部带变更缓存）
        snap = logparse.snapshot(log_path)
        run = snap["run"]
        stage = snap["stage"] if runner.is_running() else None
        enabled_map = {a.get("id") or a.get("label"): bool(a.get("enabled", True))
                       for a in self.cfg.get("accounts", [])}
        for card in self.acc_cards:
            card.refresh(run, stage, enabled_map.get(card.acc["key"], True))
        self.last_strip.refresh(run)
        self.status_card.refresh(self.cfg, self.adb_ok)
        # 残留锁清理（上次运行中断）：clear_stale_lock 内部复核 PID 与内容，
        # 避免和 master.ps1 的抢锁写入竞态
        if runner.clear_stale_lock() is not None:
            InfoBar.warning("已清理残留锁文件", "上次运行可能被中断，本次可正常启动",
                            parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                            duration=5000)
