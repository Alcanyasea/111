# -*- coding: utf-8 -*-
"""主题配色方案测试：5 套配色 × 明暗两套的组合完整性与切换正确性。"""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gui"))

import theme  # noqa: E402


class PaletteDefinitionsTest(unittest.TestCase):
    def test_five_palettes_with_labels(self):
        self.assertEqual(theme.PALETTES,
                         ("neutral", "sand", "moss", "mist", "plum"))
        for name, label in theme.palette_labels():
            self.assertTrue(label.strip())

    def test_every_palette_complete_in_both_modes(self):
        """每个配色 × 明暗的合并调色板必须包含 neutral 全部键（缺键 = 运行期
        AttributeError / 样式丢色），覆盖键的值必须与模板真正不同（改了就生效）。"""
        for mode in ("light", "dark"):
            base = theme._merged(mode, "neutral")
            for name in theme.PALETTES:
                if name == "neutral":
                    continue
                merged = theme._merged(mode, name)
                missing = set(base) - set(merged)
                self.assertEqual(missing, set(), (name, mode, missing))
                changed = {k for k in base if merged[k] != base[k]}
                for key in ("BG", "CARD", "TEXT", "ACCENT",
                            "PRIMARY_BG", "INFOBAR_BG"):
                    self.assertIn(key, changed, (name, mode, key))


class ApplyTest(unittest.TestCase):
    def setUp(self):
        # 每个用例结束恢复默认（neutral 明亮），不污染其他测试
        self.addCleanup(theme.apply, "light", "neutral")

    def test_apply_switches_palette_tokens(self):
        theme.apply("dark", "sand")
        sand_dark = theme._merged("dark", "sand")
        self.assertEqual(theme.BG, sand_dark["BG"])
        self.assertEqual(theme.CARD, sand_dark["CARD"])
        self.assertEqual(theme.ACCENT, sand_dark["ACCENT"])
        self.assertTrue(theme.is_dark())
        self.assertEqual(theme.palette_name(), "sand")
        # 两套一次写入的常量随配色刷新（窗口背景 / 通知条取的就是它们）
        self.assertEqual(theme.BG_LIGHT, theme._merged("light", "sand")["BG"])
        self.assertEqual(theme.BG_DARK, sand_dark["BG"])
        self.assertEqual(theme.INFOBAR_BG_LIGHT,
                         theme._merged("light", "sand")["INFOBAR_BG"])

    def test_apply_back_to_neutral_restores(self):
        theme.apply("dark", "moss")
        theme.apply("light", "neutral")
        self.assertEqual(theme.BG, theme._LIGHT["BG"])
        self.assertEqual(theme.BG_LIGHT, theme._LIGHT["BG"])
        self.assertFalse(theme.is_dark())
        self.assertEqual(theme.palette_name(), "neutral")

    def test_unknown_palette_falls_back_to_default(self):
        theme.apply("light", "不存在的配色")
        self.assertEqual(theme.BG, theme._LIGHT["BG"])
        self.assertEqual(theme.palette_name(), "neutral")

    def test_accent_of_prefetches_target_palette(self):
        # 切换前预取目标配色的强调色（setThemeColor 先于 apply 的时序依赖）
        theme.apply("light", "neutral")
        self.assertEqual(theme.accent_of("light", "mist"),
                         theme._merged("light", "mist")["ACCENT"])
        self.assertEqual(theme.accent_of("light"), theme._LIGHT["ACCENT"])

    def test_config_load_does_not_pull_qt(self):
        # config.load() 的配色规范化引用 theme；theme 必须保持零 Qt 依赖
        # （计划任务场景只跑 PowerShell，插件侧即便引用 config 也不该连带
        # 拖起 GUI 框架）。独立子进程验证，避免被同进程其他测试的 import 干扰。
        import subprocess
        code = (
            "import sys; sys.path.insert(0, r'%s'); "
            "import config; config.load(); "
            "assert 'PySide6' not in sys.modules, 'theme pulled in Qt'"
        ) % (ROOT / "gui")
        subprocess.run([sys.executable, "-c", code], check=True)


if __name__ == "__main__":
    unittest.main()
