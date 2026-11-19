import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from plugins.account_tasks import account_tasks as tasks


class AccountTasksTests(unittest.TestCase):
    def settings(self):
        return {'depot': {'enabled': True, 'plans': [
            {'stage': '1-7', 'drop_id': '30012', 'target': 100}]},
            'progress': {'enabled': True, 'plans': [
                {'name': '阿米娅', 'role': 'Caster', 'elite': 2,
                 'skill_level': 7, 'mastery': [0, 0, 0]}]}}

    def data(self):
        return {'Current': 'Default', 'Configurations': {'Default': {
            'TaskQueue': [{'$type': 'StartUpTask'},
                          {'$type': 'FightTask', 'StagePlan': ['Annihilation']},
                          {'$type': 'FightTask', 'StagePlan': ['1-7']},
                          {'$type': 'DepotMaintainTask', 'Name': 'manual', 'IsEnable': True}]}}}

    def test_translation_safe_spend_and_order_preserves_default(self):
        data = self.data()
        original = copy.deepcopy(data['Configurations']['Default'])
        out = tasks.build_configuration(data, self.settings())
        self.assertEqual(out['Configurations']['Default'], original)
        queue = out['Configurations'][tasks.SCHEME]['TaskQueue']
        types = [t['$type'] for t in queue]
        self.assertEqual(types[:5], ['StartUpTask', 'FightTask', 'DepotMaintainTask', 'OperProgressTask', 'FightTask'])
        depot = queue[2]
        self.assertTrue(depot['UpdateDepot'])
        self.assertFalse(depot['UseMedicine'])
        self.assertFalse(depot['UseStone'])
        self.assertEqual(depot['PlanList'][0]['MedicineCount'], 0)
        self.assertEqual(queue[3]['Plans'][0]['SkillMastery'], [0, 0, 0])
        self.assertFalse(queue[-1]['IsEnable'])

    def test_disabled_account_never_inherits_owned_plans(self):
        first = tasks.build_configuration(self.data(), self.settings())
        second = tasks.build_configuration(first, {})
        queue = second['Configurations'][tasks.SCHEME]['TaskQueue']
        self.assertFalse(any(t.get('IsEnable') for t in queue if t['$type'] in tasks.MANAGED_TYPES))
        self.assertFalse(any(t.get('Name', '').startswith(tasks.PREFIX) for t in queue))

    def test_missing_settings_and_no_previous_managed_scheme_is_noop(self):
        data = self.data()
        self.assertEqual(tasks.build_configuration(data, {}), data)

    def test_invalid_plans_rejected(self):
        for field, value in [('target', -1), ('medicine', 10000), ('stage', '')]:
            settings = self.settings()
            settings['depot']['plans'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                tasks.normalize_settings(settings)
        settings = self.settings()
        settings['progress']['plans'][0]['mastery'] = [3, 0, 0]
        settings['progress']['plans'][0]['elite'] = 1
        with self.assertRaises(ValueError):
            tasks.normalize_settings(settings)

    def test_sort_by_progress_is_explicitly_rejected_for_safe_isolation(self):
        settings = self.settings()
        settings['depot']['sort_by_progress'] = True
        with self.assertRaisesRegex(ValueError, '达成率排序暂不可用'):
            tasks.normalize_settings(settings)

    def test_native_role_is_enum_name_and_unknown_fields_are_preserved(self):
        settings = self.settings()
        settings['future'] = {'keep': True}
        settings['depot']['future'] = 'yes'
        settings['depot']['plans'][0]['future'] = 42
        normalized = tasks.normalize_settings(settings)
        self.assertEqual(normalized['future'], {'keep': True})
        self.assertEqual(normalized['depot']['future'], 'yes')
        self.assertEqual(normalized['depot']['plans'][0]['future'], 42)
        self.assertEqual(tasks.managed_tasks(normalized)[1]['Plans'][0]['Role'], 'Caster')
        tasks.build_configuration(self.data(), settings)

    def test_current_activity_uses_real_resource_metadata_and_local_timezone(self):
        group = {'Activity': {'StageName': '当前活动', 'TimeZone': 8,
                              'UtcStartTime': '2026/11/01 16:00:00',
                              'UtcExpireTime': '2026/11/20 03:59:59'},
                 'MinimumRequired': 'v6.22.0',
                 'Stages': [{'Value': 'TEST-7', 'Drop': '30012'},
                            {'Value': 'TEST-8', 'Drop': 'text-only'}]}
        metadata = {'Official': {'sideStoryStage': {'test': group}}}
        now = datetime(2026, 11, 13, tzinfo=timezone.utc)
        items = {'30012': {'name': '固源岩'}}
        choices = tasks.activity_presets(metadata, items, now)
        self.assertEqual(choices[0][0], '当前活动')
        self.assertEqual(choices[0][1][0]['drop_name'], '固源岩')
        self.assertEqual(len(choices[0][1]), 1)
        self.assertEqual(tasks.activity_presets(metadata, items, datetime(2027, 1, 1, tzinfo=timezone.utc)), [])

    def test_metadata_loader_uses_cache_gui_and_item_index_and_battle_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'resource').mkdir()
            (root / 'cache/gui').mkdir(parents=True)
            (root / 'resource/item_index.json').write_text('{"30012":{"name":"rock"}}', encoding='utf-8')
            (root / 'resource/battle_data.json').write_text('{"chars":{"char_002_test":{"name":"test","profession":"CASTER","rarity":5}}}', encoding='utf-8')
            (root / 'cache/gui/StageActivityV2.json').write_text('{"Official":{"sideStoryStage":{}}}', encoding='utf-8')
            metadata = tasks.load_metadata(root)
            self.assertEqual(metadata['operators']['test']['role'], 'Caster')
            self.assertEqual(metadata['items']['30012']['name'], 'rock')
            self.assertEqual(metadata['activities'], [])
            self.assertEqual(metadata['errors'], [])

    def test_preflight_does_not_write_and_rejects_bad_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'config').mkdir()
            path = root / 'config/gui.new.json'
            path.write_text(json.dumps(self.data()), encoding='utf-8')
            before = path.read_bytes()
            with patch.object(tasks, 'maa_running', return_value=False):
                tasks.preflight(root, self.settings())
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(root.rglob('*.bak')), [])

    def test_atomic_failure_restores_both_cache_and_gui(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'config').mkdir()
            (root / 'data').mkdir()
            gui, cache = root / 'config/gui.new.json', root / 'data/DepotData.json'
            gui.write_text(json.dumps(self.data()), encoding='utf-8')
            cache.write_text('{"done":true,"data":{"30012":99}}', encoding='utf-8')
            original_gui, original_cache = tasks.read_json(gui), tasks.read_json(cache)
            writer = os.replace
            calls = []
            def fail_once(path, value):
                calls.append(path)
                if len(calls) == 2:
                    raise OSError('replace blocked')
                return writer(path, value)
            with patch.object(tasks, 'maa_running', return_value=False), patch.object(os, 'replace', side_effect=fail_once):
                with self.assertRaises(OSError):
                    tasks.apply(root, self.settings())
            self.assertEqual(tasks.read_json(gui), original_gui)
            self.assertEqual(tasks.read_json(cache), original_cache)

    def test_apply_backup_fresh_cache_and_no_partial_write_on_active_maa(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'config').mkdir()
            (root / 'data').mkdir()
            gui = root / 'config/gui.new.json'
            gui.write_text(json.dumps(self.data()), encoding='utf-8')
            cache = root / 'data/DepotData.json'
            cache.write_text('{"done":true,"data":{"30012":999}}', encoding='utf-8')
            before = gui.read_bytes()
            with patch.object(tasks, 'maa_running', return_value=True):
                with self.assertRaises(ValueError):
                    tasks.apply(root, self.settings())
            self.assertEqual(before, gui.read_bytes())
            with patch.object(tasks, 'maa_running', return_value=False), patch('shutil.copy2', side_effect=OSError('backup blocked')):
                with self.assertRaises(OSError):
                    tasks.apply(root, self.settings())
            self.assertEqual(before, gui.read_bytes())
            self.assertEqual(json.loads(cache.read_text())['data']['30012'], 999)
            with patch.object(tasks, 'maa_running', return_value=False):
                tasks.apply(root, self.settings())
            self.assertEqual(json.loads(cache.read_text()), {'done': False, 'data': {}})
            self.assertTrue(list(root.rglob('*.bak')))

    def test_restore_only_switches_owned_scheme_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'config').mkdir()
            path = root / 'config' / 'gui.new.json'
            data = {'Current': tasks.SCHEME, 'Configurations': {'Default': {}, tasks.SCHEME: {}}}
            path.write_text(json.dumps(data), encoding='utf-8')
            old = path.read_bytes()
            with patch.object(tasks, 'load_config', return_value={'paths': {}}), \
                 patch.object(tasks, 'maa_dir_for', return_value=root), \
                 patch.object(tasks, 'maa_running', return_value=False):
                self.assertEqual(tasks.main(['restore', '--config', str(root / 'config.json')]), 0)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['Current'], 'Default')
            self.assertTrue(any(p.name.endswith('.bak') for p in root.joinpath('config').iterdir()))
            self.assertNotEqual(old, path.read_bytes())


if __name__ == '__main__':
    unittest.main()
