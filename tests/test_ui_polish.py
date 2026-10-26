# -*- coding: utf-8 -*-
"""UI 打磨层的行为固化测试（offscreen 渲染，appconfig.save 打补丁）。

覆盖：theme 字号标尺与派生色工具（mix / card_shadow_color）、
卡片投影随背景色相派生与换肤重建（refresh_card_shadows）、
空态组件 empty_state、数据数字等宽字体（big_number / apply_mono_font /
班次时间输入框）、账号卡片悬停反馈、键值行内嵌大数字富文本。
"""
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gui"))

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit  # noqa: E402

from tests.test_support import appconfig  # noqa: E402
import theme  # noqa: E402
from pages.dashboard import (AccountCard as DashAccountCard,  # noqa: E402
                             CollectScheduleCard, ScheduleCard)
from pages.accounts import AccountCard as GridAccountCard  # noqa: E402
from widgets import (Card, apply_mono_font, big_number, empty_state,  # noqa: E402
                     refresh_card_shadows)


class ThemeTokenTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(theme.apply, "light", "github")

    def test_type_scale_ordered_and_integral(self):
        sizes = (theme.FS_MICRO, theme.FS_CAPTION, theme.FS_BODY,
                 theme.FS_SUBTITLE, theme.FS_TITLE, theme.FS_STAT,
                 theme.FS_DISPLAY)
        for s in sizes:
            self.assertIsInstance(s, int)
        self.assertEqual(sizes, tuple(sorted(sizes)))

    def test_mix_endpoints_and_blend(self):
        self.assertEqual(theme.mix("#123456", "#123456", 0.37), "#123456")
        self.assertEqual(theme.mix("#000000", "#ffffff", 0.0), "#000000")
        self.assertEqual(theme.mix("#000000", "#ffffff", 1.0), "#ffffff")
        self.assertEqual(theme.mix("#000000", "#ffffff", 0.5), "#808080")

    def test_card_shadow_derives_from_background_hue_in_light(self):
        for name in theme.PALETTES:
            theme.apply("light", name)
            r, g, b, a = theme.card_shadow_color()
            br, bg_, bb = (int(theme.BG[i:i + 2], 16) for i in (1, 3, 5))
            self.assertEqual((r, g, b), (int(br * 0.30), int(bg_ * 0.30),
                                         int(bb * 0.30)), name)
            self.assertTrue(0 < a <= 255)

    def test_card_shadow_is_black_in_dark(self):
        theme.apply("dark", "everforest")
        self.assertEqual(theme.card_shadow_color(), (0, 0, 0, 120))


class CardShadowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.addCleanup(theme.apply, "light", "github")

    def test_apply_shadow_uses_theme_color(self):
        theme.apply("dark", "github")
        card = Card("标题")
        self.addCleanup(card.deleteLater)
        card.apply_shadow()
        self.assertEqual(card.graphicsEffect().color().getRgb()[:3], (0, 0, 0))

    def test_refresh_card_shadows_rebuilds_after_theme_switch(self):
        theme.apply("light", "github")
        card = Card()
        self.addCleanup(card.deleteLater)
        card.apply_shadow()
        light_rgb = card.graphicsEffect().color().getRgb()
        theme.apply("dark", "nord")
        refresh_card_shadows()
        dark_rgb = card.graphicsEffect().color().getRgb()
        self.assertNotEqual(light_rgb, dark_rgb)
        self.assertEqual(dark_rgb, QColor(*theme.card_shadow_color()).getRgb())


class EmptyStateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.apply("light", "github")

    def test_title_and_hint_labels(self):
        w = empty_state("暂无账号", "点击上方「添加账号」开始")
        self.addCleanup(w.deleteLater)
        texts = [lab.text() for lab in w.findChildren(QLabel)]
        self.assertIn("暂无账号", texts)
        self.assertIn("点击上方「添加账号」开始", texts)

    def test_icon_version_taller_than_plain(self):
        from qfluentwidgets import FluentIcon
        plain = empty_state("标题")
        with_icon = empty_state("标题", "提示", icon=FluentIcon.HISTORY)
        self.addCleanup(plain.deleteLater)
        self.addCleanup(with_icon.deleteLater)
        self.assertGreater(with_icon.sizeHint().height(),
                           plain.sizeHint().height())


class MonoFontTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.apply("light", "github")

    def test_big_number_uses_mono_family(self):
        w = big_number("22", "分钟")
        self.addCleanup(w.deleteLater)
        num = w.findChild(QLabel)
        self.assertEqual(num.font().families(), theme.FONT_MONO_FAMILIES)

    def test_apply_mono_font_sets_families(self):
        edit = QLineEdit()
        apply_mono_font(edit)
        self.assertEqual(edit.font().families(), theme.FONT_MONO_FAMILIES)

    def test_schedule_time_edit_is_mono(self):
        """班次行的时间输入框等宽：一列时间按位对齐。"""
        cfg = {"schedule": {"times": [{"time": "04:00", "enabled": True,
                                       "shutdown": False}]},
               "accounts": []}
        card = ScheduleCard(cfg)
        self.addCleanup(card.deleteLater)
        edits = card.findChildren(QLineEdit)
        self.assertTrue(edits)
        for e in edits:
            self.assertEqual(e.font().families(), theme.FONT_MONO_FAMILIES)


class AccountCardHoverTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.apply("light", "github")

    def test_clickable_account_card_has_hover_tint(self):
        cfg = {"accounts": [{"id": "a1", "label": "官服 1", "server": "official",
                             "slot": "s1"}],
               "paths": {"script_dir": "missing-dir-for-test"}}
        card = GridAccountCard(cfg, cfg["accounts"][0], 0)
        self.addCleanup(card.deleteLater)
        normal = card._normalBackgroundColor().getRgb()
        hover = card._hoverBackgroundColor().getRgb()
        self.assertNotEqual(normal, hover)
        self.assertEqual(hover,
                         QColor(theme.mix(theme.CARD, theme.TEXT, 0.05)).getRgb())


class KvBigNumberTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.apply("light", "github")

    def test_inline_big_number_rich_text_uses_mono(self):
        acc = {"key": "a1", "name": "官服 1", "slot": "s1",
               "log_names": {"官服 1"}, "meta": "官服 · 槽位切号", "char": "1"}
        card = DashAccountCard(acc)
        self.addCleanup(card.deleteLater)
        card._set_kv1_big("42")
        html = card.kv1_val.text()
        self.assertIn(theme.FONT_MONO, html)
        self.assertIn(str(theme.FS_STAT_SM), html)
        self.assertIn("42", html)


class IconBadgeTest(unittest.TestCase):
    """账号序号圆标：淡底圆标的安静质感（替代旧 38px 渐变色块）。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.apply("light", "github")

    def test_badge_is_tinted_circle_not_gradient(self):
        from widgets import IconBadge, _rgba
        b = IconBadge("1")
        self.addCleanup(b.deleteLater)
        self.assertEqual(b.minimumSize().height(), 28)
        qss = b._theme_qss()
        self.assertNotIn("qlineargradient", qss)       # 不再是渐变色块
        self.assertIn(_rgba(theme.ACCENT, 0.13), qss)  # 强调色淡底
        self.assertIn(theme.ACCENT, qss)               # 同色数字

    def test_badge_follows_current_palette_accent(self):
        from widgets import IconBadge
        theme.apply("dark", "everforest")
        b = IconBadge("2")
        self.addCleanup(b.deleteLater)
        self.assertIn(theme.ACCENT, b._theme_qss())


class _InfoBarSpy:
    """记录 dashboard 弹的提示条（level, title, content），不真正创建浮窗。"""

    calls = []

    @classmethod
    def _record(cls, level, title, content="", **_kwargs):
        cls.calls.append((level, title, content))

    @classmethod
    def success(cls, title, content="", **kwargs):
        cls._record("success", title, content, **kwargs)

    @classmethod
    def warning(cls, title, content="", **kwargs):
        cls._record("warning", title, content, **kwargs)

    @classmethod
    def info(cls, title, content="", **kwargs):
        cls._record("info", title, content, **kwargs)

    @classmethod
    def error(cls, title, content="", **kwargs):
        cls._record("error", title, content, **kwargs)


class _FakeApplyWorker:
    """替代 SchedulerApplyWorker：不跑真实 pwsh 计划任务同步，只记录是否被发起。"""

    started = []   # 每次 start() 追加 collect 标志

    def __init__(self, cfg, parent=None, collect=False):
        self.collect = collect
        self.done = types.SimpleNamespace(connect=lambda *_a: None)

    def isRunning(self):
        return False

    def start(self):
        _FakeApplyWorker.started.append(self.collect)


class ScheduleToggleFeedbackTest(unittest.TestCase):
    """班次/收菜计划的「启用」「关机」开关：切换后必须立即弹提示条。

    开关本体只有深灰/浅灰一档色差（set_switch_checked_gray），用户无法
    从开关本身确认是否生效；关机开关此前更是完全无提示。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.apply("light", "github")

    def setUp(self):
        import pages.dashboard as dash
        self.dash = dash
        _InfoBarSpy.calls = []
        _FakeApplyWorker.started = []
        # 绝不碰真实 config.json / 真实计划任务
        self._save_patch = mock.patch.object(dash.appconfig, "save")
        self._save_patch.start()
        self.addCleanup(self._save_patch.stop)
        self._worker_patch = mock.patch.object(
            dash.poller, "SchedulerApplyWorker", _FakeApplyWorker)
        self._worker_patch.start()
        self.addCleanup(self._worker_patch.stop)
        self._infobar_patch = mock.patch.object(dash, "InfoBar", _InfoBarSpy)
        self._infobar_patch.start()
        self.addCleanup(self._infobar_patch.stop)

    def _shift_card(self):
        cfg = {"schedule": {"times": [
            {"time": "04:00", "enabled": True, "shutdown": True},
            {"time": "16:00", "enabled": True, "shutdown": False},
        ]}, "accounts": []}
        card = ScheduleCard(cfg)
        self.addCleanup(card.deleteLater)
        return card

    def _collect_card(self):
        cfg = {"schedule": {"collect_times": [
            {"time": "10:00", "enabled": True, "shutdown": False},
        ]}}
        card = CollectScheduleCard(cfg)
        self.addCleanup(card.deleteLater)
        return card

    def test_shift_enabled_toggle_shows_toast_and_syncs(self):
        card = self._shift_card()
        entry = card._entries()[1]
        card._on_enabled(entry, False)
        self.assertEqual(entry["enabled"], False)
        self.assertIn(("success", "已停用 16点 班次", "配置已保存，正在同步计划任务"),
                      _InfoBarSpy.calls)
        self.assertEqual(_FakeApplyWorker.started, [False])  # 发起挂机任务同步
        self.dash.appconfig.save.assert_called()

    def test_shift_shutdown_toggle_shows_toast_without_sync(self):
        card = self._shift_card()
        entry = card._entries()[1]
        card._on_shutdown(entry, True)
        self.assertIn(("success", "已开启 16点 班次关机",
                       "只影响该班次结束后的收尾，已保存，无需同步计划任务"),
                      _InfoBarSpy.calls)
        self.assertEqual(entry["shutdown"], True)
        self.assertEqual(_FakeApplyWorker.started, [])  # 关机不入触发，无需同步

    def test_apply_done_success_toast_lists_trigger_times(self):
        card = self._shift_card()
        card._on_apply_done(True, "", {"exists": True, "enabled": True,
                                       "times": ["04:00", "16:00"]})
        self.assertIn(("success", "已更新计划任务", "班次触发：04:00、16:00"),
                      _InfoBarSpy.calls)

    def test_apply_done_toast_when_all_disabled(self):
        card = self._shift_card()
        card._on_apply_done(True, "", {"exists": True, "enabled": False,
                                       "times": []})
        self.assertIn(("success", "已更新计划任务",
                       "全部班次已停用，计划任务已禁用"), _InfoBarSpy.calls)

    def test_collect_toggles_show_toasts(self):
        card = self._collect_card()
        entry = card._entries()[0]
        card._on_enabled(entry, False)
        card._on_shutdown(entry, True)
        titles = [c[1] for c in _InfoBarSpy.calls]
        self.assertIn("已停用 10:00 收菜", titles)
        self.assertIn("已开启 10:00 收菜关机", titles)
        self.assertEqual(_FakeApplyWorker.started, [True])  # 仅启用开关发起收菜任务同步

    def test_collect_apply_done_success_toast_lists_trigger_times(self):
        card = self._collect_card()
        card._on_apply_done(True, "", {"exists": True, "enabled": True,
                                       "times": ["10:00"]})
        self.assertIn(("success", "已更新收菜计划任务", "收菜触发：10:00"),
                      _InfoBarSpy.calls)


if __name__ == "__main__":
    unittest.main()
