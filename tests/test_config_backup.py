# -*- coding: utf-8 -*-
"""config.json 每日滚动备份测试（backup_daily；全部在临时目录，不碰真实配置）。

需求：每天更新一次、新记录替换旧记录后只保留一份、记录更新前确认没有损坏
（坏配置不覆盖好备份；坏备份视为无效自动重做）。
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins"))

from common import backup_daily, load_config  # noqa: E402


def _good_cfg(label="源"):
    return {"paths": {"script_dir": "D:\\1\\scripts"},
            "accounts": [{"id": "official1", "label": label, "slot": "official_1"}],
            "schedule": {"times": [{"time": "04:00", "enabled": True}]}}


class BackupDailyTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.cfg = self.root / "config.json"
        self.bak = self.root / "config.json.daily.bak"

    def _write(self, path, obj_or_text):
        text = (json.dumps(obj_or_text, ensure_ascii=False)
                if not isinstance(obj_or_text, str) else obj_or_text)
        path.write_text(text, encoding="utf-8")

    def _age_bak(self, days=1):
        """把备份文件 mtime 拨回 N 天前，模拟「昨天的备份」。"""
        old = time.time() - days * 86400
        os.utime(self.bak, (old, old))

    def test_first_run_creates_backup(self):
        self._write(self.cfg, _good_cfg())
        self.assertTrue(backup_daily(self.cfg))
        self.assertEqual(json.loads(self.bak.read_text(encoding="utf-8")),
                         _good_cfg())
        self.assertEqual([p.name for p in self.root.iterdir()],
                         ["config.json", "config.json.daily.bak"])  # 无临时文件残留

    def test_second_run_same_day_keeps_morning_snapshot(self):
        # 每天只备份一次：当天配置后续再改，备份保持当天首次的内容
        self._write(self.cfg, _good_cfg("上午"))
        backup_daily(self.cfg)
        self._write(self.cfg, _good_cfg("下午"))
        self.assertFalse(backup_daily(self.cfg))
        self.assertEqual(json.loads(self.bak.read_text(encoding="utf-8")),
                         _good_cfg("上午"))

    def test_next_day_replaces_old_record(self):
        # 新的一天（备份 mtime 是昨天）：重新备份，新记录原子替换旧记录，
        # 旧记录删除、永远只有一份
        self._write(self.cfg, _good_cfg("昨天"))
        backup_daily(self.cfg)
        self._age_bak(1)
        self._write(self.cfg, _good_cfg("今天"))
        self.assertTrue(backup_daily(self.cfg))
        self.assertEqual(json.loads(self.bak.read_text(encoding="utf-8")),
                         _good_cfg("今天"))
        self.assertEqual([p.name for p in self.root.glob("*.bak*")],
                         ["config.json.daily.bak"])

    def test_broken_config_never_overwrites_backup(self):
        # 配置被覆盖成残片（合法 JSON 但没有 accounts）：绝不能把坏内容
        # 备份上去冲掉最后一份完好记录
        self._write(self.cfg, _good_cfg("完好记录"))
        backup_daily(self.cfg)
        self._age_bak(1)
        self._write(self.cfg, {"schedule": {"times": []}})   # 事故现场残片
        self.assertFalse(backup_daily(self.cfg))
        self.assertEqual(json.loads(self.bak.read_text(encoding="utf-8")),
                         _good_cfg("完好记录"))

    def test_half_written_config_skipped(self):
        # 写一半的 JSON（解析失败）同样不备份
        self.cfg.write_text('{"accounts": ["半截', encoding="utf-8")
        self.assertFalse(backup_daily(self.cfg))
        self.assertFalse(self.bak.exists())

    def test_corrupted_backup_is_redone(self):
        # 既有备份损坏（写一半）：视为无备份，配置完好时自动重做
        self._write(self.cfg, _good_cfg())
        self._write(self.bak, '{"accounts": ["坏掉的备份')
        self.assertTrue(backup_daily(self.cfg))
        self.assertEqual(json.loads(self.bak.read_text(encoding="utf-8")),
                         _good_cfg())

    def test_missing_config_noop(self):
        self.assertFalse(backup_daily(self.cfg))
        self.assertFalse(self.bak.exists())

    def test_load_config_triggers_backup_once(self):
        # 插件读配置的入口（master 各路径共用）顺带触发每日备份
        self._write(self.cfg, _good_cfg())
        load_config(self.cfg)
        self.assertTrue(self.bak.exists())
        load_config(self.cfg)   # 同一天第二次读取不再重写
        self._age_bak(1)
        load_config(self.cfg)   # 到了第二天（模拟）重新备份
        self.assertTrue(self.bak.exists())


if __name__ == "__main__":
    unittest.main()
