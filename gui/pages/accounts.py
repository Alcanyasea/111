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
import os
import subprocess
import uuid
from pathlib import Path

from PySide6.QtCore import (QEasingCurve, QPoint, QPropertyAnimation, QRect,
                            Qt, QThread, QTimer, Signal)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QApplication, QDialog, QGraphicsDropShadowEffect,
                               QGraphicsOpacityEffect, QGridLayout, QHBoxLayout,
                               QLabel, QPlainTextEdit, QVBoxLayout, QWidget)

from qfluentwidgets import (BodyLabel, ComboBox, InfoBar, InfoBarPosition,
                            LineEdit, MessageBox, PrimaryPushButton, PushButton,
                            ScrollArea, SubtitleLabel, SwitchButton)

import config as appconfig
import theme
from core import runner, token_check
from core.poller import FuncWorker
from core.util import CREATE_NO_WINDOW, decode_console
from pages.stage_plan_dialog import show_stage_plan_dialog
from widgets import (Card, IconBadge, Pill, _label_transparent,
                     set_switch_checked_gray, style_button,
                     style_primary_button, style_scroll_area)

# ---- 拖动排序的版式与节奏（数值参考 SortableJS / react-beautiful-dnd）----
GRID_COLS = 2          # 每行 2 张卡片
GRID_SPACING = 20      # 卡片间距（px）
CARD_MIN_H = 86        # 卡片最小高度，实际取内容高度
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
        self._token_worker = None
        self._slot = ""
        self._closing = False
        self.setWindowTitle("捕获账号" if acc is None else "重新捕获账号")
        self.setModal(True)
        self.resize(560, 560)
        self.setStyleSheet("QDialog { background: %s; }" % theme.BG)

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
            "QPlainTextEdit { background: %s; color: %s; font-family: %s;"
            " font-size: 12px; border: none; border-radius: %dpx;"
            " padding: 12px 14px; }"
            % (theme.LOG_BG, theme.LOG_FG, theme.FONT_MONO, theme.RADIUS_CARD))
        self.log_view.setMinimumHeight(220)
        root.addWidget(self.log_view, 1)

        btns = QHBoxLayout()
        btns.setSpacing(10)
        self.start_btn = style_primary_button(PrimaryPushButton("开始捕获"))
        self.close_btn = style_button(PushButton("关闭"))
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
        # -Label 仍走命令行传参：以「-」开头的值会被 PowerShell 当成参数名
        if label.startswith("-"):
            self._show_error("名称不能以「-」开头")
            return
        server = "bilibili" if self.server_combo.currentIndex() == 1 else "official"
        self._slot = (self.acc or {}).get("slot") or ("acc_" + uuid.uuid4().hex[:8])

        script = Path(self.cfg["paths"]["script_dir"]) / "capture_account.ps1"
        args = [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
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
        self.hint_label.setText(
            "捕获进行中：自动启动模拟器（如未运行）→ 清空登录态 → 重启游戏 → "
            "点掉弹窗 → 输入账号密码 → 拉取数据。特殊字符密码或验证码时请留意提示，"
            "在模拟器窗口手动登录。")
        self.hint_label.setStyleSheet("color: %s; font-size: 12px;" % theme.TEXT_3)

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
            self._append_log(">>> 捕获成功！点击关闭返回列表。")
            self._apply_result()
            InfoBar.success("捕获成功", "登录数据已保存到槽位",
                            parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                            duration=5000)
            # 官服：立即向官方接口验证新 token（通过即解除仪表盘标红）
            if self.server_combo.currentIndex() == 0:
                self._verify_token()
        else:
            self._append_log(">>> 捕获失败（退出码 %d）。可修改后重试；登录态已自动恢复。" % code)
            self._show_error("捕获失败，详见下方日志。可修改后点「开始捕获」重试。")

    def _verify_token(self):
        """捕获成功后立即探测新 token（<1 秒），结果直接显示在提示行。"""
        cfg, slot = self.cfg, self._slot
        self.hint_label.setText("正在验证新 token...")
        self.hint_label.setStyleSheet("color: %s; font-size: 12px;" % theme.TEXT_3)
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
            self.hint_label.setText(
                "槽位里没有可校验的 SDK 凭据（USER_CACHE），挂机前会再做屏幕级检查")
            self.hint_label.setStyleSheet("color: %s; font-size: 12px;" % theme.TEXT_3)
        elif st.get("status") == "ok":
            self.hint_label.setText("✓ 新 token 验证通过，账号已可正常挂机")
            self.hint_label.setStyleSheet("color: %s; font-size: 12px;" % theme.OK)
        else:
            self.hint_label.setText(
                "⚠ token 验证未通过（%s），仪表盘会保持标红" % (st.get("detail") or "未知原因"))
            self.hint_label.setStyleSheet("color: %s; font-size: 12px;" % theme.ERR)

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
        # 不在这里刷新列表：三个调用方都在 exec() 返回后各自刷新。
        # 弹窗还挂着就 page.refresh() 会把「卡片 → 详情弹窗 → 本弹窗」整条
        # 父子链排进延迟删除队列，之后对已销毁 C++ 对象的访问会抛 RuntimeError
        return

    def on_close(self):
        self._closing = True
        if self.worker is not None:
            self.worker.kill()
            if not self.worker.wait(3000):
                # PowerShell 偶发僵死：terminate 兜底；仍不退出就摘掉 parent，
                # 让线程随进程退出，避免对话框析构时杀掉运行中的 QThread 直接 abort
                self.worker.terminate()
                if not self.worker.wait(1000):
                    self.worker.setParent(None)
                    _detached_threads.append(self.worker)
            self.worker = None
        # token 验证线程：探测一般 <1 秒，仍在跑就断开父级随进程收尾
        if self._token_worker is not None and self._token_worker.isRunning():
            self._token_worker.setParent(None)
            _detached_threads.append(self._token_worker)
        self._token_worker = None
        self.reject()

    def closeEvent(self, event):
        self.on_close()
        event.accept()


class AccountDetailDialog(QDialog):
    """账号详细控制：捕获、基建配置、候选关卡、删除都集中在这里。"""

    def __init__(self, cfg, acc, page=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.acc = acc
        self.page = page
        self.move_delta = 0   # 关闭窗口后由列表执行的上移/下移（-1/+1）
        self.deleted = False  # 关闭窗口后由列表刷新（删除改变了账号列表）
        self.export_wanted = False   # 关闭窗口后请求导出该账号（主窗口接手）
        self.switch_wanted = False   # 关闭窗口后请求切换到该账号（主窗口接手）
        self.setWindowTitle("账号详情 - %s" % (acc.get("label") or ""))
        self.setModal(True)
        self.resize(560, 480)
        self.setStyleSheet("QDialog { background: %s; }" % theme.BG)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(14)

        # 头部：名称 + 服务器/槽位 + UID 状态
        server = acc.get("server", "official")
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(IconBadge("1"))
        name_box = QVBoxLayout()
        name_box.setSpacing(2)
        name = SubtitleLabel(acc.get("label") or "?")
        _label_transparent(name)
        meta = BodyLabel("%s · 槽位 %s" % (SERVER_LABELS.get(server, server),
                                           acc.get("slot", "未设置")))
        meta.setStyleSheet("color: %s; font-size: 12px;" % theme.TEXT_2)
        _label_transparent(meta)
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
        _label_transparent(sec)
        root.addWidget(sec)

        grid = QGridLayout()
        grid.setSpacing(10)
        self.capture_btn = style_button(PushButton("重新捕获登录数据"))
        self.capture_btn.setToolTip("清空登录态并重新登录，拉取该账号的登录数据")
        self.bs_btn = style_button(PushButton("精确基建配置"))
        self.bs_btn.setToolTip(
            "配置该账号精确基建派驻（布局/批次/干员/无人机/菲亚梅塔恢复）")
        self.fight_btn = style_button(PushButton("第二理智候选关卡"))
        self.fight_btn.setToolTip("按 MAA 候选关卡界面修改该账号刷图候选")
        self.export_btn = style_button(PushButton("导出干员资料"))
        self.export_btn.setToolTip(
            "立即为该账号运行 MAA 干员识别（约 3 分钟）：切号 → 更新等待 → "
            "登录校验 → 识别，结果写入 exports\\。\n与挂机、MAA 更新互斥。")
        self.switch_btn = style_button(PushButton("切换到此账号"))
        self.switch_btn.setToolTip(
            "只切到该账号并完成登录校验，不跑日常任务：\n"
            "结束后模拟器保持运行，可直接手动游戏。\n与挂机、收菜、导出互斥。")
        self.delete_btn = style_button(PushButton("删除该账号"), "danger")
        for i, b in enumerate((self.capture_btn, self.bs_btn,
                               self.fight_btn, self.export_btn,
                               self.switch_btn, self.delete_btn)):
            b.setMinimumHeight(38)
            grid.addWidget(b, i // 2, i % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        root.addLayout(grid)

        # 运行顺序：列表里可直接拖动卡片，这里再给一份「上移/下移」按钮
        order_row = QHBoxLayout()
        order_row.setSpacing(10)
        order_lab = BodyLabel("运行顺序")
        order_lab.setStyleSheet("color: %s; font-size: 12.5px;" % theme.TEXT_2)
        _label_transparent(order_lab)
        order_row.addWidget(order_lab)
        self.up_btn = style_button(PushButton("↑ 上移"), small=True)
        self.up_btn.setToolTip("与上一个账号交换位置（运行顺序 = 列表顺序）")
        self.down_btn = style_button(PushButton("↓ 下移"), small=True)
        self.down_btn.setToolTip("与下一个账号交换位置（运行顺序 = 列表顺序）")
        order_row.addWidget(self.up_btn)
        order_row.addWidget(self.down_btn)
        order_row.addStretch(1)
        root.addLayout(order_row)

        self.hint = BodyLabel(
            "「启用 / 精确基建」开关在账号卡片上直接操作；"
            "导出干员资料点上方按钮，立即导这一个号（与挂机互斥）。"
            "运行顺序 = 账号列表顺序，列表里按住卡片（或账号名）拖到目标位置即可调整。")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: %s; font-size: 12px;" % theme.TEXT_3)
        _label_transparent(self.hint)
        root.addWidget(self.hint)
        root.addStretch(1)

        self.delete_btn.clicked.connect(self._on_delete)

        self.capture_btn.clicked.connect(self._on_capture)
        self.bs_btn.clicked.connect(self._on_base_config)
        self.fight_btn.clicked.connect(self._on_fight_plan)
        self.export_btn.clicked.connect(self._on_export)
        self.switch_btn.clicked.connect(self._on_switch)
        # 已有导出在跑：按钮直接置灰（其余互斥情形点击时由主窗口提示）
        if self.page is not None and self.page.export_busy():
            self.export_btn.setEnabled(False)
        self.up_btn.clicked.connect(lambda: self._on_move(-1))
        self.down_btn.clicked.connect(lambda: self._on_move(1))
        self._refresh_uid()

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
        self.deleted = True
        # 只记录删除并关窗，page.refresh() 由卡片在 exec() 返回后执行：
        # 弹窗未关就重建列表会把自己（卡片的子级）排进延迟删除队列
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
    """两列网格中的账号卡片：表面只放「精确基建 / 启用」两个开关，
    点击名字可直接改名，点击卡片空白处打开详细控制界面。"""

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
        self.vbox.setContentsMargins(12, 12, 12, 12)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.badge = IconBadge(str(index + 1))
        row.addWidget(self.badge)
        self.name_box = QVBoxLayout()
        self.name_box.setSpacing(2)
        self.name_label = ClickLabel(acc.get("label") or "?")
        self.name_label.setStyleSheet(
            "QLabel { background: transparent; color: %s;"
            " font-size: 20px; font-weight: 600; }" % theme.TEXT)
        self.name_label.setToolTip(
            "点击修改账号名称\n%s · 槽位 %s"
            % (SERVER_LABELS.get(server, server), acc.get("slot", "未设置")))
        self.name_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.name_label.clicked.connect(self._start_rename)
        self.name_label.drag_target = self   # 按住名字拖动也能调整顺序
        self.name_box.addWidget(self.name_label)
        row.addLayout(self.name_box)
        row.addStretch(1)

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
            # 两个开关自己的区域绝不触发详情/拖动（即使事件冒泡回卡片）
            if not (self.base_sw.geometry().contains(pos)
                    or self.sw.geometry().contains(pos)):
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
        if dlg.deleted and self.page is not None:
            self.page.refresh()
        if dlg.export_wanted and self.page is not None:
            self.page.export_requested.emit(self.acc)
        if dlg.switch_wanted and self.page is not None:
            self.page.switch_requested.emit(self.acc)

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
    switch_requested = 点了「切换到此账号」；都由主窗口做互斥检查与确认后启动。"""

    export_requested = Signal(object)
    switch_requested = Signal(object)

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

        # 「添加账号」操作放最上面；账号卡片与仪表盘一样直接铺在页面背景上
        action = Card()
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.add_btn = style_primary_button(PrimaryPushButton("添加账号"))
        self.add_btn.setToolTip("添加账号：输入名称/服务器/账号/密码，自动登录并保存登录数据")
        self.add_btn.clicked.connect(self._on_add)
        bar.addWidget(self.add_btn)
        bar.addStretch(1)
        order_hint = BodyLabel("按住卡片（或账号名）拖动：其他账号会让位预览，松手即调整运行顺序")
        order_hint.setStyleSheet("color: %s; font-size: 12.5px;" % theme.TEXT_2)
        _label_transparent(order_hint)
        bar.addWidget(order_hint)
        action.vbox.addLayout(bar)
        root.addWidget(action)

        # 卡片区：绝对定位 + 动画，故不使用布局管理器
        self.grid_area = CardGridArea(self)
        self.grid_area.setFixedHeight(0)
        self.empty_label = BodyLabel("暂无账号，点击上方「添加账号」开始。")
        self.empty_label.setStyleSheet("color: %s; font-size: 13px;" % theme.TEXT_3)
        _label_transparent(self.empty_label)
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
        self.refresh()

    def set_export_busy(self, busy):
        """导出运行状态由主窗口同步：期间新开的详情弹窗导出按钮置灰。"""
        self._export_busy = bool(busy)

    def export_busy(self):
        return self._export_busy

    def refresh(self):
        # 清空并重建账号卡片（固定每行 2 个，位置由 slot_rect 计算）
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
    def slot_rect(self, index, count=None):
        """第 index 个槽位的矩形（grid_area 坐标）。最后一张落单时横跨整行。"""
        n = len(self.cards) if count is None else count
        width = max(1, self.grid_area.width())
        card_w = max(160, (width - (GRID_COLS - 1) * GRID_SPACING) // GRID_COLS)
        row, _col = divmod(index, GRID_COLS)
        y = row * (self.card_h + GRID_SPACING)
        if n % GRID_COLS and index == n - 1:
            return QRect(0, y, width, self.card_h)
        return QRect(_col * (card_w + GRID_SPACING), y, card_w, self.card_h)

    def relayout(self):
        """按当前卡片顺序重排（窗口尺寸变化时直接对齐，不做动画）。"""
        if self._in_relayout:
            return
        self._in_relayout = True
        try:
            n = len(self.cards)
            rows = (n + GRID_COLS - 1) // GRID_COLS
            if rows:
                self.grid_area.setFixedHeight(
                    rows * self.card_h + (rows - 1) * GRID_SPACING)
            else:
                self.grid_area.setFixedHeight(self.empty_label.sizeHint().height())
            self.empty_label.setGeometry(0, 0, max(1, self.grid_area.width()),
                                         self.empty_label.sizeHint().height())
            for i, card in enumerate(self.cards):
                self._stop_ani(card)
                card.setGeometry(self.slot_rect(i, n))
        finally:
            self._in_relayout = False

    def slot_index_at(self, pos):
        """grid_area 坐标 pos 落在哪个槽位（按最近槽位中心判定）。"""
        n = len(self.cards)
        if n == 0:
            return 0
        best, best_dist = 0, None
        for i in range(n):
            center = self.slot_rect(i, n).center()
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
            self._animate_card(card, self.slot_rect(i, len(preview)))

    def end_drag(self):
        """松手：定稿顺序 + 拖影飞回槽位（卡片同步淡入）。"""
        card, proxy = self._drag_card, self._drag_proxy
        if card is None:
            return
        preview = list(self._drag_preview) or list(self._drag_order)
        slot = preview.index(card)
        target = self.slot_rect(slot, len(preview))
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
            self._animate_card(c, self.slot_rect(i, len(self.cards)))
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
            self._animate_card(c, self.slot_rect(i, len(cards)))
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
        """不做响应式重排：固定两列，窗口过窄时由横向滚动兜底。"""
        super().resizeEvent(event)
