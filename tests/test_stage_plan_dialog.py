# -*- coding: utf-8 -*-
"""候选关卡弹窗冒烟测试（offscreen 渲染，appconfig.save 打补丁，不写真实配置）。

覆盖 1-7 固定保底：保底永远排最后、新加关卡插最前、空选择保存为保底、
关闭备选只存首项、下拉选 1-7 不重复添加。
"""
import os
import unittest
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gui"))

from PySide6.QtWidgets import QApplication  # noqa: E402

import config as appconfig  # noqa: E402
from pages.stage_plan_dialog import FALLBACK_STAGE, StagePlanDialog  # noqa: E402


def _acc(plan=None, use_optional=True):
    return {"id": "a1", "label": "测试号", "server": "official",
            "second_fight_plan": list(plan or []),
            "second_fight_use_optional": use_optional}


class StagePlanDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.cfg = {"paths": {}, "accounts": []}   # 假配置：不指向任何真实 MAA 目录
        cls.saved = []
        cls._real_save = appconfig.save
        appconfig.save = lambda cfg: cls.saved.append(cfg)

    @classmethod
    def tearDownClass(cls):
        appconfig.save = cls._real_save
        cls.app.processEvents()

    def _dlg(self, plan=None, use_optional=True):
        acc = _acc(plan, use_optional)
        dlg = StagePlanDialog(self.cfg, acc)
        self.addCleanup(dlg.deleteLater)
        return dlg

    def test_fallback_stripped_and_repinned_on_save(self):
        # 历史计划 1-7 写在最前：剥离出用户选择，保存时规范回最后
        dlg = self._dlg(["1-7", "CE-6"])
        self.assertEqual(dlg.selected, ["CE-6"])
        n = len(self.saved)
        dlg._on_save()
        self.assertEqual(dlg.acc["second_fight_plan"], ["CE-6", "1-7"])
        self.assertEqual(len(self.saved), n + 1)   # 保存走 appconfig.save（已打补丁）

    def test_new_pick_inserted_first_fallback_last(self):
        dlg = self._dlg(["CE-6"])
        # 下拉第 3 项 = 1-7（0 占位、1 当前/上次、2 常驻第一项）；先换成 LS-6
        idx = next(i for i in range(dlg.picker.count())
                   if dlg.picker.itemData(i) == "LS-6")
        dlg.picker.setCurrentIndex(idx)
        dlg._on_pick_stage()
        self.assertEqual(dlg.selected[0], "LS-6")
        dlg._on_save()
        self.assertEqual(dlg.acc["second_fight_plan"],
                         ["LS-6", "CE-6", "1-7"])

    def test_pick_fallback_stage_is_noop(self):
        dlg = self._dlg(["CE-6"])
        before = list(dlg.selected)
        idx = next(i for i in range(dlg.picker.count())
                   if dlg.picker.itemData(i) == FALLBACK_STAGE)
        dlg.picker.setCurrentIndex(idx)
        dlg._on_pick_stage()
        self.assertEqual(dlg.selected, before)   # 保底已固定存在，不重复入列

    def test_empty_selection_saves_fallback_only(self):
        dlg = self._dlg([])
        dlg._on_save()
        self.assertEqual(dlg.acc["second_fight_plan"], ["1-7"])

    def test_optional_off_saves_first_stage_only(self):
        dlg = self._dlg(["CE-6", "LS-6"], use_optional=False)
        dlg._on_save()
        self.assertEqual(dlg.acc["second_fight_plan"], ["CE-6"])
        self.assertFalse(dlg.acc["second_fight_use_optional"])

    def test_optional_off_empty_saves_fallback(self):
        dlg = self._dlg([], use_optional=False)
        dlg._on_save()
        self.assertEqual(dlg.acc["second_fight_plan"], ["1-7"])

    def test_fallback_chip_rendered_last_without_buttons(self):
        dlg = self._dlg(["CE-6", "LS-6"])
        chips = []
        for i in range(dlg.tags_layout.count()):
            row = dlg.tags_layout.itemAt(i).widget()
            if row is None:
                continue                            # 空态提示标签等非容器项
            for j in range(row.layout().count()):
                w = row.layout().itemAt(j).widget()
                if w is not None and w.objectName() == "stageTag":
                    chips.append(w)
        self.assertEqual(len(chips), 3)          # 2 个用户候选 + 1 个保底
        self.assertEqual(chips[-1].layout().count(), 3)   # 保底：序号+名+弹簧（无按钮）
        self.assertEqual(chips[0].layout().count(), 6)    # 用户候选：+3 个按钮


if __name__ == "__main__":
    unittest.main()
