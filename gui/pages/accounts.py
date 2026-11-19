# -*- coding: utf-8 -*-
"""账号管理页：增删账号、启用/停用、捕获登录数据（槽位）。

- 添加账号：控制台输入账号/密码 → 调 capture_account.ps1 自动登录并拉取
  登录数据到 scripts\\accounts\\<slot>\\；特殊字符密码或验证码时自动转人工登录。
- 删除账号：仅从列表移除（槽位文件保留在磁盘，如需彻底删除可手动清理）。
- 改名账号：点击卡片上的账号名字原地改名（回车/失焦保存，Esc 取消）。
- 调整顺序：按住卡片（或账号名）拖到目标位置换位，卡片左上角数字 = 运行顺序；
  账号详情里另有「↑ 上移 / ↓ 下移」。顺序即 config.json 的 accounts 数组顺序，
  master.ps1 按它逐个切号运行（正在挂机时改动从下次运行生效）。
- 切换账号不再走游戏内点击流程：master.ps1 用 slot_switch.ps1 重启游戏+推入数据。
"""
import copy
import os
import subprocess
import uuid
from pathlib import Path

from PySide6.QtCore import (QEasingCurve, QPoint, QPropertyAnimation, QRect,
                            Qt, QThread, QTimer, Signal)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QApplication, QGraphicsDropShadowEffect,
                               QGraphicsOpacityEffect, QFrame, QHBoxLayout,
                               QLabel, QPlainTextEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from qfluentwidgets import (BodyLabel, ComboBox, FluentIcon, InfoBar,
                            InfoBarPosition, LineEdit,
                            PrimaryPushButton, PushButton, ScrollArea,
                            SubtitleLabel, SwitchButton)

import config as appconfig
import theme
from core import runner, secret, token_check
from core.poller import FuncWorker
from core.util import CREATE_NO_WINDOW, decode_console, pwsh_exe
from motion import PageStack, press_feedback
from pages.home import navigation
from pages.stage_plan_dialog import show_stage_plan_dialog
from sheets import MessageBox, SheetDialog
from widgets import (Card, IconBadge, Pill, _label_transparent, dark_log_qss,
                     empty_state, label, set_switch_checked_gray, style_button,
                     style_primary_button, style_scroll_area,
                     style_tinted_button)

# ---- 拖动排序的版式与节奏（数值参考 SortableJS / react-beautiful-dnd）----
GRID_SPACING = 10      # 账号行之间的间距（px）
CARD_MIN_H = 96        # 卡片最小高度，实际取内容高度（名字 + 元信息两行后略增）
MOVE_MS = 150          # 其他卡片让位动画时长（SortableJS 默认 150ms）
DROP_MS = 170          # 松手落位动画时长（按距离微调）
AUTOSCROLL_ZONE = 54   # 拖到上下边缘这个范围内开始自动滚动
AUTOSCROLL_STEP_MAX = 26

SERVER_LABELS = {"official": "官服", "bilibili": "B 服"}

# 关窗时杀不死的工作线程在这里「断线」保活：线程随进程退出，而不是随
# 对话框析构（QThread 运行中被析构会直接 abort 整个进程）
_detached_threads = []


class ClickLabel(QLabel):
    """可点击的普通文本标签：点击发 clicked；按住拖动则交由所属卡片调整顺序。

    标签会吃掉鼠标事件（不冒泡给父卡片，父卡片上点击=打开详情），
    所以这里在按住移动超过拖拽阈值时，主动把拖拽交给 drag_target（账号卡片）。
    """

    clicked = Signal()

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.drag_target = None
        self._press_pos = None
        self._dragging = False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
            self._dragging = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        target = self.drag_target
        if target is None or not event.buttons() & Qt.MouseButton.LeftButton:
            super().mouseMoveEvent(event)
            return
        global_pos = event.globalPosition().toPoint()
        if self._dragging:
            target.drag_to(global_pos)
            event.accept()
            return
        if self._press_pos is None:
            super().mouseMoveEvent(event)
            return
        moved = (event.position().toPoint() - self._press_pos).manhattanLength()
        if moved < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return
        hotspot = self.mapTo(target, self._press_pos)   # 卡片坐标下的按下点
        self._press_pos = None
        self._dragging = True
        target.begin_drag(global_pos, hotspot)
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._dragging:
                self._dragging = False
                self._press_pos = None
                self.drag_target.end_drag()
                event.accept()
                return
            if self._press_pos is not None:
                self.clicked.emit()
            self._press_pos = None
            event.accept()
            return
        super().mouseReleaseEvent(event)


class RenameEdit(LineEdit):
    """账号名行内编辑框：回车/失焦提交，Esc 取消。"""

    cancelled = Signal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            event.accept()
            return
        super().keyPressEvent(event)


def slot_uid(cfg, slot):
    """读取槽位 uid.txt；槽位不存在返回 None。"""
    if not slot:
        return None
    p = Path(cfg["paths"]["script_dir"]) / "accounts" / slot / "uid.txt"
    try:
        return p.read_text(encoding="utf-8", errors="replace").strip() or None
    except OSError:
        return None


class CaptureWorker(QThread):
    """后台跑 capture_account.ps1：逐行转发输出，结束时发 done(exit_code)。

    subprocess.Popen + CREATE_NO_WINDOW（无控制台窗口闪现），
    阻塞 readline 放在工作线程里，GUI 线程不卡。
    账号密码经环境变量传递（命令行参数对本机任意进程可见）。
    """

    line = Signal(str)
    done = Signal(int)

    def __init__(self, args, env=None, parent=None):
        super().__init__(parent)
        self.args = args
        self.env = env
        self.proc = None

    def run(self):
        try:
            self.proc = subprocess.Popen(
                self.args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW, env=self.env)
        except OSError as e:
            self.line.emit(">>> 启动捕获脚本失败：%s" % e)
            self.done.emit(-1)
            return
        try:
            # 在 Popen 返回前关闭对话框时，GUI 线程尚未拿到子进程。
            if self.isInterruptionRequested():
                self.kill()
            for raw in iter(self.proc.stdout.readline, b""):
                self.line.emit(decode_console(raw).rstrip())
        finally:
            # 兜底保证 done 必发：任何未预期异常都不能让「捕获中」状态卡死
            try:
                self.proc.stdout.close()
            except OSError:
                pass
            self.done.emit(self.proc.wait())

    def kill(self):
        self.requestInterruption()
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()


class CaptureDialog(SheetDialog):
    """添加/重新捕获账号：填账号密码 → 跑 capture_account.ps1，实时显示输出。"""

    def __init__(self, cfg, acc=None, page=None, parent=None):
        super().__init__(parent, title="捕获账号" if acc is None else "重新捕获账号",
                         size=(840, 466))
        self.cfg = cfg
        self.acc = acc          # None=新增；dict=重新捕获（预填）
        self.page = page        # AccountsPage，成功后刷新列表
        self.worker = None
        self._token_worker = None
        self._slot = ""
        self._closing = False
        self._password_broken = False   # 原密文无法解密 → 保存时不得用空值覆盖
        columns = QHBoxLayout()
        columns.setSpacing(18)
        self.body.addLayout(columns, 1)
        form = QFrame()
        form.setObjectName("captureFields")
        theme.bind(form, lambda: "QFrame#captureFields { background:%s; border-radius:14px; }"
                   % theme.ROW_INSET)
        f = QVBoxLayout(form)
        f.setContentsMargins(16, 14, 16, 14)
        f.setSpacing(10)
        f.addWidget(label("登录资料", 16, "600", transparent=True))
        columns.addWidget(form, 4)

        def make_row(label_text, widget):
            row = QHBoxLayout()
            row.setSpacing(10)
            lab = BodyLabel(label_text)
            theme.bind(lab, lambda: "color: %s; %s"
                       % (theme.TEXT_2, theme.font_stack(theme.FS_BODY)))
            lab.setFixedWidth(44)
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
        f.addStretch()

        self.hint_label = BodyLabel(
            "密码由 Windows DPAPI 加密，仅当前 Windows 用户可解密。遇到特殊字符或验证码时，"
            "请按提示到模拟器中完成登录。")
        self.hint_label.setWordWrap(True)
        # 提示行配色随运行状态变（正常灰 / 失败红 / 通过绿）：
        # 状态记在 _hint_state，配方读它，主题切换时按当前状态重套
        self._hint_state = "normal"
        theme.bind(self.hint_label, self._hint_qss)
        f.addWidget(self.hint_label)

        output = QVBoxLayout()
        output.setSpacing(8)
        output.addWidget(label("捕获日志", 16, "600"))
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet(dark_log_qss())
        self.log_view.setPlaceholderText("开始捕获后，这里会显示登录进度。")
        output.addWidget(self.log_view, 1)
        columns.addLayout(output, 5)

        btns = QHBoxLayout()
        btns.setSpacing(10)
        self.start_btn = style_primary_button(PrimaryPushButton("开始捕获"))
        self.close_btn = style_button(PushButton("关闭"))
        btns.addStretch(1)
        btns.addWidget(self.close_btn)
        btns.addWidget(self.start_btn)
        self.body.addLayout(btns)

        self.start_btn.clicked.connect(self.on_start)
        self.close_btn.clicked.connect(self.reject)

        if acc is not None:
            self.name_edit.setText(acc.get("label", ""))
            self.server_combo.setCurrentIndex(
                1 if acc.get("server") == "bilibili" else 0)
            self.user_edit.setText(acc.get("username", ""))
            self._fill_password(acc)
        self._set_running(False)

    def _fill_password(self, acc):
        """密码框显示解密后的明文；旧配置的明文原样显示。

        DPAPI 解密失败（换了 Windows 用户/机器）时留空并标红提示，保存时保留原密文，
        不会把凭据覆盖成空串。
        """
        stored = acc.get("password", "")
        value = secret.unprotect(stored) if isinstance(stored, str) else ""
        if value is None:
            self._password_broken = True
            self.pass_edit.setText("")
            self._set_hint(
                "原密码无法解密（DPAPI 密文属于其他 Windows 用户或已损坏），"
                "请重新输入密码并保存；不输入则保留原密文。", "err")
        else:
            self.pass_edit.setText(value)

    def _append_log(self, line):
        self.log_view.appendPlainText(line)
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _set_running(self, running):
        self.close_btn.setText("停止捕获" if running else "关闭")
        self.start_btn.setEnabled(not running)
        self.name_edit.setEnabled(not running)
        self.server_combo.setEnabled(not running)
        self.user_edit.setEnabled(not running)
        self.pass_edit.setEnabled(not running)

    def _hint_qss(self):
        color = {"err": theme.ERR, "ok": theme.OK}.get(self._hint_state,
                                                       theme.TEXT_3)
        return "color: %s; %s" % (color, theme.font_stack(theme.FS_CAPTION))

    def _set_hint(self, text, state="normal"):
        """改提示文案与配色（normal 灰 / err 红 / ok 绿），主题切换后按状态重套。"""
        self._hint_state = state
        self.hint_label.setText(text)
        theme.refresh(self.hint_label)

    def _show_error(self, text):
        """对话框内的红色提示（InfoBar 在模态 QDialog 中可能不显示，用可见文案兜底）。"""
        self._set_hint(text, "err")

    def on_start(self):
        label = self.name_edit.text().strip()
        username = self.user_edit.text().strip()
        password = self.pass_edit.text()
        if not label or not username or not password:
            self._show_error("名称 / 账号 / 密码不能为空")
            return
        # -Label 仍走命令行传参：以「-」开头的值会被 PowerShell 当成参数名
        if label.startswith("-"):
            self._show_error("名称不能以「-」开头")
            return
        server = "bilibili" if self.server_combo.currentIndex() == 1 else "official"
        self._slot = (self.acc or {}).get("slot") or ("acc_" + uuid.uuid4().hex[:8])

        script = Path(self.cfg["paths"]["script_dir"]) / "capture_account.ps1"
        args = [
            pwsh_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(script),
            "-Server", server, "-Slot", self._slot, "-Label", label,
        ]
        # 账号密码走环境变量：命令行参数在进程存活期内对本机任意进程可读
        env = dict(os.environ)
        env["MAA_CAPTURE_USERNAME"] = username
        env["MAA_CAPTURE_PASSWORD"] = password
        self.log_view.clear()
        self._append_log(">>> 开始捕获：%s（%s）槽位 %s" % (label, SERVER_LABELS[server], self._slot))
        self._append_log(">>> 正在启动捕获脚本...")
        self._set_hint(
            "捕获进行中：自动启动模拟器（如未运行）→ 清空登录态 → 重启游戏 → "
            "点掉弹窗 → 输入账号密码 → 拉取数据。特殊字符密码或验证码时请留意提示，"
            "在模拟器窗口手动登录。")

        self.worker = CaptureWorker(args, env=env, parent=self)
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
            self._append_log(">>> 捕获成功。点击关闭返回列表。")
            self._apply_result()
            InfoBar.success("捕获成功", "登录数据已保存到槽位",
                            parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                            duration=5000)
            # 官服：立即向官方接口验证新 token（通过即解除仪表盘标红）
            if self.server_combo.currentIndex() == 0:
                self._verify_token()
        else:
            self._append_log(">>> 捕获失败（退出码 %d）。可修改后重试；登录态已自动恢复。" % code)
            self._show_error("捕获失败，详见捕获日志。可修改后点「开始捕获」重试。")

    def _verify_token(self):
        """捕获成功后立即探测新 token（<1 秒），结果直接显示在提示行。"""
        cfg, slot = self.cfg, self._slot
        self._set_hint("正在验证新 token...")
        self._token_worker = FuncWorker(
            lambda: token_check.check_slot(cfg, slot), parent=self)
        self._token_worker.done.connect(self._on_token_verified)
        self._token_worker.start()

    def _on_token_verified(self, res):
        self._token_worker = None
        if self._closing:
            return
        st = res[1] if (isinstance(res, tuple) and res[0] == "ok") else None
        if st is None:
            self._set_hint(
                "槽位里没有可校验的 SDK 凭据（USER_CACHE），挂机前会再做屏幕级检查")
        elif st.get("status") == "ok":
            self._set_hint("✓ 新 token 验证通过，账号已可正常挂机", "ok")
        else:
            self._set_hint(
                "⚠ token 验证未通过（%s），「账号 → 运行状态」会保持标红" % (st.get("detail") or "未知原因"),
                "err")

    def _apply_result(self):
        """成功：写入 cfg（新增或更新账号项）并保存。"""
        server = "bilibili" if self.server_combo.currentIndex() == 1 else "official"
        # 原密文解不开且用户没重填时保留原值：空串写回去等于删掉凭据
        password = self.pass_edit.text()
        if not password and self._password_broken and self.acc is not None:
            password = self.acc.get("password", "")
        entry = {
            "id": (self.acc or {}).get("id") or ("acc_" + uuid.uuid4().hex[:8]),
            "label": self.name_edit.text().strip(),
            "server": server,
            "enabled": (self.acc or {}).get("enabled", True),
            "slot": self._slot,
            "username": self.user_edit.text().strip(),
            "password": password,
            "second_fight_plan": list((self.acc or {}).get("second_fight_plan")
                                      or []),
            "second_fight_use_optional": bool(
                (self.acc or {}).get("second_fight_use_optional", True)),
            "maa_tasks": copy.deepcopy(
                (self.acc or {}).get("maa_tasks") or {
                    "depot": {"enabled": False, "plans": [],
                               "sort_by_progress": False, "count_dual_chip": False},
                    "progress": {"enabled": False, "plans": []},
                }),
            "base_schedule": copy.deepcopy(self.acc["base_schedule"])
                if self.acc is not None and "base_schedule" in self.acc
                else appconfig.default_base_schedule(
                    batches=appconfig.schedule_batches(self.cfg)),
        }
        if self.acc is not None:
            self.acc.update(entry)
        else:
            self.cfg["accounts"].append(entry)
        appconfig.save(self.cfg)
        # 不在这里刷新列表：三个调用方都在 exec() 返回后各自刷新。
        # 弹窗还挂着就 page.refresh() 会把「卡片 → 详情弹窗 → 本弹窗」整条
        # 父子链排进延迟删除队列，之后对已销毁 C++ 对象的访问会抛 RuntimeError
        return

    def _app_state_changed(self, state):
        # 验证码要求到模拟器手动操作，切出应用不能打断捕获。
        if self.worker is None and self._token_worker is None:
            super()._app_state_changed(state)

    def reject(self):
        if not self._closing and self.worker is not None and self.worker.isRunning():
            box = MessageBox("停止本次捕获？", "中途停止可能需要在模拟器里重新登录。", self)
            box.yesButton.setText("停止捕获")
            box.cancelButton.setText("继续捕获")
            confirmed = box.exec()
            box.deleteLater()
            if not confirmed:
                return
        super().reject()

    def done(self, result):
        if not self._closing:
            self._closing = True
            if self.worker is not None:
                self.worker.kill()
            # done 信号可能在 QThread.run 真正返回前到达，所以也检查已回调的子线程。
            for thread in self.findChildren(QThread):
                if thread.isRunning():
                    thread.setParent(None)
                    _detached_threads.append(thread)
                    thread.finished.connect(thread.deleteLater)
                    thread.destroyed.connect(lambda _=None, t=thread: _detached_threads.remove(t))
                    if thread.isFinished():
                        thread.deleteLater()
            self.worker = None
            self._token_worker = None
        super().done(result)


def _detail_action(title, hint, icon, callback, *, danger=False):
    """整行可点的设置入口，子标签让鼠标事件直接到按钮。"""
    button = QPushButton()
    button.setAutoDefault(False)
    button.setAccessibleName(title)
    button.setAccessibleDescription(hint)
    button.setCursor(Qt.PointingHandCursor)
    button.setProperty("pressRadius", 12)
    layout = QHBoxLayout(button)
    layout.setContentsMargins(13, 8, 14, 8)
    layout.setSpacing(12)
    badge = QLabel()
    badge.setAlignment(Qt.AlignCenter)
    badge.setFixedSize(33, 33)
    def badge_style():
        ink = theme.ERR if danger else theme.ACCENT
        badge.setPixmap(icon.icon(color=ink).pixmap(17, 17))
        return "background:%s; border-radius:10px;" % theme.mix(theme.CARD, ink, .12)
    theme.bind(badge, badge_style)
    layout.addWidget(badge)
    texts = QVBoxLayout()
    texts.setSpacing(2)
    texts.addWidget(label(title, 15, "600", color="ERR" if danger else "TEXT", transparent=True))
    description = label(hint, 12, color="TEXT_2", transparent=True)
    description.setWordWrap(True)
    texts.addWidget(description)
    layout.addLayout(texts, 1)
    layout.addWidget(label("›", 20, color="TEXT_2", transparent=True))
    for child in button.findChildren(QLabel):
        child.setAttribute(Qt.WA_TransparentForMouseEvents)
    theme.bind(button, lambda: "QPushButton {background:%s; border:1px solid transparent;"
               "border-radius:12px;} QPushButton:hover {background:%s;}"
               "QPushButton:focus {border-color:%s;} QPushButton:pressed {background:%s;}"
               % (theme.ROW_INSET, theme.mix(theme.ROW_INSET, theme.ACCENT, .08),
                  theme.ACCENT, theme.mix(theme.ROW_INSET, theme.ACCENT, .15)))
    button.setMinimumHeight(60)
    button.clicked.connect(callback)
    return press_feedback(button)


class AccountDetailDialog(SheetDialog):
    """覆盖主窗口的账号详情，遮罩接收外部点击，内部编辑器保留各自的保存流程。"""

    def __init__(self, cfg, acc, page=None, parent=None):
        super().__init__(parent, size=(718, 372))
        self.cfg = cfg
        self.acc = acc
        self.page = page
        self.move_delta = 0
        self.deleted = False
        self.export_wanted = False
        self.switch_wanted = False
        self.capture_opened = False
        self.body.setContentsMargins(0, 0, 0, 0)
        columns = QHBoxLayout()
        columns.setContentsMargins(10, 10, 18, 10)
        columns.setSpacing(18)
        self.body.addLayout(columns)
        identity = QFrame()
        identity.setFixedWidth(202)
        identity.setObjectName("accountIdentity")
        theme.bind(identity, lambda: "QFrame#accountIdentity {background:%s; border-radius:15px;}"
                   % theme.mix(theme.CARD, theme.ACCENT, .10))
        profile = QVBoxLayout(identity)
        profile.setContentsMargins(18, 22, 16, 20)
        profile.setSpacing(5)
        avatar = QLabel()
        avatar.setFixedSize(48, 48)
        avatar.setAlignment(Qt.AlignCenter)
        def avatar_style():
            avatar.setPixmap(FluentIcon.PEOPLE.icon(color=theme.PRIMARY_FG).pixmap(25, 25))
            return "background:%s; border-radius:16px;" % theme.PRIMARY_BG
        theme.bind(avatar, avatar_style)
        profile.addWidget(avatar)
        profile.addSpacing(10)
        self.name_label = label("", 24, "700", transparent=True)
        self.name_label.setTextFormat(Qt.PlainText)
        profile.addWidget(self.name_label)
        self.server_label = label("", 14, color="TEXT_2", transparent=True)
        self.server_label.setTextFormat(Qt.PlainText)
        profile.addWidget(self.server_label)
        profile.addStretch()
        profile.addWidget(label("登录槽位", 12, color="TEXT_2", transparent=True))
        self.slot_label = label("", 15, "600", transparent=True)
        self.slot_label.setTextFormat(Qt.PlainText)
        profile.addWidget(self.slot_label)
        profile.addSpacing(7)
        self.uid_pill = Pill()
        profile.addWidget(self.uid_pill, 0, Qt.AlignLeft)
        columns.addWidget(identity)

        body = QVBoxLayout()
        body.setContentsMargins(0, 12, 0, 8)
        body.setSpacing(12)
        body.addWidget(label("账号详情", 23, "700"))
        self.tabs = navigation(("日常方案", "登录与资料", "账号管理"), compact=True)
        body.addWidget(self.tabs, 0, Qt.AlignLeft)
        self.sections = PageStack()
        self.tabs.currentChanged.connect(self.sections.setCurrentIndex)
        body.addWidget(self.sections, 1)
        columns.addLayout(body, 1)

        daily = self._section()
        daily.addWidget(_detail_action("精确基建", "安排房间、干员与换班批次", FluentIcon.TILES,
                                       self._on_base_config))
        daily.addWidget(_detail_action("第二理智候选关卡", "设置该账号刷图时可选的关卡", FluentIcon.GAME,
                                       self._on_fight_plan))
        daily.addWidget(_detail_action("库存与干员培养", "配置库存目标和培养计划", FluentIcon.LIBRARY,
                                       self._on_account_tasks))
        daily.addStretch()

        access = self._section()
        access.addWidget(_detail_action("切换到此账号", "校验登录后留在游戏内，方便手动游玩",
                                        FluentIcon.PEOPLE, self._on_switch))
        access.addWidget(_detail_action("重新捕获登录数据", "重新登录并更新本机保存的凭据",
                                        FluentIcon.FINGERPRINT, self._on_capture))
        busy = page is not None and page.export_busy()
        export = _detail_action("导出干员资料", "已有导出任务正在运行" if busy else "通过 MAA 识别并导出干员资料",
                                FluentIcon.DOWNLOAD, self._on_export)
        export.setEnabled(not busy)
        access.addWidget(export)
        access.addStretch()

        manage = self._section()
        accounts = cfg.get("accounts", [])
        index = accounts.index(acc) if acc in accounts else -1
        order = QHBoxLayout()
        order.setSpacing(8)
        order.addWidget(label("运行顺序", 15, "600"))
        if index >= 0:
            order.addWidget(label("第 %d / %d 位" % (index + 1, len(accounts)), 13, color="TEXT_2"))
        order.addStretch()
        for text, delta in (("上移", -1), ("下移", 1)):
            button = style_button(QPushButton(text), small=True)
            button.setAutoDefault(False)
            button.setEnabled(index >= 0 and 0 <= index + delta < len(accounts))
            button.clicked.connect(lambda checked=False, delta=delta: self._on_move(delta))
            order.addWidget(button)
        manage.addLayout(order)
        manage.addStretch()
        manage.addWidget(_detail_action("从运行列表移除", "保留本机登录槽位，移除前需要确认",
                                        FluentIcon.DELETE, self._on_delete, danger=True))
        self._refresh_identity()

    def _section(self):
        pane = QWidget()
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(7)
        self.sections.addWidget(pane)
        return layout

    def _refresh_identity(self):
        name = self.acc.get("label") or "未命名账号"
        self.setWindowTitle("账号详情 · " + name)
        self.ensurePolished()
        for widget, value in ((self.name_label, name), (self.slot_label, self.acc.get("slot") or "未设置")):
            widget.setText(widget.fontMetrics().elidedText(value, Qt.ElideRight, 164))
            widget.setToolTip(value)
        server = self.acc.get("server", "official")
        self.server_label.setText(SERVER_LABELS.get(server, server))
        uid = slot_uid(self.cfg, self.acc.get("slot", ""))
        self.uid_pill.set_state("ok" if uid else "wait", ("UID " + uid) if uid else "未捕获登录数据")

    def _on_move(self, delta):
        """只记录意图并关窗：列表（卡片的父级）负责真正移动，避免重建时窗口还挂着。"""
        self.move_delta = delta
        self.accept()

    def _on_export(self):
        """只记录意图并关窗：导出确认与互斥检查由主窗口接手——
        模态详情弹窗里再叠确认框/日志窗会双层数，且导出日志窗以主窗口为父。"""
        self.export_wanted = True
        self.accept()

    def _on_switch(self):
        """只记录意图并关窗：互斥检查与确认由主窗口接手（同导出的考虑）。"""
        self.switch_wanted = True
        self.accept()

    def _on_capture(self):
        dlg = CaptureDialog(self.cfg, self.acc, page=self.page, parent=self)
        dlg.exec()
        dlg.deleteLater()
        self.capture_opened = True
        self._refresh_identity()

    def _on_base_config(self):
        from pages.base_schedule_dialog import show_base_schedule_dialog
        show_base_schedule_dialog(self.cfg, self.acc, parent=self)

    def _on_fight_plan(self):
        show_stage_plan_dialog(self.cfg, self.acc, parent=self)

    def _on_account_tasks(self):
        from pages.account_tasks_dialog import show_account_tasks_dialog
        show_account_tasks_dialog(self.cfg, self.acc, parent=self)

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
        self.deleted = True
        # 退出详情后由列表重建卡片。
        self.accept()


class DragProxy(QWidget):
    """跟随鼠标的拖影：被拖动卡片的截图 + 投影，看起来像被「拿起来」了。"""

    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        self._pixmap = pixmap
        # 拖影不吃鼠标事件：拖动期间鼠标仍由原卡片持有（隐式抓取）
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.resize(pixmap.size())
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(26)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 120))
        self.setGraphicsEffect(shadow)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(0.96)
        painter.drawPixmap(0, 0, self._pixmap)


class CardGridArea(QWidget):
    """卡片容器：卡片由页面绝对定位（不用布局管理），这样才能做位移动画。"""

    def __init__(self, page, parent=None):
        super().__init__(parent)
        self.page = page

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.page.relayout()      # 宽度变化（窗口缩放/滚动条出现）后重新排位


class AccountCard(Card):
    """账号列表行：左侧身份、中部开关、右侧单号操作，
    点击名字可直接改名，点击卡片空白处打开详细控制界面。

    卡片整张可点击（开详情），所以悬停给一档「 CARD 混入 5% 文字色」的
    提亮反馈——可点击的表面必须有悬停态，否则不知道能点。
    """

    def _hoverBackgroundColor(self):
        return QColor(theme.mix(theme.CARD, theme.TEXT, 0.05))

    def __init__(self, cfg, acc, index, page=None, parent=None):
        super().__init__(parent=parent)
        self.cfg = cfg
        self.acc = acc
        self.page = page
        self.index = index        # 在 cfg["accounts"] 中的位置 = 运行顺序
        self._rename_edit = None
        self._rename_active = False
        self._press_pos = None    # 按下点（卡片坐标），用于区分「点击」与「拖动」
        self._maybe_click = False
        self._dragging = False    # 正在被拖动（拖动期间卡片透明，拖影跟着鼠标）
        server = acc.get("server", "official")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.vbox.setContentsMargins(theme.CARD_PAD_SM, theme.CARD_PAD_SM - 2,
                                     theme.CARD_PAD_SM, theme.CARD_PAD_SM)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.badge = IconBadge(str(index + 1))
        self.badge.setFixedSize(38, 38)
        row.addWidget(self.badge, 0, Qt.AlignVCenter)
        self.name_box = QVBoxLayout()
        self.name_box.setSpacing(2)
        self.name_label = ClickLabel(acc.get("label") or "?")
        theme.bind(self.name_label,
                   lambda: "QLabel { background: transparent; color: %s;"
                           " %s }" % (theme.TEXT,
                                      theme.font_stack(theme.FS_TITLE, "600")))
        self.name_label.setToolTip("点击修改账号名称")
        self.name_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.name_label.clicked.connect(self._start_rename)
        self.name_label.drag_target = self   # 按住名字拖动也能调整顺序
        self.name_box.addWidget(self.name_label)
        # 第二行：服务器 · 槽位元信息（此前只藏在 tooltip 里）
        self.meta_label = QLabel("%s · 槽位 %s"
                                 % (SERVER_LABELS.get(server, server),
                                    acc.get("slot") or "未设置"))
        theme.bind(self.meta_label,
                   lambda: "QLabel { background: transparent; color: %s;"
                           " %s }" % (theme.TEXT_2,
                                      theme.font_stack(theme.FS_CAPTION)))
        self.name_box.addWidget(self.meta_label)
        self.uid_pill = Pill()
        self.name_box.addWidget(self.uid_pill, 0, Qt.AlignLeft)
        for text_label in (self.name_label, self.meta_label):
            text_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addLayout(self.name_box, 1)


        self.base_sw = set_switch_checked_gray(SwitchButton())
        self.base_sw.setOnText("精确基建")
        self.base_sw.setOffText("精确基建")
        self.base_sw.setText("精确基建")
        self.base_sw.setToolTip("启用精确基建派驻；关闭时使用 MAA 自带基建换班")
        self.base_sw.setChecked(
            bool((acc.get("base_schedule") or {}).get("enabled", False)))
        self.base_sw.checkedChanged.connect(self._on_base_toggle)
        row.addWidget(self.base_sw, 0, Qt.AlignmentFlag.AlignVCenter)

        self.sw = set_switch_checked_gray(SwitchButton())
        self.sw.setOnText("启用")
        self.sw.setOffText("启用")
        self.sw.setText("启用")
        self.sw.setToolTip("是否把该账号加入自动挂机运行列表")
        self.sw.setChecked(bool(acc.get("enabled", True)))
        self.sw.checkedChanged.connect(self._on_toggle)
        row.addWidget(self.sw, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addSpacing(8)
        self.launch_btn = style_button(QPushButton("快速启动"), small=True)
        self.launch_btn.setToolTip(
            "只把该账号的登录数据（token）推入游戏并启动模拟器：不等待游戏更新、"
            "不做登录校验，导入完成即可手动游戏（无需输账号密码）。\n"
            "若槽位里的 token 已失效，游戏会停在登录界面，需手动登录或重新捕获。\n"
            "与挂机、收菜、导出互斥。")
        self.launch_btn.clicked.connect(self._on_launch)
        row.addWidget(self.launch_btn)
        self.collect_btn = style_tinted_button(QPushButton("收菜"), small=True, icon=FluentIcon.LEAF)
        self.run_btn = style_primary_button(QPushButton("运行"), small=True, icon=FluentIcon.PLAY_SOLID)
        self.collect_btn.setToolTip("只为此账号执行一次基建收菜")
        self.run_btn.setToolTip("只为此账号执行一次完整日常挂机")
        if page is not None:
            self.collect_btn.clicked.connect(lambda: page.task_requested.emit(self.acc, True))
            self.run_btn.clicked.connect(lambda: page.task_requested.emit(self.acc, False))
        row.addWidget(self.collect_btn)
        row.addWidget(self.run_btn)
        self.vbox.addLayout(row)
        self.refresh_uid()

    def refresh_uid(self):
        uid = slot_uid(self.cfg, self.acc.get("slot", ""))
        if uid:
            self.uid_pill.set_state("ok", "已捕获 UID %s" % uid)
        else:
            self.uid_pill.set_state("wait", "未捕获")

    # ---- 拖动排序：拖影、预览排列与落位动画都由 AccountsPage 负责 ----
    def set_index(self, index):
        """更新运行位置（拖动排序后同步左上角数字）。"""
        self.index = index
        self.badge.setText(str(index + 1))

    def begin_drag(self, global_pos, hotspot):
        if self._dragging or self.page is None:
            return
        self._dragging = True
        self._maybe_click = False
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.page.begin_drag(self, global_pos, hotspot)

    def drag_to(self, global_pos):
        if self._dragging:
            self.page.drag_to(global_pos)

    def end_drag(self):
        if not self._dragging:
            return
        self._dragging = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.page.end_drag()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            controls = (self.base_sw, self.sw, self.launch_btn, self.collect_btn, self.run_btn)
            if not any(control.geometry().contains(pos) for control in controls):
                self._press_pos = pos
                self._maybe_click = True
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging:
            self.drag_to(event.globalPosition().toPoint())
            event.accept()
            return
        if (self._maybe_click and self._press_pos is not None
                and event.buttons() & Qt.MouseButton.LeftButton
                and (event.position().toPoint() - self._press_pos).manhattanLength()
                >= QApplication.startDragDistance()):
            self.begin_drag(event.globalPosition().toPoint(), self._press_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self.end_drag()
            event.accept()
            return
        open_detail = (self._maybe_click
                       and event.button() == Qt.MouseButton.LeftButton)
        self._maybe_click = False
        self._press_pos = None
        super().mouseReleaseEvent(event)
        if open_detail:
            self._open_detail()

    def _on_toggle(self, checked):
        self.acc["enabled"] = bool(checked)
        appconfig.save(self.cfg)

    def _on_launch(self):
        """卡片「快速启动」：只记录意图，互斥检查与确认由主窗口接手
        （同详情弹窗导出/切换的考虑）。"""
        if self.page is not None:
            self.page.start_requested.emit(self.acc)

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
        if dlg.move_delta and self.page is not None:
            self.page.move_relative(self.index, dlg.move_delta)
        if (dlg.deleted or dlg.capture_opened) and self.page is not None:
            self.page.refresh()
        if dlg.export_wanted and self.page is not None:
            self.page.export_requested.emit(self.acc)
        if dlg.switch_wanted and self.page is not None:
            self.page.switch_requested.emit(self.acc)
        dlg.deleteLater()

    def _start_rename(self):
        """点击账号名字：原地换成输入框，回车/失焦保存，Esc 取消。"""
        if self._rename_active:
            return
        self._rename_active = True
        edit = RenameEdit(self)
        self._rename_edit = edit
        edit.setText(self.acc.get("label") or "")
        edit.selectAll()
        edit.setMinimumWidth(220)
        edit.setClearButtonEnabled(False)
        edit.setToolTip("回车或点击其他位置保存；Esc 取消")
        edit.returnPressed.connect(self._commit_rename)
        edit.editingFinished.connect(self._commit_rename)
        edit.cancelled.connect(self._cancel_rename)
        self.name_box.replaceWidget(self.name_label, edit)
        self.name_label.hide()
        edit.setFocus()

    def _remove_rename_edit(self):
        if self._rename_edit is None:
            return
        edit = self._rename_edit
        self._rename_edit = None
        self.name_box.replaceWidget(edit, self.name_label)
        edit.deleteLater()
        self.name_label.show()

    def _commit_rename(self):
        if not self._rename_active:
            return
        self._rename_active = False
        edit = self._rename_edit
        text = (edit.text() or "").strip()
        changed = bool(text) and text != self.acc.get("label")
        old = self.acc.get("label") or ""
        self._remove_rename_edit()
        if not changed:
            self.name_label.setText(old)
            return
        self.acc["label"] = text
        appconfig.save(self.cfg)
        self.name_label.setText(text)
        if self.page is not None:
            self.page.refresh()

    def _cancel_rename(self):
        if not self._rename_active:
            return
        self._rename_active = False
        self._remove_rename_edit()
        self.name_label.setText(self.acc.get("label") or "?")


class AccountsPage(ScrollArea):
    """账号管理页。export_requested = 详情弹窗里点了「导出干员资料」，
    switch_requested = 点了「切换到此账号」，
    start_requested = 卡片上点了「快速启动」；都由主窗口做互斥检查与确认后启动。"""

    export_requested = Signal(object)
    switch_requested = Signal(object)
    start_requested = Signal(object)
    task_requested = Signal(object, bool)  # 账号，是否只收菜

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self._export_busy = False   # 导出运行中：详情弹窗里的导出按钮置灰
        self.cards = []          # 与 cfg["accounts"] 同序的卡片
        self.card_h = CARD_MIN_H
        self._slot_anis = {}     # 卡片 → 位移/落位动画
        self._in_relayout = False
        self._land_ani = None    # 松手后「飞回槽位」的淡入动画
        # 拖动状态（拖动期间：原卡片透明 + 拖影跟手 + 其他卡片平滑让位）
        self._drag_card = None
        self._drag_proxy = None
        self._drag_order = []
        self._drag_preview = []
        self._drag_slot = -1
        self._drag_hotspot = QPoint()
        self._drag_global = None
        self.view = QWidget()
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        # 与仪表盘一致：滚动区透明 + 浅色细滚动条；右侧让出 12px 给滑块
        style_scroll_area(self)
        root = QVBoxLayout(self.view)
        root.setContentsMargins(12, 16, 12, 16)
        root.setSpacing(20)

        bar = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(4)
        heading.addWidget(label("我的账号", 30, "700"))
        heading.addWidget(label("拖动账号即可调整运行顺序", 14, color="TEXT_2"))
        bar.addLayout(heading)
        bar.addStretch()
        self.add_btn = style_primary_button(QPushButton("添加账号"), icon=FluentIcon.ADD)
        self.add_btn.setToolTip("添加账号：输入名称/服务器/账号/密码，自动登录并保存登录数据")
        self.add_btn.clicked.connect(self._on_add)
        bar.addWidget(self.add_btn)
        root.addLayout(bar)

        # 卡片区：绝对定位 + 动画，故不使用布局管理器
        self.grid_area = CardGridArea(self)
        self.grid_area.setFixedHeight(0)
        # 空态：居中构图（图标 + 标题 + 辅注）替代一行灰字；
        # 属性名沿用 empty_label，relayout 的按 sizeHint 定位逻辑不变
        self.empty_label = empty_state(
            "暂无账号", "点击上方「添加账号」开始", icon=FluentIcon.PEOPLE)
        self.empty_label.setParent(self.grid_area)
        self.empty_label.hide()
        root.addWidget(self.grid_area)
        root.addStretch(1)

        # 拖到上下边缘时自动滚动（对齐 SortableJS 的 smart auto-scroll）
        self._autoscroll = QTimer(self)
        self._autoscroll.setInterval(16)
        self._autoscroll.timeout.connect(self._autoscroll_step)
        self.refresh()

    def _on_add(self):
        dlg = CaptureDialog(self.cfg, None, page=self, parent=self)
        dlg.exec()
        dlg.deleteLater()
        self.refresh()

    def set_export_busy(self, busy):
        """导出运行状态由主窗口同步：期间新开的详情弹窗导出按钮置灰。"""
        self._export_busy = bool(busy)

    def export_busy(self):
        return self._export_busy

    def refresh(self):
        # 清空并重建账号卡片（每个账号一行，位置由 slot_rect 计算）
        if self._drag_card is not None:
            self.cancel_drag()
        for card in self.cards:
            ani = self._slot_anis.pop(card, None)
            if ani is not None:
                ani.stop()
                ani.deleteLater()
            card.setParent(None)
            card.deleteLater()
        self.cards = []
        for i, acc in enumerate(self.cfg["accounts"]):
            card = AccountCard(self.cfg, acc, i, page=self)
            card.setParent(self.grid_area)
            self.cards.append(card)
            card.show()
        if self.cards:
            self.card_h = max(self.card_h,
                              max(c.sizeHint().height() for c in self.cards))
        self.empty_label.setVisible(not self.cards)
        self.relayout()

    # ---- 版式：卡片按「槽位」绝对定位 ----
    def slot_rect(self, index):
        """第 index 个账号行的完整点击与拖放区域。"""
        return QRect(0, index * (self.card_h + GRID_SPACING),
                     max(1, self.grid_area.width()), self.card_h)

    def relayout(self):
        """按当前卡片顺序重排（窗口尺寸变化时直接对齐，不做动画）。"""
        if self._in_relayout:
            return
        self._in_relayout = True
        try:
            n = len(self.cards)
            if n:
                self.grid_area.setFixedHeight(
                    n * self.card_h + (n - 1) * GRID_SPACING)
            else:
                self.grid_area.setFixedHeight(self.empty_label.sizeHint().height())
            self.empty_label.setGeometry(0, 0, max(1, self.grid_area.width()),
                                         self.empty_label.sizeHint().height())
            for i, card in enumerate(self.cards):
                self._stop_ani(card)
                card.setGeometry(self.slot_rect(i))
        finally:
            self._in_relayout = False

    def slot_index_at(self, pos):
        """grid_area 坐标 pos 落在哪个槽位（按最近槽位中心判定）。"""
        n = len(self.cards)
        if n == 0:
            return 0
        best, best_dist = 0, None
        for i in range(n):
            center = self.slot_rect(i).center()
            dist = (center.x() - pos.x()) ** 2 + (center.y() - pos.y()) ** 2
            if best_dist is None or dist < best_dist:
                best, best_dist = i, dist
        return best

    # ---- 位移动画（iOS 式让位：只动位置，曲线先快后缓）----
    def _animate_card(self, card, rect, duration=MOVE_MS):
        ani = self._slot_anis.get(card)
        if ani is None:
            ani = QPropertyAnimation(card, b"geometry", self)
            ani.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._slot_anis[card] = ani
        if ani.endValue() == rect and ani.state() == QPropertyAnimation.State.Running:
            return          # 目标没变，动画继续跑，不要重启（否则会顿）
        if card.geometry() == rect:
            ani.stop()
            return
        ani.stop()
        ani.setDuration(duration)
        ani.setStartValue(card.geometry())
        ani.setEndValue(rect)
        ani.start()

    def _stop_ani(self, card):
        ani = self._slot_anis.get(card)
        if ani is not None:
            ani.stop()

    # ---- 拖动排序 ----
    def begin_drag(self, card, global_pos, hotspot):
        """开始拖动：原卡片透明（腾出位置），拖影跟手，其他卡片准备让位。"""
        if self._drag_card is not None or card not in self.cards:
            return
        self._drag_card = card
        self._drag_order = list(self.cards)
        self._drag_preview = list(self.cards)
        self._drag_hotspot = QPoint(hotspot)
        self._drag_slot = self.cards.index(card)
        pixmap = card.grab()                     # 先截图，再让原卡片透明
        effect = QGraphicsOpacityEffect(card)
        effect.setOpacity(0.0)
        card.setGraphicsEffect(effect)
        proxy = DragProxy(pixmap, self.viewport())
        proxy.show()
        proxy.raise_()
        self._drag_proxy = proxy
        self._drag_global = global_pos
        self._move_proxy(global_pos)
        self._autoscroll.start()
        self.drag_to(global_pos)      # 立即按当前鼠标位置摆好预览

    def _move_proxy(self, global_pos):
        if self._drag_proxy is None:
            return
        self._drag_proxy.move(self.viewport().mapFromGlobal(global_pos)
                              - self._drag_hotspot)

    def drag_to(self, global_pos):
        if self._drag_card is None:
            return
        self._drag_global = global_pos
        self._move_proxy(global_pos)
        self.set_preview(self.slot_index_at(
            self.grid_area.mapFromGlobal(global_pos)))

    def set_preview(self, slot):
        """把被拖动卡片放进 slot 槽位：其余卡片按新顺序平滑让位（预览）。"""
        if self._drag_card is None:
            return
        slot = max(0, min(slot, len(self._drag_order) - 1))
        if slot == self._drag_slot:
            return
        self._drag_slot = slot
        others = [c for c in self._drag_order if c is not self._drag_card]
        preview = others[:slot] + [self._drag_card] + others[slot:]
        self._drag_preview = preview
        for i, card in enumerate(preview):
            self._animate_card(card, self.slot_rect(i))

    def end_drag(self):
        """松手：定稿顺序 + 拖影飞回槽位（卡片同步淡入）。"""
        card, proxy = self._drag_card, self._drag_proxy
        if card is None:
            return
        preview = list(self._drag_preview) or list(self._drag_order)
        slot = preview.index(card)
        target = self.slot_rect(slot)
        # 拖影与 card 同属一个坐标系换算：grid_area → viewport
        target_vp = QRect(self.grid_area.mapTo(self.viewport(), target.topLeft()),
                          target.size())
        accs = self.cfg["accounts"]
        new_accs = [c.acc for c in preview]
        changed = (len(new_accs) != len(accs)
                   or any(a is not b for a, b in zip(new_accs, accs)))
        if changed:
            accs[:] = new_accs
            appconfig.save(self.cfg)
            self.cards = preview
            for i, c in enumerate(self.cards):
                c.set_index(i)
            tip = "「%s」现在排在第 %d 位，运行顺序即列表顺序" % (
                card.acc.get("label") or "", slot + 1)
            if runner.is_running():
                tip += "；当前正在运行，新顺序从下次运行开始生效"
            InfoBar.info("已调整账号顺序", tip, parent=self.window(),
                         position=InfoBarPosition.TOP_RIGHT, duration=3000)

        self._autoscroll.stop()
        self._drag_card = None
        self._drag_order = []
        self._drag_preview = []
        self._drag_slot = -1
        self._drag_global = None

        # 被拖动卡片淡入（此时它已在自己槽位上，只是透明）
        effect = card.graphicsEffect()
        if isinstance(effect, QGraphicsOpacityEffect):
            fade = QPropertyAnimation(effect, b"opacity", self)
            fade.setDuration(DROP_MS)
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)
            fade.setEasingCurve(QEasingCurve.Type.OutCubic)
            fade.finished.connect(lambda: self._finish_landing(card))
            fade.start()
            self._land_ani = fade
        else:
            self._finish_landing(card)

        if proxy is not None:
            self._drag_proxy = None
            self._fly_proxy(proxy, target_vp, card)

    def _fly_proxy(self, proxy, target, card):
        """拖影从鼠标位置飞回槽位；落位动画时长随距离变化（参考 dnd 的 drop）。"""
        start = proxy.geometry()
        span = ((start.center().x() - target.center().x()) ** 2
                + (start.center().y() - target.center().y()) ** 2) ** 0.5
        duration = int(max(110, min(260, 90 + span * 0.6)))
        ani = QPropertyAnimation(proxy, b"geometry", self)
        ani.setDuration(duration)
        ani.setEasingCurve(QEasingCurve.Type.OutCubic)
        ani.setStartValue(start)
        ani.setEndValue(target)
        ani.finished.connect(lambda: self._finish_proxy(proxy, card))
        ani.start()
        self._slot_anis[proxy] = ani

    def _finish_proxy(self, proxy, card):
        ani = self._slot_anis.pop(proxy, None)
        if ani is not None:
            ani.deleteLater()
        proxy.hide()
        proxy.deleteLater()

    def _finish_landing(self, card):
        ani = self._land_ani
        self._land_ani = None
        if ani is not None:
            ani.deleteLater()
        try:
            # 移除拖动用的透明度特效（Qt 会接管删除旧 effect），
            # 卡片自身的投影特效在换特效时已被替换删除，这里补回
            card.setGraphicsEffect(None)
            card.apply_shadow()
        except RuntimeError:
            pass                             # 卡片已被列表重建销毁

    def cancel_drag(self):
        """中断拖动（重建列表等情况）：不提交顺序，直接回到原状。"""
        card, proxy = self._drag_card, self._drag_proxy
        if card is None and proxy is None:
            return
        self._autoscroll.stop()
        self._drag_card = None
        self._drag_proxy = None
        self._drag_order = []
        self._drag_preview = []
        self._drag_slot = -1
        self._drag_global = None
        if proxy is not None:
            ani = self._slot_anis.pop(proxy, None)
            if ani is not None:
                ani.deleteLater()
            proxy.hide()
            proxy.deleteLater()
        # 预览期间其他卡片已经让位，这里按「未改变的顺序」把它们移回去
        for i, c in enumerate(self.cards):
            self._animate_card(c, self.slot_rect(i))
        if card is not None:
            self._finish_landing(card)

    def _autoscroll_step(self):
        """拖动时靠近上下边缘自动滚动（滚动后重新算预览槽位）。"""
        if self._drag_card is None or self._drag_global is None:
            return
        viewport = self.viewport()
        y = viewport.mapFromGlobal(self._drag_global).y()
        height = viewport.height()
        delta = 0
        if y < AUTOSCROLL_ZONE:
            delta = -max(4, min(AUTOSCROLL_STEP_MAX,
                                int((AUTOSCROLL_ZONE - y) / 2.5)))
        elif y > height - AUTOSCROLL_ZONE:
            delta = max(4, min(AUTOSCROLL_STEP_MAX,
                               int((y - (height - AUTOSCROLL_ZONE)) / 2.5)))
        if not delta:
            return
        bar = self.verticalScrollBar()
        before = bar.value()
        bar.setValue(before + delta)
        if bar.value() != before:
            self.drag_to(self._drag_global)

    def move_account(self, src, dest):
        """移动账号：src 为原位置，dest 为插入位置（插到第 dest 张卡片之前）。

        账号详情里的「上移 / 下移」走这里：同样用位移动画，不重建列表。
        """
        accs = self.cfg["accounts"]
        cards = list(self.cards)
        if not (0 <= src < len(accs)) or len(cards) != len(accs):
            return
        dest = max(0, min(int(dest), len(accs)))
        if dest in (src, src + 1):   # 原地不动
            return
        acc = accs.pop(src)
        card = cards.pop(src)
        if dest > src:
            dest -= 1
        accs.insert(dest, acc)
        cards.insert(dest, card)
        appconfig.save(self.cfg)
        self.cards = cards
        for i, c in enumerate(cards):
            c.set_index(i)
            self._animate_card(c, self.slot_rect(i))
        tip = "「%s」现在排在第 %d 位，运行顺序即列表顺序" % (
            acc.get("label") or "", dest + 1)
        if runner.is_running():
            tip += "；当前正在运行，新顺序从下次运行开始生效"
        InfoBar.info("已调整账号顺序", tip, parent=self.window(),
                     position=InfoBarPosition.TOP_RIGHT, duration=3000)

    def move_relative(self, index, delta):
        """相对移动一位：delta=-1 上移，+1 下移（账号详情里的按钮用）。"""
        n = len(self.cfg["accounts"])
        if not (0 <= index < n) or not delta:
            return
        target = max(0, min(index + delta, n - 1))
        if target == index:
            return
        self.move_account(index, target + 1 if target > index else target)

    def resizeEvent(self, event):
        """版式固定每行 2 张不随宽度重排：窗口最小宽度已由主窗口限制在
        双列能完整显示的尺寸（见 MainWindow.setMinimumSize）。"""
        super().resizeEvent(event)
