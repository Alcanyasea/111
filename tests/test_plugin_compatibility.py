import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins.account_tasks import account_tasks as tasks
from plugins.farm_guard import farm_guard
from plugins.fiammetta import fiammetta
from plugins.fight_stage import fight_stage
from plugins.infrast_collect import infrast_collect as collect
from tests.ps_harness import run_ps
from tests.test_base_schedule import bs


class PluginCompatibilityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'config').mkdir()
        self.gui = self.root / 'config/gui.new.json'
        self.legacy = self.root / 'config/gui.json'
        self.data = {'Current': 'Default', 'Configurations': {'Default': {'TaskQueue': [
            {'$type': 'InfrastTask', 'RoomList': [{'Room': 'Recycle', 'IsEnabled': False}]},
            {'$type': 'FightTask'}, {'$type': 'FightTask'}]}}}
        self.gui.write_text(json.dumps(self.data), encoding='utf-8')
        self.config = self.root / 'console.json'
        self.account = {'id': 'fixture', 'second_fight_stage': '1-7',
                        'base_schedule': {'enabled': True, 'fiammetta': {'enable': True, 'target': '清流'}}}
        self.set_maa_path(self.root)
        self.enter_patch(collect, 'PLUGIN_DIR', self.root)
        self.enter_patch(bs, 'PLANS_DIR', self.root / 'plans')

    def enter_patch(self, module, name, value):
        override = patch.object(module, name, value)
        override.start()
        self.addCleanup(override.stop)

    def set_maa_path(self, directory):
        self.config.write_text(json.dumps({'accounts': [self.account],
            'paths': {'maa_official_dir': str(directory)}}), encoding='utf-8')

    def cli(self, module):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = module.main(['apply', '--config', str(self.config), '--account', 'fixture',
                                '--server', 'official'])
        return code, output.getvalue()

    def test_account_tasks_and_collect_use_unversioned_installation(self):
        settings = {'depot': {'enabled': True, 'plans': [{'stage': '1-7', 'drop_id': '30012', 'target': 10}]}}
        (self.root / 'MAA.exe').touch()
        with patch.object(tasks, 'maa_running', return_value=False):
            tasks.preflight(self.root, settings)
            tasks.apply(self.root, settings)
            self.assertEqual(tasks.read_json(self.root / 'data/DepotData.json'), {'done': False, 'data': {}})
            self.assertTrue(collect.apply_collect(self.root)[0])
            schemes = tasks.read_json(self.gui)['Configurations']
            self.assertEqual(schemes['Default'], self.data['Configurations']['Default'])
            task = schemes['收菜']['TaskQueue'][0]
            self.assertTrue(next(r['IsEnabled'] for r in task['RoomList'] if r['Room'] == 'Recycle'))
            self.assertTrue(tasks.read_json(task['Filename'])['plans'][0]['rooms']['recycle'][0]['skip'])

    def test_activity_is_listed_from_resources_without_version_lookup(self):
        for folder in ['resource', 'cache/gui']:
            (self.root / folder).mkdir(parents=True)
        (self.root / 'resource/item_index.json').write_text('{"1":{"name":"rock"}}')
        (self.root / 'resource/battle_data.json').write_text('{}')
        group = {'Activity': {'UtcStartTime': '2000/01/01 00:00:00', 'UtcExpireTime': '2099/12/31 23:59:59',
                              'TimeZone': 8}, 'MinimumRequired': 'v99.0.0', 'Stages': [{'Drop': '1', 'Value': 'A-1'}]}
        (self.root / 'cache/gui/StageActivityV2.json').write_text(json.dumps({'Official': {'sideStoryStage': {'event': group}}}))
        metadata = tasks.load_metadata(self.root)
        self.assertEqual(metadata['errors'], [])
        self.assertEqual(metadata['activities'][0][1][0]['stage'], 'A-1')

    def test_explicit_plugins_report_missing_directory(self):
        self.set_maa_path(self.root / 'absent')
        for plugin in [bs, fiammetta, fight_stage, farm_guard, collect]:
            with self.subTest(plugin=plugin):
                code, output = self.cli(plugin)
                self.assertNotEqual(code, 0)
                self.assertIn('absent', output)

    def test_fiammetta_reports_missing_task_or_bad_json(self):
        for content, expected in [('{}', 'InfrastTask'), ('{', 'gui.new.json')]:
            self.gui.write_text(content)
            code, output = self.cli(fiammetta)
            self.assertNotEqual(code, 0)
            self.assertIn(expected, output)
            self.assertEqual(self.gui.read_text(), content)

    def test_disabled_stock_does_not_enforce_stock_order(self):
        queue = self.data['Configurations']['Default']['TaskQueue']
        queue[-1]['StagePlan'] = ['Annihilation']
        self.data['Configurations'][tasks.SCHEME] = {}
        result = tasks.build_configuration(self.data, {})
        self.assertEqual(result['Configurations'][tasks.SCHEME]['TaskQueue'], queue)
        enabled = {'depot': {'enabled': True, 'plans': [{'stage': '1-7', 'drop_id': '1', 'target': 10}]}}
        with self.assertRaisesRegex(ValueError, '剿灭'):
            tasks.build_configuration(self.data, enabled)

    def test_failed_gui_replace_preserves_even_invalid_cache_bytes(self):
        cache = self.root / 'data/DepotData.json'
        cache.parent.mkdir()
        cache.write_bytes(b'{partial')
        before = self.gui.read_bytes()
        replace = os.replace

        def fail_gui(source, target):
            if Path(target) == self.gui:
                raise OSError('fixture write conflict')
            replace(source, target)

        settings = {'depot': {'enabled': True, 'plans': [{'stage': '1-7', 'drop_id': '1', 'target': 10}]}}
        with patch.object(tasks, 'maa_running', return_value=False), patch.object(os, 'replace', side_effect=fail_gui):
            with self.assertRaisesRegex(OSError, 'fixture write conflict'):
                tasks.apply(self.root, settings)
        self.assertEqual(self.gui.read_bytes(), before)
        self.assertEqual(cache.read_bytes(), b'{partial')

    def test_corrupt_console_config_reports_file_before_account_lookup(self):
        self.config.write_text('{broken')
        for plugin in [bs, fiammetta, fight_stage, collect, tasks]:
            with self.subTest(plugin=plugin):
                code, output = self.cli(plugin)
                self.assertNotEqual(code, 0)
                self.assertIn(self.config.name, output)

    def test_old_facility_restore_state_without_rooms_is_accepted(self):
        state = self.gui.with_name('maa-console-training.json')
        state.write_text('{"version": 1, "schemes": {}}')
        self.assertIn('gui.new.json', bs.apply_maa_config(self.root, None))

    def test_collect_noop_restore_allows_running_maa_but_blocks_actual_write(self):
        with patch.object(collect, 'ensure_maa_stopped', side_effect=ValueError('fixture MAA busy')) as guard:
            self.assertTrue(collect.restore_default(self.root)[0])
            guard.assert_not_called()
            self.data['Current'] = '收菜'
            self.gui.write_text(json.dumps(self.data))
            before = self.gui.read_bytes()
            ok, message = collect.restore_default(self.root)
            self.assertFalse(ok)
            self.assertIn('fixture MAA busy', message)
            self.assertEqual(before, self.gui.read_bytes())

    def test_collect_without_recycle_uses_standard_plan(self):
        self.data['Configurations']['Default']['TaskQueue'][0]['RoomList'] = []
        self.gui.write_text(json.dumps(self.data))
        ok, message = collect.apply_collect(self.root)
        self.assertTrue(ok, message)
        task = tasks.read_json(self.gui)['Configurations']['收菜']['TaskQueue'][0]
        self.assertNotIn('Recycle', [r['Room'] for r in task['RoomList']])
        self.assertNotIn('recycle', tasks.read_json(task['Filename'])['plans'][0]['rooms'])

    def test_farm_guard_second_write_failure_restores_first(self):
        self.data['Current'] = '收菜'
        self.data['Configurations']['Default']['TaskQueue'] = [{'$type': name} for name in farm_guard.KEY_TASKS]
        self.gui.write_text(json.dumps(self.data), encoding='utf-8-sig')
        self.legacy.write_text('{"Current": "收菜"}', encoding='utf-8')
        before = self.gui.read_bytes(), self.legacy.read_bytes()
        replace = os.replace

        def fail_legacy(source, target):
            if Path(target) == self.legacy:
                raise OSError('fixture legacy write failed')
            replace(source, target)

        with patch.object(os, 'replace', side_effect=fail_legacy):
            ok, message = farm_guard.apply_guard(self.root)
        self.assertFalse(ok)
        self.assertIn('fixture legacy write failed', message)
        self.assertEqual((self.gui.read_bytes(), self.legacy.read_bytes()), before)

    def test_legacy_config_optional_but_corruption_prevents_partial_switch(self):
        self.gui.write_text(json.dumps(self.data), encoding='utf-8-sig')
        ok, message = collect.apply_collect(self.root)
        self.assertTrue(ok, message)
        ok, message = collect.restore_default(self.root)
        self.assertTrue(ok, message)
        self.assertTrue(collect.restore_default(self.root)[0])
        self.legacy.write_text('{')
        for action, current in [(collect.apply_collect, 'Default'), (collect.restore_default, '收菜')]:
            self.data['Current'] = current
            self.gui.write_text(json.dumps(self.data))
            before = self.gui.read_bytes()
            ok, message = action(self.root)
            self.assertFalse(ok)
            self.assertIn('gui.json', message)
            self.assertEqual(self.gui.read_bytes(), before)

    def test_master_keeps_plugin_failure_details(self):
        fixture = self.root / 'plugin.py'
        fixture.write_text("raise ValueError('fixture detail')")
        cases = run_ps(self, self.root, ['scripts/master_plugins.ps1'], r'''
$venvPython = $env:FIXTURE_PYTHON
$path = Join-Path $root 'plugin.py'
$null = Invoke-Plugin $path 'test' 'test' @() 'stop'
$crash = $script:LastPluginError
$null = Invoke-Plugin (Join-Path $root 'absent.py') 'test' 'test' @() 'stop'
$missing = $script:LastPluginError
[IO.File]::WriteAllText($path, 'raise SystemExit(3)')
$null = Invoke-Plugin $path 'test' 'test' @() 'stop'
@{crash=$crash; missing=$missing; silent=$script:LastPluginError} | ConvertTo-Json
''', env={'FIXTURE_PYTHON': sys.executable})
        self.assertIn('ValueError: fixture detail', cases['crash'])
        self.assertIn('absent.py', cases['missing'])
        self.assertIn('退出码 3', cases['silent'])
