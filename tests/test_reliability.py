"""可靠性回归：仅使用临时配置、锁、信号和排班数据。"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from tests.test_support import appconfig

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "base_schedule"))
import base_schedule as bs
from core import cleanup
from core.util import CREATE_NO_WINDOW, pwsh_exe


class ConfigReliabilityTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "config.json"
        self.original = '{"accounts": [{"id": "test-account"}]}'
        self.path.write_text(self.original, encoding="utf-8")
        p = patch.object(appconfig, "CONFIG_PATH", self.path)
        p.start()
        self.addCleanup(p.stop)

    def test_read_error_cannot_return_empty_configuration(self):
        with patch("builtins.open", side_effect=PermissionError("busy")):
            with self.assertRaises(OSError):
                appconfig.load()
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.original)

    def test_failed_corruption_backup_cannot_return_writable_defaults(self):
        self.path.write_text("{broken", encoding="utf-8")
        with patch.object(Path, "replace", side_effect=PermissionError("busy")):
            with self.assertRaises(OSError):
                appconfig.load()
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{broken")

    def test_empty_schedule_stays_empty_after_save_reload(self):
        self.path.write_text('{"schedule": {"times": []}}', encoding="utf-8")
        cfg = appconfig.load()
        self.assertEqual(cfg["schedule"]["times"], [])
        appconfig.save(cfg)
        self.assertEqual(appconfig.load()["schedule"]["times"], [])

    def test_missing_schedule_retains_default_times(self):
        self.assertEqual([x["time"] for x in appconfig.load()["schedule"]["times"]],
                         ["04:00", "16:00"])

    def test_valid_load_after_failed_read_preserves_accounts(self):
        with patch("builtins.open", side_effect=PermissionError("busy")):
            try:
                appconfig.load()
            except OSError:
                pass
        cfg = appconfig.load()
        appconfig.save(cfg)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))
                         ["accounts"][0]["id"], "test-account")


class MinuteBatchesTest(unittest.TestCase):
    def test_normal_mode_fiammetta_uses_legacy_minute_batch(self):
        sys.path.insert(0, str(ROOT / "plugins" / "fiammetta"))
        import fiammetta
        acc = {"base_schedule": {"batches": {
            "4点": {"fiammetta": {"enable": True, "target": "但书"}},
            "8点": {"fiammetta": {"enable": True, "target": "清流"}}}}}
        with patch.object(fiammetta, "active_batch", return_value=("8点30分", ["8点30分"])):
            self.assertEqual(fiammetta.maa_settings({}, acc), (True, "清流", "8点30分"))

    def test_gui_and_plugin_distinguish_minutes(self):
        entries = [{"time": "08:00"}, {"time": "08:30"}, {"time": "00:15"}]
        self.assertEqual(appconfig.schedule_batches({"schedule": {"times": entries}}),
                         ["24点15分", "8点", "8点30分"])
        for t, want in [("08:00", "8点"), ("08:30", "8点30分"),
                        ("00:00", "24点"), ("00:15", "24点15分")]:
            self.assertEqual(bs.batch_name(t), want)

    def test_old_hour_schedule_is_copied_without_losing_original(self):
        original = {"batches": {"8点": {"control": ["legacy"],
                     "fiammetta": {"enable": True, "target": "清流"}}}}
        snapshot = copy.deepcopy(original)
        result = bs.normalize(original, ["8点", "8点30分"])
        self.assertEqual(result["batches"]["8点30分"]["control"][0], "legacy")
        result["batches"]["8点30分"]["control"][0] = "changed"
        self.assertEqual(result["batches"]["8点"]["control"][0], "legacy")
        self.assertEqual(original, snapshot)

    def test_explicit_minute_plan_wins_and_both_plans_are_independent(self):
        entries = [{"time": "08:00"}, {"time": "08:30"}]
        source = {"batches": {"8点": {"control": ["first"]},
                              "8点30分": {"control": ["second"]}}}
        plans = bs.build_plan_document(source, entries)["plans"]
        self.assertEqual([p["rooms"]["control"][0]["operators"] for p in plans],
                         [["first"], ["second"]])
        self.assertEqual(bs.current_batch(datetime(2026, 1, 1, 8, 30), entries),
                         "8点30分")


class CleanupReliabilityTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for obj, attr in [(cleanup, "LOCK_FILE"), (cleanup.runner, "LOCK_FILE")]:
            p = patch.object(obj, attr, self.root / "master.lock")
            p.start()
            self.addCleanup(p.stop)
        self.signal = self.root / "maa_done.signal"
        self.signal.write_text("done", encoding="ascii")
        self.items = [{"path": str(self.signal), "kind": "tmp", "size": 4}]

    def test_active_run_blocks_all_cleanup_not_only_lock_deletion(self):
        with patch.object(cleanup.runner, "is_running", return_value=True):
            with self.assertRaises(OSError):
                cleanup.perform(self.items)
        self.assertEqual(self.signal.read_text(encoding="ascii"), "done")

    def test_idle_cleanup_removes_only_requested_temporary_file(self):
        with patch.object(cleanup.runner, "is_running", return_value=False):
            self.assertEqual(cleanup.perform(self.items), (4, 1, 0))
        self.assertFalse(self.signal.exists())

    def test_lock_acquired_after_idle_check_still_blocks_cleanup(self):
        # 模拟 master 刚取得句柄但还未写 PID，GUI 判活暂时仍为 False。
        with cleanup.runner.maintenance_lock(self.root / "master.lock"):
            with patch.object(cleanup.runner, "is_running", return_value=False):
                with self.assertRaises(OSError):
                    cleanup.perform(self.items)
        self.assertEqual(self.signal.read_text(encoding="ascii"), "done")

    def test_powershell_master_lock_is_excluded_until_cleanup_finishes(self):
        lock = self.root / "master.lock"
        # 仅运行 .NET 打开临时锁文件，不加载 master.ps1 或任何用户配置。
        script = ("try { $f = [IO.File]::Open($env:MAA_TEST_LOCK, 'OpenOrCreate', "
                  "'ReadWrite', 'Read'); $f.Dispose(); exit 0 } catch { exit 17 }")
        def try_master():
            return subprocess.run([pwsh_exe(), "-NoProfile", "-Command", script],
                                  env=dict(os.environ, MAA_TEST_LOCK=str(lock)),
                                  capture_output=True, timeout=15,
                                  creationflags=CREATE_NO_WINDOW).returncode
        original = cleanup._perform_unlocked
        def clean_while_master_attempts_start(items):
            self.assertEqual(try_master(), 17)
            return original(items)
        with patch.object(cleanup.runner, "is_running", return_value=False), \
                patch.object(cleanup, "_perform_unlocked", clean_while_master_attempts_start):
            self.assertEqual(cleanup.perform(self.items), (4, 1, 0))
        self.assertEqual(try_master(), 0)

    def test_stale_lock_is_not_deleted_under_maintenance_handle(self):
        lock = self.root / "master.lock"
        lock.write_text("999999|123", encoding="ascii")
        items = self.items + [{"path": str(lock), "kind": "tmp", "size": 10}]
        with patch.object(cleanup.runner, "is_running", return_value=False):
            self.assertEqual(cleanup.perform(items), (4, 1, 0))
        self.assertEqual(lock.read_text(encoding="ascii"), "999999|123")


class ReadChildTextResilienceTest(unittest.TestCase):
    """master_plugins.ps1 的 Read-ChildText：切换子脚本退出后，重定向输出文件
    可能仍被孙进程（adb server 等继承句柄的守护进程）持有，读取不得抛异常、
    不得丢已落盘内容（2026-10-27 实测：异常导致切换日志丢失 + tmp 残留）。
    仅用临时文件；用语法树单独摘出函数定义执行，不加载 master 脚本链。"""

    LOAD_FN = (
        "$log = [System.Collections.Generic.List[string]]::new()\n"
        "function Log { param($m) $log.Add($m) }\n"
        "$ast = [System.Management.Automation.Language.Parser]::ParseFile("
        "$env:MAA_TEST_PLUGIN_PS1, [ref]$null, [ref]$null)\n"
        "$fn = $ast.FindAll({ param($a) $a -is "
        "[System.Management.Automation.Language.FunctionDefinitionAst] "
        "-and $a.Name -eq 'Read-ChildText' }, $true)[0]\n"
        ". ([scriptblock]::Create($fn.Extent.Text))\n"
    )

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / "switch_output.tmp"

    def _run(self, body):
        result = subprocess.run(
            [pwsh_exe(), "-NoProfile", "-Command", self.LOAD_FN + body],
            env=dict(os.environ, MAA_TEST_PLUGIN_PS1=str(
                ROOT / "scripts" / "master_plugins.ps1"),
                MAA_TEST_TMP=str(self.path)),
            capture_output=True, timeout=60,
            creationflags=CREATE_NO_WINDOW)
        if result.returncode != 0:
            self.fail(f"pwsh exit {result.returncode}: "
                      f"{result.stderr.decode('utf-8', 'replace')}")

    def test_read_succeeds_while_writer_still_holds_handle(self):
        # 复刻 Start-Process 重定向的句柄状态：写访问 + 共享读。旧实现
        # （ReadAllBytes）在此场景直接抛 sharing violation。
        self._run(
            '[IO.File]::WriteAllText($env:MAA_TEST_TMP, "line1`r`nline2")\n'
            "$w = [IO.File]::Open($env:MAA_TEST_TMP, 'Open', 'Write', 'Read')\n"
            "try {\n"
            "    $lines = @(Read-ChildText $env:MAA_TEST_TMP)\n"
            "    if ($lines.Count -ne 2 -or $lines[0] -ne 'line1' "
            "-or $lines[1] -ne 'line2') { exit 21 }\n"
            "} finally { $w.Dispose() }\n"
            "exit 0\n")

    def test_exclusive_lock_returns_empty_with_warn_instead_of_throwing(self):
        self._run(
            '[IO.File]::WriteAllText($env:MAA_TEST_TMP, "data")\n'
            "$h = [IO.File]::Open($env:MAA_TEST_TMP, 'Open', 'Read', 'None')\n"
            "try {\n"
            "    $lines = @(Read-ChildText $env:MAA_TEST_TMP)\n"
            "    if ($lines.Count -ne 0) { exit 22 }\n"
            "    if (-not ($log | Where-Object { $_ -like '*WARN*' })) "
            "{ exit 23 }\n"
            "} finally { $h.Dispose() }\n"
            "exit 0\n")

    def test_utf8_bom_gbk_and_empty_decoding_unchanged(self):
        self._run(
            "[IO.File]::WriteAllBytes($env:MAA_TEST_TMP, "
            "[Text.Encoding]::UTF8.GetPreamble() + "
            '[Text.Encoding]::UTF8.GetBytes("中文`r`n第二行"))\n'
            "$l = @(Read-ChildText $env:MAA_TEST_TMP)\n"
            "if ($l.Count -ne 2 -or $l[0] -ne '中文' -or $l[1] -ne '第二行') "
            "{ exit 31 }\n"
            "[IO.File]::WriteAllBytes($env:MAA_TEST_TMP, "
            '[Text.Encoding]::GetEncoding(936).GetBytes("中文GBK"))\n'
            "$l = @(Read-ChildText $env:MAA_TEST_TMP)\n"
            "if ($l.Count -ne 1 -or $l[0] -ne '中文GBK') { exit 32 }\n"
            "[IO.File]::WriteAllBytes($env:MAA_TEST_TMP, [byte[]]@())\n"
            "$l = @(Read-ChildText $env:MAA_TEST_TMP)\n"
            "if ($l.Count -ne 0) { exit 33 }\n"
            "exit 0\n")


if __name__ == "__main__":
    unittest.main()
