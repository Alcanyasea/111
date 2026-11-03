"""Run a copied master entry + real pipeline with isolated side-effect adapters."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tests.ps_harness import PWSH, ROOT, run_ps


LIB = r'''
$scriptDir = $PSScriptRoot
$root = $PSScriptRoot
$fixture = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'fixture.json') -Raw | ConvertFrom-Json
$accountList = @($fixture.accounts)
$config = $fixture.config
$configPath = Join-Path $PSScriptRoot 'fixture.json'
$closeEmulator = $false
$morningShutdown = $false
$eveningShutdown = $false
$retryFailedDelaySec = 0
$runMode = 'farm'
$runStartTs = '2026-10-28 00:00:00'
$script:events = [Collections.Generic.List[string]]::new()
$script:logs = [Collections.Generic.List[string]]::new()
$script:attempts = @{}
$maaOfficial = 'fake.exe'; $maaOfficialDir = $PSScriptRoot
$maaBilibili = 'fake.exe'; $maaBilibiliDir = $PSScriptRoot
$infrastCollectPy = 'collect'; $farmGuardPy = 'guard'; $fightStagePy = 'fight'
$baseSchedulePy = 'base'; $fiammettaPy = 'fia'
function Log($message) { $script:logs.Add([string]$message) }
function Release-Lock { $script:events.Add('unlock') }
function Clear-CacheData { }
function Clear-UnnecessaryData { }
function Start-MuMu { return $true }
function shutdown { throw 'Unexpected real shutdown path' }
function Start-Process { throw 'Unexpected unisolated process launch' }
function New-Object {
    param($ComObject)
    if ($ComObject -ne 'WScript.Shell') { throw 'Unexpected object creation' }
    $fake = [pscustomobject]@{}
    $fake | Add-Member ScriptMethod Popup { return 0 }
    return $fake
}
'''
PLUGINS = r'''
function Run-Switch {
    param($ScriptName, [string[]]$ScriptArgs)
    $slot = $ScriptArgs[-1]
    $script:events.Add("$ScriptName|$slot")
    if ($ScriptName -eq 'slot_switch.ps1') {
        if (-not $script:attempts.ContainsKey($slot)) { $script:attempts[$slot] = 0 }
        $script:attempts[$slot]++
    }
    return 0
}
function Refresh-SlotData($Server, $Slot) { $script:events.Add("refresh|$Slot"); return $true }
function Invoke-Plugin {
    param($pyPath, $tag, $name, $argList, $missNote)
    $script:events.Add("plugin|$pyPath|$($argList[0])")
    return $true
}
'''
MAA = r'''
function Run-MAA($exe, $dir, $label) {
    $script:events.Add("maa|$label")
    if ($fixture.fail_always -and $label -eq 'one') {
        $script:LastMaaStatus = 'stall'
        return $false
    }
    if ($fixture.fail_first -and $label -eq 'one' -and $script:attempts['slot 1'] -eq 1) {
        $script:LastMaaStatus = 'stall'
        return $false
    }
    return $true
}
'''
HISTORY = r'''
function Save-RunHistory {
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot 'result.json'),
        (ConvertTo-Json -Depth 8 -InputObject @{results=$results; mode=$runMode; events=@($script:events); logs=@($script:logs); passed=$passed; failed=$failed; batch=$bsBatch; shutdown=$shutdownEnabled; sched_entries=@($scheduleEntries | ForEach-Object { $_.time })}))
}
function Send-RunNotify {
    param($title, $body)
    $script:events.Add("notify|$title|$body")
    Save-RunHistory
}
'''


@unittest.skipUnless(PWSH, "PowerShell 7 required")
class MasterEntryTest(unittest.TestCase):
    def run_entry(self, args=(), fail_first=False, selected=None, disabled=False,
                  daily_times=None, collect_times=None, fail_always=False,
                  legacy_schedule=False, notify_on_success=False,
                  legacy_times=("0٤:00", "16:00"),
                  legacy_enabled=(True, True),
                  legacy_shutdown=(False, False)):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("master.ps1", "master_pipeline.ps1"):
                shutil.copyfile(ROOT / "scripts" / name, root / name)
            for name, body in (("master_lib.ps1", LIB), ("master_plugins.ps1", PLUGINS),
                               ("master_maa.ps1", MAA), ("master_history.ps1", HISTORY)):
                (root / name).write_text(body, encoding="utf-8")
            accounts = [dict(id=f"a{i}", label=label, slot=f"slot {i}", server="official", enabled=True)
                        for i, label in enumerate(("one", "two"), 1)]
            for acc in accounts:
                (root / "accounts" / acc["slot"]).mkdir(parents=True)
            if disabled:
                accounts[1]["enabled"] = False
            if daily_times is None:
                daily_times = [{"time": "00:00", "enabled": True, "shutdown": True,
                                "accounts": selected or []}]
            if collect_times is None:
                collect_times = [{"time": "00:00", "enabled": True, "shutdown": True}]
            fixture = {"accounts": accounts, "fail_first": fail_first,
                       "fail_always": fail_always, "config": {
                           "notify": {"enabled": notify_on_success,
                                      "on_success": notify_on_success},
                           "schedule": {
                               "times": daily_times, "collect_times": collect_times}}}
            if legacy_schedule:
                legacy = {}
                for i, key in enumerate(("morning", "evening")):
                    item = {"time": legacy_times[i]}
                    if legacy_enabled[i] is not None:
                        item["enabled"] = legacy_enabled[i]
                    legacy[key] = item
                fixture["config"]["schedule"] = legacy
                fixture["config"]["behavior"] = {
                    "morning_shutdown": legacy_shutdown[0],
                    "evening_shutdown": legacy_shutdown[1]}
            (root / "fixture.json").write_text(json.dumps(fixture), encoding="utf-8")
            proc = subprocess.run([PWSH, "-NoProfile", "-File", str(root / "master.ps1"),
                                   "-NoShutdown", *args], capture_output=True,
                                  encoding="utf-8", errors="replace", timeout=20,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(proc.stderr, "", proc.stderr)
            return json.loads((root / "result.json").read_text(encoding="utf-8"))

    def test_refresh_finishes_before_next_account_switch(self):
        r = self.run_entry()
        self.assertEqual(len(r["results"]), 2)
        events = r["events"]
        self.assertLess(events.index("refresh|slot 1"), events.index("slot_switch.ps1|slot 2"))
        self.assertTrue(all(x["OK"] for x in r["results"]))

    def test_retry_reuses_pipeline_and_retains_first_failure(self):
        r = self.run_entry(fail_first=True)
        one, two = r["results"]
        self.assertTrue(one["OK"])
        self.assertTrue(one["Retried"])
        self.assertEqual(one["FirstFailReason"], "MAA 无进展超时")
        self.assertFalse(two["Retried"])
        self.assertEqual(r["events"].count("slot_switch.ps1|slot 1"), 2)
        self.assertEqual(r["events"].count("slot_switch.ps1|slot 2"), 1)

    def test_collect_ignores_daily_account_selection_and_restores(self):
        r = self.run_entry(["-InfrastCollect"], selected=["a1"])
        self.assertEqual(r["mode"], "collect")
        self.assertEqual(len(r["results"]), 2)
        self.assertEqual(r["events"].count("plugin|collect|apply"), 2)
        self.assertIn("plugin|collect|restore", r["events"])
        self.assertNotIn("plugin|guard|apply", r["events"])

    def test_daily_mode_preserves_shift_account_filter(self):
        r = self.run_entry(selected=["a2"])
        self.assertEqual([x["Account"] for x in r["results"]], ["two"])

    def test_fast_switch_does_not_login_run_maa_or_refresh(self):
        r = self.run_entry(["-SwitchTo", "slot 1", "-NoLoginCheck"])
        self.assertEqual(r["mode"], "start")
        self.assertEqual(r["events"], ["slot_switch.ps1|slot 1"])

    def test_disabled_account_is_not_counted_as_passed(self):
        r = self.run_entry(disabled=True)
        self.assertEqual(r["passed"], 1)
        self.assertEqual(r["failed"], 0)
        self.assertIsNone(r["results"][1]["Minutes"])

    def test_invalid_daily_schedule_time_is_ignored(self):
        r = self.run_entry(daily_times=[
            {"time": "00:00", "enabled": True, "shutdown": False},
            {"time": "4:00", "enabled": True, "shutdown": True},
            {"time": "0٤:00", "enabled": True, "shutdown": True},
        ])
        self.assertEqual(r["batch"], "24点")
        self.assertFalse(r["shutdown"])

    def test_invalid_collect_schedule_time_is_ignored(self):
        r = self.run_entry(["-InfrastCollect"], collect_times=[
            {"time": "00:00", "enabled": True, "shutdown": False},
            {"time": "4:00", "enabled": True, "shutdown": True},
            {"time": "0٤:00", "enabled": True, "shutdown": True},
        ])
        self.assertEqual(r["mode"], "collect")
        self.assertFalse(r["shutdown"])

    def test_invalid_legacy_schedule_time_is_ignored(self):
        r = self.run_entry(legacy_schedule=True)
        self.assertEqual(r["batch"], "16点")
        self.assertFalse(r["shutdown"])

    def test_padded_daily_schedule_time_is_trimmed(self):
        r = self.run_entry(daily_times=[
            {"time": " 00:00 ", "enabled": True, "shutdown": True},
        ])
        self.assertEqual(r["batch"], "24点")
        self.assertTrue(r["shutdown"])
        self.assertNotIn("忽略非法班次时间", "\n".join(r["logs"]))

    def test_padded_collect_schedule_time_is_trimmed(self):
        r = self.run_entry(["-InfrastCollect"], collect_times=[
            {"time": " 00:00 ", "enabled": True, "shutdown": True},
        ])
        self.assertTrue(r["shutdown"])
        self.assertNotIn("忽略非法收菜时间", "\n".join(r["logs"]))

    def test_padded_legacy_schedule_time_is_trimmed(self):
        r = self.run_entry(legacy_schedule=True,
                           legacy_times=(" 16:00 ", " 04:00 "),
                           legacy_shutdown=(True, True))
        self.assertTrue(r["shutdown"])

    def test_legacy_disabled_schedule_entry_is_ignored(self):
        # 旧格式 enabled=false 不参与批次/关机判定（config.py 迁移后即停用项）
        r = self.run_entry(legacy_schedule=True,
                           legacy_times=("00:00", "16:00"),
                           legacy_enabled=(False, True))
        entries = r["sched_entries"]
        if isinstance(entries, str):
            entries = [entries]
        self.assertEqual(entries, ["16:00"])

    def test_legacy_entry_without_enabled_defaults_to_enabled(self):
        r = self.run_entry(legacy_schedule=True,
                           legacy_times=("00:00", "16:00"),
                           legacy_enabled=(None, None))
        entries = r["sched_entries"]
        if isinstance(entries, str):
            entries = [entries]
        self.assertEqual(entries, ["00:00", "16:00"])

    def test_daily_time_without_enabled_defaults_to_enabled(self):
        r = self.run_entry(daily_times=[
            {"time": "00:00", "shutdown": True},
            {"time": "16:00", "enabled": False, "shutdown": False},
        ])
        self.assertEqual(r["batch"], "24点")
        self.assertTrue(r["shutdown"])

    def test_collect_time_without_enabled_defaults_to_enabled(self):
        r = self.run_entry(["-InfrastCollect"], collect_times=[
            {"time": "00:00", "shutdown": True},
            {"time": "16:00", "enabled": False, "shutdown": False},
        ])
        self.assertTrue(r["shutdown"])

    def test_failure_notification_includes_skipped_count(self):
        r = self.run_entry(disabled=True, fail_always=True)
        notifications = [e for e in r["events"] if e.startswith("notify|")]
        self.assertEqual(len(notifications), 1)
        self.assertIn("本轮共 2 个账号：成功 0 · 失败 1 · 跳过 1", notifications[0])

    def test_retry_notification_includes_skipped_count(self):
        r = self.run_entry(disabled=True, fail_first=True)
        notifications = [e for e in r["events"] if e.startswith("notify|")]
        self.assertEqual(len(notifications), 1)
        self.assertIn("本轮共 2 个账号：成功 1 · 失败 0 · 跳过 1", notifications[0])

    def test_retry_log_counts_only_accounts_that_actually_ran(self):
        # 停用账号 OK=true 但 Minutes=null，不能算进「最终成功」分子
        r = self.run_entry(disabled=True, fail_first=True)
        logs = "\n".join(r["logs"])
        self.assertIn("=== [RETRY] 结束：最终成功 1/1 ===", logs)

    def test_retry_log_all_failed_after_retry(self):
        r = self.run_entry(disabled=True, fail_always=True)
        logs = "\n".join(r["logs"])
        self.assertIn("=== [RETRY] 结束：最终成功 0/1 ===", logs)

    def test_success_notification_includes_skipped_count(self):
        r = self.run_entry(disabled=True, notify_on_success=True)
        notifications = [e for e in r["events"] if e.startswith("notify|")]
        self.assertEqual(len(notifications), 1)
        # 实际跑的账号数不变，另标注停用跳过数
        self.assertIn("1 个账号全部成功", notifications[0])
        self.assertIn("跳过 1", notifications[0])

    def test_success_notification_without_skipped_keeps_original_format(self):
        r = self.run_entry(notify_on_success=True)
        notifications = [e for e in r["events"] if e.startswith("notify|")]
        self.assertEqual(len(notifications), 1)
        self.assertIn("2 个账号全部成功 · 总用时", notifications[0])
        self.assertNotIn("跳过", notifications[0])

    def test_invalid_daily_schedule_time_logs_warning(self):
        r = self.run_entry(daily_times=[
            {"time": "00:00", "enabled": True, "shutdown": False},
            {"time": "4:00", "enabled": True, "shutdown": True},
            {"time": "0٤:00", "enabled": True, "shutdown": True},
        ])
        logs = "\n".join(r["logs"])
        self.assertIn("[WARN]", logs)
        self.assertIn("4:00", logs)
        self.assertIn("0٤:00", logs)

    def test_invalid_collect_schedule_time_logs_warning(self):
        r = self.run_entry(["-InfrastCollect"], collect_times=[
            {"time": "00:00", "enabled": True, "shutdown": False},
            {"time": "4:00", "enabled": True, "shutdown": True},
            {"time": "0٤:00", "enabled": True, "shutdown": True},
        ])
        logs = "\n".join(r["logs"])
        self.assertIn("[WARN]", logs)
        self.assertIn("4:00", logs)
        self.assertIn("0٤:00", logs)


class RunHistoryTest(unittest.TestCase):
    def test_disabled_account_is_not_counted_as_passed(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = run_ps(self, Path(tmp), ["scripts/master_history.ps1"], r'''
$runHistoryDir = Join-Path $scriptDir 'history'
$runStartTs = '2026-10-30 00:00:00'
$runStamp = 'test'
$runMode = 'farm'
$bsBatch = '4点'
$totalSw = [Diagnostics.Stopwatch]::StartNew()
$results = @(
    [pscustomobject]@{Account='ran'; OK=$true; Minutes=1.0; Reason=''; Step=''; Retried=$false},
    [pscustomobject]@{Account='disabled'; OK=$true; Minutes=$null; Reason=''; Step=''; Retried=$false}
)
Save-RunHistory $null
$rec = Get-Content -LiteralPath (Join-Path $runHistoryDir 'run_test.json') -Raw | ConvertFrom-Json
@{passed=$rec.passed; failed=$rec.failed; skipped=@($rec.accounts | Where-Object {$_.skipped}).Count; stamp=$rec.stamp} | ConvertTo-Json
''')
        self.assertEqual(r, {"passed": 1, "failed": 0, "skipped": 1, "stamp": "test"})


if __name__ == "__main__":
    unittest.main()
