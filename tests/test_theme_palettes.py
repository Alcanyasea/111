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
                         ("github", "catppuccin", "everforest",
                          "nord", "tokyonight"))
        for name, label in theme.palette_labels():
            self.assertTrue(label.strip())

    def test_every_palette_complete_in_both_modes(self):
        """每个配色 × 明暗的合并调色板必须包含模板全部键（缺键 = 运行期
        AttributeError / 样式丢色），色相层覆盖必须真正生效（改了就看得见）。"""
        for mode in ("light", "dark"):
            base = theme._DARK if mode == "dark" else theme._LIGHT
            for name in theme.PALETTES:
                merged = theme._merged(mode, name)
                missing = set(base) - set(merged)
                self.assertEqual(missing, set(), (name, mode, missing))
                changed = {k for k in base if merged[k] != base[k]}
                for key in ("BG", "CARD", "TEXT", "ACCENT",
                            "PRIMARY_BG", "INFOBAR_BG"):
                    self.assertIn(key, changed, (name, mode, key))


class ApplyTest(unittest.TestCase):
    def setUp(self):
        # 每个用例结束恢复默认（GitHub 风明亮），不污染其他测试
        self.addCleanup(theme.apply, "light", "github")

    def test_apply_switches_palette_tokens(self):
        theme.apply("dark", "nord")
        nord_dark = theme._merged("dark", "nord")
        self.assertEqual(theme.BG, nord_dark["BG"])
        self.assertEqual(theme.CARD, nord_dark["CARD"])
        self.assertEqual(theme.ACCENT, nord_dark["ACCENT"])
        self.assertTrue(theme.is_dark())
        self.assertEqual(theme.palette_name(), "nord")
        # 两套一次写入的常量随配色刷新（窗口背景 / 通知条取的就是它们）
        self.assertEqual(theme.BG_LIGHT, theme._merged("light", "nord")["BG"])
        self.assertEqual(theme.BG_DARK, nord_dark["BG"])
        self.assertEqual(theme.INFOBAR_BG_LIGHT,
                         theme._merged("light", "nord")["INFOBAR_BG"])

    def test_apply_back_to_neutral_restores(self):
        theme.apply("dark", "everforest")
        theme.apply("light", "github")
        base = theme._merged("light", "github")
        self.assertEqual(theme.BG, base["BG"])
        self.assertEqual(theme.BG_LIGHT, base["BG"])
        self.assertFalse(theme.is_dark())
        self.assertEqual(theme.palette_name(), "github")

    def test_semantic_colors_follow_palette(self):
        # 语义功能色随主题自带色板：各主题的 OK 值互不相同（官方色板原值）
        oks = {name: theme._merged("dark", name)["OK"]
               for name in theme.PALETTES}
        self.assertEqual(len(set(oks.values())), len(oks))
        self.assertEqual(oks["github"], "#3fb950")
        self.assertEqual(oks["catppuccin"], "#a6d189")
        self.assertEqual(oks["nord"], "#a3be8c")

    def test_unknown_palette_falls_back_to_default(self):
        theme.apply("light", "不存在的配色")
        self.assertEqual(theme.BG, theme._merged("light", "github")["BG"])
        self.assertEqual(theme.palette_name(), "github")

    def test_accent_of_prefetches_target_palette(self):
        # 切换前预取目标配色的强调色（setThemeColor 先于 apply 的时序依赖）
        theme.apply("light", "github")
        self.assertEqual(theme.accent_of("light", "mist"),
                         theme._merged("light", "mist")["ACCENT"])
        self.assertEqual(theme.accent_of("light"),
                         theme._merged("light", "github")["ACCENT"])

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
