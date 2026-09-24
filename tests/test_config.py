# -*- coding: utf-8 -*-
"""gui/config.py load/save 测试（CONFIG_PATH 重定向到临时目录，不碰真实配置）。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gui"))

import config as appconfig  # noqa: E402


class ConfigSaveLoadTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self._old = appconfig.CONFIG_PATH
        appconfig.CONFIG_PATH = self.root / "config.json"

    def tearDown(self):
        appconfig.CONFIG_PATH = self._old

    def test_defaults_and_roundtrip(self):
        cfg = appconfig.load()
        self.assertEqual(cfg["accounts"][0]["id"], "official1")
        appconfig.save(cfg)
        self.assertEqual(appconfig.load(), cfg)

    def test_repeated_save_no_tmp_residue(self):
        cfg = appconfig.load()
        appconfig.save(cfg)
        appconfig.save(cfg)
        self.assertEqual(appconfig.load(), cfg)
        residue = [p.name for p in self.root.iterdir() if ".tmp" in p.name]
        self.assertEqual(residue, [])

    def test_corrupt_file_backed_up(self):
        appconfig.CONFIG_PATH.write_text("{corrupt!!", encoding="utf-8")
        cfg = appconfig.load()
        self.assertTrue(appconfig.LAST_LOAD_WARNING)
        backups = list(self.root.glob("config.json.corrupt-*"))
        self.assertEqual(len(backups), 1)


class DetectMaaDirTest(unittest.TestCase):
    """detect_maa_official_dir：官服 MAA 目录探测（默认值不再钉死版本号）。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def _mk(self, name, with_exe=True):
        d = self.root / name
        d.mkdir()
        if with_exe:
            (d / "MAA.exe").write_bytes(b"MZ")
        return d

    def test_picks_highest_version(self):
        self._mk("MAA-v6.9.1-win-x64")
        want = self._mk("MAA-v6.11.1-win-x64")
        self._mk("MAA-v6.2.0-win-x64")
        self.assertEqual(appconfig.detect_maa_official_dir(self.root), str(want))

    def test_shorter_version_loses_to_patch(self):
        # 6.11 < 6.11.1：位数不同按位比较，短版本号不能反超
        self._mk("MAA-v6.11-win-x64")
        want = self._mk("MAA-v6.11.1-win-x64")
        self.assertEqual(appconfig.detect_maa_official_dir(self.root), str(want))

    def test_ignores_no_exe_and_bad_names(self):
        self._mk("MAA-v6.11.1-win-x64", with_exe=False)  # 无 MAA.exe 不算
        self._mk("MAA")                                  # 目录名不符
        self._mk("MAA-vX.Y-win-x64")                     # 版本段非数字
        self._mk("MAA-v6.11.1")                          # 缺 -win-x64 后缀
        self.assertIsNone(appconfig.detect_maa_official_dir(self.root))

    def test_missing_base_returns_none(self):
        self.assertIsNone(appconfig.detect_maa_official_dir(self.root / "不存在"))

    def test_defaults_use_detection_when_base_has_maa(self):
        # DEFAULTS 构建于 import 时（扫真实 D:\软件\MAA），这里只验证两键
        # 恒一致且指向 MAA.exe，不依赖本机装没装 MAA
        self.assertEqual(appconfig.DEFAULTS["paths"]["maa_official_dir"],
                         str(Path(appconfig.DEFAULTS["paths"]["maa_official"]).parent))
        self.assertTrue(
            str(appconfig.DEFAULTS["paths"]["maa_official"]).endswith("MAA.exe"))


class CollectTimesMigrationTest(unittest.TestCase):
    """schedule.collect_times（基建收菜定时）的默认值与迁移规范化。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self._old = appconfig.CONFIG_PATH
        appconfig.CONFIG_PATH = self.root / "config.json"

    def tearDown(self):
        appconfig.CONFIG_PATH = self._old

    def test_default_empty(self):
        self.assertEqual(appconfig.load()["schedule"]["collect_times"], [])

    def test_invalid_entries_dropped_and_flags_coerced(self):
        appconfig.CONFIG_PATH.write_text(json.dumps({
            "schedule": {"collect_times": [
                {"time": "8:00", "enabled": True},   # 小时非零填充 → 剔除
                {"time": "25:00", "enabled": True},  # 越界 → 剔除
                {"time": "12:30", "enabled": 1},     # enabled 强转 bool
                {"time": "23:00", "enabled": True,
                 "shutdown": True},                  # 关机开关保留
                "junk",                              # 非对象 → 剔除
            ]},
        }), encoding="utf-8")
        cfg = appconfig.load()
        self.assertEqual(cfg["schedule"]["collect_times"], [
            {"time": "12:30", "enabled": True, "shutdown": False},
            {"time": "23:00", "enabled": True, "shutdown": True},
        ])
        appconfig.save(cfg)
        self.assertEqual(appconfig.load()["schedule"], cfg["schedule"])


if __name__ == "__main__":
    unittest.main()
