"""Existing MAA/MuMu processes are synthetic; no production entry point is run."""
import tempfile
import sys
import unittest
from pathlib import Path

from tests.ps_harness import run_ps


SETUP = r'''
$maaOfficialDir = Join-Path $scriptDir 'official'
$maaBilibiliDir = Join-Path $scriptDir 'bilibili'
$script:events = [Collections.Generic.List[string]]::new()
$script:processes = @()
function Add-FakeMaa($Id, $Dir) {
    $p = [pscustomobject]@{Id=$Id; Path=(Join-Path $Dir 'MAA.exe'); HasExited=$false}
    $p | Add-Member ScriptMethod WaitForExit {
        param($Timeout)
        $script:events.Add("wait:$($this.Id)")
        if ($script:exitBlocked) { return $false }
        $this.HasExited = $true
        return $true
    }
    $script:processes += $p
}
function Get-Process {
    param($Name, $Id, $ErrorAction)
    if ($Name -eq 'MuMuNxMain') {
        if ($script:mumuOpen) { [pscustomobject]@{Id=999999} }
    } elseif ($Name -eq 'MAA') {
        $script:processes | Where-Object { -not $_.HasExited }
    } elseif ($null -ne $Id) {
        $script:processes | Where-Object { $_.Id -eq $Id -and -not $_.HasExited }
    }
}
function Stop-Process {
    param($Id, $InputObject, [switch]$Force, $ErrorAction)
    if ($InputObject) { $Id = $InputObject.Id }
    $script:events.Add("stop:$Id")
    if ($script:stopBlocked) { throw 'access denied (synthetic)' }
}
function Start-Sleep { }
function Fake-Adb { $script:events.Add('adb') }
function Fake-Cli { $script:events.Add('launch') }
$adb = 'Fake-Adb'
$cli = 'Fake-Cli'
$device = '127.0.0.1:16384'
$configPath = Join-Path $scriptDir 'fake-config.json'
$config = $null
$mumuLaunchTimeoutSec = 2
function Get-MuMuVmConfigPath { return '' }
function Find-AdbDevice { return '127.0.0.1:16384' }
function Wait-AdbBootReady { return $true }
function Sync-ConfirmedAdbDevice {
    $script:events.Add('sync')
    if (@($script:processes | Where-Object { $_.Id -in @(1,2) -and -not $_.HasExited }).Count) {
        throw 'MAA still owns configuration'
    }
    return $true
}
'''


class StartupTakeoverTest(unittest.TestCase):
    def run_case(self, body):
        with tempfile.TemporaryDirectory() as tmp:
            return run_ps(self, Path(tmp), ['scripts/master_lib.ps1'], SETUP + body,
                          env={'TEST_PYTHON': sys.executable})

    def test_directory_junction_is_the_same_installation(self):
        result = self.run_case(r'''
$null = New-Item -ItemType Directory -Path $maaOfficialDir
$null = New-Item -ItemType File -Path (Join-Path $maaOfficialDir 'MAA.exe')
$other = Join-Path $scriptDir 'other'
$null = New-Item -ItemType Directory -Path $other
$null = New-Item -ItemType HardLink -Path (Join-Path $other 'MAA.exe') -Target (Join-Path $maaOfficialDir 'MAA.exe')
$alias = Join-Path $scriptDir 'alias'
$null = New-Item -ItemType Junction -Path $alias -Target $maaOfficialDir
$venvPython = $env:TEST_PYTHON
Add-FakeMaa 1 $maaOfficialDir
Add-FakeMaa 3 $other
Stop-StaleMaa $alias
@{exited=$script:processes[0].HasExited; otherExited=$script:processes[1].HasExited} | ConvertTo-Json
''')
        self.assertTrue(result['exited'])
        self.assertFalse(result['otherExited'])

    def test_both_configured_maas_exit_before_device_or_config_changes(self):
        for mumu_open in (True, False):
            with self.subTest(mumu_open=mumu_open):
                result = self.run_case(
                    '$script:mumuOpen = $' + str(mumu_open).lower() + r'''
Add-FakeMaa 1 $maaOfficialDir
Add-FakeMaa 2 $maaBilibiliDir
Add-FakeMaa 3 ($maaOfficialDir + '-backup')
$ok = Start-MuMu
@{ok=$ok; events=@($script:events); otherExited=$script:processes[2].HasExited} | ConvertTo-Json
''')
                self.assertTrue(result['ok'])
                self.assertFalse(result['otherExited'])
                events = result['events']
                for pid in (1, 2):
                    self.assertLess(events.index(f'stop:{pid}'), events.index(f'wait:{pid}'))
                    self.assertLess(events.index(f'wait:{pid}'), events.index('adb'))
                self.assertNotIn('stop:3', events)
                self.assertEqual(events.count('launch'), 0 if mumu_open else 1)

    def test_open_mumu_is_reused_without_launching_a_second_instance(self):
        result = self.run_case(r'''
$script:mumuOpen = $true
$ok = Start-MuMu
@{ok=$ok; events=@($script:events)} | ConvertTo-Json
''')
        self.assertTrue(result['ok'])
        self.assertNotIn('launch', result['events'])

    def test_cleanup_failure_reports_reason_before_touching_device(self):
        for failure in ('stopBlocked', 'exitBlocked'):
            with self.subTest(failure=failure):
                result = self.run_case('$script:' + failure + ' = $true\n' + r'''
Add-FakeMaa 1 $maaOfficialDir
$ok = Start-MuMu
@{ok=$ok; error=$script:StartMuMuError; events=@($script:events)} | ConvertTo-Json
''')
                self.assertFalse(result['ok'])
                self.assertIn('MAA', result['error'])
                self.assertNotIn('sync', result['events'])
                self.assertNotIn('adb', result['events'])

    def test_cleanup_normalizes_directory_and_ignores_blank_paths(self):
        result = self.run_case(r'''
Add-FakeMaa 1 $maaOfficialDir
Add-FakeMaa 3 ($maaOfficialDir + '-backup')
Stop-StaleMaa ''
Stop-StaleMaa ($maaOfficialDir.Replace('\', '/') + '/')
@{events=@($script:events); exited=$script:processes[0].HasExited} | ConvertTo-Json
''')
        self.assertTrue(result['exited'])
        self.assertEqual(result['events'], ['stop:1', 'wait:1'])
