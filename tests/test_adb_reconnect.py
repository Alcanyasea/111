"""ADB reconnect / device discovery / config sync behavior (all state in temp dirs)."""
import json
import tempfile
from pathlib import Path
import unittest

from tests.ps_harness import run_ps


ADB_SOURCE = "scripts/adb_lib.ps1"
SYNC_SOURCE = "scripts/adb_sync.ps1"


class AdbScriptWiringTest(unittest.TestCase):
    """脚本靠 dot-source 拿函数；漏加载只会在运行到重连分支时才炸（实测漏过一次）。"""

    SCRIPTS = ("master_lib.ps1", "slot_switch.ps1", "login_check.ps1",
               "login_check_bilibili.ps1", "refresh_slot.ps1")

    def test_scripts_that_sync_load_adb_sync(self):
        root = Path(__file__).resolve().parents[1]
        for name in self.SCRIPTS:
            text = (root / "scripts" / name).read_text(encoding="utf-8-sig")
            if "Sync-AdbDeviceForProject" in text or "Sync-ConfirmedAdbDevice" in text:
                self.assertIn('"adb_sync.ps1"', text, f"{name} 调用同步函数但没 dot-source adb_sync.ps1")

    def test_scripts_using_adb_lib_load_it(self):
        root = Path(__file__).resolve().parents[1]
        for name in self.SCRIPTS:
            text = (root / "scripts" / name).read_text(encoding="utf-8-sig")
            if "Ensure-AdbOnline" in text or "Find-AdbDevice" in text:
                self.assertIn('"adb_lib.ps1"', text, f"{name} 用到 adb_lib 函数但没加载")


class AdbReconnectTest(unittest.TestCase):
    def run_case(self, mode):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "fake-adb.ps1").write_text(r'''
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args)
$state = Join-Path $PSScriptRoot 'adb-online'
$calls = Join-Path $PSScriptRoot 'adb-calls'
Add-Content -LiteralPath $calls -Value ($Args -join ' ')
if ($Args -contains 'devices') {
    'List of devices attached'
    if (Test-Path $state) { "fake-device`tdevice" } else { "fake-device`toffline" }
    exit 0
}
if ($Args -contains 'connect') {
    if ($env:ADB_RECONNECT_MODE -eq 'recover') {
        New-Item -ItemType File -Path $state -Force | Out-Null
    }
    'unable to connect'; exit 1
}
if ($Args -contains 'disconnect') { exit 0 }
if ($Args -contains 'kill-server') { exit 0 }
exit 0
''', encoding="utf-8")
            body = r'''
$adb = Join-Path $scriptDir 'fake-adb.ps1'
$ok = Ensure-AdbOnline -Device 'fake-device' -Attempts 3 -RetryDelaySec 0
@{ok=$ok; error=$script:LastAdbError;
  calls=@(Get-Content (Join-Path $scriptDir 'adb-calls'))} | ConvertTo-Json -Depth 4
'''
            return run_ps(self, root, [ADB_SOURCE], body,
                          env={"ADB_RECONNECT_MODE": mode})

    def test_offline_device_is_reconnected(self):
        result = self.run_case("recover")
        self.assertTrue(result["ok"])
        self.assertTrue(any("connect fake-device" in line for line in result["calls"]))

    def test_persistent_offline_device_fails_after_bounded_attempts(self):
        result = self.run_case("offline")
        self.assertFalse(result["ok"])
        self.assertIn("offline", result["error"])
        connects = [c for c in result["calls"] if "connect" in c and "disconnect" not in c]
        kills = [c for c in result["calls"] if "kill-server" in c]
        self.assertLessEqual(len(connects), 2, "Attempts=3 时最多重连 2 次")
        self.assertLessEqual(len(kills), 1, "kill-server 只允许兜底一次")

    def test_candidates_put_current_device_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vm = root / "vm_config.json"
            vm.write_text(json.dumps({"vm": {"nat": {"port_forward": {
                "adb": {"host_port": 16555}}}}}), encoding="utf-8")
            result = run_ps(self, root, [ADB_SOURCE], r'''
$result = @(Get-AdbCandidates -VmConfigPath (Join-Path $scriptDir 'vm_config.json') `
    -CurrentDevice '127.0.0.1:16384' -KnownSerials @('emulator-5554', '192.168.3.52:5555'))
@{first=$result[0]; second=$result[1]; third=$result[2];
  hasEmulator=($result -contains 'emulator-5554');
  hasLan=($result -contains '192.168.3.52:5555')} | ConvertTo-Json
''')
            self.assertEqual(result["first"], "127.0.0.1:16384")
            self.assertEqual(result["second"], "127.0.0.1:16555")
            self.assertEqual(result["third"], "127.0.0.1:16556")
            self.assertTrue(result["hasEmulator"])
            self.assertFalse(result["hasLan"], "局域网真机不进入候选")

    def test_candidates_scan_only_without_vm_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_ps(self, root, [ADB_SOURCE], r'''
$a = @(Get-AdbCandidates -CurrentDevice '127.0.0.1:16384')
$b = @(Get-AdbCandidates -VmConfigPath (Join-Path $scriptDir 'missing.json') -CurrentDevice '127.0.0.1:16384')
@{first=$a[0]; hasScan=($a -contains '127.0.0.1:16390'); beyondScan=($a -contains '127.0.0.1:16400');
  firstB=$b[0]} | ConvertTo-Json
''')
            self.assertEqual(result["first"], "127.0.0.1:16384")
            self.assertTrue(result["hasScan"], "没有实例配置时才退化为端口扫描")
            self.assertFalse(result["beyondScan"], "扫描范围不能再扩散到 16400（会拖慢启动）")
            self.assertEqual(result["firstB"], "127.0.0.1:16384")

    def test_pm_error_output_is_treated_as_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "fake-adb.ps1").write_text(r'''
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args)
$all = $Args -join ' '
if ($all -match 'pm path') { 'Error: java.lang.IllegalStateException: PackageManager busy'; exit 0 }
if ($Args -contains 'devices') { "List of devices attached`n127.0.0.1:16384`tdevice"; exit 0 }
exit 0
''', encoding="utf-8")
            result = run_ps(self, root, [ADB_SOURCE], r'''
$adb = Join-Path $scriptDir 'fake-adb.ps1'
$found = Find-AdbDevice -CurrentDevice '127.0.0.1:16384' -RequireAnyPackage @('com.hypergryph.arknights')
@{device=$found} | ConvertTo-Json
''')
            self.assertEqual(result["device"], "127.0.0.1:16384", "pm 报错属于未知，不能据此判没装")

    def test_ensure_rejects_online_device_without_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "fake-adb.ps1").write_text(r'''
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args)
$all = $Args -join ' '
Add-Content -LiteralPath (Join-Path $PSScriptRoot 'calls') -Value $all
if ($all -match 'pm path') {
    if ($all -match '16385') { 'package:/data/app/arknights/base.apk'; exit 0 }
    exit 0
}
if ($Args -contains 'devices') {
    "List of devices attached"
    "127.0.0.1:16384`tdevice"
    "127.0.0.1:16385`tdevice"
    exit 0
}
if ($Args -contains 'connect') { exit 0 }
if ($Args -contains 'disconnect') { exit 0 }
exit 0
''', encoding="utf-8")
            vm = root / "vm_config.json"
            vm.write_text(json.dumps({"vm": {"nat": {"port_forward": {
                "adb": {"host_port": 16385}}}}}), encoding="utf-8")
            result = run_ps(self, root, [ADB_SOURCE], r'''
$adb = Join-Path $scriptDir 'fake-adb.ps1'
$dev = Ensure-AdbOnline -Device '127.0.0.1:16384' -Attempts 2 -RetryDelaySec 0 `
    -VmConfigPath (Join-Path $scriptDir 'vm_config.json') -RequireAnyPackage @('com.hypergryph.arknights')
@{device=$dev; calls=@(Get-Content (Join-Path $scriptDir 'calls'))} | ConvertTo-Json
''')
            self.assertEqual(result["device"], "127.0.0.1:16385",
                             "首轮在线也要校验包名，不能直接放行没装游戏的设备")
            self.assertFalse(any("disconnect 127.0.0.1:16384" in c for c in result["calls"]),
                             "包校验不过时不能 disconnect 一台在线设备")

    def test_find_prefers_tcp_endpoint_over_emulator_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "fake-adb.ps1").write_text(r'''
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args)
if ($Args -contains 'devices') {
    "List of devices attached"
    "emulator-5554`tdevice"
    "127.0.0.1:16384`tdevice"
    exit 0
}
exit 0
''', encoding="utf-8")
            result = run_ps(self, root, [ADB_SOURCE], r'''
$adb = Join-Path $scriptDir 'fake-adb.ps1'
$found = Find-AdbDevice -CurrentDevice '127.0.0.1:16384'
@{device=$found} | ConvertTo-Json
''')
            self.assertEqual(result["device"], "127.0.0.1:16384",
                             "有 TCP 端点时不能用 emulator-5554 别名顶替")

    def test_find_accepts_only_device_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "fake-adb.ps1").write_text(r'''
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args)
if ($Args -contains 'devices') {
    "List of devices attached"
    "127.0.0.1:16384`t offline"
    "127.0.0.1:16385`t device"
    "emulator-5554`t unauthorized"
    exit 0
}
exit 0
''', encoding="utf-8")
            vm = root / "vm_config.json"
            vm.write_text(json.dumps({"vm": {"nat": {"port_forward": {
                "adb": {"host_port": 16384}}}}}), encoding="utf-8")
            result = run_ps(self, root, [ADB_SOURCE], r'''
$adb = Join-Path $scriptDir 'fake-adb.ps1'
$result = Find-AdbDevice -VmConfigPath (Join-Path $scriptDir 'vm_config.json') -CurrentDevice '127.0.0.1:16384'
@{device=$result} | ConvertTo-Json
''')
            self.assertEqual(result["device"], "127.0.0.1:16385")

    def test_find_skips_device_without_target_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "fake-adb.ps1").write_text(r'''
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args)
$all = $Args -join ' '
if ($all -match 'pm path') {
    if ($all -match '16385') { 'package:/data/app/arknights/base.apk'; exit 0 }
    exit 0
}
if ($Args -contains 'devices') {
    "List of devices attached"
    "127.0.0.1:16384`tdevice"
    "127.0.0.1:16385`tdevice"
    exit 0
}
exit 0
''', encoding="utf-8")
            vm = root / "vm_config.json"
            vm.write_text(json.dumps({"vm": {"nat": {"port_forward": {
                "adb": {"host_port": 16384}}}}}), encoding="utf-8")
            result = run_ps(self, root, [ADB_SOURCE], r'''
$adb = Join-Path $scriptDir 'fake-adb.ps1'
$found = Find-AdbDevice -VmConfigPath (Join-Path $scriptDir 'vm_config.json') `
    -CurrentDevice '127.0.0.1:16384' -RequireAnyPackage @('com.hypergryph.arknights')
@{device=$found; error=$script:LastAdbError} | ConvertTo-Json
''')
            self.assertEqual(result["device"], "127.0.0.1:16385")

    def test_ensure_rediscovers_when_port_drifted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "fake-adb.ps1").write_text(r'''
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args)
$marker = Join-Path $PSScriptRoot 'connected-16399'
$all = $Args -join ' '
if ($Args -contains 'connect') {
    if ($all -match '16399') { New-Item -ItemType File -Path $marker -Force | Out-Null }
    exit 0
}
if ($Args -contains 'disconnect') { exit 0 }
if ($Args -contains 'devices') {
    'List of devices attached'
    if (Test-Path $marker) { "127.0.0.1:16399`tdevice" }
    exit 0
}
exit 0
''', encoding="utf-8")
            vm = root / "vm_config.json"
            vm.write_text(json.dumps({"vm": {"nat": {"port_forward": {
                "adb": {"host_port": 16399}}}}}), encoding="utf-8")
            result = run_ps(self, root, [ADB_SOURCE], r'''
$adb = Join-Path $scriptDir 'fake-adb.ps1'
$dev = Ensure-AdbOnline -Device '127.0.0.1:16384' -Attempts 2 -RetryDelaySec 0 `
    -VmConfigPath (Join-Path $scriptDir 'vm_config.json')
@{device=$dev; error=$script:LastAdbError; connected=(Test-Path (Join-Path $scriptDir 'connected-16399'))} | ConvertTo-Json
''')
            self.assertEqual(result["device"], "127.0.0.1:16399")
            self.assertTrue(result["connected"], "必须真的 connect 过新端口才能算重发现")
            self.assertEqual(result["error"], "")

    def test_ensure_invokes_heartbeat_callback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "fake-adb.ps1").write_text(r'''
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args)
if ($Args -contains 'devices') { 'List of devices attached'; "fake`toffline"; exit 0 }
exit 0
''', encoding="utf-8")
            (root / "vm_config.json").write_text(json.dumps({"vm": {"nat": {
                "port_forward": {"adb": {"host_port": 16399}}}}}), encoding="utf-8")
            result = run_ps(self, root, [ADB_SOURCE], r'''
$adb = Join-Path $scriptDir 'fake-adb.ps1'
$script:heartbeats = 0
function Write-LoginHeartbeat { $script:heartbeats++ }
$dev = Ensure-AdbOnline -Device 'fake' -Attempts 3 -RetryDelaySec 0 `
    -VmConfigPath (Join-Path $scriptDir 'vm_config.json') -OnAttempt { Write-LoginHeartbeat }
@{heartbeats=$script:heartbeats; dev=$dev} | ConvertTo-Json
''')
            self.assertIsNone(result["dev"])
            self.assertGreaterEqual(result["heartbeats"], 4,
                                    "每轮探测 + 重发现前都要刷心跳，否则父进程会按超时杀掉")

    def test_boot_requires_two_consecutive_ready_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "fake-adb.ps1").write_text(r'''
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args)
if ($Args -contains 'devices') { "List of devices attached`n127.0.0.1:16384`tdevice"; exit 0 }
if ($Args -contains 'shell') {
    $p = Join-Path $PSScriptRoot 'boot-count'
    $n = 0; if (Test-Path $p) { $n = [int](Get-Content $p) }
    $n++; Set-Content $p $n
    if ($n -eq 2) { '0' } else { '1' }
    exit 0
}
exit 0
''', encoding="utf-8")
            result = run_ps(self, root, [ADB_SOURCE], r'''
$adb = Join-Path $scriptDir 'fake-adb.ps1'
$ok = Wait-AdbBootReady -Device '127.0.0.1:16384' -Samples 2 -IntervalSec 0
@{ok=$ok; samples=[int](Get-Content (Join-Path $scriptDir 'boot-count'))} | ConvertTo-Json
''')
            self.assertTrue(result["ok"])
            self.assertEqual(result["samples"], 4)

    def test_live_vm_config_wins_over_export_logs_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = (root / "vms" / "MuMuPlayer-12.0-0" / "configs")
            stale = (root / "vms" / "MuMuPlayer-12.0-0" / "data" / "exportLogs" /
                     "configs" / "current_vm")
            live.mkdir(parents=True)
            stale.mkdir(parents=True)
            (live / "vm_config.json").write_text(json.dumps({"vm": {"nat": {
                "port_forward": {"adb": {"host_port": 16385}}}}}), encoding="utf-8")
            (stale / "vm_config.json").write_text(json.dumps({"vm": {"nat": {
                "port_forward": {"adb": {"host_port": 16384}}}}}), encoding="utf-8")
            # 字典序更小的干扰目录：朴素递归搜索会先命中它
            decoy = root / "aaa" / "exportLogs" / "configs"
            decoy.mkdir(parents=True)
            (decoy / "vm_config.json").write_text(json.dumps({"vm": {"nat": {
                "port_forward": {"adb": {"host_port": 16400}}}}}), encoding="utf-8")
            (root / "nx_main").mkdir()
            (root / "nx_main" / "adb.exe").write_text("", encoding="utf-8")
            result = run_ps(self, root, [ADB_SOURCE], r'''
$p = Get-MuMuVmConfigPath -ToolPath (Join-Path $scriptDir 'nx_main\adb.exe')
@{path=$p} | ConvertTo-Json
''')
            self.assertEqual(Path(result["path"]), live / "vm_config.json")
            self.assertNotIn("exportLogs", result["path"])

    def test_prefer_port_selects_matching_instance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, port in (("MuMuPlayer-12.0-0", 16384), ("MuMuPlayer-12.0-1", 16420)):
                d = root / "vms" / name / "configs"
                d.mkdir(parents=True)
                (d / "vm_config.json").write_text(json.dumps({"vm": {"nat": {
                    "port_forward": {"adb": {"host_port": port}}}}}), encoding="utf-8")
            (root / "nx_main").mkdir()
            (root / "nx_main" / "adb.exe").write_text("", encoding="utf-8")
            result = run_ps(self, root, [ADB_SOURCE], r'''
$tool = Join-Path $scriptDir 'nx_main\adb.exe'
@{preferred=(Get-MuMuVmConfigPath -ToolPath $tool -PreferPort 16420);
  fallback=(Get-MuMuVmConfigPath -ToolPath $tool)} | ConvertTo-Json
''')
            self.assertEqual(Path(result["preferred"]).parent.parent.name, "MuMuPlayer-12.0-1")
            self.assertEqual(Path(result["fallback"]).parent.parent.name, "MuMuPlayer-12.0-0")

    def _sync_setup(self, root, schemes=None, device="old"):
        cfg = root / "config.json"
        cfg.write_text(json.dumps({"paths": {"device": device}, "accounts": []}), encoding="utf-8")
        maa = root / "maa" / "config"
        maa.mkdir(parents=True)
        data = {
            "Current": "Night",
            "Configurations": {
                name: {"Gui": {"ConnectSettings": {"Address": "old"}}} for name in (schemes or ["Night", "Default"])
            },
        }
        (maa / "gui.new.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return cfg, maa / "gui.new.json"

    def test_confirmed_device_syncs_config_and_all_maa_schemes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._sync_setup(root, schemes=["Night", "Default", "收菜"])
            result = run_ps(self, root, [SYNC_SOURCE], r'''
$ok = Sync-ConfirmedAdbDevice -ConfigPath (Join-Path $scriptDir 'config.json') `
    -ConfirmedDevice '127.0.0.1:16555' -MaaDirs (Join-Path $scriptDir 'maa')
$cfg = Get-Content (Join-Path $scriptDir 'config.json') -Raw | ConvertFrom-Json
$maa = Get-Content (Join-Path $scriptDir 'maa/config/gui.new.json') -Raw | ConvertFrom-Json
@{ok=$ok; config=$cfg.paths.device;
  night=$maa.Configurations.Night.Gui.ConnectSettings.Address;
  default=$maa.Configurations.Default.Gui.ConnectSettings.Address;
  collect=$maa.Configurations.'收菜'.Gui.ConnectSettings.Address;
  warning=$script:LastAdbSyncWarning;
  configBackup=(Test-Path (Join-Path $scriptDir 'config.json.adb.bak'));
  maaBackup=(Test-Path (Join-Path $scriptDir 'maa/config/gui.new.json.adb.bak'))} | ConvertTo-Json
''')
            self.assertTrue(result["ok"])
            self.assertEqual(result["config"], "127.0.0.1:16555")
            self.assertEqual(result["night"], "127.0.0.1:16555")
            self.assertEqual(result["default"], "127.0.0.1:16555")
            self.assertEqual(result["collect"], "127.0.0.1:16555", "所有方案都要改，不能只改 Current/Default")
            self.assertEqual(result["warning"], "")
            self.assertTrue(result["configBackup"])
            self.assertTrue(result["maaBackup"])
            # 备份必须是「写前内容」，不是随便一个空文件
            backup_cfg = json.loads((root / "config.json.adb.bak").read_text(encoding="utf-8"))
            backup_maa = json.loads((root / "maa" / "config" / "gui.new.json.adb.bak").read_text(encoding="utf-8"))
            self.assertEqual(backup_cfg["paths"]["device"], "old")
            self.assertEqual(backup_maa["Configurations"]["Default"]["Gui"]["ConnectSettings"]["Address"], "old")

    def test_sync_is_noop_when_address_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg, gui = self._sync_setup(root, device="127.0.0.1:16385")
            for p in (cfg, gui):
                data = json.loads(p.read_text(encoding="utf-8"))
                if p is cfg:
                    data["paths"]["device"] = "127.0.0.1:16385"
                else:
                    for conf in data["Configurations"].values():
                        conf["Gui"]["ConnectSettings"]["Address"] = "127.0.0.1:16385"
                p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            before = {p: p.read_bytes() for p in (cfg, gui)}
            result = run_ps(self, root, [SYNC_SOURCE], r'''
$ok = Sync-ConfirmedAdbDevice -ConfigPath (Join-Path $scriptDir 'config.json') `
    -ConfirmedDevice '127.0.0.1:16385' -MaaDirs (Join-Path $scriptDir 'maa')
@{ok=$ok;
  configBackup=(Test-Path (Join-Path $scriptDir 'config.json.adb.bak'));
  maaBackup=(Test-Path (Join-Path $scriptDir 'maa/config/gui.new.json.adb.bak'))} | ConvertTo-Json
''')
            self.assertTrue(result["ok"])
            self.assertFalse(result["configBackup"], "地址未变不应重写 config")
            self.assertFalse(result["maaBackup"], "地址未变不应重写 MAA 配置")
            for path, payload in before.items():
                self.assertEqual(path.read_bytes(), payload, f"{path.name} 内容被无谓改写")

    def test_broken_maa_config_keeps_config_synced_and_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root / "config.json"
            cfg.write_text(json.dumps({"paths": {"device": "old"}, "accounts": []}), encoding="utf-8")
            maa = root / "maa" / "config"
            maa.mkdir(parents=True)
            (maa / "gui.new.json").write_text("{ broken", encoding="utf-8")
            result = run_ps(self, root, [SYNC_SOURCE], r'''
$ok = Sync-ConfirmedAdbDevice -ConfigPath (Join-Path $scriptDir 'config.json') `
    -ConfirmedDevice '127.0.0.1:16555' -MaaDirs (Join-Path $scriptDir 'maa')
$cfg = Get-Content (Join-Path $scriptDir 'config.json') -Raw | ConvertFrom-Json
@{ok=$ok; device=$cfg.paths.device; warning=$script:LastAdbSyncWarning} | ConvertTo-Json
''')
            self.assertTrue(result["ok"], "MAA 坏掉不应让整轮挂机 FATAL")
            self.assertEqual(result["device"], "127.0.0.1:16555", "ADB 地址仍必须写进项目配置")
            self.assertIn("MAA", result["warning"])

    def test_sync_fails_when_config_cannot_be_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg, _ = self._sync_setup(root)
            # 用「非空同名目录」占住临时文件路径：WriteAllText 失败且删不掉，
            # 稳定模拟文件被占用/只读（空目录会被清理后重试成功）
            blocked = root / "config.json.adb.tmp"
            blocked.mkdir()
            (blocked / "keep").write_text("x", encoding="utf-8")
            result = run_ps(self, root, [SYNC_SOURCE], r'''
$ok = Sync-ConfirmedAdbDevice -ConfigPath (Join-Path $scriptDir 'config.json') `
    -ConfirmedDevice '127.0.0.1:16555' -MaaDirs (Join-Path $scriptDir 'maa')
$cfg = Get-Content (Join-Path $scriptDir 'config.json') -Raw | ConvertFrom-Json
$maa = Get-Content (Join-Path $scriptDir 'maa/config/gui.new.json') -Raw | ConvertFrom-Json
@{ok=$ok; device=$cfg.paths.device; maa=$maa.Configurations.Default.Gui.ConnectSettings.Address} | ConvertTo-Json
''')
            self.assertFalse(result["ok"], "config 写不进去必须报失败")
            self.assertEqual(result["device"], "old", "写失败时原文件不能被改坏")
            self.assertEqual(result["maa"], "127.0.0.1:16555",
                             "config 写失败也要继续写 MAA，否则整轮 MAA 都连旧端口")

    def test_maa_write_failure_keeps_config_and_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._sync_setup(root)
            blocked = root / "maa" / "config" / "gui.new.json.adb.tmp"
            blocked.mkdir()
            (blocked / "keep").write_text("x", encoding="utf-8")
            result = run_ps(self, root, [SYNC_SOURCE], r'''
$ok = Sync-ConfirmedAdbDevice -ConfigPath (Join-Path $scriptDir 'config.json') `
    -ConfirmedDevice '127.0.0.1:16555' -MaaDirs (Join-Path $scriptDir 'maa')
$cfg = Get-Content (Join-Path $scriptDir 'config.json') -Raw | ConvertFrom-Json
$maa = Get-Content (Join-Path $scriptDir 'maa/config/gui.new.json') -Raw | ConvertFrom-Json
@{ok=$ok; device=$cfg.paths.device; maa=$maa.Configurations.Default.Gui.ConnectSettings.Address;
  warning=$script:LastAdbSyncWarning} | ConvertTo-Json
''')
            self.assertTrue(result["ok"], "MAA 写失败不应阻断挂机")
            self.assertEqual(result["device"], "127.0.0.1:16555")
            self.assertEqual(result["maa"], "old", "写失败时 MAA 文件保持原样")
            self.assertIn("MAA", result["warning"])

    def test_sync_warns_when_maa_config_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root / "config.json"
            cfg.write_text(json.dumps({"paths": {"device": "127.0.0.1:16555"}, "accounts": []}), encoding="utf-8")
            (root / "maa" / "config").mkdir(parents=True)   # 目录在，gui.new.json 不在
            result = run_ps(self, root, [SYNC_SOURCE], r'''
$ok = Sync-ConfirmedAdbDevice -ConfigPath (Join-Path $scriptDir 'config.json') `
    -ConfirmedDevice '127.0.0.1:16555' -MaaDirs (Join-Path $scriptDir 'maa')
$cfg = Get-Content (Join-Path $scriptDir 'config.json') -Raw | ConvertFrom-Json
@{ok=$ok; device=$cfg.paths.device; warning=$script:LastAdbSyncWarning} | ConvertTo-Json
''')
            self.assertTrue(result["ok"])
            self.assertEqual(result["device"], "127.0.0.1:16555")
            self.assertIn("MAA", result["warning"], "地址已同址时也必须因缺 gui.new.json 告警")

    def test_sync_fails_when_config_unreadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root / "config.json"
            cfg.write_text("{ not json", encoding="utf-8")
            result = run_ps(self, root, [SYNC_SOURCE], r'''
$ok = Sync-ConfirmedAdbDevice -ConfigPath (Join-Path $scriptDir 'config.json') `
    -ConfirmedDevice '127.0.0.1:16555'
@{ok=$ok} | ConvertTo-Json
''')
            self.assertFalse(result["ok"], "配置读不出来时必须报失败，不能当成已同步")

    def test_sync_creates_missing_paths_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root / "config.json"
            cfg.write_text(json.dumps({"accounts": []}), encoding="utf-8")
            result = run_ps(self, root, [SYNC_SOURCE], r'''
$ok = Sync-ConfirmedAdbDevice -ConfigPath (Join-Path $scriptDir 'config.json') `
    -ConfirmedDevice '127.0.0.1:16385'
$cfg = Get-Content (Join-Path $scriptDir 'config.json') -Raw | ConvertFrom-Json
@{ok=$ok; device=$cfg.paths.device} | ConvertTo-Json
''')
            self.assertTrue(result["ok"])
            self.assertEqual(result["device"], "127.0.0.1:16385")

    def test_start_mumu_continues_when_sync_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "fake-adb.cmd").write_text("@echo off\r\nexit /b 0\r\n", encoding="ascii")
            (root / "fake-cli.cmd").write_text("@echo off\r\nexit /b 0\r\n", encoding="ascii")
            result = run_ps(self, root, ["scripts/master_lib.ps1", "scripts/adb_lib.ps1", "scripts/adb_sync.ps1"], r'''
$adb = Join-Path $scriptDir 'fake-adb.cmd'
$cli = Join-Path $scriptDir 'fake-cli.cmd'
$device = '127.0.0.1:16384'
$mumuLaunchTimeoutSec = 3
$configPath = Join-Path $scriptDir 'config.json'
$config = $null
$maaOfficialDir = ''
$maaBilibiliDir = ''
function Get-Process { return $null }
function Find-AdbDevice { param($VmConfigPath, $CurrentDevice, $RequireAnyPackage) return '127.0.0.1:16385' }
function Wait-AdbBootReady { param($Device, $Samples, $IntervalSec, $TimeoutSec) return $true }
function Test-AdbOnline { param($Device) return $true }
function Sync-ConfirmedAdbDevice {
    param($ConfigPath, $ConfirmedDevice, $MaaDirs)
    $script:LastAdbSyncWarning = 'config.json 写入失败（模拟）'
    return $false
}
$ok = Start-MuMu
@{ok=$ok; warning=$script:LastAdbSyncWarning; device=$device} | ConvertTo-Json
''')
            self.assertTrue(result["ok"], "config 写不进时不能判整轮 FATAL")
            self.assertEqual(result["device"], "127.0.0.1:16385", "Start-MuMu 必须把新地址写回脚本作用域")
            self.assertIn("config.json", result["warning"])

    def test_device_states_tolerate_extra_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "fake-adb.ps1").write_text(r'''
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args)
if ($Args -contains 'devices') {
    "List of devices attached"
    "127.0.0.1:16384`toffline transport_id:2"
    "127.0.0.1:16385`tdevice transport_id:4"
    exit 0
}
exit 0
''', encoding="utf-8")
            result = run_ps(self, root, [ADB_SOURCE], r'''
$adb = Join-Path $scriptDir 'fake-adb.ps1'
$s = Get-AdbDeviceStates
@{count=$s.Count; a=$s['127.0.0.1:16384']; b=$s['127.0.0.1:16385']} | ConvertTo-Json
''')
            self.assertEqual(result["count"], 2)
            self.assertEqual(result["a"], "offline")
            self.assertEqual(result["b"], "device")


if __name__ == "__main__":
    unittest.main()
