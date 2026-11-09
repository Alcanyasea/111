# -*- coding: utf-8 -*-
"""gui/config.py load/save 测试（CONFIG_PATH 重定向到临时目录，不碰真实配置）。"""
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gui"))

from tests.test_support import appconfig  # noqa: E402


class ConfigSaveLoadTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self._old = appconfig.CONFIG_PATH
        appconfig.CONFIG_PATH = self.root / "config.json"

    def tearDown(self):
        appconfig.CONFIG_PATH = self._old

    def test_defaults_and_roundtrip(self):
        cfg = appconfig.load()
        # 新机器默认没有任何账号（用几个加几个），旧版三账号迁移另行覆盖
        self.assertEqual(cfg["accounts"], [])
        appconfig.save(cfg)
        self.assertEqual(appconfig.load(), cfg)

    def test_repeated_save_no_tmp_residue(self):
        cfg = appconfig.load()
        appconfig.save(cfg)
        appconfig.save(cfg)
        self.assertEqual(appconfig.load(), cfg)
        residue = [p.name for p in self.root.iterdir() if ".tmp" in p.name]
        self.assertEqual(residue, [])

    def _seed_accounts(self):
        cfg = appconfig.load()
        cfg["accounts"] = [
            {"id": "a", "label": "A", "password": "pa", "slot": "slot-a"},
            {"id": "b", "label": "B", "password": "pb", "slot": "slot-b"},
        ]
        cfg["schedule"] = {"times": [{"time": "04:00"}], "collect_times": []}
        appconfig.save(cfg)
        return appconfig.load()

    def test_save_rebases_gui_change_on_newer_telegram_config(self):
        cfg = self._seed_accounts()
        cfg["appearance"]["theme"] = "dark"
        remote = deepcopy(cfg)
        remote["accounts"][0]["base_schedule"] = {"enabled": True}
        from gui.core.remote_revision import save_cfg
        save_cfg(appconfig.CONFIG_PATH, remote)
        appconfig.save(cfg)
        merged = appconfig.load()
        self.assertEqual(merged["appearance"]["theme"], "dark")
        self.assertTrue(merged["accounts"][0]["base_schedule"]["enabled"])
        self.assertEqual(merged["accounts"][0]["password"], "pa")

    def test_local_account_delete_does_not_write_null_or_resurrect(self):
        cfg = self._seed_accounts()
        remote = deepcopy(cfg)
        remote["accounts"][1]["label"] = "B remote"
        from gui.core.remote_revision import save_cfg
        save_cfg(appconfig.CONFIG_PATH, remote)
        cfg["accounts"] = [cfg["accounts"][1]]  # local deletes A
        appconfig.save(cfg)
        data = json.loads(appconfig.CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual([a["id"] for a in data["accounts"]], ["b"])
        self.assertEqual(data["accounts"][0]["label"], "B remote")
        self.assertTrue(all(isinstance(a, dict) for a in data["accounts"]))

    def test_remote_account_delete_beats_local_edit(self):
        cfg = self._seed_accounts()
        remote = deepcopy(cfg)
        remote["accounts"] = [remote["accounts"][1]]  # remote deletes A
        from gui.core.remote_revision import save_cfg
        save_cfg(appconfig.CONFIG_PATH, remote)
        cfg["accounts"][0]["label"] = "A local edit"
        cfg["accounts"][1]["label"] = "B local edit"
        appconfig.save(cfg)
        data = json.loads(appconfig.CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual([a["id"] for a in data["accounts"]], ["b"])
        self.assertEqual(data["accounts"][0]["label"], "B local edit")

    def test_remote_delete_wins_even_when_same_account_was_edited(self):
        cfg = self._seed_accounts()
        remote = deepcopy(cfg)
        remote["accounts"] = [remote["accounts"][1]]
        from gui.core.remote_revision import save_cfg
        save_cfg(appconfig.CONFIG_PATH, remote)
        cfg["accounts"][0]["label"] = "A changed locally"
        appconfig.save(cfg)
        data = json.loads(appconfig.CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual([a["id"] for a in data["accounts"]], ["b"])

    def test_merge_handles_both_sides_deleting_same_field(self):
        merged = appconfig._merge_concurrent({"a": 1, "b": 2}, {"b": 3}, {"b": 2})
        self.assertEqual(merged, {"b": 3})

    def test_merge_keeps_live_account_object_identity(self):
        cfg = self._seed_accounts()
        account = cfg["accounts"][0]
        remote = deepcopy(cfg)
        remote["accounts"][0]["label"] = "A remote"
        from gui.core.remote_revision import save_cfg
        save_cfg(appconfig.CONFIG_PATH, remote)
        cfg["appearance"]["theme"] = "dark"
        appconfig.save(cfg)
        self.assertIs(cfg["accounts"][0], account)
        self.assertEqual(account["label"], "A remote")

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
        # 导入时探测已隔离；这里只验证两键恒一致且指向 MAA.exe。
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


class AppearancePaletteTest(unittest.TestCase):
    """appearance.palette（配色方案）的默认值与规范化。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self._old = appconfig.CONFIG_PATH
        appconfig.CONFIG_PATH = self.root / "config.json"

    def tearDown(self):
        appconfig.CONFIG_PATH = self._old

    def test_default_neutral(self):
        self.assertEqual(appconfig.load()["appearance"]["palette"], "github")

    def test_default_palette_is_valid(self):
        # DEFAULTS 里的默认值必须本身合法（load() 的回退不能只靠规范化兜底）
        import theme
        self.assertIn(appconfig.DEFAULTS["appearance"]["palette"],
                      theme.PALETTES)

    def test_invalid_palette_falls_back(self):
        appconfig.CONFIG_PATH.write_text(json.dumps({
            "appearance": {"theme": "dark", "palette": "不存在的配色"},
        }), encoding="utf-8")
        cfg = appconfig.load()
        self.assertEqual(cfg["appearance"]["palette"], "github")
        self.assertEqual(cfg["appearance"]["theme"], "dark")   # 主题不受影响

    def test_valid_palette_kept_and_roundtrip(self):
        appconfig.CONFIG_PATH.write_text(json.dumps({
            "appearance": {"theme": "dark", "palette": "nord"},
        }), encoding="utf-8")
        cfg = appconfig.load()
        self.assertEqual(cfg["appearance"]["palette"], "nord")
        appconfig.save(cfg)
        self.assertEqual(appconfig.load()["appearance"], cfg["appearance"])


class BaseSchedulePresetsTest(unittest.TestCase):
    """账号级 base_schedule_presets（基建排班预设，账号之间不互通）。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self._old = appconfig.CONFIG_PATH
        appconfig.CONFIG_PATH = self.root / "config.json"

    def tearDown(self):
        appconfig.CONFIG_PATH = self._old

    def test_default_empty_per_account(self):
        for acc in appconfig.load()["accounts"]:
            self.assertEqual(acc["base_schedule_presets"], [])

    def test_invalid_entries_dropped_and_dedup(self):
        appconfig.CONFIG_PATH.write_text(json.dumps({
            "accounts": [
                {"id": "a", "base_schedule_presets": [
                    {"name": "日常", "schedule": {"layout": "333"}},
                    {"name": "日常", "schedule": {"layout": "243"}},  # 重名 → 保留首个
                    {"name": "", "schedule": {}},      # 空名 → 剔除
                    {"name": "   "},                   # 纯空白名/无 schedule → 剔除
                    "junk",                            # 非对象 → 剔除
                    {"schedule": {"layout": "333"}},   # 缺名 → 剔除
                ]},
                {"id": "b", "base_schedule_presets": [
                    {"name": "日常", "schedule": {"layout": "243"}},
                ]},
            ],
        }), encoding="utf-8")
        cfg = appconfig.load()
        self.assertEqual(cfg["accounts"][0]["base_schedule_presets"],
                         [{"name": "日常", "schedule": {"layout": "333"}}])
        # 账号之间不互通：b 的同名预设内容独立，不受 a 规范化影响
        self.assertEqual(cfg["accounts"][1]["base_schedule_presets"],
                         [{"name": "日常", "schedule": {"layout": "243"}}])
        appconfig.save(cfg)
        self.assertEqual(
            appconfig.load()["accounts"][0]["base_schedule_presets"],
            cfg["accounts"][0]["base_schedule_presets"])

    def test_non_dict_schedule_reset_to_empty(self):
        # schedule 坏成标量：名字保留（用户可重新覆盖保存），schedule 置空
        appconfig.CONFIG_PATH.write_text(json.dumps({
            "accounts": [{"id": "a", "base_schedule_presets":
                          [{"name": "坏预设", "schedule": 123}]}],
        }), encoding="utf-8")
        cfg = appconfig.load()
        self.assertEqual(cfg["accounts"][0]["base_schedule_presets"],
                         [{"name": "坏预设", "schedule": {}}])

    def test_legacy_global_presets_copied_to_every_account(self):
        """旧版顶层全局共享预设 → 每账号一份独立副本，顶层键删除。"""
        legacy = [{"name": "搓玉", "schedule": {"layout": "243"}}]
        appconfig.CONFIG_PATH.write_text(json.dumps({
            "base_schedule_presets": legacy,
            "accounts": [{"id": "a"}, {"id": "b"}],
        }), encoding="utf-8")
        cfg = appconfig.load()
        self.assertNotIn("base_schedule_presets", cfg)
        self.assertEqual(cfg["accounts"][0]["base_schedule_presets"], legacy)
        self.assertEqual(cfg["accounts"][1]["base_schedule_presets"], legacy)
        # 独立副本：改 a 的预设不影响 b
        cfg["accounts"][0]["base_schedule_presets"] = []
        appconfig.save(cfg)
        cfg = appconfig.load()
        self.assertEqual(cfg["accounts"][0]["base_schedule_presets"], [])
        self.assertEqual(cfg["accounts"][1]["base_schedule_presets"], legacy)

    def test_legacy_global_presets_not_over_account_own(self):
        # 账号已有自己的预设时不覆盖（迁移只补缺失）
        own = [{"name": "自己存的", "schedule": {}}]
        appconfig.CONFIG_PATH.write_text(json.dumps({
            "base_schedule_presets": [{"name": "旧全局", "schedule": {}}],
            "accounts": [{"id": "a", "base_schedule_presets": own}],
        }), encoding="utf-8")
        cfg = appconfig.load()
        self.assertEqual(cfg["accounts"][0]["base_schedule_presets"], own)


if __name__ == "__main__":
    unittest.main()
