# -*- coding: utf-8 -*-
"""精确基建派驻配置弹窗。

账号列表「基建」按钮打开此窗口：可切换 333/243 布局、按启动时间自动划分的批次
（如 8点/12点/24点），制造站/贸易站每台可先选择产品类别（赤金/原石碎片/作战记录），
再逐个设施/槽位填写干员名；保存后写入 config.json 并调用插件重新生成
MAA 自定义计划 JSON（master.ps1 启动 MAA 前会自动应用）。
"""
import copy
import json
import re
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFileDialog, QGridLayout, QHBoxLayout,
                               QScrollArea, QStackedWidget, QVBoxLayout,
                               QWidget)

from qfluentwidgets import (BodyLabel, ComboBox, InfoBar, InfoBarPosition,
                            LineEdit, MessageBox, PrimaryPushButton, PushButton,
                            SwitchButton)

import config as appconfig
import theme
from widgets import Card, set_switch_checked_gray

PLUGIN_DIR = Path(r"D:\1\plugins\base_schedule")
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))
import base_schedule as bsplugin

MANUFACTURE_OPTIONS = [
    ("赤金", "Pure Gold"),
    ("原石碎片", "Originium Shard"),
    ("作战记录", "Battle Record"),
]
TRADING_OPTIONS = [
    ("赤金（龙门币订单）", "LMD"),
    ("原石碎片（合成玉订单）", "Orundum"),
]


def _batch_labels(cfg):
    """按当前启动时间生成批次标签：如「8点批（08:00 - 11:59 生效）」。"""
    entries, names, periods = bsplugin.schedule_spec(cfg)
    return {
        name: "%s批（%s 生效）" % (
            name, "、".join("%s - %s" % (s[0], s[1]) for s in segs))
        for name, segs in zip(names, periods)
    }


def _safe_filename(text):
    """账号标签等 → 可用于 Windows 文件名的片段。"""
    name = re.sub(r'[\\/:*?"<>|\s]+', "_", str(text).strip())
    return name or "排班"


class BaseScheduleDialog(QDialog):
    def __init__(self, cfg, acc, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.acc = acc
        self.batches = appconfig.schedule_batches(cfg)
        self.batch_labels = _batch_labels(cfg)
        self.bs = bsplugin.normalize(acc.get("base_schedule"), self.batches)
        self.layout = self.bs["layout"]
        self.data = {
            b: copy.deepcopy(self.bs["batches"][b]) for b in self.batches
        }
        self.edit_map = {}
        self.pages = {}

        self.setWindowTitle("精确基建派驻 - %s" % acc.get("label", ""))
        self.setModal(True)
        self.resize(880, 720)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 16)
        root.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(BodyLabel("布局:"))
        self.layout_combo = ComboBox()
        self.layout_combo.addItems([
            "333 布局（贸易3台 · 制造3台 · 发电3台）",
            "243 布局（贸易2台 · 制造4台 · 发电3台）",
        ])
        self.layout_combo.currentIndexChanged.connect(self._on_layout_changed)
        self.layout_combo.blockSignals(True)
        self.layout_combo.setCurrentIndex(0 if self.layout == "333" else 1)
        self.layout_combo.blockSignals(False)
        top.addWidget(self.layout_combo)
        top.addStretch(1)
        top.addWidget(BodyLabel("批次:"))
        self.batch_combo = ComboBox()
        self.batch_combo.addItems([self.batch_labels[b] for b in self.batches])
        self.batch_combo.currentIndexChanged.connect(self._on_batch_changed)
        top.addWidget(self.batch_combo)
        root.addLayout(top)

        drones_row = QHBoxLayout()
        drones_row.setSpacing(10)
        drones_row.addWidget(BodyLabel("无人机:"))
        self.drones_switch = set_switch_checked_gray(SwitchButton())
        self.drones_switch.setText("启用")
        self.drones_switch.setChecked(bool(self.bs.get("drones", {}).get("enable")))
        drones_row.addWidget(self.drones_switch)
        drones_row.addSpacing(8)
        drones_row.addWidget(BodyLabel("目标"))
        self.drones_room_combo = ComboBox()
        self.drones_room_combo.addItem("制造站", userData="manufacture")
        self.drones_room_combo.addItem("贸易站", userData="trading")
        room = self.bs.get("drones", {}).get("room", "manufacture")
        idx = self.drones_room_combo.findData(room)
        self.drones_room_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.drones_room_combo.currentIndexChanged.connect(self._refresh_drones_index)
        drones_row.addWidget(self.drones_room_combo)
        self.drones_index_combo = ComboBox()
        drones_row.addWidget(self.drones_index_combo)
        drones_row.addWidget(BodyLabel("时机"))
        self.drones_order_combo = ComboBox()
        self.drones_order_combo.addItem("换班前投放", userData="pre")
        self.drones_order_combo.addItem("换班后投放", userData="post")
        order = self.bs.get("drones", {}).get("order", "pre")
        oidx = self.drones_order_combo.findData(order)
        self.drones_order_combo.setCurrentIndex(oidx if oidx >= 0 else 0)
        drones_row.addWidget(self.drones_order_combo)
        drones_row.addStretch(1)
        root.addLayout(drones_row)
        self._refresh_drones_index()

        self.hint = BodyLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: %s; font-size: 12px;" % theme.TEXT_2)
        root.addWidget(self.hint)

        self.stack = QStackedWidget()
        for i, b in enumerate(self.batches):
            page = self._build_page(b)
            self.stack.addWidget(page)
            self.pages[b] = page
        root.addWidget(self.stack, 1)

        btns = QHBoxLayout()
        btns.setSpacing(10)
        self.import_btn = PushButton("导入排班文件")
        self.import_btn.setToolTip(
            "选择一图流基建排班表 / MAA 自定义基建导出的 JSON，"
            "自动识别换班时段、布局与干员配置，填入当前账号对应批次。\n"
            "识别后仍需点「保存并生成计划」才会写入配置并生成 MAA 计划文件。")
        self.import_btn.clicked.connect(self._on_import)
        self.export_btn = PushButton("导出排班文件")
        self.export_btn.setToolTip(
            "把当前弹窗里的排班按一图流 / MAA 自定义基建 JSON 格式保存。\n"
            "默认保存到桌面，方便备份或导入其它工具 / MAA。")
        self.export_btn.clicked.connect(self._on_export)
        self.cancel_btn = PushButton("取消")
        self.save_btn = PrimaryPushButton("保存并生成计划")
        btns.addWidget(self.import_btn)
        btns.addWidget(self.export_btn)
        btns.addStretch(1)
        btns.addWidget(self.cancel_btn)
        btns.addWidget(self.save_btn)
        root.addLayout(btns)

        self.cancel_btn.clicked.connect(self.reject)
        self.save_btn.clicked.connect(self._on_save)
        self._refresh_hint()

    # ---------- 界面构建 ----------

    def _refresh_hint(self):
        if self.bs.get("enabled"):
            head = "该账号已启用精确基建：运行时按下面各批次计划让 MAA 精确派驻干员。"
        else:
            head = "该账号当前未启用：请在账号列表打开「精确基建」开关后才会生效，否则仍使用 MAA 自带基建换班。"
        head += (" 批次随启动时间自动划分：本次运行发生在哪个时间点之后，"
                 "就使用对应批次（如 8点/12点/24点）。")
        self.hint.setText(
            head + " 制造站/贸易站每台先选产品类别（制造：赤金/原石碎片/作战记录；"
            "贸易：赤金/原石碎片订单），MAA 应用计划时会按类别匹配游戏内对应设施"
            "并设置配方。干员休整（宿舍）：填入的干员放入，剩余空位自动补满"
            "（全部留空也自动安排）。其他设施：全部留空时 MAA 自动补满；"
            "只填了部分时，剩余位置保持空着（MAA 自定义模式不支持自动补位）。"
            "干员名需与游戏内名称一致（MAA 靠截图识别干员）。"
            "无人机：顶部开启后，每个班次换班时都会按设置向目标站台投放无人机。")

    @staticmethod
    def _make_edits(values, placeholders):
        edits = []
        for i, val in enumerate(values):
            e = LineEdit()
            e.setText(str(val))
            if i < len(placeholders):
                e.setPlaceholderText(placeholders[i])
            edits.append(e)
        return edits

    def _facility_card(self, title, rows):
        card = Card(title)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        for r, (row_label, edits) in enumerate(rows):
            if row_label:
                lab = BodyLabel(row_label)
                lab.setFixedWidth(52)
                lab.setStyleSheet("color: %s; font-size: 12px;" % theme.TEXT_2)
                grid.addWidget(lab, r, 0, Qt.AlignmentFlag.AlignVCenter)
                start = 1
            else:
                start = 0
            for c, e in enumerate(edits):
                grid.addWidget(e, r, start + c)
        grid.setColumnStretch(max(1, start + 8), 1)
        card.vbox.addLayout(grid)
        return card

    def _station_card(self, title, stations):
        """制造站/贸易站卡片：每行 = 站号 + 产品下拉框 + 3 个干员输入框。"""
        card = Card(title)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        for r, st in enumerate(stations):
            lab = BodyLabel("%d号站" % (r + 1))
            lab.setFixedWidth(52)
            lab.setStyleSheet("color: %s; font-size: 12px;" % theme.TEXT_2)
            grid.addWidget(lab, r, 0, Qt.AlignmentFlag.AlignVCenter)
            st["combo"].setMinimumWidth(150)
            grid.addWidget(st["combo"], r, 1, Qt.AlignmentFlag.AlignVCenter)
            for c, e in enumerate(st["edits"]):
                grid.addWidget(e, r, 2 + c)
        grid.setColumnStretch(5, 1)
        card.vbox.addLayout(grid)
        return card

    def _build_page(self, batch):
        data = self.data[batch]
        em = {}
        m = 4 if self.layout in ("423", "243") else 3
        t = 2 if self.layout in ("423", "243") else 3

        control = self._make_edits(
            data["control"], ["干员 1", "干员 2", "干员 3", "干员 4", "干员 5"])
        meeting = self._make_edits(data["meeting"], ["干员 1", "干员 2"])
        office = self._make_edits(data["office"], ["干员 1"])
        processing = self._make_edits(data["processing"], ["干员 1"])
        manufacture = [
            self._make_station(row, MANUFACTURE_OPTIONS)
            for row in self._take(data["manufacture"], m, 3,
                                  bsplugin.DEFAULT_MANUFACTURE_PRODUCT)
        ]
        trading = [
            self._make_station(row, TRADING_OPTIONS)
            for row in self._take(data["trading"], t, 3,
                                  bsplugin.DEFAULT_TRADING_PRODUCT)
        ]
        power = [self._make_edits(row, ["干员"]) for row in data["power"]]
        dormitory = [
            self._make_edits(row, ["干员 1", "干员 2", "干员 3", "干员 4", "干员 5"])
            for row in self._pad_rows(data.get("dormitory", []), 4, 5)
        ]

        em["control"] = [control]
        em["meeting"] = [meeting]
        em["office"] = [office]
        em["processing"] = [processing]
        em["manufacture"] = manufacture
        em["trading"] = trading
        em["power"] = power
        em["dormitory"] = dormitory
        self.edit_map[batch] = em

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(2, 2, 2, 2)
        v.setSpacing(12)

        v.addWidget(self._facility_card("控制中枢（5 人）", [("", control)]))
        v.addWidget(self._facility_card("会客室（2 人）", [("", meeting)]))
        v.addWidget(self._station_card(
            "贸易站（3 人 / 台，共 %d 台）" % len(trading),
            trading))
        v.addWidget(self._station_card(
            "制造站（3 人 / 台，共 %d 台）" % len(manufacture),
            manufacture))
        v.addWidget(self._facility_card(
            "发电站（1 人 / 台，共 %d 台）" % len(power),
            [("%d号站" % (i + 1), row) for i, row in enumerate(power)]))
        v.addWidget(self._facility_card(
            "干员休整（宿舍，5 人 / 间，共 %d 间，留空自动安排）" % len(dormitory),
            [("宿舍 %d" % (i + 1), row) for i, row in enumerate(dormitory)]))
        v.addWidget(self._facility_card("办公室（1 人）", [("", office)]))
        v.addWidget(self._facility_card(
            "加工站（1 人，可留空）",
            [("", processing)],
        ))
        v.addStretch(1)

        scroll.setWidget(inner)
        return scroll

    # ---------- 交互 ----------

    def _on_batch_changed(self, index):
        self.stack.setCurrentIndex(index)

    def _on_import(self):
        """导入一图流/MAA 自定义基建 JSON：识别后填入当前账号各批次（不自动保存）。"""
        start = Path.home() / "Desktop"
        if not start.exists():
            start = Path.home()
        fname, _ = QFileDialog.getOpenFileName(
            self, "选择排班文件（一图流 / MAA 自定义基建 JSON）",
            str(start), "JSON 文件 (*.json);;所有文件 (*)")
        if not fname:
            return
        try:
            with open(fname, "r", encoding="utf-8-sig") as f:
                doc = json.load(f)
            result = bsplugin.convert_import_document(self.cfg, doc)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            box = MessageBox("无法识别该排班文件", str(exc), self.window())
            box.yesButton.setText("知道了")
            box.cancelButton.hide()
            box.exec()
            return

        # 先把当前页面各批次读回 self.data，未覆盖到的批次保留未保存的修改
        for b in self.batches:
            self._capture(b)
        self.layout = result["layout"]
        self.layout_combo.blockSignals(True)
        self.layout_combo.setCurrentIndex(0 if self.layout == "333" else 1)
        self.layout_combo.blockSignals(False)
        for b, data in result["batches"].items():
            if b in self.data:
                self.data[b] = data
        # 统一按文件布局规范化：旧批次多余站台裁剪、旧数组格式转新格式
        norm = bsplugin.normalize(
            {"layout": self.layout, "batches": self.data}, self.batches)
        self.data = norm["batches"]

        drones = result.get("drones")
        if drones is not None:
            idx = self.drones_room_combo.findData(drones.get("room"))
            if idx >= 0:
                self.drones_room_combo.setCurrentIndex(idx)
            self._refresh_drones_index()
            d_idx = self.drones_index_combo.findData(drones.get("index"))
            if d_idx >= 0:
                self.drones_index_combo.setCurrentIndex(d_idx)
            o_idx = self.drones_order_combo.findData(drones.get("order"))
            if o_idx >= 0:
                self.drones_order_combo.setCurrentIndex(o_idx)
            self.drones_switch.setChecked(True)
        elif result.get("drones_explicit"):
            # 文件里明确写了不使用无人机：关掉，而不是沿用旧设置
            self.drones_switch.setChecked(False)

        self._rebuild_pages()
        self._refresh_drones_index()
        self._refresh_hint()

        summary = []
        if result.get("title"):
            summary.append("识别到排班：%s（%s 布局）" % (result["title"], self.layout))
        summary.append("已填入 %d 个批次：" % len(result["batches"]))
        for b in self.batches:
            if b not in result["batches"]:
                continue
            src = result["plan_names"].get(b)
            summary.append("  %s批 ← %s" % (b, src or "计划"))
        fia_lines = []
        for b in self.batches:
            fia = self.data.get(b, {}).get("fiammetta")
            if isinstance(fia, dict) and fia.get("enable") and fia.get("target"):
                fia_lines.append("  %s批：菲亚梅塔 → %s（%s）"
                                 % (b, fia["target"],
                                    "换班前" if fia.get("order") == "pre"
                                    else "换班后"))
        if fia_lines:
            summary.append("")
            summary.append("菲亚梅塔设置：")
            summary.extend(fia_lines)
        if result.get("drones"):
            d = result["drones"]
            summary.append("")
            summary.append("无人机：%s%d号站，%s投放"
                           % ("贸易站" if d.get("room") == "trading"
                              else "制造站",
                              d.get("index"),
                              "换班前" if d.get("order") == "pre"
                              else "换班后"))
        summary.append("")
        summary.append("内容已填入弹窗，尚未写入配置——点下方「保存并生成计划」即可生效。")
        for note in result.get("notes") or []:
            summary.append("")
            summary.append("说明：%s" % note)
        box = MessageBox("已识别并导入排班文件", "\n".join(summary), self.window())
        box.yesButton.setText("知道了")
        box.cancelButton.hide()
        box.exec()

    def _refresh_drones_index(self, *_):
        """按目标设施与布局刷新无人机站号下拉（制造 3/4 台，贸易 2/3 台）。"""
        room = self.drones_room_combo.currentData()
        if room == "trading":
            n = 2 if self.layout in ("423", "243") else 3
        else:
            n = 4 if self.layout in ("423", "243") else 3
        cur = self.drones_index_combo.currentData()
        self.drones_index_combo.blockSignals(True)
        self.drones_index_combo.clear()
        for i in range(1, n + 1):
            self.drones_index_combo.addItem("%d号站" % i, userData=i)
        if cur is not None:
            idx = self.drones_index_combo.findData(cur)
            if idx >= 0:
                self.drones_index_combo.setCurrentIndex(idx)
        self.drones_index_combo.blockSignals(False)

    def _on_layout_changed(self, index):
        self.layout = "243" if index == 1 else "333"
        for b in self.batches:
            self._capture(b)
        self._rebuild_pages()
        self._refresh_drones_index()
        self._refresh_hint()

    def _rebuild_pages(self):
        for i, b in enumerate(self.batches):
            old = self.pages.pop(b)
            self.stack.removeWidget(old)
            old.deleteLater()
            page = self._build_page(b)
            self.stack.insertWidget(i, page)
            self.pages[b] = page
        self.stack.setCurrentIndex(self.batch_combo.currentIndex())

    def _make_station(self, row, options):
        """一行制造/贸易站：产品下拉框 + 干员输入框。"""
        combo = ComboBox()
        for text, data in options:
            combo.addItem(text, userData=data)
        idx = combo.findData(row.get("product"))
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        edits = self._make_edits(row.get("operators", []), ["干员 1", "干员 2", "干员 3"])
        return {"combo": combo, "edits": edits}

    @staticmethod
    def _pad_rows(rows, count, inner):
        """取前 count 行、每行补到 inner 个元素；不足补空列表。"""
        out = []
        for i in range(count):
            row = rows[i] if i < len(rows) else []
            if not isinstance(row, list):
                row = []
            row = [str(x) for x in row]
            out.append((row + [""] * inner)[:inner])
        return out

    @staticmethod
    def _take(rows, count, inner, default_product):
        """取前 count 台展示，不足补齐空槽位（多余数据保留在 self.data 中）。"""
        out = []
        for i in range(count):
            item = rows[i] if i < len(rows) else {}
            if isinstance(item, list):  # 旧格式兼容
                item = {"product": default_product, "operators": item}
            if not isinstance(item, dict):
                item = {}
            ops = item.get("operators")
            if not isinstance(ops, list):
                ops = []
            ops = [str(x) for x in ops]
            out.append({
                "product": item.get("product") or default_product,
                "operators": (ops + [""] * inner)[:inner],
            })
        return out

    @staticmethod
    def _capture_stations(em_key):
        """从界面控件读回制造/贸易站：{product, operators}。"""
        return [
            {"product": st["combo"].currentData(),
             "operators": [e.text().strip() for e in st["edits"]]}
            for st in em_key
        ]

    def _capture(self, batch):
        em = self.edit_map[batch]
        self.data[batch]["control"] = [e.text().strip() for e in em["control"][0]]
        self.data[batch]["meeting"] = [e.text().strip() for e in em["meeting"][0]]
        self.data[batch]["office"] = [e.text().strip() for e in em["office"][0]]
        self.data[batch]["processing"] = [e.text().strip() for e in em["processing"][0]]
        self.data[batch]["power"] = [
            [e.text().strip() for e in row] for row in em["power"]]
        self.data[batch]["dormitory"] = [
            [e.text().strip() for e in row] for row in em["dormitory"]]
        for key in ("manufacture", "trading"):
            shown = self._capture_stations(em[key])
            old = self.data[batch][key]
            surplus = old[len(shown):] if isinstance(old, list) else []
            self.data[batch][key] = shown + list(surplus)

    def _collect_bs(self):
        """把当前界面状态收集为规范化后的 base_schedule 结构。"""
        for b in self.batches:
            self._capture(b)
        bs = {
            "enabled": bool(self.bs.get("enabled")),
            "layout": self.layout,
            "drones": {
                "room": self.drones_room_combo.currentData(),
                "index": int(self.drones_index_combo.currentData() or 1),
                "enable": self.drones_switch.isChecked(),
                "order": self.drones_order_combo.currentData(),
            },
            "batches": self.data,
        }
        return bsplugin.normalize(bs, self.batches)

    def _on_save(self):
        bs = self._collect_bs()
        self.acc["base_schedule"] = bs
        appconfig.save(self.cfg)
        try:
            bsplugin.regenerate_for_account(self.cfg, self.acc)
        except Exception as exc:  # noqa: BLE001 - 保存失败要给用户明确提示
            box = MessageBox(
                "保存失败",
                "配置已写入，但计划文件生成失败：\n%s" % exc,
                self.window())
            box.yesButton.setText("知道了")
            box.cancelButton.hide()
            box.exec()
            return
        self.accept()

    def _on_export(self):
        """按一图流 / MAA 自定义基建格式导出当前弹窗里的排班（默认存桌面）。"""
        bs = self._collect_bs()
        entries, names, _periods = bsplugin.schedule_spec(self.cfg)
        if not entries:
            box = MessageBox("无法导出", "当前没有启用的班次计划时间，无法生成换班时段。",
                             self.window())
            box.yesButton.setText("知道了")
            box.cancelButton.hide()
            box.exec()
            return
        doc = bsplugin.build_plan_document(
            bs, entries,
            title=(self.acc.get("label") or "自定义基建"),
            description="由 MAA 挂机控制台导出（%s）" % "、".join(names),
        )

        start = Path.home() / "Desktop"
        if not start.exists():
            start = Path.home()
        default_name = "排班表%s.json" % _safe_filename(self.acc.get("label")
                                                        or "排班")
        fname, _ = QFileDialog.getSaveFileName(
            self, "导出排班文件", str(start / default_name),
            "JSON 文件 (*.json);;所有文件 (*)")
        if not fname:
            return
        out = Path(fname)
        if out.suffix.lower() != ".json":
            out = out.with_suffix(".json")
        tmp = out.with_name(out.name + ".tmp")
        try:
            tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                           encoding="utf-8", newline="\n")
            tmp.replace(out)
        except OSError as exc:
            box = MessageBox("导出失败", str(exc), self.window())
            box.yesButton.setText("知道了")
            box.cancelButton.hide()
            box.exec()
            return
        box = MessageBox(
            "已导出排班文件",
            "已保存到：\n%s\n\n格式：一图流 / MAA 自定义基建 JSON（%d 个班次：%s）。"
            % (out, len(doc.get("plans") or []), "、".join(names)),
            self.window())
        box.yesButton.setText("知道了")
        box.cancelButton.hide()
        box.exec()


def show_base_schedule_dialog(cfg, acc, parent=None):
    """打开配置弹窗；成功后返回 True，供账号行显示提示。"""
    dlg = BaseScheduleDialog(cfg, acc, parent=parent)
    if dlg.exec():
        names = bsplugin.schedule_spec(cfg)[1]
        InfoBar.success(
            "基建计划已保存",
            "运行该账号时将按 %s 各批精确派驻干员" % "、".join(names),
            parent=parent.window() if parent is not None else None,
            position=InfoBarPosition.TOP_RIGHT, duration=4000)
        return True
    return False
