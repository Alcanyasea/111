"""MAA lifecycle regressions, with synthetic logs/signals and process boundaries."""
import tempfile
import unittest
from pathlib import Path

from tests.ps_harness import run_ps


SETUP = r'''
$signalFile = Join-Path $scriptDir 'maa_done.signal'
$maaDir = Join-Path $scriptDir 'fake-maa'
$null = New-Item -ItemType Directory -Path (Join-Path $maaDir 'debug') -Force
$logPath = Join-Path $maaDir 'debug/asst.log'
[IO.File]::WriteAllText($logPath, '')
$maaStallTimeoutSec = 0
$script:launches = 0
$script:kills = 0
function Start-Sleep { }
function Stop-StaleMaa { }
function Stop-Process { param($Id, [switch]$Force, $ErrorAction) $script:kills++ }
function Wait-Event { param($SourceIdentifier, $Timeout) }
'''


class MaaLifecycleTest(unittest.TestCase):
    def run_case(self, body):
        with tempfile.TemporaryDirectory() as tmp:
            return run_ps(self, Path(tmp),
                          ["scripts/master.ps1", "scripts/master_maa.ps1"], SETUP + body)

    def test_completion_during_launch_is_not_deleted(self):
        result = self.run_case(r'''
function Start-Process {
    $script:launches++
    [IO.File]::WriteAllText($signalFile, '')
    [pscustomobject]@{ HasExited = $false; Id = 424242 }
}
$ok = Run-MAA 'fake.exe' $maaDir 'test'
@{ok=$ok; status=$script:LastMaaStatus; launches=$script:launches} | ConvertTo-Json
''')
        self.assertTrue(result["ok"])
        self.assertEqual(result["launches"], 1)

    def test_old_errors_do_not_contaminate_new_run(self):
        result = self.run_case(r'''
[IO.File]::WriteAllText($logPath, 'TaskChainError {"taskchain":"Fight"}')
function Start-Process {
    [IO.File]::WriteAllText($signalFile, '')
    [pscustomobject]@{ HasExited = $true; Id = 424242 }
}
$ok = Run-MAA 'fake.exe' $maaDir 'test'
@{ok=$ok; errors=@($script:LastMaaChainErrors)} | ConvertTo-Json
''')
        self.assertTrue(result["ok"])
        self.assertEqual(result["errors"], [])

    def test_empty_startup_crash_retries_only_once(self):
        result = self.run_case(r'''
$maaStallTimeoutSec = 10
function Start-Process {
    $script:launches++
    [pscustomobject]@{ HasExited = $true; Id = 424242 }
}
$ok = Run-MAA 'fake.exe' $maaDir 'test'
@{ok=$ok; status=$script:LastMaaStatus; launches=$script:launches} | ConvertTo-Json
''')
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "dead-early")
        self.assertEqual(result["launches"], 2)

    def test_locked_log_keeps_unread_errors_for_recovery(self):
        result = self.run_case(r'''
$state = New-MaaLogState $maaDir
[IO.File]::WriteAllText($logPath, 'TaskChainError {"taskchain":"Fight"}')
$handle = [IO.File]::Open($logPath, 'Open', 'Read', 'None')
try { Read-MaaLogProgress $state; $blockedPosition = $state.Position }
finally { $handle.Dispose() }
Read-MaaLogProgress $state
@{blockedPosition=$blockedPosition; errors=@($state.Errors); readOK=$state.ReadOK} | ConvertTo-Json
''')
        self.assertEqual(result["blockedPosition"], 0)
        self.assertEqual(result["errors"], ["Fight"])
        self.assertTrue(result["readOK"])

    def test_rotation_and_split_error_are_read(self):
        result = self.run_case(r'''
[IO.File]::WriteAllText($logPath, ('x' * 100))
$state = New-MaaLogState $maaDir
[IO.File]::WriteAllText($logPath, 'TaskChainError {"task')
Read-MaaLogProgress $state
[IO.File]::AppendAllText($logPath, 'chain":"Fight"}')
Read-MaaLogProgress $state
@{errors=@($state.Errors); readOK=$state.ReadOK} | ConvertTo-Json
''')
        self.assertEqual(result["errors"], ["Fight"])
        self.assertTrue(result["readOK"])

    def test_old_heartbeat_in_carry_does_not_refresh_idle_timer(self):
        result = self.run_case(r'''
$state = New-MaaLogState $maaDir
[IO.File]::WriteAllText($logPath, 'append_callback | SubTaskStart')
Read-MaaLogProgress $state
$activity = $state.LastActivity
[IO.File]::AppendAllText($logPath, ' unrelated debug output')
Read-MaaLogProgress $state
@{unchanged=($activity -eq $state.LastActivity)} | ConvertTo-Json
''')
        self.assertTrue(result["unchanged"])

    def test_error_arriving_with_completion_is_not_reported_success(self):
        result = self.run_case(r'''
$maaStallTimeoutSec = 10
function Start-Process { [pscustomobject]@{ HasExited = $false; Id = 424242 } }
function Wait-Event {
    [IO.File]::AppendAllText($logPath, 'TaskChainError {"taskchain":"Fight"}')
    [IO.File]::WriteAllText($signalFile, '')
}
$ok = Run-MAA 'fake.exe' $maaDir 'test'
@{ok=$ok; status=$script:LastMaaStatus; errors=@($script:LastMaaChainErrors)} | ConvertTo-Json
''')
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "chain-error")
        self.assertEqual(result["errors"], ["Fight"])

    def test_startup_log_is_checked_even_if_completion_is_immediate(self):
        result = self.run_case(r'''
function Start-Process {
    [IO.File]::AppendAllText($logPath, 'TaskChainError { "taskchain" : "Infrast" }')
    [IO.File]::WriteAllText($signalFile, '')
    [pscustomobject]@{ HasExited = $false; Id = 424242 }
}
$ok = Run-MAA 'fake.exe' $maaDir 'test'
@{ok=$ok; status=$script:LastMaaStatus; errors=@($script:LastMaaChainErrors)} | ConvertTo-Json
''')
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "chain-error")
        self.assertEqual(result["errors"], ["Infrast"])

    def test_missing_executable_is_a_failure_result(self):
        result = self.run_case(r'''
function Start-Process { throw 'cannot launch' }
$ok = Run-MAA 'fake.exe' $maaDir 'test'
@{ok=$ok; status=$script:LastMaaStatus} | ConvertTo-Json
''')
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "launch-error")

    def test_completion_without_readable_log_is_not_success(self):
        r = self.run_case(r'''
$state = New-MaaLogState $maaDir
Remove-Item -LiteralPath $logPath
[IO.File]::WriteAllText($signalFile, '')
$state.Timer = [pscustomobject]@{Elapsed=[pscustomobject]@{TotalSeconds=0; TotalMinutes=0}}
function Wait-Event { $state.Timer.Elapsed.TotalSeconds += 31 }
$status = Wait-MAADone 10 $maaDir ([pscustomobject]@{HasExited=$true}) $state
@{status=$status} | ConvertTo-Json
''')
        self.assertEqual(r["status"], "log-error")

    def test_crash_after_startup_progress_does_not_retry(self):
        result = self.run_case(r'''
$maaStallTimeoutSec = 10
function Start-Process {
    $script:launches++
    [IO.File]::AppendAllText($logPath, 'append_callback | SubTaskStart')
    [pscustomobject]@{ HasExited = $true; Id = 424242 }
}
$ok = Run-MAA 'fake.exe' $maaDir 'test'
@{ok=$ok; status=$script:LastMaaStatus; launches=$script:launches} | ConvertTo-Json
''')
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "dead")
        self.assertEqual(result["launches"], 1)


class StaleMaaCleanupTest(unittest.TestCase):
    def test_directory_prefix_does_not_match_sibling_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = run_ps(self, Path(tmp), ["scripts/master_lib.ps1"], r'''
$script:stopped = [Collections.Generic.List[int]]::new()
function Get-Process {
    param($Name, $ErrorAction)
    return @(
        [pscustomobject]@{ Id=1; Path='D:\MAA\MAA-v6\MAA.exe' },
        [pscustomobject]@{ Id=2; Path='D:\MAA\MAA-v6-backup\MAA.exe' }
    )
}
function Stop-Process {
    param($Id, [switch]$Force, $ErrorAction)
    $script:stopped.Add($Id)
}
Stop-StaleMaa 'D:\MAA\MAA-v6'
@{stopped=@($script:stopped)} | ConvertTo-Json
''')
        self.assertEqual(r["stopped"], [1])


if __name__ == "__main__":
    unittest.main()
