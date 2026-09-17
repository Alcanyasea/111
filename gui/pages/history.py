# -*- coding: utf-8 -*-
"""运行历史：master.ps1 每轮结束写入 scripts\\run_history\\run_*.json 的留存视图。

左侧按时间倒序列出每一轮（挂机/收菜/切号），右侧展示该轮每号结果、
耗时与失败原因。记录保留 60 天（master.ps1 写入时自动清理），纯本机数据；
正在挂机时不能清空历史，查看不受影响。
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QVBoxLayout, QWidget)

from qfluentwidgets import (BodyLabel, InfoBar, InfoBarPosition, MessageBox,
                            PushButton)

import theme
from core import runner
from widgets import (Card, Pill, _label_transparent, hline, style_button,
                     style_scroll_area)

MODE_LABELS = {"farm": "挂机", "collect": "收菜", "switch": "切号",
               "start": "快速启动"}
# 切号/快速启动是单账号手动操作，班次名无意义不显示
NO_BATCH_MODES = ("switch", "start")


def _fmt_dt(ts, short=False):
    """start/end 时间戳 → 「今天 04:00」/「09-14 04:00」；解析失败原样返回。"""
    try:
        dt = datetime.strptime(str(ts), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return str(ts or "—")
    if dt.date() == datetime.now().date():
        return "今天 " + dt.strftime("%H:%M")
    if short:
        return dt.strftime("%m-%d %H:%M")
    return dt.strftime("%Y-%m-%d %H:%M")


def _fmt_dur(mins):
    try:
        m = float(mins)
    except (TypeError, ValueError):
        return None
    if m >= 60:
        return "%.1f 小时" % (m / 60.0)
    return "%g 分钟" % m


def _label(text, size="13px", weight="400", color=None):
    """主题色文本标签。color 传调色板令牌名（如 "TEXT_2"），
    配方随主题切换自动重读；None = TEXT。"""
    token = color if isinstance(color, str) else "TEXT"
    lab = QLabel(text)
    theme.bind(lab, lambda: "font-family: %s; font-size: %s; font-weight: %s;"
               " color: %s; background: transparent;"
               % (theme.FONT_FAMILY, size, weight, getattr(theme, token)))
    return lab


class HistoryPage(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self._records = []          # 按时间倒序的全部记录（dict，含 _file 文件名）
        self._selected_file = None  # 当前选中的文件名（自动刷新后恢复选中）
        self._suppress_select = False

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 16, 12, 16)
        root.setSpacing(10)

        # 工具栏
        bar = QHBoxLayout()
        bar.setSpacing(10)
        refresh_btn = style_button(PushButton("刷新"), small=True)
        refresh_btn.clicked.connect(self.refresh)
        open_btn = style_button(PushButton("打开记录目录"), small=True)
        open_btn.clicked.connect(self._open_dir)
        clear_btn = style_button(PushButton("清空历史"), "danger", small=True)
        clear_btn.clicked.connect(self._on_clear)
        bar.addWidget(refresh_btn)
        bar.addWidget(open_btn)
        bar.addWidget(clear_btn)
        bar.addStretch(1)
        hint = BodyLabel("每轮挂机结束自动记录 · 保留 60 天")
        theme.bind(hint, lambda: "color: %s; font-size: 12px;" % theme.TEXT_3)
        bar.addWidget(hint)
        root.addLayout(bar)

        # 左列表 + 右详情
        main = QHBoxLayout()
        main.setSpacing(12)
        self.list = QListWidget()
        self.list.setFixedWidth(300)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        theme.bind(
            self.list,
            lambda: "QListWidget { background: %s; border: 1px solid %s;"
                    " border-radius: %dpx; padding: 6px; font-family: %s; }"
                    "QListWidget::item { color: %s; padding: 9px 10px; margin: 2px 0;"
                    " border-radius: 8px; }"
                    "QListWidget::item:selected { background: %s; color: %s; }"
                    % (theme.CARD, theme.BORDER, theme.RADIUS_CARD,
                       theme.FONT_FAMILY, theme.TEXT, theme.ROW_INSET, theme.TEXT))
        self.list.currentItemChanged.connect(self._on_select)
        main.addWidget(self.list)

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)
        self.detail_slot = QVBoxLayout()
        right_lay.addLayout(self.detail_slot)
        right_lay.addStretch(1)
        self.detail_card = None
        main.addWidget(right, 1)
        root.addLayout(main)

        self.refresh()
        # 30 秒自动刷新：挂机结束后新记录自动出现；选中项按文件名恢复
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(30000)

    # ---------- 数据 ----------

    def _history_dir(self):
        script_dir = (self.cfg.get("paths") or {}).get("script_dir") or r"D:\1\scripts"
        return Path(script_dir) / "run_history"

    def _load(self):
        recs = []
        d = self._history_dir()
        if d.is_dir():
            for f in sorted(d.glob("run_*.json"), reverse=True):
                try:
                    data = json.loads(f.read_text(encoding="utf-8-sig"))
                except (OSError, json.JSONDecodeError, ValueError):
                    continue   # 写了一半/损坏的记录直接跳过，不影响其余
                if isinstance(data, dict):
                    data["_file"] = f.name
                    recs.append(data)
        return recs

    @staticmethod
    def _counts(rec):
        accs = rec.get("accounts") or []
        ok = sum(1 for a in accs if a.get("ok") and not a.get("skipped"))
        skip = sum(1 for a in accs if a.get("skipped"))
        fail = sum(1 for a in accs if not a.get("ok"))
        return ok, skip, fail

    def _summary_text(self, rec):
        """列表项与详情头部共用的结果摘要（含失败标红的富文本）。"""
        if rec.get("fatal"):
            return '<span style="color:%s">启动失败：%s</span>' % (
                theme.ERR, rec.get("fatal"))
        ok, skip, fail = self._counts(rec)
        parts = []
        if fail:
            parts.append('<span style="color:%s">%d 失败</span>' % (theme.ERR, fail))
        if ok:
            parts.append("%d 成功" % ok)
        if skip:
            parts.append("%d 跳过" % skip)
        if not parts:
            return "无账号记录"
        dur = _fmt_dur(rec.get("total_min"))
        text = " · ".join(parts)
        return text + (" · " + dur if dur else "")

    def _make_item(self, rec):
        mode = MODE_LABELS.get(rec.get("mode"), "挂机")
        head = "%s · %s" % (_fmt_dt(rec.get("start")), mode)
        if rec.get("batch") and rec.get("mode") not in NO_BATCH_MODES:
            head += "（%s班）" % rec["batch"]
        item = QListWidgetItem(head + "\n" + self._plain_summary(rec))
        if rec.get("fatal") or self._counts(rec)[2]:
            item.setForeground(QColor(theme.ALERT))
        item.setData(Qt.ItemDataRole.UserRole, rec.get("_file"))
        return item

    def _plain_summary(self, rec):
        """列表项第二行（QListWidgetItem 不吃富文本，去标签取纯文字）。"""
        return re.sub(r"<[^>]+>", "", self._summary_text(rec))

    # ---------- 渲染 ----------

    def refresh(self):
        self._records = self._load()
        self._suppress_select = True
        self.list.clear()
        for rec in self._records:
            self.list.addItem(self._make_item(rec))
        # 恢复选中（自动刷新不打断正在查看的记录）；没有则展示空态
        row = -1
        if self._selected_file:
            for i, rec in enumerate(self._records):
                if rec.get("_file") == self._selected_file:
                    row = i
                    break
        self._suppress_select = False
        if row >= 0:
            self.list.setCurrentRow(row)
        else:
            self._selected_file = None
            self.list.setCurrentRow(-1)
            self._show_detail(None)

    def _on_select(self, cur, _prev=None):
        if self._suppress_select:
            return
        if cur is None:
            if not self._records:
                self._show_detail(None)
            return
        self._selected_file = cur.data(Qt.ItemDataRole.UserRole)
        for rec in self._records:
            if rec.get("_file") == self._selected_file:
                self._show_detail(rec)
                return
        self._show_detail(None)

    def _clear_detail(self):
        while self.detail_slot.count():
            item = self.detail_slot.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _show_detail(self, rec):
        self._clear_detail()
        self.detail_card = Card()
        if rec is None:
            tip = ("暂无运行记录，挂机/收菜结束后自动生成。"
                   if self._records == [] else "在左侧选择一轮查看详情。")
            empty = BodyLabel(tip)
            theme.bind(empty, lambda: "color: %s; font-size: 13px;"
                       " background: transparent;" % theme.TEXT_3)
            self.detail_card.vbox.addWidget(empty)
        else:
            self._build_detail(rec)
        self.detail_slot.addWidget(self.detail_card)

    def _build_detail(self, rec):
        box = self.detail_card.vbox
        mode = MODE_LABELS.get(rec.get("mode"), "挂机")
        batch = "" if rec.get("mode") in NO_BATCH_MODES or not rec.get("batch") \
            else "（%s班）" % rec["batch"]
        title = _label("%s → %s · %s%s" % (
            _fmt_dt(rec.get("start")), _fmt_dt(rec.get("end"), short=True),
            mode, batch), size="15px", weight="600")
        box.addWidget(title)
        box.addSpacing(6)

        meta = QHBoxLayout()
        meta.setSpacing(8)
        if rec.get("fatal"):
            pill = Pill()
            pill.set_state("fail", "启动失败")
            pill.setToolTip(str(rec.get("fatal")))
            meta.addWidget(pill)
        else:
            ok, _skip, fail = self._counts(rec)
            pill = Pill()
            pill.set_state("ok" if not fail else "fail",
                           "全部成功" if not fail else "%d 失败" % fail)
            meta.addWidget(pill)
        dur = _fmt_dur(rec.get("total_min"))
        if dur:
            meta.addWidget(_label(dur, size="12.5px", color="TEXT_2"))
        meta.addStretch(1)
        box.addLayout(meta)
        box.addSpacing(8)
        box.addWidget(hline())
        box.addSpacing(8)

        accs = rec.get("accounts") or []
        if not accs:
            note = BodyLabel(str(rec.get("fatal")) if rec.get("fatal")
                             else "本轮没有账号记录")
            note.setWordWrap(True)
            theme.bind(note, lambda: "color: %s; font-size: 12.5px;"
                       " background: transparent;" % theme.TEXT_2)
            box.addWidget(note)
        for a in accs:
            box.addWidget(self._account_row(a))
            box.addSpacing(6)
        box.addSpacing(2)

    def _account_row(self, a):
        skipped = bool(a.get("skipped"))
        ok = bool(a.get("ok")) and not skipped
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        pill = Pill()
        pill.set_state("wait" if skipped else ("ok" if ok else "fail"),
                       "跳过" if skipped else ("成功" if ok else "失败"))
        lay.addWidget(pill)
        name = _label(str(a.get("name") or "—"), size="13px", weight="500")
        lay.addWidget(name)
        lay.addStretch(1)
        reason = str(a.get("reason") or "")
        if not ok and not skipped and reason:
            tip = _label(reason, size="12px", color="ALERT")
            lay.addWidget(tip)
        dur = _fmt_dur(a.get("dur_min"))
        if dur:
            lay.addWidget(_label(dur, size="12px", color="TEXT_2"))
        return row

    # ---------- 操作 ----------

    def _open_dir(self):
        d = self._history_dir()
        try:
            d.mkdir(parents=True, exist_ok=True)
            os.startfile(str(d))
        except OSError as exc:
            InfoBar.warning("无法打开目录", str(exc), parent=self.window(),
                            position=InfoBarPosition.TOP_RIGHT, duration=4000)

    def _on_clear(self):
        if runner.is_running():
            InfoBar.warning("挂机运行中", "运行期间不能清空历史（master.ps1 可能正在写入）",
                            parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                            duration=5000)
            return
        if not self._records:
            InfoBar.info("无需清空", "当前没有运行记录",
                         parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                         duration=3000)
            return
        box = MessageBox(
            "清空运行历史",
            "将删除 scripts\\run_history 下全部 %d 条运行记录，不可恢复。"
            % len(self._records), self.window())
        box.yesButton.setText("清空")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        removed = 0
        for f in self._history_dir().glob("run_*.json"):
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
        self._selected_file = None
        self.refresh()
        InfoBar.success("已清空运行历史", "删除 %d 条记录" % removed,
                        parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                        duration=3000)
