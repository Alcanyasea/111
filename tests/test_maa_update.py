# -*- coding: utf-8 -*-
"""maa_update.py 崩溃恢复标记生命周期测试（模拟断电跳过 finally 的场景）。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gui"))

from core import maa_update as mu  # noqa: E402


@unittest.skipUnless(sys.platform == "win32", "Windows 专属")
class UpdateRecoverTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.maa_dir = self.root / "MAA"
        (self.maa_dir / "config").mkdir(parents=True)
        self.gui_new = self.maa_dir / "config" / "gui.new.json"
        self.gui_new.write_text(json.dumps({
            "Current": "Default",
            "Update": {"Proxy": "", "ProxyType": "Http", "CheckOnStartup": False,
                       "AutoDownloadUpdatePackage": False,
                       "AutoInstallUpdatePackage": False},
            "Configurations": {"Default": {
                "Gui": {"StartUpSettings": {"RunDirectly": True}}}},
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        self.cfg = {"paths": {"maa_official_dir": str(self.maa_dir),
                               "maa_bilibili_dir": ""}}

    def test_full_lifecycle(self):
        saved = mu._apply_update_config(self.maa_dir, "http://127.0.0.1:7897")
        marker = self.maa_dir / "config" / mu.RECOVER_MARKER
        # apply 后：标记存在、RunDirectly 被临时改为 False
        self.assertTrue(marker.exists())
        cur = json.loads(self.gui_new.read_text(encoding="utf-8"))
        self.assertIs(cur["Configurations"]["Default"]["Gui"]["StartUpSettings"]
                      ["RunDirectly"], False)
        # 模拟断电：不调 finally，直接 recover_all → 配置复原、标记删除
        logs = []
        self.assertEqual(mu.recover_all(self.cfg, log=logs.append), 1)
        self.assertFalse(marker.exists())
        restored = json.loads(self.gui_new.read_text(encoding="utf-8"))
        self.assertIs(restored["Configurations"]["Default"]["Gui"]
                      ["StartUpSettings"]["RunDirectly"], True)
        self.assertIs(restored["Update"]["CheckOnStartup"], False)
        self.assertEqual(saved["RunDirectly"], True)

    def test_no_marker_is_noop(self):
        self.assertEqual(mu.recover_all(self.cfg), 0)

    def test_corrupt_marker_self_deletes(self):
        marker = self.maa_dir / "config" / mu.RECOVER_MARKER
        marker.write_text('{"RunDir', encoding="utf-8")
        self.assertFalse(mu.recover_pending_update(self.maa_dir))
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
