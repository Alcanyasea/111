# -*- coding: utf-8 -*-
"""控制台 UI 重做的行为固化测试（offscreen 渲染，appconfig.save 打补丁）。

覆盖：设置页配色色卡（PaletteSwatch）与下拉框同源联动、
theme.palette_preview 预览令牌、widgets.Pill 圆点徽章与 _rgba 工具。
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gui"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.test_support import appconfig  # noqa: E402
import theme  # noqa: E402
from pages.settings import PaletteSwatch, SettingsPage  # noqa: E402
from widgets import Pill, _rgba  # noqa: E402


def _fake_cfg(theme_name="light", palette="github"):
    return {"appearance": {"theme": theme_name, "palette": palette},
            "paths": {"script_dir": "x"}, "timeouts": {}, "behavior": {},
            "cleanup": {}, "maa_update": {}, "notify": {}}


class PalettePreviewTest(unittest.TestCase):
    def test_preview_tokens_do_not_depend_on_current_theme(self):
        theme.apply("dark", "github")     # 当前处于暗色也不影响预览令牌
        pv = theme.palette_preview("nord")
        self.assertEqual(pv["light_bg"], "#e5e9f0")
        self.assertEqual(pv["dark_bg"], "#2e3440")
        self.assertTrue(pv["accent"].startswith("#"))
        theme.apply("light", "github")    # 还原，避免影响其它用例

    def test_unknown_palette_falls_back(self):
        pv = theme.palette_preview("no-such-palette")
        pv_default = theme.palette_preview(theme.DEFAULT_PALETTE)
        self.assertEqual(pv, pv_default)


class PaletteSwatchPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.saved = []
        cls._real_save = appconfig.save
        appconfig.save = lambda cfg: cls.saved.append(cfg)
        theme.apply("light", "github")

    @classmethod
    def tearDownClass(cls):
        appconfig.save = cls._real_save
        cls.app.processEvents()

    def _page(self, palette="github", on_theme_change=None):
        page = SettingsPage(_fake_cfg(palette=palette),
                            on_theme_change=on_theme_change)
        self.addCleanup(page.deleteLater)
        return page

    def test_swatches_mirror_initial_palette(self):
        page = self._page(palette="nord")
        sel = [sw._selected for sw in page.palette_swatches]
        self.assertEqual(sel, [False, False, False, True, False])

    def test_click_swatch_switches_palette_via_combo_signal(self):
        called = []

        def on_change(name, palette_name=None):
            called.append((name, palette_name))
            return True

        page = self._page(palette="nord", on_theme_change=on_change)
        page._pick_palette("tokyonight")
        self.assertEqual(page.palette_combo.currentData(), "tokyonight")
        self.assertEqual([sw._selected for sw in page.palette_swatches],
                         [False, False, False, False, True])
        self.assertEqual(called, [("light", "tokyonight")])

    def test_click_same_swatch_keeps_state(self):
        page = self._page(palette="nord")
        page._pick_palette("nord")   # 重复点击当前配色：不触发主题切换，仅刷高亮
        self.assertEqual(page.palette_combo.currentData(), "nord")
        self.assertEqual([sw._selected for sw in page.palette_swatches],
                         [False, False, False, True, False])

    def test_widget_selected_roundtrip(self):
        sw = PaletteSwatch("github", "清爽极简 · GitHub")
        self.assertFalse(sw._selected)
        sw.set_selected(True)
        self.assertTrue(sw._selected)
        sw.set_selected(True)   # 重复设置同一状态不重复重画
        self.assertTrue(sw._selected)


class PillBadgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.apply("light", "github")

    def test_plain_pill_keeps_text(self):
        pill = Pill("空闲")
        self.addCleanup(pill.deleteLater)
        self.assertEqual(pill.text(), "空闲")

    def test_dot_pill_prepends_bullet(self):
        pill = Pill("运行中", dot=True)
        self.addCleanup(pill.deleteLater)
        self.assertIn("&#9679;", pill.text())
        self.assertIn("运行中", pill.text())

    def test_set_state_updates_text_and_kind(self):
        pill = Pill()
        self.addCleanup(pill.deleteLater)
        pill.set_state("ok", "已完成", dot=True)
        self.assertEqual(pill._kind, "ok")
        self.assertTrue(pill._dot)
        self.assertIn("&#9679;", pill.text())

    def test_rgba_helper(self):
        self.assertEqual(_rgba("#0969da", 0.28), "rgba(9, 105, 218, 0.28)")


if __name__ == "__main__":
    unittest.main()
