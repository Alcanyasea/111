# -*- coding: utf-8 -*-
"""scheduler.py 计划任务动作渲染测试（打桩 _ps，不真注册计划任务）。"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gui"))

from core import scheduler  # noqa: E402


class SchedulerRenderTest(unittest.TestCase):
    def test_master_ps1_derived_from_project_root(self):
        self.assertEqual(Path(scheduler.MASTER_PS1),
                         ROOT / "scripts" / "master.ps1")

    def test_apply_action_renders_quoted_paths(self):
        captured = {}

        def fake_ps(script, timeout=0):
            captured["script"] = script
            return 0, "APPLIED", b""

        original = scheduler._ps
        scheduler._ps = fake_ps
        try:
            ok, msg = scheduler.apply({"schedule": {"times": [
                {"time": "04:00", "enabled": True, "shutdown": True},
                {"time": "16:00", "enabled": True, "shutdown": False},
            ]}})
        finally:
            scheduler._ps = original
        self.assertTrue(ok, msg)
        s = captured["script"]
        self.assertIn(scheduler.TASK_NAME, s)
        self.assertIn('-File "%s"' % scheduler.MASTER_PS1, s,
                      "master.ps1 路径必须加引号且来自推导常量")
        m = re.search(r"-Execute '([^']+)'", s)
        self.assertIsNotNone(m)
        self.assertTrue(m.group(1).endswith("pwsh.exe"))
        self.assertIn("04:00", s)
        self.assertIn("16:00", s)


class SchedulerCollectRenderTest(unittest.TestCase):
    """apply_collect：收菜计划任务（MAA_基建收菜）的动作渲染。"""

    def _capture(self):
        captured = {}

        def fake_ps(script, timeout=0):
            captured["script"] = script
            return 0, "APPLIED", b""

        return captured, fake_ps

    def test_apply_collect_renders_infrast_args(self):
        captured, fake_ps = self._capture()
        original = scheduler._ps
        scheduler._ps = fake_ps
        try:
            ok, msg = scheduler.apply_collect({"schedule": {"collect_times": [
                {"time": "08:00", "enabled": True},
                {"time": "20:30", "enabled": False},
            ]}})
        finally:
            scheduler._ps = original
        self.assertTrue(ok, msg)
        s = captured["script"]
        self.assertIn(scheduler.COLLECT_TASK_NAME, s)
        self.assertNotIn(scheduler.TASK_NAME, s,
                         "收菜任务脚本不应触碰挂机任务")
        # 任务动作只带 -InfrastCollect：关机由 master.ps1 按收菜计划每项的
        # shutdown 判定，动作里不能带 -NoShutdown（否则关机开关永远不生效）
        self.assertIn("-InfrastCollect", s)
        self.assertNotIn("-NoShutdown", s)
        self.assertIn("08:00", s)
        self.assertNotIn("20:30", s, "禁用的时间点不写入触发")
        self.assertIn("if ($true)", s)

    def test_apply_collect_empty_disables_instead_of_register(self):
        captured, fake_ps = self._capture()
        original = scheduler._ps
        scheduler._ps = fake_ps
        try:
            ok, msg = scheduler.apply_collect({"schedule": {"collect_times": []}})
        finally:
            scheduler._ps = original
        self.assertTrue(ok, msg)
        # 无启用时间：不注册无触发任务，已存在则只禁用（elseif ($t) 分支）
        self.assertIn("if ($false)", captured["script"])
        self.assertIn("@()", captured["script"])


if __name__ == "__main__":
    unittest.main()
