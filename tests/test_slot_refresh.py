"""Execute the real refresh script against fake ADB and temporary slot files."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tests.ps_harness import PWSH, ROOT, run_ps


@unittest.skipUnless(PWSH, "PowerShell 7 required")
class SlotRefreshTest(unittest.TestCase):
    def run_refresh(self, mode="success", no_log_file=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("refresh_slot.ps1", "slot_snapshot.ps1", "login_device_lib.ps1"):
                source = ROOT / "scripts" / name
                if source.exists():
                    shutil.copyfile(source, root / name)
            (root / "config_lib.ps1").write_text(r'''
function Read-AppConfigJson {
    [pscustomobject]@{paths=[pscustomobject]@{
        adb=(Join-Path $PSScriptRoot 'fake-adb.ps1'); device='fake'
        script_dir=$PSScriptRoot; log_file=(Join-Path $PSScriptRoot 'fake.log')
    }}
}
''', encoding="utf-8")
            (root / "fake-adb.ps1").write_text(r'''
if ($args[2] -eq 'shell') { 'com.fake.v2.playerprefs.xml'; exit 0 }
$remote = $args[3]
$target = $args[4]
if ($remote -like '*playerprefs.xml') {
    $uid = if ($env:REFRESH_MODE -eq 'wrong-uid') { '999' } else { '123' }
    [IO.File]::WriteAllText($target, ('<map><string name="u8sdk_cached_uid">' + $uid + '</string></map>'))
} elseif ($remote -like '*HypergryphSdkPreferences.xml') {
    if ($env:REFRESH_MODE -eq 'partial') {
        [IO.File]::WriteAllText($target, 'truncated')
        exit 1
    }
    [IO.File]::WriteAllText($target, 'new-sdk')
} else { [IO.File]::WriteAllText($target, 'new-cache') }
exit 0
''', encoding="utf-8")
            slot = root / "accounts" / "slot 1"
            (slot / "shared_prefs").mkdir(parents=True)
            (slot / "files" / "zx").mkdir(parents=True)
            (slot / "uid.txt").write_text("123", encoding="utf-8")
            files = {
                "shared_prefs/com.fake.v2.playerprefs.xml": "old-prefs",
                "shared_prefs/HypergryphSdkPreferences.xml": "old-sdk",
                "files/zx/lc.cache": "old-cache",
            }
            for name, text in files.items():
                (slot / name).write_text(text, encoding="utf-8")
            entry = root / "refresh_slot.ps1"
            arguments = ["-Slot", "slot 1"]
            if no_log_file:
                arguments.append("-NoLogFile")
            if mode.startswith("login-"):
                mode = mode.removeprefix("login-")
                entry = root / "login-test.ps1"
                entry.write_text(r'''
. (Join-Path $PSScriptRoot 'login_device_lib.ps1')
$adb = Join-Path $PSScriptRoot 'fake-adb.ps1'
$slotDir = Join-Path $PSScriptRoot 'accounts/slot 1'
$pkg = 'com.fake'; $device = 'fake'
function LogLine { }
$ok = Update-SlotData $false
if (-not $ok) { exit 1 }
''', encoding="utf-8")
                arguments = []
            result = subprocess.run(
                [PWSH, "-NoProfile", "-File", str(entry), *arguments],
                env=dict(os.environ, REFRESH_MODE=mode), capture_output=True,
                encoding="utf-8", errors="replace", timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            after = {name: (slot / name).read_text(encoding="utf-8") for name in files}
            log_path = root / "fake.log"
            log_text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
            backups = {str(p.relative_to(slot / ".refresh-backup")).replace("\\", "/"):
                       p.read_text(encoding="utf-8")
                       for p in (slot / ".refresh-backup").rglob("*") if p.is_file()}
            return result, files, after, backups, log_text

    def test_snapshot_backs_up_all_originals_before_update(self):
        result, before, after, backups, log_text = self.run_refresh()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(backups, before)
        self.assertEqual(after["shared_prefs/HypergryphSdkPreferences.xml"], "new-sdk")
        self.assertIn(">123<", after["shared_prefs/com.fake.v2.playerprefs.xml"])
        self.assertIn("[Refresh] slot 'slot 1' updated", log_text)

    def test_parent_mode_keeps_refresh_output_out_of_log_file(self):
        result, _before, _after, _backups, log_text = self.run_refresh(no_log_file=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("[Refresh]", log_text)

    def test_wrong_uid_preserves_every_slot_file(self):
        _, before, after, _, _ = self.run_refresh("wrong-uid")
        self.assertEqual(after, before)

    def test_partial_adb_pull_cannot_replace_valid_credentials(self):
        result, before, after, _, _ = self.run_refresh("partial")
        self.assertEqual(after, before)
        self.assertNotEqual(result.returncode, 0)

    def test_login_refresh_also_rejects_partial_downloads(self):
        result, before, after, _, _ = self.run_refresh("login-partial")
        self.assertEqual(after, before)
        self.assertNotEqual(result.returncode, 0)

    def test_login_refresh_publishes_and_backs_up_successfully(self):
        result, before, after, backups, _ = self.run_refresh("login-success")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(backups, before)
        self.assertEqual(after["shared_prefs/HypergryphSdkPreferences.xml"], "new-sdk")
        self.assertIn("key_GB_viewed", after["shared_prefs/com.fake.v2.playerprefs.xml"])

    def test_login_without_slot_skips_snapshot_successfully(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copyfile(ROOT / "scripts" / "login_device_lib.ps1", root / "login_device_lib.ps1")
            (root / "login-test.ps1").write_text(r'''
. (Join-Path $PSScriptRoot 'login_device_lib.ps1')
$slotDir = ''
$ok = Update-SlotData $false
@{ok=$ok} | ConvertTo-Json
''', encoding="utf-8")
            result = subprocess.run(
                [PWSH, "-NoProfile", "-File", str(root / "login-test.ps1")],
                capture_output=True, encoding="utf-8", errors="replace", timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["ok"])

    def test_rollback_continues_after_one_restore_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = run_ps(self, Path(tmp), ["scripts/slot_snapshot.ps1"], r'''
$slot = Join-Path $scriptDir 'slot'
$null = New-Item -ItemType Directory -Path $slot
$files = [ordered]@{}
foreach ($name in @('first','second','third')) {
    [IO.File]::WriteAllText((Join-Path $slot $name), "old-$name")
    $files[$name] = Join-Path $scriptDir "new-$name"
    [IO.File]::WriteAllText($files[$name], "new-$name")
}
function Copy-Item {
    param($LiteralPath, $Destination, [switch]$Force, $ErrorAction)
    if ($LiteralPath -eq $files['third']) { throw 'publish denied' }
    if ($LiteralPath -like '*.refresh-backup*' -and $Destination -eq (Join-Path $slot 'first')) { throw 'restore denied' }
    Microsoft.PowerShell.Management\Copy-Item -LiteralPath $LiteralPath -Destination $Destination -Force -ErrorAction Stop
}
try { Publish-SlotSnapshot $slot $files } catch { }
@{second=[IO.File]::ReadAllText((Join-Path $slot 'second'))} | ConvertTo-Json
''')
        self.assertEqual(r["second"], "old-second")

    def test_unfinished_refresh_cannot_overwrite_recovery_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = run_ps(self, Path(tmp), ["scripts/slot_snapshot.ps1"], r'''
$slot = Join-Path $scriptDir 'slot'
$backup = Join-Path $slot '.refresh-backup'
$null = New-Item -ItemType Directory -Path $backup -Force
[IO.File]::WriteAllText((Join-Path $backup 'recovery-required.txt'), 'interrupted')
[IO.File]::WriteAllText((Join-Path $backup 'data'), 'recover-me')
[IO.File]::WriteAllText((Join-Path $slot 'data'), 'partial')
$source = Join-Path $scriptDir 'source'
[IO.File]::WriteAllText($source, 'new')
$blocked = $false
try { Publish-SlotSnapshot $slot @{data=$source} } catch { $blocked = $true }
@{blocked=$blocked; backup=[IO.File]::ReadAllText((Join-Path $backup 'data')); data=[IO.File]::ReadAllText((Join-Path $slot 'data'))} | ConvertTo-Json
''')
        self.assertTrue(r["blocked"])
        self.assertEqual(r["backup"], "recover-me")
        self.assertEqual(r["data"], "partial")

    def test_unfinished_snapshot_blocks_switch_before_adb(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copyfile(ROOT / "scripts" / "slot_switch.ps1", root / "slot_switch.ps1")
            (root / "config_lib.ps1").write_text(r'''
function Read-AppConfigJson {
    [pscustomobject]@{paths=[pscustomobject]@{adb=(Join-Path $PSScriptRoot 'fake-adb.ps1')}}
}
''', encoding="utf-8")
            (root / "fake-adb.ps1").write_text(
                "[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'adb-touched'), 'bad')\nexit 1\n",
                encoding="utf-8")
            recovery = root / "accounts" / "slot 1" / ".refresh-backup"
            recovery.mkdir(parents=True)
            (recovery / "recovery-required.txt").write_text("interrupted", encoding="utf-8")
            result = subprocess.run(
                [PWSH, "-NoProfile", "-File", str(root / "slot_switch.ps1"), "-Slot", "slot 1", "-WaitSec", "0"],
                capture_output=True, timeout=25, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "adb-touched").exists())

    def test_publish_failure_rolls_back_already_replaced_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = run_ps(self, Path(tmp), ["scripts/slot_snapshot.ps1"], r'''
$slot = Join-Path $scriptDir 'slot'
$null = New-Item -ItemType Directory -Path $slot
[IO.File]::WriteAllText((Join-Path $slot 'first'), 'old-first')
[IO.File]::WriteAllText((Join-Path $slot 'second'), 'old-second')
$one = Join-Path $scriptDir 'one'
$two = Join-Path $scriptDir 'two'
[IO.File]::WriteAllText($one, 'new-first')
[IO.File]::WriteAllText($two, 'new-second')
function Copy-Item {
    param($LiteralPath, $Destination, [switch]$Force, $ErrorAction)
    if ($LiteralPath -eq $two -and $Destination -eq (Join-Path $slot 'second')) { throw 'disk write failed' }
    Microsoft.PowerShell.Management\Copy-Item -LiteralPath $LiteralPath -Destination $Destination -Force -ErrorAction Stop
}
$failed = $false
try { Publish-SlotSnapshot $slot ([ordered]@{'first'=$one; 'second'=$two}) }
catch { $failed = $true; $message = $_.Exception.Message }
@{failed=$failed; message=$message; first=[IO.File]::ReadAllText((Join-Path $slot 'first')); second=[IO.File]::ReadAllText((Join-Path $slot 'second'))} | ConvertTo-Json
''')
        self.assertTrue(r["failed"])
        self.assertEqual(r["message"], "disk write failed")
        self.assertEqual(r["first"], "old-first")
        self.assertEqual(r["second"], "old-second")


if __name__ == "__main__":
    unittest.main()
