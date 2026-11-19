"""默认主题的常用操作在各交互状态下保持文字可读。"""
import unittest

from tests.test_support import appconfig  # 隔离配置模块导入时的目录探测
import theme


def luminance(color):
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
              for v in channels]
    return sum(a * b for a, b in zip(linear, (0.2126, 0.7152, 0.0722)))


class ButtonContrastTests(unittest.TestCase):
    def test_primary_button_text_on_light_and_dark_surfaces(self):
        for mode in ("light", "dark"):
            palette = theme._merged(mode, theme.DEFAULT_PALETTE)
            for state in ("PRIMARY_BG", "PRIMARY_HOVER", "PRIMARY_PRESSED"):
                with self.subTest(mode=mode, state=state):
                    high, low = sorted((luminance(palette[state]),
                                        luminance(palette["PRIMARY_FG"])), reverse=True)
                    self.assertGreaterEqual((high + .05) / (low + .05), 4.5)
