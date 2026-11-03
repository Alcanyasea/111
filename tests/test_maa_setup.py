# -*- coding: utf-8 -*-
"""控制台「MAA 服务器配置」测试（临时目录构造假 MAA，不碰真实安装）。

重点：任务自检（farm_guard）每次运行都会把 Current 归位 Default，所以
一键配置必须同时修正 Default 方案。否则 B 服 MAA 的 Default 会一直停在
Official，MAA 启动时 am start 官服包，把正在跑的 B 服顶掉。
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gui"))

from core.maa_setup import apply_server_config  # noqa: E402


def _scheme(client_type, enable_all=True):
    return {
        "TaskQueue": [
            {"$type": "StartUpTask", "IsEnable": enable_all},
            {"$type": "RecruitTask", "IsEnable": True},
            {"$type": "InfrastTask", "IsEnable": True},
            {"$type": "FightTask", "IsEnable": True},
            {"$type": "MallTask", "IsEnable": True},
            {"$type": "AwardTask", "IsEnable": True},
        ],
        "Gui": {
            "ConnectSettings": {"AdbPath": "", "Address": ""},
            "RuntimeSettings": {"ClientType": client_type, "PostRunScript": ""},
            "StartUpSettings": {"RunDirectly": False},
        },
        "Toolbox": {},
        "Copilot": {},
        "TaskSelectedIndex": 0,
    }


class MaaSetupTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "config").mkdir()
        self.scripts = self.root / "scripts"
        self.scripts.mkdir()
        self.cfg = {
            "paths": {
                "maa_bilibili_dir": str(self.root),
                "maa_official_dir": str(self.root),
                "adb": r"D:\adb.exe",
                "device": "127.0.0.1:16384",
                "script_dir": str(self.scripts),
            }
        }

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, current, configurations):
        data = {"Configurations": configurations, "Current": current}
        (self.root / "config" / "gui.new.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        (self.root / "config" / "gui.json").write_text(
            json.dumps({"Current": current}, ensure_ascii=False), encoding="utf-8")

    def _read(self):
        return json.loads(
            (self.root / "config" / "gui.new.json").read_text(encoding="utf-8"))

    def test_default_scheme_also_fixed_for_bilibili(self):
        """当前方案已是 Bilibili，Default 仍是 Official —— 两个都要修。"""
        self._write("B服", {"Default": _scheme("Official"),
                            "B服": _scheme("Bilibili")})
        ok, msg = apply_server_config(self.cfg, "bilibili")
        self.assertTrue(ok, msg)
        cfgs = self._read()["Configurations"]
        self.assertEqual(cfgs["Default"]["Gui"]["RuntimeSettings"]["ClientType"],
                         "Bilibili")
        self.assertEqual(cfgs["B服"]["Gui"]["RuntimeSettings"]["ClientType"],
                         "Bilibili")

    def test_default_scheme_gets_connection_settings(self):
        """Default 方案的 ADB / 结束脚本 / RunDirectly 也要补齐。"""
        self._write("Default", {"Default": _scheme("Official")})
        ok, msg = apply_server_config(self.cfg, "official")
        self.assertTrue(ok, msg)
        gui = self._read()["Configurations"]["Default"]["Gui"]
        self.assertEqual(gui["RuntimeSettings"]["ClientType"], "Official")
        self.assertEqual(gui["ConnectSettings"]["AdbPath"], r"D:\adb.exe")
        self.assertEqual(gui["ConnectSettings"]["Address"], "127.0.0.1:16384")
        self.assertTrue(gui["StartUpSettings"]["RunDirectly"])
        self.assertTrue(gui["RuntimeSettings"]["PostRunScript"].endswith(
            "signal_done.bat"))

    def test_unrelated_scheme_untouched(self):
        """同一份配置里别的服务器方案不应被改写（官服目录里的「B服」方案）。"""
        self._write("Default", {"Default": _scheme("Official"),
                                "B服": _scheme("Bilibili")})
        ok, msg = apply_server_config(self.cfg, "official")
        self.assertTrue(ok, msg)
        cfgs = self._read()["Configurations"]
        self.assertEqual(cfgs["B服"]["Gui"]["RuntimeSettings"]["ClientType"],
                         "Bilibili")

    def test_already_correct_reports_no_change(self):
        self._write("Default", {"Default": _scheme("Official")})
        ok, msg = apply_server_config(self.cfg, "official")
        self.assertTrue(ok, msg)
        # 第二次执行应当完全没有改动
        ok2, msg2 = apply_server_config(self.cfg, "official")
        self.assertTrue(ok2, msg2)
        self.assertIn("无需修改", msg2)


if __name__ == "__main__":
    unittest.main()
