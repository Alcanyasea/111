"""Account pipeline behavior; device, config, process effects stay in temporary data."""
import tempfile
import unittest
import re
from pathlib import Path

from tests.ps_harness import run_ps


PIPELINE_SETUP = r'''
$total = 1
$SwitchTo = ''
$NoLoginCheck = $false
$SkipMAA = $false
$InfrastCollect = $false
$bsBatch = '4点'
$configPath = Join-Path $scriptDir 'config.json'
$maaOfficial = 'fake.exe'
$maaOfficialDir = $scriptDir
$baseSchedulePy = 'base.py'
$fightStagePy = 'fight.py'
$farmGuardPy = Join-Path $scriptDir 'guard.py'
$fiammettaPy = 'fia.py'
$infrastCollectPy = 'collect.py'
[IO.File]::WriteAllText($farmGuardPy, '')
$acc = [pscustomobject]@{ id='a1'; label='test'; slot='slot 1'; server='official'; enabled=$true }
$null = New-Item -ItemType Directory -Path (Join-Path $scriptDir 'accounts/slot 1') -Force
$runHistoryDir = Join-Path $scriptDir 'run_history'
$runStamp = '20261103_040000'
$script:ranMaa = $false
$script:refreshed = $false
function Run-Switch { return 0 }
function Refresh-SlotData { $script:refreshed = $true; return $true }
function Invoke-Plugin { return $true }
function Run-MAA { $script:ranMaa = $true; return $true }
'''


class AccountPipelineTest(unittest.TestCase):
    def run_case(self, body):
        with tempfile.TemporaryDirectory() as tmp:
            return run_ps(self, Path(tmp),
                          ["scripts/master.ps1", "scripts/master_pipeline.ps1"],
                          PIPELINE_SETUP + body)

    def test_collect_failure_does_not_launch_daily_tasks(self):
        r = self.run_case(r'''
$InfrastCollect = $true
function Invoke-Plugin { $script:LastPluginError = 'bad collect plan'; return $false }
$result = Invoke-AccountRun $acc 1 $false
@{result=$result; ranMaa=$script:ranMaa} | ConvertTo-Json -Depth 5
''')
        self.assertFalse(r["ranMaa"])
        self.assertFalse(r["result"]["OK"])
        self.assertEqual(r["result"]["Step"], "基建收菜")

    def test_requested_fight_plan_failure_blocks_maa(self):
        r = self.run_case(r'''
$acc | Add-Member second_fight_stage '1-7'
function Invoke-Plugin { param($pyPath) return ($pyPath -ne 'fight.py') }
$result = Invoke-AccountRun $acc 1 $false
@{result=$result; ranMaa=$script:ranMaa} | ConvertTo-Json -Depth 5
''')
        self.assertFalse(r["ranMaa"])
        self.assertEqual(r["result"]["Step"], "理智关卡")

    def test_failure_records_elapsed_time(self):
        r = self.run_case(r'''
function Run-Switch { Start-Sleep -Milliseconds 650; return 1 }
Invoke-AccountRun $acc 1 $false | ConvertTo-Json
''')
        self.assertFalse(r["OK"])
        self.assertGreater(r["Minutes"], 0)

    def test_switch_uses_array_arguments_preserving_slot_spaces(self):
        r = self.run_case(r'''
$SwitchTo = 'slot 1'
$NoLoginCheck = $true
$script:received = $null
function Run-Switch {
    param($ScriptName, [string[]]$ScriptArgs)
    $script:received = @{name=$ScriptName; args=$ScriptArgs}
    return 0
}
$result = Invoke-AccountRun $acc 1 $false
@{received=$script:received; result=$result; refreshed=$script:refreshed; ranMaa=$script:ranMaa} | ConvertTo-Json -Depth 5
''')
        self.assertEqual(r["received"]["name"], "slot_switch.ps1")
        self.assertEqual(r["received"]["args"], ["-Server", "official", "-Slot", "slot 1"])
        self.assertTrue(r["result"]["OK"])
        self.assertFalse(r["refreshed"])
        self.assertFalse(r["ranMaa"])

    def test_disabled_account_skips_all_device_work(self):
        r = self.run_case(r'''
$acc.enabled = $false
function Run-Switch { throw 'disabled account attempted switch' }
Invoke-AccountRun $acc 1 $false | ConvertTo-Json
''')
        self.assertTrue(r["OK"])
        self.assertIsNone(r["Minutes"])

    def test_missing_bilibili_slot_still_switches_client(self):
        r = self.run_case(r'''
$acc.server = 'bilibili'
$acc.slot = 'missing'
$SwitchTo = 'missing'
$NoLoginCheck = $true
$script:called = $false
function Run-Switch { param($ScriptName) $script:called = ($ScriptName -eq 'switch_to_B服.ps1'); return 0 }
$result = Invoke-AccountRun $acc 1 $false
@{switched=$script:called; result=$result} | ConvertTo-Json -Depth 4
''')
        self.assertTrue(r["switched"])
        self.assertTrue(r["result"]["OK"])

    def test_refresh_failure_marks_account_failed(self):
        r = self.run_case(r'''
function Refresh-SlotData { return $false }
$result = Invoke-AccountRun $acc 1 $false
@{result=$result; ranMaa=$script:ranMaa} | ConvertTo-Json -Depth 5
''')
        self.assertTrue(r["ranMaa"])
        self.assertFalse(r["result"]["OK"])
        self.assertEqual(r["result"]["Step"], "槽位回拉")
        self.assertEqual(r["result"]["Reason"], "槽位回拉失败")


class OptimizationRegressionTest(unittest.TestCase):
    def test_child_lines_with_timestamps_are_not_prefixed_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "child.ps1").write_text(
                "Write-Output '2026-11-03 12:34:56 - [Refresh] done'\n"
                "Write-Output '12:34:57 [Switch] done'\n"
                "Write-Output 'plain output at 12:34:58'\n"
                "[Console]::Error.WriteLine('2026-11-03 12:34:59 - error detail')\n"
                "exit 7\n",
                encoding="utf-8")
            r = run_ps(self, root,
                       ["scripts/master_lib.ps1", "scripts/master_plugins.ps1",
                        "scripts/master_process.ps1"],
                       r'''
$pwshExe = (Get-Process -Id $PID).Path
$logFile = Join-Path $scriptDir 'master.log'
# The harness silences Log by default. Restore the real implementation,
# keeping only host display suppressed so stdout remains valid JSON.
$source = @($env:TEST_PS_SOURCES | ConvertFrom-Json)[0]
$ast = [Management.Automation.Language.Parser]::ParseFile($source, [ref]$null, [ref]$null)
$fn = $ast.FindAll({ param($n)
    $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Log'
}, $false)[0]
. ([scriptblock]::Create($fn.Extent.Text))
function Write-Host { param($Object) }
$code = Run-Switch 'child.ps1' -NoHeartbeatTimeoutSec 5 -PollMilliseconds 50
@{code=$code; lines=@(Get-Content -LiteralPath $logFile)} | ConvertTo-Json -Depth 4
''')
        self.assertEqual(r["code"], 7)
        messages = []
        for line in r["lines"]:
            # The GUI parser requires this exact outer date/time format.
            match = re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} - (.*)", line)
            self.assertIsNotNone(match, line)
            messages.append(match.group(1))
        self.assertIn("  [Refresh] done", messages)
        self.assertIn("  [Switch] done", messages)
        self.assertIn("  plain output at 12:34:58", messages)
        self.assertIn("  [stderr] error detail", messages)


class TimestampSourceTest(unittest.TestCase):
    def test_run_history_names_come_from_one_datetime_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = run_ps(self, Path(tmp), ["scripts/master_lib.ps1"], r'''
$script:clockCalls = 0
function Get-Date($Format) {
    $now = [datetime]::new(2026, 11, 3, 23, 59, 59).AddSeconds($script:clockCalls)
    $script:clockCalls++
    if ($Format) { return $now.ToString($Format) }
    return $now
}
# Execute only the timestamp assignments, never the entry point or config load.
$source = @($env:TEST_PS_SOURCES | ConvertFrom-Json)[0]
$ast = [Management.Automation.Language.Parser]::ParseFile($source, [ref]$null, [ref]$null)
foreach ($statement in $ast.EndBlock.Statements) {
    if ($statement -is [Management.Automation.Language.AssignmentStatementAst] -and
        $statement.Left.Extent.Text -in @('$runNow', '$runStartTs', '$runStamp')) {
        . ([scriptblock]::Create($statement.Extent.Text))
    }
}
@{start=$runStartTs; stamp=$runStamp; calls=$script:clockCalls} | ConvertTo-Json
''')
        self.assertEqual(r, {"start": "2026-11-03 23:59:59",
                             "stamp": "20261103_235959", "calls": 1})


class FailureEvidenceTest(unittest.TestCase):
    """失败现场归档：debug 目录每轮开头会被清空，必须随运行历史留一份。"""

    def run_case(self, body):
        with tempfile.TemporaryDirectory() as tmp:
            return run_ps(self, Path(tmp),
                          ["scripts/master.ps1", "scripts/master_pipeline.ps1",
                           "scripts/master_history.ps1"],
                          PIPELINE_SETUP + body)

    def test_failed_account_archives_only_its_own_screenshots(self):
        r = self.run_case(r'''
$debugDir = Join-Path $scriptDir 'debug'
$null = New-Item -ItemType Directory -Path $debugDir -Force
[IO.File]::WriteAllText((Join-Path $debugDir 'login_check_slot 1.png'), 'frame')
[IO.File]::WriteAllText((Join-Path $debugDir 'other_account.png'), 'noise')
function Run-Switch { return 1 }
$result = Invoke-AccountRun $acc 1 $false
$dest = Join-Path (Join-Path $runHistoryDir $runStamp) 'slot 1'
@{ok=$result.OK; exists=(Test-Path -LiteralPath $dest);
  files=@(Get-ChildItem -LiteralPath $dest -ErrorAction SilentlyContinue | ForEach-Object Name)} | ConvertTo-Json
''')
        self.assertFalse(r["ok"])
        self.assertTrue(r["exists"])
        self.assertEqual(r["files"], ["login_check_slot 1.png"])

    def test_failed_account_archives_nested_debug_files(self):
        r = self.run_case(r'''
$nested = Join-Path $scriptDir 'debug/operbox/slot 1/debug/oper'
$null = New-Item -ItemType Directory -Path $nested -Force
[IO.File]::WriteAllText((Join-Path $nested '2026_raw.png'), 'frame')
[IO.File]::WriteAllText((Join-Path (Split-Path $nested -Parent) 'asst.log'), 'diagnostic')
function Run-Switch { return 1 }
$result = Invoke-AccountRun $acc 1 $false
$dest = Join-Path (Join-Path $runHistoryDir $runStamp) 'slot 1'
@{ok=$result.OK;
  files=@(Get-ChildItem -LiteralPath $dest -Recurse -File -ErrorAction SilentlyContinue |
          ForEach-Object { $_.FullName.Substring($dest.Length + 1).Replace('\','/') } |
          Sort-Object)} | ConvertTo-Json
''')
        self.assertFalse(r["ok"])
        self.assertEqual(r["files"], [
            "operbox/slot 1/debug/asst.log",
            "operbox/slot 1/debug/oper/2026_raw.png",
        ])

    def test_successful_account_leaves_no_evidence_directory(self):
        r = self.run_case(r'''
$debugDir = Join-Path $scriptDir 'debug'
$null = New-Item -ItemType Directory -Path $debugDir -Force
[IO.File]::WriteAllText((Join-Path $debugDir 'login_check_slot 1.png'), 'frame')
$result = Invoke-AccountRun $acc 1 $false
@{ok=$result.OK; dirs=@(Get-ChildItem -LiteralPath $runHistoryDir -Directory -ErrorAction SilentlyContinue).Count} | ConvertTo-Json
''')
        self.assertTrue(r["ok"])
        self.assertEqual(r["dirs"], 0)

    def test_retry_replaces_previous_evidence_instead_of_accumulating(self):
        r = self.run_case(r'''
$debugDir = Join-Path $scriptDir 'debug'
$null = New-Item -ItemType Directory -Path $debugDir -Force
[IO.File]::WriteAllText((Join-Path $debugDir 'login_check_slot 1.png'), 'frame')
[IO.File]::WriteAllText((Join-Path $debugDir 'fail_slot 1_120000.png'), 'first fail')
function Run-Switch { return 1 }
$null = Invoke-AccountRun $acc 1 $false
Remove-Item (Join-Path $debugDir 'fail_slot 1_120000.png') -Force
$null = Invoke-AccountRun $acc 1 $true
$dest = Join-Path (Join-Path $runHistoryDir $runStamp) 'slot 1'
@{files=@(Get-ChildItem -LiteralPath $dest -ErrorAction SilentlyContinue | ForEach-Object Name)} | ConvertTo-Json
''')
        self.assertEqual(r["files"], ["login_check_slot 1.png"])

    def test_cleanup_drops_evidence_with_expired_history(self):
        r = self.run_case(r'''
$results = @([pscustomobject]@{Account='a'; OK=$true; Minutes=1; Reason=''; Step=''; Retried=$false})
$totalSw = [Diagnostics.Stopwatch]::StartNew()
$bsBatch = '4点'
$runMode = 'farm'
$runStartTs = '2026-11-03 04:00:00'
$runStamp = '20261103_040000'
$null = New-Item -ItemType Directory -Path $runHistoryDir -Force
$oldJson = Join-Path $runHistoryDir 'run_20200101_000000.json'
[IO.File]::WriteAllText($oldJson, '{}')
(Get-Item $oldJson).LastWriteTime = (Get-Date).AddDays(-61)
$oldDir = Join-Path $runHistoryDir '20200101_000000'
$null = New-Item -ItemType Directory -Path $oldDir -Force
[IO.File]::WriteAllText((Join-Path $oldDir 'login_check_a.png'), 'old')
(Get-Item $oldDir).LastWriteTime = (Get-Date).AddDays(-61)
Save-RunHistory ''
@{oldJson=(Test-Path $oldJson); oldEvidence=(Test-Path $oldDir);
  newJson=(Test-Path (Join-Path $runHistoryDir ('run_' + $runStamp + '.json')))} | ConvertTo-Json
''')
        self.assertFalse(r["oldJson"])
        self.assertFalse(r["oldEvidence"])
        self.assertTrue(r["newJson"])


class ChildProcessTest(unittest.TestCase):
    def test_timeout_kills_descendants_and_keeps_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "child.ps1").write_text(r'''
$child = Start-Process -FilePath (Get-Process -Id $PID).Path -ArgumentList '-NoProfile -Command Start-Sleep 30' -WindowStyle Hidden -PassThru
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'child.pid'), [string]$child.Id)
Write-Output 'diagnostic-before-timeout'
Start-Sleep 30
''', encoding="utf-8")
            r = run_ps(self, root, ["scripts/master_plugins.ps1", "scripts/master_process.ps1"], r'''
$pwshExe = (Get-Process -Id $PID).Path
$script:messages = [Collections.Generic.List[string]]::new()
function Log($message) { $script:messages.Add($message) }
$code = Run-Switch 'child.ps1' -NoHeartbeatTimeoutSec 3 -PollMilliseconds 100
$childPid = [int][IO.File]::ReadAllText((Join-Path $scriptDir 'child.pid'))
try {
    $remaining = Get-Process -Id $childPid -ErrorAction SilentlyContinue
    @{code=$code; alive=($null -ne $remaining); diagnostic=([bool]($script:messages -match 'diagnostic-before-timeout'))} | ConvertTo-Json
} finally { if ($remaining) { Stop-Process -Id $childPid -Force -ErrorAction SilentlyContinue } }
''')
        self.assertEqual(r["code"], 124)
        self.assertFalse(r["alive"])
        self.assertTrue(r["diagnostic"])

    def test_missing_interpreter_returns_launch_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = run_ps(self, Path(tmp), ["scripts/master_plugins.ps1", "scripts/master_process.ps1"], r'''
$pwshExe = Join-Path $scriptDir 'no-such-interpreter.exe'
@{code=(Run-Switch 'child.ps1')} | ConvertTo-Json
''')
        self.assertEqual(r["code"], 127)

    def test_slot_argument_survives_real_child_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "child.ps1").write_text(
                "param([string]$Slot)\n"
                "[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'received.txt'), $Slot)\n"
                "exit 7\n", encoding="utf-8")
            r = run_ps(self, root, ["scripts/master_plugins.ps1", "scripts/master_process.ps1"], r'''
$pwshExe = (Get-Process -Id $PID).Path
$code = Run-Switch 'child.ps1' @('-Slot', '槽位 1 "quoted"')
@{code=$code; value=[IO.File]::ReadAllText((Join-Path $scriptDir 'received.txt'))} | ConvertTo-Json
''')
        self.assertEqual(r["code"], 7)
        self.assertEqual(r["value"], '槽位 1 "quoted"')


if __name__ == "__main__":
    unittest.main()
