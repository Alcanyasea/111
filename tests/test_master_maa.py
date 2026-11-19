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
$script:MaaRestartGraceSec = 0
function Get-CimInstance { @() }
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
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["launches"], 1)

    def test_update_child_is_followed_without_launching_again(self):
        result = self.run_case(r'''
$script:MaaRestartGraceSec = 30
$maaStallTimeoutSec = 100
$script:child = [pscustomobject]@{Id=222; HasExited=$false; Path=(Join-Path $maaDir 'MAA.exe'); StartTime=(Get-Date)}
function Start-Process {
    $script:launches++
    [pscustomobject]@{Id=111; HasExited=$true; StartTime=(Get-Date).AddSeconds(-5)}
}
function Get-CimInstance {
    [pscustomobject]@{ProcessId=222; ParentProcessId=111; ExecutablePath=$script:child.Path}
}
function Get-Process { return $script:child }
function Wait-Event {
    if ($script:LastMaaActiveProcess.Id -eq 222) { [IO.File]::WriteAllText($signalFile, '') }
}
$ok = Run-MAA (Join-Path $maaDir 'MAA.exe') $maaDir 'test'
@{ok=$ok; launches=$script:launches; kills=$script:kills} | ConvertTo-Json
''')
        self.assertTrue(result['ok'])
        self.assertEqual(result['launches'], 1)
        self.assertEqual(result['kills'], 1, '接续的新进程也必须收尾')

    def test_unrelated_reopen_is_not_adopted_or_relaunched_over(self):
        result = self.run_case(r'''
$maaStallTimeoutSec = 100
function Start-Process {
    $script:launches++
    [pscustomobject]@{Id=111; HasExited=$true; StartTime=(Get-Date).AddSeconds(-5)}
}
function Get-CimInstance {
    [pscustomobject]@{ProcessId=222; ParentProcessId=999; ExecutablePath=(Join-Path $maaDir 'MAA.exe')}
}
$ok = Run-MAA (Join-Path $maaDir 'MAA.exe') $maaDir 'test'
@{ok=$ok; status=$script:LastMaaStatus; launches=$script:launches; kills=$script:kills} | ConvertTo-Json
''')
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'interrupted')
        self.assertEqual(result['launches'], 1)
        self.assertEqual(result['kills'], 0)

    def test_known_chain_error_is_not_downgraded_when_log_becomes_unreadable(self):
        result = self.run_case(r'''
$state = New-MaaLogState $maaDir
$state.Errors.Add('Fight')
Remove-Item -LiteralPath $logPath
[IO.File]::WriteAllText($signalFile, '')
$state.Timer = [pscustomobject]@{Elapsed=[pscustomobject]@{TotalSeconds=0; TotalMinutes=0}}
function Wait-Event { $state.Timer.Elapsed.TotalSeconds += 31 }
$status = Wait-MAADone 10 $maaDir ([pscustomobject]@{HasExited=$true}) $state
@{status=$status} | ConvertTo-Json
''')
        self.assertEqual(result['status'], 'chain-error')

    def test_cleanup_exception_does_not_discard_confirmed_completion(self):
        result = self.run_case(r'''
function Start-Process {
    [IO.File]::WriteAllText($signalFile, '')
    [pscustomobject]@{HasExited=$false; Id=333}
}
function Stop-Process { throw 'access denied' }
$ok = Run-MAA 'fake.exe' $maaDir 'test'
@{ok=$ok; warning=$script:LastMaaCleanupWarning} | ConvertTo-Json
''')
        self.assertTrue(result['ok'])
        self.assertIn('access denied', result['warning'])

    def test_unreadable_log_timeout_is_unknown_not_retryable_stall(self):
        result = self.run_case(r'''
Remove-Item -LiteralPath $logPath
function Start-Process { [pscustomobject]@{Id=111; HasExited=$false} }
$ok = Run-MAA 'fake.exe' $maaDir 'test'
@{ok=$ok; status=$script:LastMaaStatus} | ConvertTo-Json
''')
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'interrupted')

    def test_completion_from_unrelated_reopen_is_not_success(self):
        result = self.run_case(r'''
function Start-Process {
    [IO.File]::WriteAllText($signalFile, '')
    [pscustomobject]@{Id=111; HasExited=$true; StartTime=(Get-Date).AddSeconds(-5)}
}
function Get-CimInstance {
    [pscustomobject]@{ProcessId=222; ParentProcessId=999; ExecutablePath=(Join-Path $maaDir 'MAA.exe')}
}
$ok = Run-MAA 'fake.exe' $maaDir 'test'
@{ok=$ok; status=$script:LastMaaStatus} | ConvertTo-Json
''')
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'interrupted')

    def test_monitor_exception_after_launch_does_not_allow_repeat(self):
        result = self.run_case(r'''
function Start-Process { [pscustomobject]@{Id=111; HasExited=$false} }
function Wait-MAADone { throw 'monitor unavailable' }
$ok = Run-MAA 'fake.exe' $maaDir 'test'
@{ok=$ok; status=$script:LastMaaStatus} | ConvertTo-Json
''')
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'interrupted')


class StaleMaaCleanupTest(unittest.TestCase):
    def test_directory_prefix_does_not_match_sibling_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = run_ps(self, Path(tmp), ["scripts/master_lib.ps1"], r'''
$script:stopped = [Collections.Generic.List[int]]::new()
function Get-Process {
    param($Name, $ErrorAction)
    $items = @(
        [pscustomobject]@{ Id=1; Path='D:\MAA\MAA-v6\MAA.exe' },
        [pscustomobject]@{ Id=2; Path='D:\MAA\MAA-v6-backup\MAA.exe' }
    )
    foreach ($item in $items) { $item | Add-Member ScriptMethod WaitForExit { return $true } }
    return $items
}
function Stop-Process {
    param($InputObject, [switch]$Force, $ErrorAction)
    $script:stopped.Add($InputObject.Id)
}
Stop-StaleMaa 'D:\MAA\MAA-v6'
@{stopped=@($script:stopped)} | ConvertTo-Json
''')
        self.assertEqual(r["stopped"], [1])


if __name__ == "__main__":
    unittest.main()
