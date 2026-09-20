# -*- coding: utf-8 -*-
"""插件集成测试：合成 config + 假 MAA 目录，真实跑五个插件的 CLI 子命令。

覆盖 import 链（plugins/common.py）、参数路径、MAA 配置写入与收菜/还原循环。
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugins"


def _run(plugin, *argv):
    # PYTHONIOENCODING：CI/部分机器控制台是 cp1252 等编不了中文的代码页，
    # 插件 print 中文会 UnicodeEncodeError 直接崩——子进程统一强制 UTF-8 输出
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, str(plugin), *argv],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


@unittest.skipUnless(sys.platform == "win32", "Windows 专属")
class PluginsIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp())
        cls.maa = cls.root / "MAA"
        (cls.maa / "config").mkdir(parents=True)
        gui_new = {
            "Current": "Default",
            "Update": {},
            "Configurations": {"Default": {"TaskQueue": [
                {"$type": "StartUpTask", "IsEnable": True},
                {"$type": "RecruitTask", "IsEnable": True},
                {"$type": "InfrastTask", "Mode": "Rotation"},
                {"$type": "InfrastTask", "Mode": "Rotation"},  # 旧版双任务残留
                {"$type": "FightTask", "IsEnable": True},
                {"$type": "FightTask", "IsEnable": True, "StagePlan": []},
                {"$type": "MallTask", "IsEnable": True},
                {"$type": "AwardTask", "IsEnable": True},
            ], "Gui": {"StartUpSettings": {"RunDirectly": True}}}},
        }
        gui_json = {"Current": "Default", "Configurations": {
            "Default": {"Infrast.InfrastMode": "Rotation"}}}
        (cls.maa / "config" / "gui.new.json").write_text(
            json.dumps(gui_new, ensure_ascii=False), encoding="utf-8")
        (cls.maa / "config" / "gui.json").write_text(
            json.dumps(gui_json, ensure_ascii=False), encoding="utf-8")

        def fia():
            return {"enable": True, "target": "清流"}

        cfg = {
            "paths": {"maa_official_dir": str(cls.maa), "maa_bilibili_dir": "",
                      "log_file": str(cls.root / "master_log.txt")},
            "schedule": {"times": [
                {"time": "04:00", "enabled": True, "shutdown": True, "accounts": []},
                {"time": "16:00", "enabled": True, "shutdown": False, "accounts": []}]},
            "accounts": [{"id": "a1", "label": "测试号", "server": "official",
                          "enabled": True,
                          "slot": "official 1",   # 带空格：验证计划文件防碰撞命名
                          "second_fight_plan": ["1-7", "CE-6"],
                          "second_fight_use_optional": True,
                          "base_schedule": {
                              "enabled": True, "layout": "333",
                              "drones": {"room": "manufacture", "index": 1,
                                         "enable": False, "order": "pre"},
                              "batches": {"4点": {"fiammetta": fia()},
                                           "16点": {"fiammetta": fia()}}}}],
        }
        cls.cfg_path = cls.root / "config.json"
        cls.cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                                encoding="utf-8")

    def _gui_new(self):
        return json.loads((self.maa / "config" / "gui.new.json")
                          .read_text(encoding="utf-8"))

    def _tasks(self):
        return self._gui_new()["Configurations"]["Default"]["TaskQueue"]

    def test_01_farm_guard(self):
        code, out = _run(PLUGIN_DIR / "farm_guard" / "farm_guard.py", "apply",
                         "--config", str(self.cfg_path), "--account", "a1",
                         "--server", "official")
        self.assertEqual(code, 0, out)
        self.assertIn("OK", out)
        self.assertEqual(self._gui_new().get("Current"), "Default")

    def test_02_fight_stage(self):
        code, out = _run(PLUGIN_DIR / "fight_stage" / "fight_stage.py", "apply",
                         "--config", str(self.cfg_path), "--account", "a1",
                         "--server", "official")
        self.assertEqual(code, 0, out)
        fights = [t for t in self._tasks() if t.get("$type") == "FightTask"]
        self.assertEqual(fights[1].get("StagePlan"), ["1-7", "CE-6"])
        self.assertIs(fights[1].get("UseOptionalStage"), True)

    def test_03_fiammetta(self):
        code, out = _run(PLUGIN_DIR / "fiammetta" / "fiammetta.py", "apply",
                         "--config", str(self.cfg_path), "--account", "a1",
                         "--server", "official")
        self.assertEqual(code, 0, out)
        infrasts = [t for t in self._tasks() if t.get("$type") == "InfrastTask"]
        # fiammetta 只写参数、不负责去重双任务（那是 base_schedule.apply 的职责）
        self.assertTrue(infrasts[0].get("FiammettaRecoveryEnabled"))
        self.assertEqual(infrasts[0].get("FiammettaTarget1"), "清流")

    def test_04_base_schedule_apply(self):
        code, out = _run(PLUGIN_DIR / "base_schedule" / "base_schedule.py",
                         "apply", "--config", str(self.cfg_path),
                         "--account", "a1", "--server", "official")
        self.assertEqual(code, 0, out)
        gn = self._gui_new()
        infrasts = [t for t in gn["Configurations"]["Default"]["TaskQueue"]
                    if t.get("$type") == "InfrastTask"]
        self.assertEqual(len(infrasts), 1)   # 双任务残留已清理
        plan_file = Path(infrasts[0]["Filename"])
        self.assertTrue(plan_file.exists(), plan_file)
        # 槽位 "official 1" 含空格：清洗后与原名不同，必须带 8 位哈希后缀防碰撞
        self.assertRegex(plan_file.name, r"^official_1_[0-9a-f]{8}\.json$")
        doc = json.loads(plan_file.read_text(encoding="utf-8"))
        self.assertEqual(len(doc.get("plans") or []), 2)
        self.assertTrue(doc["plans"][0].get("Fiammetta", {}).get("enable"))
        self.assertIs(gn["Configurations"]["Default"]["Gui"]["StartUpSettings"]
                      ["RunDirectly"], True)
        self.assertEqual(
            gn["Configurations"]["Default"]["Infrast.InfrastMode"]
            if "Infrast.InfrastMode" in gn["Configurations"]["Default"]
            else self._gui_json_mode(), "Custom")

    def _gui_json_mode(self):
        gj = json.loads((self.maa / "config" / "gui.json").read_text(encoding="utf-8"))
        return gj["Configurations"]["Default"].get("Infrast.InfrastMode")

    def test_05_infrast_collect_and_restore(self):
        code, out = _run(PLUGIN_DIR / "infrast_collect" / "infrast_collect.py",
                         "apply", "--config", str(self.cfg_path),
                         "--account", "a1", "--server", "official")
        self.assertEqual(code, 0, out)
        self.assertEqual(self._gui_new().get("Current"), "收菜")
        code, out = _run(PLUGIN_DIR / "infrast_collect" / "infrast_collect.py",
                         "restore", "--config", str(self.cfg_path))
        self.assertEqual(code, 0, out)
        self.assertEqual(self._gui_new().get("Current"), "Default")

    def test_06_log_tags_written(self):
        log = (self.root / "master_log.txt").read_text(encoding="utf-8")
        for tag in ("任务自检", "理智关卡", "菲亚梅塔", "基建插件", "基建收菜"):
            self.assertIn("[%s]" % tag, log)


if __name__ == "__main__":
    unittest.main()
