# -*- coding: utf-8 -*-
"""plugins/common.py 公共工具测试。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins"))

import common  # noqa: E402


class CommonTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_load_config_missing_or_corrupt(self):
        self.assertEqual(common.load_config(self.root / "nope.json"), {})
        bad = self.root / "bad.json"
        bad.write_text("{oops", encoding="utf-8")
        self.assertEqual(common.load_config(bad), {})

    def test_find_account(self):
        cfg = {"accounts": [{"id": "a1"}, {"id": "b2"}]}
        self.assertEqual(common.find_account(cfg, "b2")["id"], "b2")
        self.assertIsNone(common.find_account(cfg, "zz"))
        self.assertIsNone(common.find_account({}, "a1"))

    def test_maa_dir_for(self):
        cfg = {"paths": {"maa_official_dir": str(self.root),
                          "maa_bilibili_dir": ""}}
        self.assertEqual(common.maa_dir_for(cfg, "official"), self.root)
        self.assertIsNone(common.maa_dir_for(cfg, "bilibili"))
        self.assertIsNone(common.maa_dir_for({}, "official"))

    def test_atomic_json_write_roundtrip(self):
        target = self.root / "deep" / "dir" / "x.json"
        data = {"k": "中文", "n": 1}
        common.atomic_json_write(target, data)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), data)
        residue = [p.name for p in target.parent.iterdir() if ".tmp" in p.name]
        self.assertEqual(residue, [])

    def test_atomic_json_write_replace_failure_keeps_original(self):
        """os.replace 失败时原文件不变，临时文件被清理。"""
        target = self.root / "cfg.json"
        target.write_text('{"old": 1}', encoding="utf-8")
        before = target.read_text(encoding="utf-8")
        with mock.patch.object(common.os, "replace",
                               side_effect=OSError("simulated failure")):
            with self.assertRaises(OSError):
                common.atomic_json_write(target, {"new": 2})
        self.assertEqual(target.read_text(encoding="utf-8"), before)
        residue = [p.name for p in self.root.iterdir() if ".tmp" in p.name]
        self.assertEqual(residue, [])

    def test_atomic_json_write_unique_tmp_names(self):
        """连续两次写入使用不同临时名，后写覆盖先写，不残留。"""
        target = self.root / "cfg.json"
        seen = []
        real_replace = common.os.replace

        def record(src, dst):
            seen.append(Path(src).name)
            real_replace(src, dst)

        with mock.patch.object(common.os, "replace", side_effect=record):
            common.atomic_json_write(target, {"v": 1})
            common.atomic_json_write(target, {"v": 2})
        self.assertEqual(len(set(seen)), 2)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"v": 2})
        residue = [p.name for p in self.root.iterdir() if ".tmp" in p.name]
        self.assertEqual(residue, [])

    def test_log_file_appends_with_tag(self):
        log_path = self.root / "master_log.txt"
        cfg = {"paths": {"log_file": str(log_path)}}
        common.log_file(cfg, "测试", "hello")
        common.log_file({}, "测试", "无配置不炸")
        text = log_path.read_text(encoding="utf-8")
        self.assertIn("[测试] hello", text)


if __name__ == "__main__":
    unittest.main()
