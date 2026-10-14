# -*- coding: utf-8 -*-
"""maa_update.py 测试：更新中断残留恢复 + 打开 MAA 程序。

恢复场景模拟旧版控制台「驱动式更新」断电残留：恢复标记已写、
gui.new.json 已被临时改（RunDirectly=false 等），recover_all 应按快照复原。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gui"))

from core import maa_update as mu  # noqa: E402

# 旧版控制台 _apply_update_config 的原值快照格式（RECOVER_MARKER 内容）：
# 存的是用户原始值（未开自动更新、无代理、RunDirectly 开启）
_RESIDUE_SAVED = {
    "Proxy": "", "ProxyType": "Http",
    "CheckOnStartup": False, "AutoDownloadUpdatePackage": False,
    "AutoInstallUpdatePackage": False, "RunDirectly": True,
}


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

    def _write_residue(self):
        """模拟旧版控制台更新中途断电：标记已写、gui.new.json 已被临时改。"""
        marker = self.maa_dir / "config" / mu.RECOVER_MARKER
        marker.write_text(json.dumps(_RESIDUE_SAVED), encoding="utf-8")
        data = json.loads(self.gui_new.read_text(encoding="utf-8"))
        upd = data.setdefault("Update", {})
        upd.update({"Proxy": _RESIDUE_SAVED["Proxy"], "ProxyType": "Http",
                    "CheckOnStartup": True, "AutoDownloadUpdatePackage": True,
                    "AutoInstallUpdatePackage": True})
        data["Configurations"]["Default"]["Gui"]["StartUpSettings"][
            "RunDirectly"] = False
        self.gui_new.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                encoding="utf-8")

    def test_full_lifecycle(self):
        self._write_residue()
        marker = self.maa_dir / "config" / mu.RECOVER_MARKER
        self.assertTrue(marker.exists())
        # 模拟下次控制台启动：recover_all → 配置复原、标记删除
        logs = []
        self.assertEqual(mu.recover_all(self.cfg, log=logs.append), 1)
        self.assertFalse(marker.exists())
        restored = json.loads(self.gui_new.read_text(encoding="utf-8"))
        self.assertIs(restored["Configurations"]["Default"]["Gui"]
                      ["StartUpSettings"]["RunDirectly"], True)
        self.assertIs(restored["Update"]["CheckOnStartup"], False)
        self.assertEqual(restored["Update"]["Proxy"], "")
        self.assertEqual(logs, ["检测到 官服 MAA 上次更新异常中断，已自动恢复其临时配置修改"])

    def test_no_marker_is_noop(self):
        self.assertEqual(mu.recover_all(self.cfg), 0)

    def test_corrupt_marker_self_deletes(self):
        marker = self.maa_dir / "config" / mu.RECOVER_MARKER
        marker.write_text('{"RunDir', encoding="utf-8")
        self.assertFalse(mu.recover_pending_update(self.maa_dir))
        self.assertFalse(marker.exists())


@unittest.skipUnless(sys.platform == "win32", "Windows 专属")
class OpenMaaTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_missing_dir_reports(self):
        cfg = {"paths": {"maa_official_dir": str(self.root / "no-such"),
                         "maa_bilibili_dir": ""}}
        ok, msg = mu.open_maa(cfg, "official")
        self.assertFalse(ok)
        self.assertIn("未配置或不存在", msg)

    def test_missing_exe_reports(self):
        cfg = {"paths": {"maa_official_dir": str(self.root),
                         "maa_bilibili_dir": ""}}
        ok, msg = mu.open_maa(cfg, "official")
        self.assertFalse(ok)
        self.assertIn("找不到", msg)

    def test_unknown_key_reports(self):
        cfg = {"paths": {"maa_official_dir": str(self.root),
                         "maa_bilibili_dir": ""}}
        ok, msg = mu.open_maa(cfg, "no-such-key")
        self.assertFalse(ok)
        self.assertIn("未知的 MAA 标识", msg)


if __name__ == "__main__":
    unittest.main()
