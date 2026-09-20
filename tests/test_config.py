# -*- coding: utf-8 -*-
"""gui/config.py load/save 测试（CONFIG_PATH 重定向到临时目录，不碰真实配置）。"""
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


if __name__ == "__main__":
    unittest.main()
