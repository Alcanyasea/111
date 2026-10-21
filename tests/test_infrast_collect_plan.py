# -*- coding: utf-8 -*-
"""infrast_collect.write_collect_plan 的 changed 模式测试（临时目录）。

collect_plan.json 是 git 跟踪文件：内容不变时不得重写（保 mtime、不盖
手工调整）；内容漂移/文件损坏时按生成器重建（自愈）。
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "infrast_collect"))

import infrast_collect as ic  # noqa: E402


class WriteCollectPlanTest(unittest.TestCase):
    def setUp(self):
        self.old_dir = ic.PLUGIN_DIR
        ic.PLUGIN_DIR = Path(tempfile.mkdtemp())

    def tearDown(self):
        ic.PLUGIN_DIR = self.old_dir

    def test_first_call_writes_canonical_plan(self):
        path = ic.write_collect_plan()
        self.assertTrue(path.exists())
        self.assertEqual(ic._read_json(path)["title"], "基建收菜（不换班）")

    def test_unchanged_content_not_rewritten(self):
        ic.write_collect_plan()
        with mock.patch.object(ic, "_atomic_json_write") as w:
            ic.write_collect_plan()
            w.assert_not_called()

    def test_drifted_content_self_heals(self):
        path = ic.write_collect_plan()
        data = ic._read_json(path)
        data["plans"][0]["rooms"]["dormitory"] = []   # 模拟手工改坏
        ic._atomic_json_write(path, data)
        ic.write_collect_plan()
        healed = ic._read_json(path)
        self.assertEqual(len(healed["plans"][0]["rooms"]["dormitory"]), 4)

    def test_corrupt_file_rebuilt(self):
        path = ic.PLUGIN_DIR / "collect_plan.json"
        path.write_text("{corrupt", encoding="utf-8")
        ic.write_collect_plan()
        self.assertEqual(ic._read_json(path)["title"], "基建收菜（不换班）")


if __name__ == "__main__":
    unittest.main()
