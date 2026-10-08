# -*- coding: utf-8 -*-
"""基建排班弹窗预设功能冒烟测试（offscreen 渲染，不弹窗、不写真实配置）。

预设是账号级的：保存在账号对象 base_schedule_presets 上，账号之间不互通。
"""
import copy
import os
import sys
import types
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gui"))

from PySide6.QtWidgets import QApplication  # noqa: E402

import config as appconfig  # noqa: E402
from pages import base_schedule_dialog as bsdlg_mod  # noqa: E402
from pages.base_schedule_dialog import BaseScheduleDialog  # noqa: E402

import qfluentwidgets  # noqa: E402

# _apply_preset 末尾的摘要 MessageBox 会模态 exec() 阻塞测试：统一补丁为立即返回
_REAL_MSGBOX_EXEC = qfluentwidgets.MessageBox.exec


def _noop_msgbox_exec(self, *args, **kwargs):
    return True


def _cfg():
    return {
        "schedule": {"times": [
            {"time": "04:00", "enabled": True, "shutdown": True},
            {"time": "16:00", "enabled": True, "shutdown": False},
        ]},
    }


def _acc(acc_id="a1", label="测试号", presets=None, layout="333"):
    return {"id": acc_id, "label": label, "server": "official",
            "base_schedule": appconfig.default_base_schedule(layout),
            "base_schedule_presets": presets or []}


class _FakeNameBox:
    """替代 _PresetNameBox：固定名称、exec 立即确认，避免模态阻塞。"""

    last_acc_label = None

    def __init__(self, parent, default="", acc_label=""):
        _FakeNameBox.last_acc_label = acc_label
        self.name_edit = types.SimpleNamespace(
            text=lambda: default or "新预设")

    def exec(self):
        return True


class PresetDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        qfluentwidgets.MessageBox.exec = _noop_msgbox_exec

    @classmethod
    def tearDownClass(cls):
        qfluentwidgets.MessageBox.exec = _REAL_MSGBOX_EXEC
        cls.app.processEvents()

    def _dlg(self, acc=None):
        dlg = BaseScheduleDialog(_cfg(), acc or _acc())
        self.addCleanup(dlg.deleteLater)
        return dlg

    def test_empty_preset_list(self):
        dlg = self._dlg()
        self.assertEqual(dlg.preset_combo.count(), 1)   # 只有占位项
        self.assertIsNone(dlg.preset_combo.currentData())
        self.assertFalse(dlg.preset_del_btn.isEnabled())

    def test_presets_come_from_own_account_only(self):
        # 预设读账号自己的列表；其他账号的预设不出现（不互通）
        dlg = self._dlg(_acc(presets=[{"name": "日常", "schedule": {}},
                                      {"name": "爆金币", "schedule": {}}]))
        self.assertEqual(dlg.preset_combo.count(), 3)   # 占位 + 2 个预设
        self.assertEqual(dlg.preset_combo.itemText(1), "日常")
        self.assertEqual(dlg.preset_combo.itemText(2), "爆金币")
        other = self._dlg(_acc(acc_id="a2", label="另一个号",
                               presets=[{"name": "别人的", "schedule": {}}]))
        self.assertEqual(other.preset_combo.count(), 2)   # 占位 + 自己的 1 个
        self.assertEqual(other.preset_combo.itemText(1), "别人的")

    def test_refresh_presets_selection_state(self):
        presets = [{"name": "A", "schedule": {}}, {"name": "B", "schedule": {}}]
        dlg = self._dlg(_acc(presets=presets))
        dlg._refresh_presets(select_name="B")
        self.assertEqual(dlg.preset_combo.currentData(), "B")
        self.assertTrue(dlg.preset_del_btn.isEnabled())
        self.assertTrue(dlg.preset_apply_btn.isEnabled())
        dlg._refresh_presets()
        self.assertIsNone(dlg.preset_combo.currentData())
        self.assertFalse(dlg.preset_del_btn.isEnabled())
        self.assertFalse(dlg.preset_apply_btn.isEnabled())

    def test_select_only_does_not_touch_content(self):
        # 选中预设只是选中：不弹应用框、当前排班内容不变，「应用」才填入
        preset = {"name": "243方案",
                  "schedule": {"layout": "243", "drones": {},
                               "batches": {"4点": {"control": ["阿米娅"]}}}}
        dlg = self._dlg(_acc(presets=[copy.deepcopy(preset)]))
        before = copy.deepcopy(dlg.data)
        dlg.preset_combo.setCurrentIndex(1)   # 真实选中（触发信号 → 仅选中态）
        self.assertEqual(dlg.preset_combo.currentData(), "243方案")
        self.assertEqual(dlg.data, before)          # 内容原样
        self.assertTrue(dlg.preset_apply_btn.isEnabled())
        self.assertTrue(dlg.preset_del_btn.isEnabled())
        dlg._on_preset_apply()                      # 确认框已被 patch 为确认
        self.assertEqual(dlg.layout_name, "243")    # 此时才应用
        self.assertEqual(dlg.data["4点"]["control"][:1], ["阿米娅"])
        # 删除同样不需要先应用
        dlg._refresh_presets(select_name="243方案")
        real_save = appconfig.save
        appconfig.save = lambda cfg: None
        self.addCleanup(setattr, appconfig, "save", real_save)
        dlg._on_preset_delete()
        self.assertEqual(dlg.acc["base_schedule_presets"], [])

    def test_save_preset_writes_to_account_not_config(self):
        dlg = self._dlg()
        real_save = appconfig.save
        real_box = bsdlg_mod._PresetNameBox
        saved = []
        appconfig.save = lambda cfg: saved.append(cfg)
        bsdlg_mod._PresetNameBox = _FakeNameBox
        self.addCleanup(setattr, bsdlg_mod, "_PresetNameBox", real_box)
        self.addCleanup(setattr, appconfig, "save", real_save)
        dlg._on_preset_save()
        self.assertEqual(saved, [dlg.cfg])
        # 写在账号对象上，配置顶层没有该键
        names = [p["name"] for p in dlg.acc["base_schedule_presets"]]
        self.assertEqual(names, ["新预设"])
        self.assertNotIn("base_schedule_presets", dlg.cfg)
        # 提示账号归属
        self.assertIn("测试号", _FakeNameBox.last_acc_label)
        self.assertEqual(dlg.preset_combo.currentData(), "新预设")

    def test_save_preset_overwrite_same_name_keeps_other_accounts(self):
        other_acc = _acc(acc_id="a2", label="另一个号",
                         presets=[{"name": "新预设", "schedule": {"layout": "333"}}])
        dlg = self._dlg()
        real_save = appconfig.save
        real_box = bsdlg_mod._PresetNameBox
        appconfig.save = lambda cfg: None
        bsdlg_mod._PresetNameBox = _FakeNameBox
        self.addCleanup(setattr, bsdlg_mod, "_PresetNameBox", real_box)
        self.addCleanup(setattr, appconfig, "save", real_save)
        dlg._on_preset_save()
        self.assertEqual(len(dlg.acc["base_schedule_presets"]), 1)
        # 覆盖保存只动自己账号；其它账号的同名预设原样保留
        self.assertEqual(other_acc["base_schedule_presets"],
                         [{"name": "新预设", "schedule": {"layout": "333"}}])

    def test_delete_preset_from_account(self):
        acc = _acc(presets=[{"name": "日常", "schedule": {}},
                            {"name": "爆金币", "schedule": {}}])
        dlg = self._dlg(acc)
        real_save = appconfig.save
        appconfig.save = lambda cfg: None   # 删除确认后 _on_preset_delete 会写盘
        self.addCleanup(setattr, appconfig, "save", real_save)
        dlg._refresh_presets(select_name="日常")
        dlg._on_preset_delete()
        names = [p["name"] for p in acc["base_schedule_presets"]]
        self.assertEqual(names, ["爆金币"])
        self.assertEqual(dlg.preset_combo.currentData(), None)
        self.assertNotIn("base_schedule_presets", dlg.cfg)

    def test_apply_preset_overrides_matching_batches(self):
        preset = {
            "name": "243方案",
            "schedule": {
                "layout": "243",
                "drones": {"room": "trading", "index": 2,
                           "enable": True, "order": "post"},
                "batches": {
                    "4点": {
                        "control": ["阿米娅", "凯尔希"],
                        "fiammetta": {"enable": True, "target": "清流"},
                    },
                    # 16点批不在预设里 → 应用后保留账号当前内容
                },
            },
        }
        dlg = self._dlg(_acc(presets=[copy.deepcopy(preset)]))
        dlg._apply_preset(preset)

        self.assertEqual(dlg.layout_name, "243")
        data = dlg.data["4点"]
        self.assertEqual(data["control"][:2], ["阿米娅", "凯尔希"])
        self.assertEqual(len(data["control"]), 5)
        self.assertEqual(len(data["manufacture"]), 4)   # 243 → 制造 4 台
        self.assertTrue(data["fiammetta"]["enable"])
        self.assertEqual(data["fiammetta"]["target"], "清流")
        # 顶部菲亚梅塔行跟随当前批次（4点）
        self.assertTrue(dlg.fia_switch.isChecked())
        self.assertEqual(dlg.fia_combo.currentData(), "清流")
        # 无人机控件同步
        self.assertTrue(dlg.drones_switch.isChecked())
        self.assertEqual(dlg.drones_room_combo.currentData(), "trading")
        self.assertEqual(dlg.drones_index_combo.currentData(), 2)
        self.assertEqual(dlg.drones_order_combo.currentData(), "post")
        # 未覆盖批次保留现状
        self.assertEqual(dlg.data["16点"]["control"], [""] * 5)

        # 收集回的完整结构（_collect_bs 会先从界面读回）与预设一致
        bs = dlg._collect_bs()
        self.assertEqual(bs["layout"], "243")
        self.assertEqual(bs["batches"]["4点"]["control"][:2],
                         ["阿米娅", "凯尔希"])
        self.assertTrue(bs["batches"]["4点"]["fiammetta"]["enable"])
        self.assertEqual(bs["drones"], preset["schedule"]["drones"])

    def test_apply_preset_without_schedule_keeps_dialog(self):
        # 预设 schedule 坏成空 dict：不崩、布局不变、各批次保持当前内容
        dlg = self._dlg(_acc(presets=[{"name": "空预设", "schedule": {}}]))
        dlg._apply_preset(dlg._presets()[0])
        self.assertEqual(dlg.layout_name, "333")
        self.assertEqual(dlg.data["4点"]["control"], [""] * 5)
        self.assertEqual(len(dlg.data["4点"]["manufacture"]), 3)


if __name__ == "__main__":
    unittest.main()
