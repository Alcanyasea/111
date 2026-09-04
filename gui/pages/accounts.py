# -*- coding: utf-8 -*-
"""账号管理页：增删账号、启用/停用、捕获登录数据（槽位）。

- 添加账号：控制台输入账号/密码 → 调 capture_account.ps1 自动登录并拉取
  登录数据到 scripts\\accounts\\<slot>\\；特殊字符密码或验证码时自动转人工登录。
- 删除账号：仅从列表移除（槽位文件保留在磁盘，如需彻底删除可手动清理）。
- 切换账号不再走游戏内点击流程：master.ps1 用 slot_switch.ps1 重启游戏+推入数据。
"""
import subprocess
import uuid
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QDialog, QGridLayout, QHBoxLayout,
                               QPlainTextEdit, QVBoxLayout, QWidget)

from qfluentwidgets import (BodyLabel, ComboBox, InfoBar, InfoBarPosition,
                            LineEdit, MessageBox, PrimaryPushButton, PushButton,
                            ScrollArea, SubtitleLabel, SwitchButton)

import config as appconfig
import theme
from pages.stage_plan_dialog import (format_stage_plan, maa_second_fight_plan,
                                     show_stage_plan_dialog)
from widgets import Card, IconBadge, Pill

CREATE_NO_WINDOW = 0x08000000

SERVER_LABELS = {"official": "官服", "bilibili": "B 服"}


def slot_uid(cfg, slot):
    """读取槽位 uid.txt；槽位不存在返回 None。"""
    if not slot:
        return None
    p = Path(cfg["paths"]["script_dir"]) / "accounts" / slot / "uid.txt"
    try:
        return p.read_text(encoding="ascii").strip() or None
    except OSError:
        return None


class CaptureWorker(QThread):
    """后台跑 capture_account.ps1：逐行转发输出，结束时发 done(exit_code)。

    subprocess.Popen + CREATE_NO_WINDOW（无控制台窗口闪现），
    阻塞 readline 放在工作线程里，GUI 线程不卡。
    """

    line = Signal(str)
    done = Signal(int)

    def __init__(self, args, parent=None):
        super().__init__(parent)
        self.args = args
        self.proc = None

    def run(self):
        try:
            self.proc = subprocess.Popen(
                self.args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW)
        except OSError as e:
            self.line.emit(">>> 启动捕获脚本失败：%s" % e)
            self.done.emit(-1)
            return
        for raw in iter(self.proc.stdout.readline, b""):
            self.line.emit(raw.decode("gbk", errors="replace").rstrip())
        self.proc.stdout.close()
        self.done.emit(self.proc.wait())

    def kill(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()


class CaptureDialog(QDialog):
    """添加/重新捕获账号：填账号密码 → 跑 capture_account.ps1，实时显示输出。"""

    def __init__(self, cfg, acc=None, page=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.acc = acc          # None=新增；dict=重新捕获（预填）
        self.page = page        # AccountsPage，成功后刷新列表
        self.worker = None
        self._slot = ""
        self._closing = False
        self.setWindowTitle("捕获账号" if acc is None else "重新捕获账号")
        self.setModal(True)
        self.resize(560, 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        form = QWidget()
        f = QVBoxLayout(form)
        f.setContentsMargins(0, 0, 0, 0)
        f.setSpacing(8)

        def make_row(label_text, widget):
            row = QHBoxLayout()
            row.setSpacing(10)
            lab = BodyLabel(label_text)
            lab.setStyleSheet("color: %s; font-size: 13px;" % theme.TEXT_2)
            lab.setFixedWidth(64)
            row.addWidget(lab)
            row.addWidget(widget, 1)
            return row

        self.name_edit = LineEdit()
        self.name_edit.setPlaceholderText("显示名称，如「官服 1」「小号」")
        f.addLayout(make_row("名称", self.name_edit))
        self.server_combo = ComboBox()
        self.server_combo.addItems(["官服", "B 服"])
        self.server_combo.setCurrentIndex(0)
        f.addLayout(make_row("服务器", self.server_combo))
        self.user_edit = LineEdit()
        self.user_edit.setPlaceholderText("游戏账号（手机号/邮箱/账号）")
        f.addLayout(make_row("账号", self.user_edit))
        self.pass_edit = LineEdit()
        self.pass_edit.setEchoMode(LineEdit.EchoMode.Password)
        self.pass_edit.setPlaceholderText("密码")
        f.addLayout(make_row("密码", self.pass_edit))
        root.addWidget(form)

        self.hint_label = BodyLabel(
            "账号密码仅保存在本机 config.json（明文）。密码含 % ^ _ + = [ ] 等特殊字符"
            "或登录出现验证码时，脚本会自动提示你在模拟器窗口手动完成登录。")
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet("color: %s; font-size: 12px;" % theme.TEXT_3)
        root.addWidget(self.hint_label)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet(
            "QPlainTextEdit { background: %s; color: %s; font-family: Consolas,"
            " 'Courier New', monospace; font-size: 12px; border: 1px solid %s;"
            " border-radius: 6px; }" % (theme.LOG_BG, theme.LOG_FG, theme.BORDER))
        self.log_view.setMinimumHeight(220)
        root.addWidget(self.log_view, 1)

        btns = QHBoxLayout()
        btns.setSpacing(10)
        self.start_btn = PrimaryPushButton("开始捕获")
        self.close_btn = PushButton("关闭")
        btns.addWidget(self.start_btn)
        btns.addStretch(1)
        btns.addWidget(self.close_btn)
        root.addLayout(btns)

        self.start_btn.clicked.connect(self.on_start)
        self.close_btn.clicked.connect(self.on_close)

        if acc is not None:
            self.name_edit.setText(acc.get("label", ""))
            self.server_combo.setCurrentIndex(
                1 if acc.get("server") == "bilibili" else 0)
            self.user_edit.setText(acc.get("username", ""))
            self.pass_edit.setText(acc.get("password", ""))
        self._set_running(False)

    def _append_log(self, line):
        self.log_view.appendPlainText(line)
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _set_running(self, running):
        self.start_btn.setEnabled(not running)
        self.name_edit.setEnabled(not running)
        self.server_combo.setEnabled(not running)
        self.user_edit.setEnabled(not running)
        self.pass_edit.setEnabled(not running)

    def _show_error(self, text):
        """对话框内的红色提示（InfoBar 在模态 QDialog 中可能不显示，用可见文案兜底）。"""
        self.hint_label.setText(text)
        self.hint_label.setStyleSheet("color: %s; font-size: 12px;" % theme.ERR)

    def on_start(self):
        label = self.name_edit.text().strip()
        username = self.user_edit.text().strip()
        password = self.pass_edit.text()
        if not label or not username or not password:
            self._show_error("名称 / 账号 / 密码不能为空")
            return
        server = "bilibili" if self.server_combo.currentIndex() == 1 else "official"
        self._slot = (self.acc or {}).get("slot") or ("acc_" + uuid.uuid4().hex[:8])

        script = Path(self.cfg["paths"]["script_dir"]) / "capture_account.ps1"
        args = [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(script),
            "-Server", server, "-Slot", self._slot,
            "-Username", username, "-Password", password, "-Label", label,
        ]
        self.log_view.clear()
        self._append_log(">>> 开始捕获：%s（%s）槽位 %s" % (label, SERVER_LABELS[server], self._slot))
        self._append_log(">>> 正在启动捕获脚本...")
        self.hint_label.setText(
            "捕获进行中：自动启动模拟器（如未运行）→ 清空登录态 → 重启游戏 → "
            "点掉弹窗 → 输入账号密码 → 拉取数据。特殊字符密码或验证码时请留意提示，"
            "在模拟器窗口手动登录。")
        self.hint_label.setStyleSheet("color: %s; font-size: 12px;" % theme.TEXT_3)

        self.worker = CaptureWorker(args, parent=self)
        self.worker.line.connect(self._append_log)
        self.worker.done.connect(self._on_finished)
        self.worker.start()
        self._set_running(True)

    def _on_finished(self, code):
        self.worker = None
        self._set_running(False)
        if self._closing:
            return
        if code == 0:
            self._append_log(">>> 捕获成功！点击关闭返回列表。")
            self._apply_result()
            InfoBar.success("捕获成功", "登录数据已保存到槽位",
                            parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                            duration=5000)
        else:
            self._append_log(">>> 捕获失败（退出码 %d）。可修改后重试；登录态已自动恢复。" % code)
            self._show_error("捕获失败，详见下方日志。可修改后点「开始捕获」重试。")

    def _apply_result(self):
        """成功：写入 cfg（新增或更新账号项）并保存。"""
        server = "bilibili" if self.server_combo.currentIndex() == 1 else "official"
        entry = {
            "id": (self.acc or {}).get("id") or ("acc_" + uuid.uuid4().hex[:8]),
            "label": self.name_edit.text().strip(),
            "server": server,
            "enabled": (self.acc or {}).get("enabled", True),
            "slot": self._slot,
            "username": self.user_edit.text().strip(),
            "password": self.pass_edit.text(),
            "second_fight_plan": list((self.acc or {}).get("second_fight_plan")
                                      or []),
            "second_fight_use_optional": bool(
                (self.acc or {}).get("second_fight_use_optional", True)),
            "base_schedule": appconfig.default_base_schedule(
                batches=appconfig.schedule_batches(self.cfg)),
        }
        if self.acc is not None:
            self.acc.update(entry)
        else:
            self.cfg["accounts"].append(entry)
        appconfig.save(self.cfg)
        if self.page is not None:
            self.page.refresh()

    def on_close(self):
        self._closing = True
        if self.worker is not None:
            self.worker.kill()
            self.worker.wait(3000)
            self.worker = None
        self.reject()

    def closeEvent(self, event):
        self.on_close()
        event.accept()


class AccountRow(Card):
    """单账号行：徽标 + 名称 + 服务器 + 槽位状态 + 启用开关 + 捕获/删除。"""

    def __init__(self, cfg, acc, index, page=None, parent=None):
        super().__init__(parent=parent)
        self.cfg = cfg
        self.acc = acc
        self.page = page
        server = acc.get("server", "official")
        colors = (theme.ACCENT_B if server == "bilibili"
                  else (theme.ACCENT_O1 if index % 2 == 0 else theme.ACCENT_O2))
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(IconBadge(str(index + 1), colors))
        name_box = QVBoxLayout()
        name_box.setSpacing(1)
        name = SubtitleLabel(acc.get("label", "?"))
        meta = BodyLabel("%s · 槽位 %s" % (SERVER_LABELS.get(server, server),
                                           acc.get("slot", "未设置")))
        meta.setStyleSheet("color: %s; font-size: 12px;" % theme.TEXT_2)
        name_box.addWidget(name)
        name_box.addWidget(meta)
        row.addLayout(name_box)
        row.addStretch(1)
        self.uid_pill = Pill()
        row.addWidget(self.uid_pill)
        self.base_sw = SwitchButton()
        self.base_sw.setText("精确基建")
        self.base_sw.setChecked(bool((acc.get("base_schedule") or {}).get("enabled", False)))
        self.base_sw.setToolTip("启用精确基建派驻；关闭时使用 MAA 自带基建换班")
        self.base_sw.checkedChanged.connect(self._on_base_toggle)
        row.addWidget(self.base_sw)
        self.base_btn = PushButton("基建")
        self.base_btn.setToolTip("精确选择各设施进驻干员（批次随启动时间，支持333/243布局）")
        self.base_btn.clicked.connect(self._on_base_config)
        row.addWidget(self.base_btn)
        self.sw = SwitchButton()
        self.sw.setChecked(bool(acc.get("enabled", True)))
        self.sw.checkedChanged.connect(self._on_toggle)
        row.addWidget(self.sw)
        self.cap_btn = PushButton("捕获")
        self.cap_btn.setToolTip("清空登录态并重新登录，拉取该账号的登录数据")
        self.cap_btn.clicked.connect(self._on_capture)
        row.addWidget(self.cap_btn)
        self.del_btn = PushButton("删除")
        self.del_btn.setStyleSheet(
            "PushButton { color: %s; border: 1px solid #e5b7b1; }" % theme.ERR)
        self.del_btn.clicked.connect(self._on_delete)
        row.addWidget(self.del_btn)
        self.vbox.addLayout(row)

        # 第二理智作战候选关卡：仿 MAA，点开用下拉逐行添加/调整候选
        sub = QHBoxLayout()
        sub.setSpacing(10)
        lab = BodyLabel("候选关卡:")
        lab.setStyleSheet("color: %s; font-size: 12px;" % theme.TEXT_2)
        sub.addWidget(lab)
        self.fight_btn = PushButton()
        self.fight_btn.setMinimumWidth(300)
        self.fight_btn.setToolTip(
            "编辑该账号第二个理智作战的候选关卡，界面与 MAA 一致：\n"
            "每个候选一行下拉关卡，可「＋ 添加 / ✕ 删除 / ↑↓ 调整顺序」。")
        self.fight_btn.clicked.connect(self._on_fight_plan)
        self._refresh_fight_plan_text()
        sub.addWidget(self.fight_btn)
        tip = BodyLabel("点开按 MAA 方式修改；留空 = 跟随 MAA")
        tip.setStyleSheet("color: %s; font-size: 12px;" % theme.TEXT_3)
        sub.addWidget(tip)
        sub.addStretch(1)
        self.vbox.addLayout(sub)
        self.refresh_uid()

    def _refresh_fight_plan_text(self):
        """按钮摘要：账号已设置 → 显示其候选；否则显示 MAA 当前映射。"""
        saved = self.acc.get("second_fight_plan")
        plan = [str(s).strip() for s in saved] if isinstance(saved, list) else []
        mapped = []
        if not plan:
            mapped = maa_second_fight_plan(self.cfg,
                                           self.acc.get("server"))[0]
        text = format_stage_plan(plan or mapped) or "跟随 MAA 原设置"
        self.fight_btn.setText("候选：%s ▾" % text)

    def refresh_uid(self):
        uid = slot_uid(self.cfg, self.acc.get("slot", ""))
        if uid:
            self.uid_pill.set_state("ok", "已捕获 UID %s" % uid)
        else:
            self.uid_pill.set_state("warn", "未捕获")

    def _on_toggle(self, checked):
        self.acc["enabled"] = bool(checked)
        appconfig.save(self.cfg)

    def _on_fight_plan(self):
        if show_stage_plan_dialog(self.cfg, self.acc, parent=self):
            self._refresh_fight_plan_text()

    def _on_base_toggle(self, checked):
        bs = self.acc.get("base_schedule")
        if not isinstance(bs, dict):
            bs = appconfig.default_base_schedule(
                batches=appconfig.schedule_batches(self.cfg))
            self.acc["base_schedule"] = bs
        bs["enabled"] = bool(checked)
        appconfig.save(self.cfg)

    def _on_base_config(self):
        from pages.base_schedule_dialog import show_base_schedule_dialog
        show_base_schedule_dialog(self.cfg, self.acc, parent=self)

    def _on_capture(self):
        dlg = CaptureDialog(self.cfg, self.acc, page=self.page, parent=self)
        dlg.exec()
        self.refresh_uid()

    def _on_delete(self):
        box = MessageBox(
            "删除账号", "确定从运行列表中删除「%s」吗？\n\n"
            "仅从列表移除，登录数据槽位文件保留在磁盘。" % self.acc.get("label", ""),
            self.window())
        box.yesButton.setText("删除")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        self.cfg["accounts"].remove(self.acc)
        appconfig.save(self.cfg)
        if self.page is not None:
            self.page.refresh()


class AccountDetailDialog(QDialog):
    """账号详细控制：捕获、基建配置、候选关卡、删除都集中在这里。"""

    def __init__(self, cfg, acc, page=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.acc = acc
        self.page = page
        self.setWindowTitle("账号详情 - %s" % (acc.get("label") or ""))
        self.setModal(True)
        self.resize(560, 430)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(14)

        # 头部：名称 + 服务器/槽位 + UID 状态
        server = acc.get("server", "official")
        colors = (theme.ACCENT_B if server == "bilibili"
                  else theme.ACCENT_O1)
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(IconBadge("1", colors))
        name_box = QVBoxLayout()
        name_box.setSpacing(2)
        name = SubtitleLabel(acc.get("label") or "?")
        meta = BodyLabel("%s · 槽位 %s" % (SERVER_LABELS.get(server, server),
                                           acc.get("slot", "未设置")))
        meta.setStyleSheet("color: %s; font-size: 12px;" % theme.TEXT_2)
        name_box.addWidget(name)
        name_box.addWidget(meta)
        head.addLayout(name_box)
        head.addStretch(1)
        self.uid_pill = Pill()
        head.addWidget(self.uid_pill, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(head)

        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet("background: %s;" % theme.BORDER)
        root.addWidget(line)

        sec = BodyLabel("常用功能")
        sec.setStyleSheet("color: %s; font-size: 13px; font-weight: 600;"
                          % theme.TEXT_2)
        root.addWidget(sec)

        grid = QGridLayout()
        grid.setSpacing(10)
        self.capture_btn = PushButton("重新捕获登录数据")
        self.capture_btn.setToolTip("清空登录态并重新登录，拉取该账号的登录数据")
        self.bs_btn = PushButton("精确基建配置")
        self.bs_btn.setToolTip("配置该账号精确基建派驻（布局/批次/干员）")
        self.fight_btn = PushButton("第二理智候选关卡")
        self.fight_btn.setToolTip("按 MAA 候选关卡界面修改该账号刷图候选")
        self.delete_btn = PushButton("删除该账号")
        self.delete_btn.setStyleSheet(
            "PushButton { color: %s; border: 1px solid #e5b7b1; }" % theme.ERR)
        for i, b in enumerate((self.capture_btn, self.bs_btn,
                               self.fight_btn, self.delete_btn)):
            b.setMinimumHeight(38)
            grid.addWidget(b, i // 2, i % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        root.addLayout(grid)

        self.hint = BodyLabel(
            "「是否启用 / 精确基建」开关在账号卡片上直接操作；"
            "运行顺序 = 账号列表顺序。")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: %s; font-size: 12px;" % theme.TEXT_3)
        root.addWidget(self.hint)
        root.addStretch(1)

        self.delete_btn.clicked.connect(self._on_delete)

        self.capture_btn.clicked.connect(self._on_capture)
        self.bs_btn.clicked.connect(self._on_base_config)
        self.fight_btn.clicked.connect(self._on_fight_plan)
        self._refresh_uid()

    def _refresh_uid(self):
        uid = slot_uid(self.cfg, self.acc.get("slot", ""))
        if uid:
            self.uid_pill.set_state("ok", "已捕获 UID %s" % uid)
        else:
            self.uid_pill.set_state("warn", "未捕获")

    def _on_capture(self):
        dlg = CaptureDialog(self.cfg, self.acc, page=self.page, parent=self)
        dlg.exec()
        self._refresh_uid()
        if self.page is not None:
            self.page.refresh()

    def _on_base_config(self):
        from pages.base_schedule_dialog import show_base_schedule_dialog
        show_base_schedule_dialog(self.cfg, self.acc, parent=self)

    def _on_fight_plan(self):
        show_stage_plan_dialog(self.cfg, self.acc, parent=self)

    def _on_delete(self):
        box = MessageBox(
            "删除账号", "确定从运行列表中删除「%s」吗？\n\n"
            "仅从列表移除，登录数据槽位文件保留在磁盘。"
            % self.acc.get("label", ""), self.window())
        box.yesButton.setText("删除")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        self.cfg["accounts"].remove(self.acc)
        appconfig.save(self.cfg)
        if self.page is not None:
            self.page.refresh()
        self.accept()


class AccountCard(Card):
    """两列网格中的账号卡片：表面只放「精确基建 / 启用」两个开关，
    点击卡片空白处打开详细控制界面。"""

    def __init__(self, cfg, acc, index, page=None, parent=None):
        super().__init__(parent=parent)
        self.cfg = cfg
        self.acc = acc
        self.page = page
        server = acc.get("server", "official")
        colors = (theme.ACCENT_B if server == "bilibili"
                  else (theme.ACCENT_O1 if index % 2 == 0
                        else theme.ACCENT_O2))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.vbox.setContentsMargins(12, 12, 12, 12)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(IconBadge(str(index + 1), colors))
        name_box = QVBoxLayout()
        name_box.setSpacing(2)
        name = SubtitleLabel(acc.get("label") or "?")
        name.setToolTip("%s · 槽位 %s" % (SERVER_LABELS.get(server, server),
                                          acc.get("slot", "未设置")))
        name_box.addWidget(name)
        row.addLayout(name_box)
        row.addStretch(1)

        self.base_sw = SwitchButton()
        self.base_sw.setOnText("精确基建")
        self.base_sw.setOffText("精确基建")
        self.base_sw.setText("精确基建")
        self.base_sw.setToolTip("启用精确基建派驻；关闭时使用 MAA 自带基建换班")
        self.base_sw.setChecked(
            bool((acc.get("base_schedule") or {}).get("enabled", False)))
        self.base_sw.checkedChanged.connect(self._on_base_toggle)
        row.addWidget(self.base_sw, 0, Qt.AlignmentFlag.AlignVCenter)

        self.sw = SwitchButton()
        self.sw.setOnText("启用")
        self.sw.setOffText("启用")
        self.sw.setText("启用")
        self.sw.setToolTip("是否把该账号加入自动挂机运行列表")
        self.sw.setChecked(bool(acc.get("enabled", True)))
        self.sw.checkedChanged.connect(self._on_toggle)
        row.addWidget(self.sw, 0, Qt.AlignmentFlag.AlignVCenter)
        self.vbox.addLayout(row)

        # 捕获状态（UID）直接显示在卡片上
        uid_row = QHBoxLayout()
        uid_row.addStretch(1)
        self.uid_pill = Pill()
        uid_row.addWidget(self.uid_pill)
        self.vbox.addLayout(uid_row)
        self.vbox.addSpacing(2)
        self.refresh_uid()

    def refresh_uid(self):
        uid = slot_uid(self.cfg, self.acc.get("slot", ""))
        if uid:
            self.uid_pill.set_state("ok", "已捕获 UID %s" % uid)
        else:
            self.uid_pill.set_state("warn", "未捕获")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            # 两个开关自己的区域绝不触发详情（即使事件冒泡回卡片）
            if not (self.base_sw.geometry().contains(pos)
                    or self.sw.geometry().contains(pos)):
                self._open_detail()
        super().mousePressEvent(event)

    def _on_toggle(self, checked):
        self.acc["enabled"] = bool(checked)
        appconfig.save(self.cfg)

    def _on_base_toggle(self, checked):
        bs = self.acc.get("base_schedule")
        if not isinstance(bs, dict):
            bs = appconfig.default_base_schedule(
                batches=appconfig.schedule_batches(self.cfg))
            self.acc["base_schedule"] = bs
        bs["enabled"] = bool(checked)
        appconfig.save(self.cfg)

    def _open_detail(self):
        dlg = AccountDetailDialog(self.cfg, self.acc, page=self.page,
                                  parent=self)
        dlg.exec()


class AccountsPage(ScrollArea):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.view = QWidget()
        self.view.setObjectName("accountsView")
        self.view.setStyleSheet(
            "#accountsView { background: %s; }" % theme.BG)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setStyleSheet(
            "QScrollArea { background: %s; border: none; }" % theme.BG)
        self.viewport().setStyleSheet("background: %s;" % theme.BG)
        root = QVBoxLayout(self.view)
        root.setContentsMargins(0, 16, 0, 16)
        root.setSpacing(16)

        # 「添加账号」操作放最上面，下面只保留账号卡片网格
        action = Card()
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.add_btn = PrimaryPushButton("＋ 添加账号")
        self.add_btn.setToolTip("添加账号：输入名称/服务器/账号/密码，自动登录并保存登录数据")
        self.add_btn.clicked.connect(self._on_add)
        bar.addWidget(self.add_btn)
        bar.addStretch(1)
        action.vbox.addLayout(bar)
        root.addWidget(action)

        self.list_card = Card()
        root.addWidget(self.list_card)
        root.addStretch(1)

        self._cols = 2
        self.refresh()

    def _on_add(self):
        dlg = CaptureDialog(self.cfg, None, page=self, parent=self)
        dlg.exec()
        self.refresh()

    def refresh(self):
        # 清空并重建账号卡片（每行 2 个）
        while self.list_card.vbox.count():
            item = self.list_card.vbox.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        # 旧版本网格里的卡片是布局子项，必须一并清理，否则刷新会叠出重复卡片
        for w in self.list_card.findChildren(QWidget):
            w.deleteLater()
        if not self.cfg["accounts"]:
            empty = BodyLabel("暂无账号，点击上方「添加账号」开始。")
            empty.setStyleSheet("color: %s; font-size: 13px;" % theme.TEXT_3)
            self.list_card.vbox.addWidget(empty)
        grid = QGridLayout()
        grid.setSpacing(12)
        for i, acc in enumerate(self.cfg["accounts"]):
            card = AccountCard(self.cfg, acc, i, page=self)
            r, c = divmod(i, self._cols)
            span = (self._cols - c) if (self._cols > 1
                                        and len(self.cfg["accounts"]) % 2
                                        and i == len(self.cfg["accounts"]) - 1) else 1
            grid.addWidget(card, r, c, 1, span)
        for c in range(self._cols):
            grid.setColumnStretch(c, 1)
        self.list_card.vbox.addLayout(grid)

    def resizeEvent(self, event):
        # 太窄时自动改成一列，保证打开侧边栏/缩小窗口也不横向截断
        cols = 2 if self.viewport().width() >= 720 else 1
        if cols != self._cols:
            self._cols = cols
            self.refresh()
        super().resizeEvent(event)
