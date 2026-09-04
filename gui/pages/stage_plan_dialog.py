# -*- coding: utf-8 -*-
"""第二理智作战候选关卡配置弹窗。

先通过可滚动的下拉选择关卡，添加后每个候选以固定大小的标签显示在卡片里；
标签可删除、可上移/下移调整顺序，「使用备选关卡」对应 MAA 的 UseAlternateStage。
保存后写入 config.json，运行前由 plugins\\fight_stage 原样写回 MAA。
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QDialog, QFrame, QHBoxLayout, QLabel,
                               QScrollArea, QVBoxLayout, QWidget)

from qfluentwidgets import (BodyLabel, InfoBar, InfoBarPosition,
                            PrimaryPushButton, PushButton, SwitchButton)

import config as appconfig
import theme

PERMANENT_STAGE_OPTIONS = (
    ("1-7", "1-7"),
    ("R8-11", "R8-11"),
    ("12-17-HARD", "12-17-HARD"),
    ("龙门币-6/5", "CE-6"),
    ("红票-5", "AP-5"),
    ("技能-5", "CA-5"),
    ("经验-6/5", "LS-6"),
    ("碳-5", "SK-5"),
    ("当期剿灭", "Annihilation"),
    ("奶/盾芯片", "PR-A-1"),
    ("奶/盾芯片组", "PR-A-2"),
    ("术/狙芯片", "PR-B-1"),
    ("术/狙芯片组", "PR-B-2"),
    ("先/辅芯片", "PR-C-1"),
    ("先/辅芯片组", "PR-C-2"),
    ("近/特芯片", "PR-D-1"),
    ("近/特芯片组", "PR-D-2"),
)
# MAA StageManager 的下拉项顺序：当前/上次 -> 活动关卡 -> 常驻关卡。
# 顺序与显示名必须与 MAA 源码 AddPermanentStages / ParseActivityStages
# 及其 zh-cn 本地化一致：显示名（如「龙门币-6/5」），保存的是关卡代号。
PERMANENT_STAGE_CODES = tuple(value for _, value in PERMANENT_STAGE_OPTIONS)
PICKER_PLACEHOLDER = "选择要添加的关卡…"
_available_cache = {}

_STAGE_LABELS = {"": "当前/上次"}
_STAGE_LABELS.update(dict((value, display)
                          for display, value in PERMANENT_STAGE_OPTIONS))


def _stage_label(value):
    """把 MAA 关卡值转成 MAA 界面里显示的名字（未在下拉里的原样显示）。"""
    value = str(value or "").strip()
    return _STAGE_LABELS.get(value, value)


def format_stage_plan(plan):
    if not isinstance(plan, list):
        return ""
    return ", ".join(_stage_label(s) for s in plan)


def _activity_utc(activity, key):
    """活动 JSON 里的本地时间（带 TimeZone）转 UTC。"""
    try:
        text = activity.get(key) if isinstance(activity, dict) else None
        if not text:
            return None
        tz = int(activity.get("TimeZone") or 0)
        return (datetime.strptime(text, "%Y/%m/%d %H:%M:%S")
                - timedelta(hours=tz)).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _maa_stage_options(maa_dir):
    """按 MAA StageManager 的规则生成下拉项：[(显示名, 关卡值), ...]。

    顺序与 MAA v6.11.1 一致：当前/上次 -> 缓存中未过期的活动关卡
    （按 StageActivityV2.json 的分组顺序）-> 常驻关卡；活动已过期则隐藏。
    显示名/值取自 JSON 的 Display/Value，剿灭显示为「当期剿灭」。
    """
    maa_dir = Path(maa_dir)
    options = [("当前/上次", "")]
    seen_display = {""}
    seen_value = {""}
    cache_file = maa_dir / "cache" / "gui" / "StageActivityV2.json"
    if cache_file.is_file():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict):
            client = data.get("Official") or data.get("txwy")
            side = ((client or {}).get("sideStoryStage")
                    if isinstance(client, dict) else None)
            if isinstance(side, dict):
                now = datetime.now(timezone.utc)
                for group in side.values():
                    if not isinstance(group, dict):
                        continue
                    group_activity = group.get("Activity")
                    for stage in group.get("Stages") or []:
                        if not isinstance(stage, dict):
                            continue
                        display = stage.get("Display")
                        value = stage.get("Value")
                        display = str(display).strip() if isinstance(display, str) else ""
                        value = str(value).strip() if isinstance(value, str) else ""
                        if not display and not value:
                            continue
                        if not display:
                            display = value
                        if not value:
                            value = display
                        activity = stage.get("Activity") or group_activity
                        expire = _activity_utc(activity, "UtcExpireTime")
                        if expire is not None and now >= expire:
                            continue
                        # MAA 用 Display 作为活动关卡的键（TryAdd，先到先得）
                        if display in seen_display:
                            continue
                        seen_display.add(display)
                        seen_value.add(value)
                        options.append((display, value))
    # 常驻关卡：MAA 用关卡值做键（TryAdd），顺序照源码
    for display, value in PERMANENT_STAGE_OPTIONS:
        if value in seen_value:
            continue
        seen_value.add(value)
        seen_display.add(display)
        options.append((display, value))
    return options


def _cache_signature(maa_dir):
    cache_file = Path(maa_dir) / "cache" / "gui" / "StageActivityV2.json"
    try:
        if cache_file.is_file():
            stat = cache_file.stat()
            return (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None
    return None


def _resolve_maa_dir(cfg, server=None):
    """按账号服务器返回实际使用的 MAA 目录（与控制台启动 MAA 的逻辑一致）。"""
    paths = cfg.get("paths") or {}
    official = paths.get("maa_official_dir")
    bilibili = paths.get("maa_bilibili_dir")
    dirs = ((bilibili, official) if server == "bilibili"
            else (official, bilibili))
    for d in dirs:
        if d and Path(d).is_dir():
            return Path(d)
    return None


def available_stages(cfg, server=None):
    """返回 [(显示名, 关卡值), ...]，选项与对应 MAA 的候选关卡下拉一致。"""
    maa_dir = _resolve_maa_dir(cfg, server)
    if maa_dir is not None:
        key = str(maa_dir.resolve())
        sig = _cache_signature(maa_dir)
        cached = _available_cache.get(key)
        if cached is None or cached[0] != sig:
            cached = (sig, _maa_stage_options(maa_dir))
            _available_cache[key] = cached
        return cached[1]
    return [("当前/上次", "")] + list(PERMANENT_STAGE_OPTIONS)


def maa_second_fight_plan(cfg, server=None):
    """读取该服 MAA 当前第二个理智作战的候选与备选开关。"""
    paths = cfg.get("paths") or {}
    official = paths.get("maa_official_dir")
    bilibili = paths.get("maa_bilibili_dir")
    dirs = ((bilibili, official) if server == "bilibili"
            else (official, bilibili))
    for d in dirs:
        if not d or not Path(d).is_dir():
            continue
        gui_new = Path(d) / "config" / "gui.new.json"
        if not gui_new.is_file():
            continue
        try:
            data = json.loads(gui_new.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        cur = data.get("Current") or "Default"
        conf = (data.get("Configurations") or {}).get(cur)
        queue = conf.get("TaskQueue") if isinstance(conf, dict) else None
        if not isinstance(queue, list):
            continue
        fights = [t for t in queue
                  if isinstance(t, dict) and t.get("$type") == "FightTask"]
        if len(fights) < 2:
            continue
        task = fights[1]
        plan = task.get("StagePlan")
        plan = [str(s).strip() for s in plan] \
            if isinstance(plan, list) else []
        return plan, bool(task.get("UseOptionalStage", True))
    return [], True


def _label(text, size="12px", color=theme.TEXT_3, weight="400"):
    lab = QLabel(text)
    lab.setStyleSheet("font-size: %s; font-weight: %s; color: %s;"
                      % (size, weight, color))
    return lab


class StagePlanDialog(QDialog):
    """候选关卡：滚动选择 + 已选关卡显示为固定大小标签。"""

    def __init__(self, cfg, acc, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.acc = acc
        self.server = acc.get("server", "official")
        self.stage_options = available_stages(cfg, self.server)
        self._maa_dir = _resolve_maa_dir(cfg, self.server)
        self._picker_sig = (_cache_signature(self._maa_dir)
                            if self._maa_dir is not None else None)
        self.selected = []

        self.setWindowTitle("候选关卡 - %s" % (acc.get("label") or ""))
        self.setModal(True)
        self.resize(740, 520)
        self.setStyleSheet("QDialog { background: %s; }" % theme.BG)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        # 顶部标题
        header = QFrame()
        header.setStyleSheet(
            "QFrame { background: %s; border: 1px solid %s;"
            " border-radius: 12px; }" % (theme.CARD, theme.BORDER))
        hb = QHBoxLayout(header)
        hb.setContentsMargins(18, 14, 18, 14)
        hb.setSpacing(12)
        badge = QLabel("关")
        badge.setFixedSize(42, 42)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            "QLabel { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            " stop:0 %s, stop:1 %s); color: #fff; font-size: 17px;"
            " font-weight: 700; border-radius: 10px; }" % theme.ACCENT_O1)
        hb.addWidget(badge)
        tb = QVBoxLayout()
        tb.setSpacing(2)
        tb.addWidget(_label("第二理智作战 · 候选关卡", "15px",
                            theme.TEXT, "600"))
        tb.addWidget(_label("先选择关卡，添加后以固定标签显示，第一行最优先",
                            "12px", theme.TEXT_2))
        self.source_lab = _label("", "11px", theme.TEXT_3)
        tb.addWidget(self.source_lab)
        hb.addLayout(tb)
        hb.addStretch(1)
        self.optional_sw = SwitchButton("使用备选关卡")
        hb.addWidget(self.optional_sw, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(header)

        # 选择器：一个可滚动下拉 + 添加按钮
        picker = QFrame()
        picker.setStyleSheet(
            "QFrame { background: %s; border: 1px solid %s;"
            " border-radius: 10px; }" % (theme.CARD, theme.BORDER))
        pb = QHBoxLayout(picker)
        pb.setContentsMargins(14, 10, 14, 10)
        pb.setSpacing(10)
        pb.addWidget(_label("添加关卡：", "13px", theme.TEXT_2, "600"))
        self.picker = QComboBox()
        self.picker.setMinimumWidth(260)
        self.picker.setMaxVisibleItems(10)
        self.picker.setStyleSheet(
            "QComboBox { background: %s; color: %s; border: 1px solid %s;"
            " border-radius: 6px; padding: 4px 10px; }"
            "QComboBox QAbstractItemView { background: %s; color: %s;"
            " border: 1px solid %s; selection-background-color: %s;"
            " selection-color: white; }"
            % (theme.CARD, theme.TEXT, theme.BORDER,
               theme.CARD, theme.TEXT, theme.BORDER, theme.ACCENT))
        self._populate_picker()
        self.picker.currentIndexChanged.connect(self._refresh_add_btn)
        pb.addWidget(self.picker, 1)
        self.add_btn = PrimaryPushButton("添加候选关卡")
        self.add_btn.clicked.connect(self._on_pick_stage)
        pb.addWidget(self.add_btn)
        root.addWidget(picker)

        # 已选标签区
        list_card = QFrame()
        list_card.setStyleSheet(
            "QFrame { background: %s; border: 1px solid %s;"
            " border-radius: 12px; }" % (theme.CARD, theme.BORDER))
        lc = QVBoxLayout(list_card)
        lc.setContentsMargins(14, 12, 14, 12)
        lc.setSpacing(8)
        lc.addWidget(_label("已选候选（顺序即尝试顺序）", "13px",
                            theme.TEXT_2, "600"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setAlignment(Qt.AlignmentFlag.AlignLeft
                            | Qt.AlignmentFlag.AlignTop)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        host = QWidget()
        self.tags_layout = QVBoxLayout(host)
        self.tags_layout.setContentsMargins(0, 0, 6, 0)
        self.tags_layout.setSpacing(10)
        scroll.setWidget(host)
        lc.addWidget(scroll, 1)
        root.addWidget(list_card, 1)

        # 底部
        bar = QFrame()
        bar.setStyleSheet(
            "QFrame { background: %s; border: 1px solid %s;"
            " border-radius: 12px; }" % (theme.CARD, theme.BORDER))
        bb = QHBoxLayout(bar)
        bb.setContentsMargins(14, 10, 14, 10)
        bb.setSpacing(10)
        self.summary = _label("", "12px", theme.TEXT_2)
        bb.addWidget(self.summary)
        bb.addStretch(1)
        cancel_btn = PushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        save_btn = PrimaryPushButton("保存并映射到 MAA")
        save_btn.clicked.connect(self._on_save)
        bb.addWidget(cancel_btn)
        bb.addWidget(save_btn)
        root.addWidget(bar)

        plan, use_optional = self._initial_state()
        self.selected = list(plan)
        self.optional_sw.setChecked(use_optional)
        self.optional_sw.checkedChanged.connect(self._on_optional_toggled)
        self._refresh_tags()
        self._refresh_add_btn()
        self._refresh_summary()
        self._update_source_label()

        # 窗口打开期间持续检测 MAA 关卡缓存是否更新，更新后自动刷新下拉列表
        self._watch_timer = QTimer(self)
        self._watch_timer.setInterval(5000)
        self._watch_timer.timeout.connect(self._auto_refresh_stages)
        self._watch_timer.start()

    # ---------- 数据 ----------

    def _initial_state(self):
        plan = self.acc.get("second_fight_plan")
        if isinstance(plan, list) and len(plan) > 0:
            plan = [str(s).strip() for s in plan]
            return plan, bool(self.acc.get("second_fight_use_optional", True))
        return maa_second_fight_plan(self.cfg, self.server)

    def _on_pick_stage(self):
        if self.picker.currentText() == PICKER_PLACEHOLDER:
            return
        stage = self.picker.currentData()
        if not isinstance(stage, str):
            stage = self.picker.currentText()
        stage = str(stage)
        if self.optional_sw.isChecked():
            if stage not in self.selected:
                self.selected.append(stage)
        else:
            # 不启用备选：只保留一个，直接替换
            self.selected = [stage]
        self.picker.setCurrentIndex(0)
        self._refresh_tags()
        self._refresh_summary()

    def _on_optional_toggled(self, checked):
        if not checked and len(self.selected) > 1:
            self.selected = self.selected[:1]
            self._refresh_tags()
            self._refresh_summary()

    def _move(self, index, delta):
        j = index + delta
        if j < 0 or j >= len(self.selected):
            return
        self.selected[index], self.selected[j] = self.selected[j], self.selected[index]
        self._refresh_tags()
        self._refresh_summary()

    def _remove(self, index):
        if 0 <= index < len(self.selected):
            self.selected.pop(index)
            self._refresh_tags()
            self._refresh_summary()

    def _chip(self, stage, index):
        """固定高度的候选标签：序号 + 关卡名 + 上移/下移/删除。"""
        chip = QFrame()
        chip.setObjectName("stageTag")
        chip.setFixedHeight(40)
        chip.setStyleSheet(
            "QFrame { background: %s; border: 1px solid %s;"
            " border-radius: 8px; }" % (theme.BG, theme.BORDER))
        lay = QHBoxLayout(chip)
        lay.setContentsMargins(10, 4, 6, 4)
        lay.setSpacing(6)
        rank = _label("候选 %d" % (index + 1), "12px", theme.TEXT_2, "600")
        rank.setFixedWidth(52)
        lay.addWidget(rank)
        name = _label(_stage_label(stage), "13px", theme.TEXT, "600")
        name.setStyleSheet(name.styleSheet() + "background: transparent;")
        lay.addWidget(name)
        lay.addStretch(1)
        for text, tip, fn in (("↑", "上移", lambda: self._move(index, -1)),
                              ("↓", "下移", lambda: self._move(index, 1)),
                              ("✕", "删除", lambda: self._remove(index))):
            btn = PushButton(text)
            btn.setFixedSize(30, 28)
            btn.setToolTip(tip)
            btn.clicked.connect(fn)
            lay.addWidget(btn)
        return chip

    def _refresh_tags(self):
        while self.tags_layout.count():
            item = self.tags_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        if not self.selected:
            empty = _label("还没有选择关卡，从上方下拉选择后点「添加候选关卡」",
                           "12px", theme.TEXT_3)
            empty.setStyleSheet(empty.styleSheet() + "background: transparent;")
            self.tags_layout.addWidget(empty)
            self.tags_layout.addStretch(1)
            return
        # 每行两个固定格子：单个候选也占“两列中的一格”，和多个时大小一致
        for i in range(0, len(self.selected), 2):
            row = QWidget()
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(10)
            lay.addWidget(self._chip(self.selected[i], i), 1)
            if i + 1 < len(self.selected):
                lay.addWidget(self._chip(self.selected[i + 1], i + 1), 1)
            else:
                spacer = QWidget()
                spacer.setMinimumHeight(1)
                lay.addWidget(spacer, 1)
            self.tags_layout.addWidget(row)
        self.tags_layout.addStretch(1)

    def _refresh_add_btn(self, *_):
        self.add_btn.setEnabled(self.picker.currentText() != PICKER_PLACEHOLDER)

    def _populate_picker(self):
        self.picker.blockSignals(True)
        self.picker.clear()
        self.picker.addItem(PICKER_PLACEHOLDER, userData="")
        for display, value in self.stage_options:
            self.picker.addItem(display, userData=value)
        self.picker.setCurrentIndex(0)
        self.picker.blockSignals(False)

    def _update_source_label(self):
        if self._maa_dir is None:
            self.source_lab.setText("关卡列表跟随 MAA 自动更新")
            self.source_lab.setToolTip("")
            return
        try:
            stat = (self._maa_dir / "cache" / "gui" / "StageActivityV2.json").stat()
            updated = datetime.fromtimestamp(stat.st_mtime).strftime("%m-%d %H:%M:%S")
        except OSError:
            updated = "无缓存"
        text = "数据源：%s（更新于 %s）" % (self._maa_dir.name or self._maa_dir, updated)
        self.source_lab.setText(text)
        self.source_lab.setToolTip("关卡列表读取：%s" % self._maa_dir)
        self.source_lab.setStyleSheet(
            self.source_lab.styleSheet()
            + " background: transparent;")

    def _auto_refresh_stages(self):
        """MAA 的 StageActivityV2.json 更新后，自动把新活动关卡加进下拉。"""
        if self._maa_dir is None:
            return
        sig = _cache_signature(self._maa_dir)
        if sig is None or sig == self._picker_sig:
            return
        self._picker_sig = sig
        old_text = self.picker.currentText()
        self.stage_options = _maa_stage_options(self._maa_dir)
        self._populate_picker()
        self._update_source_label()
        if old_text and old_text != PICKER_PLACEHOLDER:
            index = self.picker.findText(old_text)
            if index < 0:
                index = 0
            self.picker.setCurrentIndex(index)
        self._refresh_add_btn()

    def _refresh_summary(self):
        if self.selected:
            self.summary.setText("当前：%s" % format_stage_plan(self.selected))
        else:
            self.summary.setText("当前：跟随 MAA 原设置")

    def _on_save(self):
        plan = list(self.selected)
        use_optional = self.optional_sw.isChecked()
        if not use_optional and len(plan) > 1:
            plan = plan[:1]
        self.acc["second_fight_plan"] = plan
        self.acc["second_fight_use_optional"] = use_optional
        appconfig.save(self.cfg)
        self.accept()


def show_stage_plan_dialog(cfg, acc, parent=None):
    dlg = StagePlanDialog(cfg, acc, parent=parent)
    if not dlg.exec():
        return False
    plan = acc.get("second_fight_plan") or []
    if plan:
        InfoBar.success(
            "候选关卡已保存",
            "下次运行该账号时将按「%s」写入 MAA 第二个理智作战。"
            % format_stage_plan(plan),
            parent=parent.window() if parent is not None else None,
            position=InfoBarPosition.TOP_RIGHT,
            duration=4000)
    return True
