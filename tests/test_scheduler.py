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


if __name__ == "__main__":
    unittest.main()
