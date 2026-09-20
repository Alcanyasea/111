# -*- coding: utf-8 -*-
"""base_schedule.py 纯函数与 apply_maa_config 测试（全部用临时目录隔离）。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "base_schedule"))

import base_schedule as bs  # noqa: E402


class BatchAndPeriodTest(unittest.TestCase):
    def test_batch_name(self):
        self.assertEqual(bs.batch_name("04:00"), "4点")
        self.assertEqual(bs.batch_name("16:00"), "16点")
        self.assertEqual(bs.batch_name("00:00"), "24点")
        self.assertEqual(bs.batch_name("08:00"), "8点")

    def test_periods_cross_midnight(self):
        periods = bs.periods_for_times([{"time": "08:00"}, {"time": "24:00"}]) \
            if False else bs.periods_for_times([{"time": "08:00"}, {"time": "00:00"}])
        # 8点批 08:00-23:59；24点（00:00）批 00:00-07:59（末批跨零点收尾）
        self.assertEqual(periods[0], [["08:00", "23:59"]])
        self.assertEqual(periods[1], [["00:00", "23:59"], ["00:00", "07:59"]])

    def test_current_batch_boundaries(self):
        from datetime import datetime
        # 契约：entries 必须升序（schedule_spec/schedule_entries 的产出即升序；
        # 「00:00」字符串排序在最前，语义是凌晨属末班 24点批）
        entries = [{"time": "00:00"}, {"time": "08:00"}, {"time": "16:00"}]
        self.assertEqual(bs.current_batch(datetime(2026, 1, 1, 7, 59), entries), "24点")
        self.assertEqual(bs.current_batch(datetime(2026, 1, 1, 8, 0), entries), "8点")
        self.assertEqual(bs.current_batch(datetime(2026, 1, 1, 15, 59), entries), "8点")
        self.assertEqual(bs.current_batch(datetime(2026, 1, 1, 16, 0), entries), "16点")
        self.assertEqual(bs.current_batch(datetime(2026, 1, 1, 23, 59), entries), "16点")
        self.assertEqual(bs.current_batch(datetime(2026, 1, 1, 0, 0), entries), "24点")

    def test_schedule_entries_dedup_sort(self):
        cfg = {"schedule": {"times": [
            {"time": "16:00", "enabled": True},
            {"time": "04:00", "enabled": True},
            {"time": "04:00", "enabled": True},   # 去重
            {"time": "25:00", "enabled": True},   # 非法时间剔除
            {"time": "12:00", "enabled": False},  # 停用剔除
        ]}}
        self.assertEqual([e["time"] for e in bs.schedule_entries(cfg)],
                         ["04:00", "16:00"])


class PlanPathTest(unittest.TestCase):
    def setUp(self):
        self.old = bs.PLANS_DIR
        bs.PLANS_DIR = Path(tempfile.mkdtemp())

    def tearDown(self):
        bs.PLANS_DIR = self.old

    def test_clean_slot_unchanged(self):
        self.assertEqual(bs.plan_path_for_slot("official_1").name, "official_1.json")

    def test_collision_slots_distinguished(self):
        names = {bs.plan_path_for_slot(s).name for s in ("acc 1", "acc_1", "acc.1")}
        self.assertEqual(len(names), 3, "清洗同名的不同槽位必须互相区分")


class ApplyMaaConfigTest(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.mkdtemp())
        self.maa = root / "MAA"
        (self.maa / "config").mkdir(parents=True)
        self.plans = root / "plans"
        self.plans.mkdir()
        self.plan = self.plans / "official_1.json"
        self.plan.write_text("{}", encoding="utf-8")
        self.logs = []

    def _write_maa(self, gui_new, gui_json=None):
        (self.maa / "config" / "gui.new.json").write_text(
            json.dumps(gui_new, ensure_ascii=False), encoding="utf-8")
        if gui_json is not None:
            (self.maa / "config" / "gui.json").write_text(
                json.dumps(gui_json, ensure_ascii=False), encoding="utf-8")

    def _sample(self):
        return {
            "Current": "Default",
            "Configurations": {"Default": {"TaskQueue": [
                {"$type": "AppTask"},
                {"$type": "InfrastTask", "Mode": "Rotation",
                 "RoomList": [{"Room": "Mfg", "IsEnabled": True},
                               {"Room": "Dorm", "IsEnabled": False}]},
                {"$type": "InfrastTask", "Mode": "Rotation"},  # 旧版双任务残留
            ]}},
        }

    def test_apply_custom(self):
        self._write_maa(self._sample(),
                        {"Current": "Default", "Configurations": {
                            "Default": {"Infrast.InfrastMode": "Rotation"}}})
        changed = bs.apply_maa_config(self.maa, self.plan, plan_index=1,
                                      log=self.logs.append)
        self.assertEqual(changed, ["gui.new.json", "gui.json"])
        data = json.loads((self.maa / "config" / "gui.new.json").read_text(encoding="utf-8"))
        tasks = [t for t in data["Configurations"]["Default"]["TaskQueue"]
                 if t.get("$type") == "InfrastTask"]
        self.assertEqual(len(tasks), 1)
        t0 = tasks[0]
        self.assertEqual(t0["Mode"], "Custom")
        self.assertEqual(t0["PlanSelect"], 1)
        self.assertEqual(t0["Filename"], str(self.plan))
        self.assertTrue(t0["DormFilterNotStationed"])
        self.assertEqual([r["Room"] for r in t0["RoomList"]], list(bs.STANDARD_ROOMS))
        self.assertTrue(all(r["IsEnabled"] for r in t0["RoomList"]))

    def test_corrupt_json_returns_none(self):
        self._write_maa(self._sample())
        (self.maa / "config" / "gui.new.json").write_text("{corrupt!!", encoding="utf-8")
        self.assertIsNone(bs.apply_maa_config(self.maa, self.plan,
                                              log=self.logs.append))
        self.assertTrue(any("ERROR" in m for m in self.logs))

    def test_idempotent(self):
        self._write_maa(self._sample())
        bs.apply_maa_config(self.maa, self.plan, plan_index=1, log=lambda m: None)
        snap1 = (self.maa / "config" / "gui.new.json").read_text(encoding="utf-8")
        bs.apply_maa_config(self.maa, self.plan, plan_index=1, log=lambda m: None)
        self.assertEqual(
            (self.maa / "config" / "gui.new.json").read_text(encoding="utf-8"), snap1)


if __name__ == "__main__":
    unittest.main()
