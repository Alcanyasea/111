"""搬迁回归：所有配置、脚本和日志均使用临时假数据。"""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_support import appconfig
from core import runner
from core.util import CREATE_NO_WINDOW, pwsh_exe

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins"))
import common


class ProjectRelocationTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.old = self.base / "old project"
        self.new = self.base / "合并目录 new project"
        (self.new / "scripts").mkdir(parents=True)
        self.config = self.new / "config.json"
        self.raw = {"paths": {
            "script_dir": str(self.old / "scripts"),
            "log_file": str(self.old / "scripts" / "master_log.txt"),
            "adb": str(self.base / "external" / "adb.exe"),
            "maa_official": str(self.base / "external" / "MAA.exe"),
            "python": str(self.base / "custom python.exe"),
        }, "accounts": []}

    def read_all(self, raw):
        self.config.write_text(json.dumps(raw), encoding="utf-8")
        original = self.config.read_bytes()
        with patch.object(appconfig, "CONFIG_PATH", self.config):
            gui = appconfig.load()
        with patch.object(common, "backup_daily"):
            plugin = common.load_config(self.config)
        command = (
            "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); "
            ". $env:TEST_CONFIG_LIB; "
            "Read-AppConfigJson $env:TEST_CONFIG_PATH | ConvertTo-Json -Depth 20 -Compress"
        )
        result = subprocess.run(
            [pwsh_exe(), "-NoProfile", "-Command", command],
            env=dict(os.environ, TEST_CONFIG_LIB=str(ROOT / "scripts" / "config_lib.ps1"),
                     TEST_CONFIG_PATH=str(self.config)),
            capture_output=True, timeout=20, creationflags=CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        powershell = json.loads(result.stdout.decode("utf-8-sig"))
        self.assertEqual(self.config.read_bytes(), original)
        return gui, plugin, powershell

    def test_readers_relocate_internal_paths_even_if_old_copy_exists(self):
        for old_exists in (False, True):
            if old_exists:
                (self.old / "scripts").mkdir(parents=True)
                (self.old / "scripts" / "master.ps1").write_text("# old copy")
            results = self.read_all(self.raw)
            for reader, cfg in zip(("gui", "plugin", "powershell"), results):
                with self.subTest(reader=reader, old_exists=old_exists):
                    self.assertEqual(Path(cfg["paths"]["script_dir"]), self.new / "scripts")
                    self.assertEqual(Path(cfg["paths"]["log_file"]),
                                     self.new / "scripts" / "master_log.txt")
                    for key in ("adb", "maa_official", "python"):
                        self.assertEqual(cfg["paths"][key], self.raw["paths"][key])

    def test_custom_log_destination_is_preserved(self):
        raw = copy.deepcopy(self.raw)
        raw["paths"]["log_file"] = str(self.base / "custom logs" / "run.txt")
        for cfg in self.read_all(raw):
            self.assertEqual(cfg["paths"]["log_file"], raw["paths"]["log_file"])

    def test_missing_internal_paths_use_config_location(self):
        for cfg in self.read_all({"paths": {}, "accounts": []}):
            self.assertEqual(Path(cfg["paths"]["script_dir"]), self.new / "scripts")
            self.assertEqual(Path(cfg["paths"]["log_file"]),
                             self.new / "scripts" / "master_log.txt")

    def test_gui_launches_relocated_master_without_saving_first(self):
        cfg = self.read_all(self.raw)[0]
        with patch.object(runner, "SCRIPT_DIR", self.new / "scripts"), \
                patch.object(runner, "_proc_ref", None), \
                patch.object(runner.subprocess, "Popen") as launch:
            try:
                runner.start(cfg)
                args = launch.call_args.args[0]
                self.assertEqual(Path(args[args.index("-File") + 1]),
                                 self.new / "scripts" / "master.ps1")
            finally:
                if runner._proc_ref and runner._proc_ref[1]:
                    runner._proc_ref[1].close()

    @unittest.skipUnless(sys.platform == "win32", "Windows batch launcher")
    def test_bilibili_launcher_uses_its_own_folder_from_another_cwd(self):
        source = ROOT / "scripts" / "switch_to_B服.bat"
        text = source.read_text(encoding="utf-8-sig")
        # 安全前置：旧入口会触发真实切号，禁止在此断言通过前执行它。
        self.assertNotRegex(text, r"(?i)[a-z]:[\\/].*\.ps1")
        self.assertTrue("%~dp0" in text or "%~dpn0" in text)
        target = self.new / "scripts" / source.name
        shutil.copyfile(source, target)
        target.with_suffix(".ps1").write_text(
            "[IO.File]::WriteAllText($env:TEST_LAUNCH_RESULT, $PSCommandPath)\nexit 23\n",
            encoding="utf-8")
        marker = self.base / "result.txt"
        result = subprocess.run(
            [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(target)],
            cwd=self.base, env=dict(os.environ, TEST_LAUNCH_RESULT=str(marker)),
            capture_output=True, timeout=20, creationflags=CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 23, result.stderr.decode("utf-8", "replace"))
        self.assertEqual(Path(marker.read_text(encoding="utf-8")), target.with_suffix(".ps1"))
