import unittest
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from plugins import maa_compat
from plugins.account_tasks import account_tasks as tasks
from tests.ps_harness import run_ps

class MaaCompatibilityTest(unittest.TestCase):
    def test_directory_junction_cannot_bypass_running_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / 'real'
            real.mkdir()
            (real / 'MAA.exe').touch()
            run_ps(self, root, [], r'''
$null = New-Item -ItemType Junction -Path (Join-Path $scriptDir 'alias') -Target (Join-Path $scriptDir 'real')
@{ok=$true} | ConvertTo-Json
''')
            responses = [SimpleNamespace(stdout='"MAA.exe","123"'),
                         SimpleNamespace(stdout=json.dumps([{'ExecutablePath': str(real / 'MAA.exe')}]))]
            with patch.object(maa_compat.subprocess, 'run', side_effect=responses):
                with self.assertRaises(ValueError):
                    maa_compat.ensure_maa_stopped(root / 'alias')

    def test_account_tasks_allow_other_installation_through_apply_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'other').mkdir()
            (root / 'config').mkdir()
            gui = root / 'config/gui.new.json'
            data = {'Current': 'Default', 'Configurations': {'Default': {'TaskQueue': [
                {'$type': 'StartUpTask'}, {'$type': 'FightTask', 'StagePlan': ['1-7']}]}}}
            gui.write_text(json.dumps(data), encoding='utf-8')
            settings = {'depot': {'enabled': True, 'plans': [
                {'stage': '1-7', 'drop_id': '30012', 'target': 100}]}}

            def processes(args, **kwargs):
                if args[0] == 'tasklist':
                    return SimpleNamespace(stdout='"MAA.exe","123"')
                return SimpleNamespace(stdout=json.dumps([
                    {'ExecutablePath': str(root / 'other/MAA.exe')}]))

            with patch.object(tasks.subprocess, 'run', side_effect=processes), \
                 patch.object(tasks, 'load_config', return_value={'paths': {}}), \
                 patch.object(tasks, 'maa_dir_for', return_value=root):
                tasks.preflight(root, settings)
                tasks.apply(root, settings)
                self.assertEqual(tasks.read_json(gui)['Current'], tasks.SCHEME)
                self.assertEqual(tasks.main(['restore', '--config', str(root / 'fake.json')]), 0)
                self.assertEqual(tasks.read_json(gui)['Current'], 'Default')

    def test_other_installation_does_not_block_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'backup').mkdir()
            (root / 'MAA.exe').touch()
            os.link(root / 'MAA.exe', root / 'backup/MAA.exe')
            responses = [SimpleNamespace(stdout='"MAA.exe","123"'),
                         SimpleNamespace(stdout=json.dumps([{'ExecutablePath': str(root / 'backup/MAA.exe')}]))]
            with patch.object(maa_compat.subprocess, 'run', side_effect=responses):
                maa_compat.ensure_maa_stopped(root)

    def test_same_installation_and_unknown_path_still_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'MAA.exe').touch()
            for path in (str(root / 'MAA.exe').upper(), None):
                with self.subTest(path=path):
                    responses = [SimpleNamespace(stdout='"MAA.exe","123"'),
                                 SimpleNamespace(stdout=json.dumps([{'ExecutablePath': path}]))]
                    with patch.object(maa_compat.subprocess, 'run', side_effect=responses):
                        with self.assertRaises(ValueError):
                            maa_compat.ensure_maa_stopped(root)

    def test_active_process_and_unavailable_enumeration_reject_write(self):
        with patch.object(maa_compat.subprocess,'run',return_value=SimpleNamespace(stdout='"MAA.exe","123"')):
            with self.assertRaises(ValueError): maa_compat.ensure_maa_stopped()
        with patch.object(maa_compat.subprocess,'run',side_effect=OSError('tasklist unavailable')):
            with self.assertRaises(ValueError): maa_compat.ensure_maa_stopped()
        with patch.object(maa_compat.subprocess,'run',return_value=SimpleNamespace(stdout='INFO: no tasks')):
            maa_compat.ensure_maa_stopped()
