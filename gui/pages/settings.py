# -*- coding: utf-8 -*-
"""运行设置：程序路径 / 连接与超时 / 行为开关 / 数据清理。保存 → config.json + 计划任务同步。"""
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (BodyLabel, ComboBox, InfoBar, InfoBarPosition,
                            LineEdit, MessageBox, PrimaryPushButton, PushButton,
                            ScrollArea, SpinBox, SwitchButton)

import config as appconfig
import theme
from core import cleanup, maa_setup, notify, poller, runner
from widgets import (Card, set_switch_checked_gray, style_button,
                     style_primary_button, style_scroll_area)

PATH_KEYS = (
    ("maa_official", "MAA 官服"),
    ("maa_bilibili", "MAA B 服"),
    ("adb", "ADB 程序"),
    ("cli", "MuMu CLI"),
)

def _row_label(text, width=96):
    lab = BodyLabel(text)
    theme.bind(lab, lambda: "font-family: %s; %s color: %s;"
               % (theme.FONT_FAMILY, theme.font_stack(theme.FS_BODY),
                  theme.TEXT_2))
    lab.setFixedWidth(width)
    return lab


class PaletteSwatch(QWidget):
    """配色方案色卡：左半亮色底、右半暗色底、中间主题色圆点；选中画描边圈。

    颜色每次绘制时从 theme.palette_preview 实时取（不随当前明暗切换变化，
    色卡本身就是两套底色的并排预览）；主题切换后主窗口全量 update 会重画。
    """
    clicked = Signal()

    def __init__(self, name, label, parent=None):
        super().__init__(parent)
        self.name = name
        self._label = label
        self._selected = False
        self.setFixedSize(64, 40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(label)

    def set_selected(self, on):
        if on != self._selected:
            self._selected = bool(on)
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 8, 8)
        pv = theme.palette_preview(self.name)
        p.setClipPath(path)
        w, h = r.width(), r.height()
        p.fillRect(QRectF(r.left(), r.top(), w / 2, h), QColor(pv["light_bg"]))
        p.fillRect(QRectF(r.left() + w / 2, r.top(), w / 2, h), QColor(pv["dark_bg"]))
        p.setPen(QPen(Qt.PenStyle.NoPen))
        p.setBrush(QColor(pv["accent"]))
        p.drawEllipse(r.center(), 5, 5)
        p.setClipping(False)
        if self._selected:
            p.setPen(QPen(QColor(theme.ACCENT), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 8, 8)
        else:
            p.setPen(QPen(QColor(*theme.HAIRLINE), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 8, 8)


class SettingsPage(ScrollArea):
    def __init__(self, cfg, on_theme_change=None):
        super().__init__()
        self.cfg = cfg
        self._on_theme_change = on_theme_change
        self._apply_worker = None    # 计划任务同步后台线程（保存后）
        self._apply_pending = False  # 同步期间又保存过：结束后再同步一次
        self._setup_worker = None    # MAA 服务器配置后台线程（可能复制整套目录）
        self._notify_worker = None   # 测试推送后台线程（HTTP 最长十几秒）
        self.view = QWidget()
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        # 视口透明化 + 浅色细滚动条；右侧让出 12px 给滑块
        style_scroll_area(self)
        root = QVBoxLayout(self.view)
        root.setContentsMargins(12, 16, 12, 16)
        root.setSpacing(16)

        # ---- 外观（明暗主题 × 配色方案）----
        self.appearance_card = Card("外观")
        self.theme_combo = ComboBox()
        self.theme_combo.addItems(["明亮", "暗夜"])
        self.theme_combo.setFixedWidth(140)
        self.theme_combo.setCurrentIndex(
            1 if (cfg.get("appearance") or {}).get("theme") == "dark" else 0)
        self.theme_combo.currentIndexChanged.connect(self._on_appearance_changed)
        appear_row = QHBoxLayout()
        appear_row.setSpacing(10)
        appear_row.addWidget(_row_label("界面主题"))
        appear_row.addWidget(self.theme_combo)
        appear_hint = BodyLabel("切换后界面立即刷新")
        theme.bind(appear_hint, lambda: "font-family: %s; %s color: %s;"
                   % (theme.FONT_FAMILY, theme.font_stack(theme.FS_CAPTION), theme.TEXT_3))
        appear_row.addWidget(appear_hint)
        appear_row.addStretch(1)
        self.appearance_card.vbox.addLayout(appear_row)

        # 配色方案（背景色调）：每个方案都有明 / 暗两套，与上面主题组合生效。
        # 用一排色卡直接预览选择（左亮右暗 + 主题色圆点）；下拉框保留为数据源
        # （隐藏），选中/回退都走原有信号路径，色卡随 currentData 同步高亮
        self.palette_combo = ComboBox()
        self._palette_items = theme.palette_labels()
        for _name, label in self._palette_items:
            self.palette_combo.addItem(label, userData=_name)
        cur = (cfg.get("appearance") or {}).get("palette") or theme.DEFAULT_PALETTE
        idx = self.palette_combo.findData(cur)
        self.palette_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.palette_combo.currentIndexChanged.connect(self._on_appearance_changed)
        palette_row = QHBoxLayout()
        palette_row.setSpacing(12)
        palette_row.addWidget(_row_label("配色方案"))
        self.palette_swatches = []
        for _name, label in self._palette_items:
            swatch = PaletteSwatch(_name, label)
            swatch.clicked.connect(
                lambda _=False, n=_name: self._pick_palette(n))
            palette_row.addWidget(swatch)
            self.palette_swatches.append(swatch)
        palette_row.addStretch(1)
        self.appearance_card.vbox.addLayout(palette_row)
        # 提示独立成行（窄窗口下色卡行放不下，同行会把整卡撑出横向滚动）
        palette_hint = BodyLabel("背景色调预览（左亮 / 右暗，圆点为主题色）；点击立即切换")
        theme.bind(palette_hint, lambda: "font-family: %s; %s color: %s;"
                   % (theme.FONT_FAMILY, theme.font_stack(theme.FS_CAPTION), theme.TEXT_3))
        palette_hint.setWordWrap(True)
        self.appearance_card.vbox.addSpacing(2)
        self.appearance_card.vbox.addWidget(palette_hint)
        self.appearance_card.vbox.addSpacing(10)
        root.addWidget(self.appearance_card)
        self._sync_palette_swatches()

        # ---- 宽窗口双栏：左 = 程序路径 / 连接与超时 / 通知推送；
        #      右 = 行为开关 / MAA 服务器配置 / 数据清理 / 保存。
        #      窄窗口下各卡片本就是可压缩的行式布局，滚动区兜底 ----
        cols = QHBoxLayout()
        cols.setSpacing(16)
        left_col = QVBoxLayout()
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(16)
        right_col = QVBoxLayout()
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(16)

        # ---- 程序路径 ----
        self.path_card = Card("程序路径")
        self.path_edits = {}
        for key, label in PATH_KEYS:
            edit = LineEdit()
            edit.setClearButtonEnabled(False)
            browse = style_button(PushButton("浏览…"), small=True)
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
        left_col.addWidget(self.path_card)

        # ---- 连接与超时 / 行为开关：固定上下两张通栏卡片，不随宽度重排 ----
        self.conn_card = Card("连接与超时")
        self.device_edit = LineEdit()
        self.device_edit.setClearButtonEnabled(False)
        self._card_row(self.conn_card, "ADB 地址", self.device_edit)
        self.maa_spin = SpinBox()
        self.maa_spin.setRange(1, 180)
        self._card_row(self.conn_card, "无进展超时", self.maa_spin,
                       "分钟（默认 3）：期间没有战斗/任务推进才放弃该号")
        self.launch_spin = SpinBox()
        self.launch_spin.setRange(10, 600)
        self._card_row(self.conn_card, "启动等待", self.launch_spin,
                       "秒（模拟器启动等待上限，开机就绪即继续，通常等不满）")
        self.update_spin = SpinBox()
        self.update_spin.setRange(5, 360)
        self._card_row(self.conn_card, "更新等待上限", self.update_spin,
                       "分钟（干员导出前等待游戏更新的上限；挂机的更新等待内置于登录检查，上限 2 小时）")
        self.behavior_card = Card("行为开关")
        self.close_emu_sw = set_switch_checked_gray(SwitchButton(), "")
        self._card_row(self.behavior_card, "完成后关模拟器", self.close_emu_sw)
        update_hint = BodyLabel(
            "游戏更新等待已内置于登录检查：检测到更新界面/安装器只等待不点击，"
            "最长 2 小时；更新失败会立即判该号失败，不空跑")
        theme.bind(update_hint, lambda: "color: %s; %s" % (theme.TEXT_3, theme.font_stack(theme.FS_CAPTION)))
        update_hint.setWordWrap(True)
        self.behavior_card.vbox.addWidget(update_hint)
        self.behavior_card.vbox.addSpacing(10)
        shutdown_hint = BodyLabel(
            "每个启动时间的「关机」开关在仪表盘「班次计划」中设置（60 秒倒计时）")
        theme.bind(shutdown_hint, lambda: "color: %s; %s" % (theme.TEXT_3, theme.font_stack(theme.FS_CAPTION)))
        shutdown_hint.setWordWrap(True)
        self.behavior_card.vbox.addWidget(shutdown_hint)
        self.behavior_card.vbox.addSpacing(10)
        acc_hint = BodyLabel("账号增删 / 启用 / 捕获请到「账号管理」页")
        theme.bind(acc_hint, lambda: "color: %s; %s" % (theme.TEXT_3, theme.font_stack(theme.FS_CAPTION)))
        self.behavior_card.vbox.addWidget(acc_hint)
        left_col.addWidget(self.conn_card)
        right_col.addWidget(self.behavior_card)

        # ---- MAA 服务器配置 ----
        self.maa_setup_card = Card("MAA 服务器配置")
        hint = BodyLabel(
            "一键修正对应服务器 MAA 的关键配置（客户端类型 / ADB / 直接运行 / "
            "结束脚本 / 常用任务），目录缺失时自动从另一服复制一份。")
        hint.setWordWrap(True)
        theme.bind(hint, lambda: "color: %s; %s" % (theme.TEXT_3, theme.font_stack(theme.FS_CAPTION)))
        self.maa_setup_card.vbox.addWidget(hint)
        self.maa_setup_card.vbox.addSpacing(10)
        setup_row = QHBoxLayout()
        setup_row.setSpacing(10)
        self.maa_official_btn = style_button(PushButton("配置官服 MAA"))
        self.maa_official_btn.setToolTip("修正官服 MAA：客户端类型 Official、ADB、直接运行、结束脚本、常用任务")
        self.maa_official_btn.clicked.connect(lambda: self._on_maa_setup("official"))
        self.maa_bili_btn = style_button(PushButton("配置B服 MAA"))
        self.maa_bili_btn.setToolTip("修正B服 MAA：客户端类型 Bilibili、ADB、直接运行、结束脚本、常用任务")
        self.maa_bili_btn.clicked.connect(lambda: self._on_maa_setup("bilibili"))
        setup_row.addWidget(self.maa_official_btn)
        setup_row.addWidget(self.maa_bili_btn)
        setup_row.addStretch(1)
        self.maa_setup_card.vbox.addLayout(setup_row)
        self.maa_setup_card.vbox.addSpacing(10)
        right_col.addWidget(self.maa_setup_card)

        # ---- 通知推送 ----
        self.notify_card = Card("通知推送")
        notify_hint = BodyLabel(
            "按账号逐步骤检查（切号 → 登录校验 → 任务自检 → MAA）："
            "某个步骤失败的账号单独推送一条，标题含账号名与失败步骤；"
            "成功默认不推送，可开「成功也推送」收每轮极简汇总。"
            "无人值守（自动关机）场景下，失败不再只有本机弹窗。")
        notify_hint.setWordWrap(True)
        theme.bind(notify_hint, lambda: "color: %s; %s" % (theme.TEXT_3, theme.font_stack(theme.FS_CAPTION)))
        self.notify_card.vbox.addWidget(notify_hint)
        self.notify_card.vbox.addSpacing(10)
        self.notify_sw = set_switch_checked_gray(SwitchButton(), "")
        self._card_row(self.notify_card, "启用推送", self.notify_sw)
        self.notify_provider = ComboBox()
        self.notify_provider.addItems(
            ["Server酱（sct.ftqq.com）", "PushPlus（pushplus.plus）", "企业微信机器人"])
        self.notify_provider.setFixedWidth(260)
        self._card_row(self.notify_card, "推送渠道", self.notify_provider)
        self.notify_key = LineEdit()
        self.notify_key.setClearButtonEnabled(False)
        self.notify_key.setPlaceholderText("SendKey / token / webhook 地址或 key")
        key_row = QHBoxLayout()
        key_row.setSpacing(10)
        key_row.addWidget(_row_label("推送密钥"))
        key_row.addWidget(self.notify_key, 1)
        self.notify_card.vbox.addLayout(key_row)
        self.notify_success_sw = set_switch_checked_gray(SwitchButton(), "")
        self._card_row(self.notify_card, "成功也推送", self.notify_success_sw,
                       "开启后全部账号成功也发一条极简汇总（班次 + 每号用时）")
        self.notify_card.vbox.addSpacing(10)
        test_row = QHBoxLayout()
        test_row.setSpacing(10)
        self.notify_test_btn = style_button(PushButton("发送测试"))
        self.notify_test_btn.setToolTip(
            "用上方当前填写的渠道与密钥发一条测试消息。\n"
            "测试成功会自动保存这组通知设置（渠道/密钥/开关/成功开关），不必再点「保存配置」。\n"
            "Server酱填 SendKey；PushPlus 填 token；企业微信机器人填 webhook 地址或 key。")
        self.notify_test_btn.clicked.connect(self.on_notify_test)
        test_row.addWidget(_row_label("测试"))
        test_row.addWidget(self.notify_test_btn)
        test_row.addStretch(1)
        self.notify_card.vbox.addLayout(test_row)
        self.notify_card.vbox.addSpacing(10)
        left_col.addWidget(self.notify_card)

        # ---- 数据清理 ----
        self.clean_card = Card("数据清理")
        self.clean_auto_sw = set_switch_checked_gray(SwitchButton(), "")
        self._card_row(self.clean_card, "自动清理", self.clean_auto_sw,
                       "控制台运行期间到期自动清理（挂机中不清理）")
        self.clean_interval = SpinBox()
        self.clean_interval.setRange(1, 30)
        self._card_row(self.clean_card, "清理间隔", self.clean_interval, "天（默认 7）")
        clean_row = QHBoxLayout()
        clean_row.setSpacing(10)
        clean_btn = style_button(PushButton("立即清理"))
        clean_btn.clicked.connect(self.on_clean)
        clean_row.addWidget(_row_label("手动清理"))
        clean_row.addWidget(clean_btn)
        clean_row.addStretch(1)
        self.clean_card.vbox.addLayout(clean_row)
        self.clean_hint = BodyLabel("")
        theme.bind(self.clean_hint, lambda: "color: %s; %s" % (theme.TEXT_3, theme.font_stack(theme.FS_CAPTION)))
        self.clean_card.vbox.addWidget(self.clean_hint)
        self.clean_card.vbox.addSpacing(10)
        right_col.addWidget(self.clean_card)

        # ---- 保存 ----
        self.action_card = Card()
        bar = QHBoxLayout()
        bar.setSpacing(10)
        save_btn = style_primary_button(PrimaryPushButton("保存配置"))
        save_btn.clicked.connect(self.on_save)
        reset_btn = style_button(PushButton("恢复默认"))
        reset_btn.clicked.connect(self.on_reset)
        bar.addWidget(save_btn)
        bar.addWidget(reset_btn)
        bar.addStretch(1)
        hint = BodyLabel("保存后计划任务自动更新，无需重启")
        theme.bind(hint, lambda: "color: %s; %s" % (theme.TEXT_3, theme.font_stack(theme.FS_CAPTION)))
        bar.addWidget(hint)
        self.action_card.vbox.addLayout(bar)
        right_col.addWidget(self.action_card)
        left_col.addStretch(1)
        right_col.addStretch(1)
        cols.addLayout(left_col, 1)
        cols.addLayout(right_col, 1)
        root.addLayout(cols)
        root.addStretch(1)
        # 版式固定双列不随宽度重排：窗口最小宽度已由主窗口限制在
        # 双列能完整显示的尺寸（见 MainWindow.setMinimumSize）

        self.load_from_cfg()

    def _pick_palette(self, name):
        """点色卡：切到对应配色（走下拉框同一信号路径）；已是当前值则只刷高亮。"""
        idx = self.palette_combo.findData(name)
        if idx < 0:
            return
        if idx != self.palette_combo.currentIndex():
            self.palette_combo.setCurrentIndex(idx)
        else:
            self._sync_palette_swatches()

    def _sync_palette_swatches(self):
        """色卡高亮跟随下拉框当前值（切换被拒绝回退后也走这里复位）。"""
        cur = self.palette_combo.currentData()
        for sw in self.palette_swatches:
            sw.set_selected(sw.name == cur)

    def _on_appearance_changed(self, _index=None):
        """明暗主题或配色方案变化：回调成功后由主窗口就地换肤；被拒绝则回退。"""
        name = "dark" if self.theme_combo.currentIndex() == 1 else "light"
        palette_name = self.palette_combo.currentData() or theme.DEFAULT_PALETTE
        appearance = self.cfg.get("appearance") or {}
        if (appearance.get("theme", "light") == name
                and appearance.get("palette", theme.DEFAULT_PALETTE) == palette_name):
            return   # 组合没变（另一行下拉触发）：不动
        if self._on_theme_change is None or not self._on_theme_change(name, palette_name):
            self.theme_combo.blockSignals(True)
            self.palette_combo.blockSignals(True)
            self.theme_combo.setCurrentIndex(
                1 if appearance.get("theme", "light") == "dark" else 0)
            idx = self.palette_combo.findData(
                appearance.get("palette", theme.DEFAULT_PALETTE))
            self.palette_combo.setCurrentIndex(idx if idx >= 0 else 0)
            self.theme_combo.blockSignals(False)
            self.palette_combo.blockSignals(False)
        self._sync_palette_swatches()

    def _card_row(self, card, label, widget, hint=None):
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(_row_label(label))
        row.addWidget(widget, 0)
        if hint:
            h = BodyLabel(hint)
            h.setWordWrap(True)   # 双栏下卡片变窄，提示文字换行不裁字
            theme.bind(h, lambda: "color: %s; %s" % (theme.TEXT_3, theme.font_stack(theme.FS_CAPTION)))
            row.addWidget(h, 1)
        else:
            row.addStretch(1)
        card.vbox.addLayout(row)
        card.vbox.addSpacing(10)

    def _fill(self, source):
        for key, edit in self.path_edits.items():
            edit.setText(source["paths"].get(key, ""))
        self.device_edit.setText(source["paths"].get("device", ""))
        self.maa_spin.setValue(int(source["timeouts"].get("maa_min", 3)))
        self.launch_spin.setValue(int(source["timeouts"].get("launch_wait_sec", 120)))
        self.update_spin.setValue(int(source["timeouts"].get("game_update_min", 90)))
        self.close_emu_sw.setChecked(bool(source["behavior"].get("close_emulator", True)))
        c = source.get("cleanup") or {}
        self.clean_auto_sw.setChecked(bool(c.get("auto", True)))
        self.clean_interval.setValue(int(c.get("interval_days", 7)))
        self._refresh_clean_hint()
        n = source.get("notify") or {}
        self.notify_sw.setChecked(bool(n.get("enabled", False)))
        self.notify_provider.setCurrentIndex(
            notify.PROVIDERS.index(n["provider"])
            if n.get("provider") in notify.PROVIDERS else 0)
        self.notify_key.setText(str(n.get("key") or ""))
        self.notify_success_sw.setChecked(bool(n.get("on_success", False)))

    def _refresh_clean_hint(self):
        self.clean_hint.setText(cleanup.last_run_text(self.cfg))

    def load_from_cfg(self):
        self._fill(self.cfg)

    def on_reset(self):
        self._fill(appconfig.DEFAULTS)
        InfoBar.info("已恢复默认值", "点击「保存配置」后生效",
                     parent=self.window(), position=InfoBarPosition.TOP_RIGHT, duration=3000)

    def on_save(self):
        paths_cfg = self.cfg.setdefault("paths", {})
        for key, edit in self.path_edits.items():
            paths_cfg[key] = edit.text().strip()
        paths_cfg["device"] = self.device_edit.text().strip()
        # script_dir 是所有脚本的定位根（本页不提供编辑框，只防它被外部清空）
        if not paths_cfg.get("script_dir"):
            InfoBar.error("无法保存", "「script_dir」不能为空", parent=self.window(),
                          position=InfoBarPosition.TOP_RIGHT, duration=5000)
            return
        self.cfg["timeouts"]["maa_min"] = self.maa_spin.value()
        self.cfg["timeouts"]["launch_wait_sec"] = self.launch_spin.value()
        self.cfg["timeouts"]["game_update_min"] = self.update_spin.value()
        self.cfg["behavior"]["close_emulator"] = self.close_emu_sw.isChecked()
        c = self.cfg.setdefault("cleanup", {})
        c["auto"] = self.clean_auto_sw.isChecked()
        c["interval_days"] = self.clean_interval.value()
        n = self.cfg.setdefault("notify", {})
        n["enabled"] = self.notify_sw.isChecked()
        n["provider"] = notify.PROVIDERS[self.notify_provider.currentIndex()]
        n["key"] = self.notify_key.text().strip()
        # v4.3 恢复成功推送（master.ps1 成功汇总推送读此开关）；曾于 v2.0.0
        # 废除过，保存时顺手清理旧值的逻辑随之删除
        n["on_success"] = self.notify_success_sw.isChecked()

        try:
            appconfig.save(self.cfg)
        except OSError as exc:
            InfoBar.error("保存失败", "写入 config.json 失败：%s" % exc,
                          parent=self.window(),
                          position=InfoBarPosition.TOP_RIGHT, duration=8000)
            return
        # 填了但不存在的路径给个提醒（不阻断保存，可能是还没装）
        missing = [label for key, label in PATH_KEYS
                   if self.path_edits[key].text().strip()
                   and not Path(self.path_edits[key].text().strip()).exists()]
        if missing:
            InfoBar.warning("以下程序路径不存在：%s" % "、".join(missing),
                            "请确认是否填错", parent=self.window(),
                            position=InfoBarPosition.TOP_RIGHT, duration=6000)
        # 计划任务同步（1~40 秒）放后台，保存动作本身立即完成
        if self._apply_worker is not None and self._apply_worker.isRunning():
            # 上一次同步用的是提交时的配置快照；这次保存记 pending，
            # 等它结束后按最新配置再同步一次
            self._apply_pending = True
            InfoBar.info("上一次计划任务同步仍在进行", "配置已保存，稍后自动按新配置同步",
                         parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                         duration=5000)
            return
        self._start_apply_worker()

    def _start_apply_worker(self):
        self._apply_worker = poller.SchedulerApplyWorker(self.cfg, self)
        self._apply_worker.done.connect(self._on_apply_done)
        self._apply_worker.start()

    def _on_apply_done(self, ok, msg, _info):
        if self._apply_pending:
            self._apply_pending = False
            # done 在 run() 末尾发出，旧 worker 的工作已结束（线程可能尚未
            # 完全退出），直接起新 worker
            self._start_apply_worker()
            return
        if ok:
            InfoBar.success("配置已保存", "计划任务已同步更新",
                            parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                            duration=3000)
        else:
            InfoBar.warning("配置已保存，但计划任务未更新", msg,
                            parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                            duration=6000)

    def on_notify_test(self):
        """发送测试推送：用界面当前值（不先保存），HTTP 放后台线程。"""
        if self._notify_worker is not None and self._notify_worker.isRunning():
            InfoBar.info("测试推送进行中", "请等上一次测试结束",
                         parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                         duration=3000)
            return
        if not self.notify_sw.isChecked():
            InfoBar.warning("推送未启用", "先打开「启用推送」开关再测试",
                            parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                            duration=4000)
            return
        key = self.notify_key.text().strip()
        if not key:
            InfoBar.warning("缺少推送密钥", "请先填写所选渠道的密钥",
                            parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                            duration=4000)
            return
        provider = notify.PROVIDERS[self.notify_provider.currentIndex()]
        name = self.notify_provider.currentText().split("（")[0]
        self.notify_test_btn.setEnabled(False)
        self._notify_worker = poller.FuncWorker(
            lambda: notify.send_test(provider, key), self)
        self._notify_worker.done.connect(
            lambda res, n=name: self._on_notify_test_done(res, n))
        self._notify_worker.start()

    def _on_notify_test_done(self, res, name):
        self.notify_test_btn.setEnabled(True)
        self._notify_worker = None
        state, payload = res if isinstance(res, tuple) else ("error", "未知错误")
        if state != "ok":
            InfoBar.error("测试推送失败", str(payload),
                          parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                          duration=8000)
            return
        ok, summary = payload
        if ok:
            # 测试成功 = 这组渠道/密钥可用：顺手保存整组通知设置。以前测试不落盘，
            # 只点测试不点「保存配置」的话重启后密钥/开关回到旧值，像配置丢失一样
            n = self.cfg.setdefault("notify", {})
            n["enabled"] = self.notify_sw.isChecked()
            n["provider"] = notify.PROVIDERS[self.notify_provider.currentIndex()]
            n["key"] = self.notify_key.text().strip()
            n["on_success"] = self.notify_success_sw.isChecked()
            saved_note = "· 通知设置已自动保存"
            try:
                appconfig.save(self.cfg)
            except OSError as exc:
                saved_note = ""
                InfoBar.error("自动保存失败",
                              "测试已通过，但写入 config.json 失败：%s（请手动点「保存配置」）" % exc,
                              parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                              duration=8000)
            InfoBar.success("测试推送已发送", "请检查手机是否收到（%s）%s" % (name, saved_note),
                            parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                            duration=5000)
        else:
            InfoBar.warning("测试推送失败", summary or "请检查渠道与密钥",
                            parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                            duration=8000)

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
        if self._setup_worker is not None and self._setup_worker.isRunning():
            InfoBar.info("MAA 配置正在进行中", "请等当前操作结束后再试",
                         parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                         duration=4000)
            return
        name = "官服" if server == "official" else "B服"
        client = "Official" if server == "official" else "Bilibili"
        box = MessageBox(
            "配置%s MAA" % name,
            "将检查并修正 %s MAA 的关键配置：\n\n"
            "· 客户端类型（%s）\n· ADB 路径与地址\n"
            "· RunDirectly（直接运行）\n· 结束脚本 signal_done.bat\n"
            "· 启用常用任务\n\n"
            "若该服 MAA 目录不存在，会自动从另一服复制一份再修正（可能耗时较久，"
            "期间请勿关闭窗口）。\n修改前会先备份原配置文件。确定继续吗？" % (name, client),
            self.window())
        box.yesButton.setText("开始配置")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        # 目录缺失时会 copytree 整套 MAA（数百 MB），放后台跑避免界面冻结
        self.maa_official_btn.setEnabled(False)
        self.maa_bili_btn.setEnabled(False)
        self._setup_worker = poller.FuncWorker(
            lambda: maa_setup.apply_server_config(self.cfg, server), self)
        self._setup_worker.done.connect(
            lambda result, n=name: self._on_setup_done(n, result))
        self._setup_worker.start()

    def _on_setup_done(self, name, result):
        self.maa_official_btn.setEnabled(True)
        self.maa_bili_btn.setEnabled(True)
        state, payload = result
        if state == "error":
            InfoBar.error("%s MAA 配置失败" % name, str(payload),
                          parent=self.window(),
                          position=InfoBarPosition.TOP_RIGHT, duration=8000)
            return
        ok, msg = payload
        if ok:
            InfoBar.success("%s MAA 配置完成" % name, msg,
                            parent=self.window(),
                            position=InfoBarPosition.TOP_RIGHT, duration=6000)
        else:
            InfoBar.error("%s MAA 配置失败" % name, msg,
                          parent=self.window(),
                          position=InfoBarPosition.TOP_RIGHT, duration=8000)

    def _browse(self, key):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择程序", "", "程序 (*.exe);;所有文件 (*.*)")
        if path:
            self.path_edits[key].setText(path)
