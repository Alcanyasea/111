# -*- coding: utf-8 -*-
"""export_operbox 回调留档测试（临时目录，不启动 MAA/模拟器）。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import export_operbox  # noqa: E402


class WriteCallbacksTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.target = self.root / "slot1.callbacks.json"

    def test_writes_compact_json(self):
        export_operbox._write_callbacks(self.target, [{"name": "测试"}])
        text = self.target.read_text(encoding="utf-8")
        self.assertNotIn("\n", text)
        self.assertEqual(json.loads(text), [{"name": "测试"}])

    def test_keeps_only_latest_three(self):
        old = [self.root / ("old%d.callbacks.json" % i) for i in range(4)]
        for i, p in enumerate(old):
            p.write_text("[]", encoding="utf-8")
            os.utime(p, (1000 + i, 1000 + i))
        export_operbox._write_callbacks(self.target, [{"v": 1}])
        left = sorted(p.name for p in self.root.glob("*.callbacks.json"))
        self.assertEqual(len(left), 3)
        self.assertIn(self.target.name, left)
        self.assertNotIn(old[0].name, left)   # 最旧的先删


if __name__ == "__main__":
    unittest.main()
