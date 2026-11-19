"""Exercise facility configuration without consulting executable version metadata."""
import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_base_schedule import bs


class BaseVersionCompatibilityTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'config').mkdir()
        (self.root / 'MAA.exe').touch()
        self.gui = self.root / 'config/gui.new.json'
        self.data = {'Current': 'Default', 'Configurations': {'Default': {'TaskQueue': [
            {'$type': 'InfrastTask', 'ContinueTraining': True,
             'RoomList': [{'Room': 'Training'}, {'Room': 'Recycle', 'IsEnabled': False}]}]}}}
        self.gui.write_text(json.dumps(self.data), encoding='utf-8')

    def run_cli(self, **extra):
        cfg = {'accounts': [{'id': 'fixture', 'label': '测试账号', 'server': 'official',
                             'base_schedule': {'enabled': True, 'batches': {
                                 '4点': {'recycle': ['测试干员'], 'continue_training': False}}}}]}
        output = io.StringIO()
        with patch.object(bs, 'load_config', return_value=cfg), \
             patch.object(bs, '_maa_dir', return_value=self.root), \
             patch.object(bs, 'regenerate_for_account', return_value=self.root / 'plan.json'), \
             patch.object(bs, '_log_file'), \
             patch.object(bs, 'ensure_maa_stopped'), \
             patch('ctypes.WinDLL', side_effect=AssertionError('unexpected version lookup'), create=True), \
             contextlib.redirect_stdout(output):
            if extra.get('write_error'):
                with patch.object(bs, 'write_transaction', side_effect=OSError('备份失败（测试）')):
                    code = bs.cmd_apply(argparse.Namespace(config='fake', account='fixture', server='official', batch='4点', disable=False))
            else:
                code = bs.cmd_apply(argparse.Namespace(config='fake', account='fixture', server='official', batch='4点', disable=False))
        return code, output.getvalue()

    def test_requested_room_applies_and_restores_without_version_metadata(self):
        code, output = self.run_cli()
        self.assertEqual(code, 0, output)
        task = json.loads(self.gui.read_text(encoding='utf-8'))['Configurations']['Default']['TaskQueue'][0]
        self.assertEqual(task['Mode'], 'Custom')
        self.assertTrue(next(r for r in task['RoomList'] if r['Room'] == 'Recycle')['IsEnabled'])
        self.assertFalse(task['ContinueTraining'])
        self.assertTrue(list(self.gui.parent.glob('*.bak')))
        bs.apply_maa_config(self.root, None)
        task = json.loads(self.gui.read_text(encoding='utf-8'))['Configurations']['Default']['TaskQueue'][0]
        self.assertFalse(next(r for r in task['RoomList'] if r['Room'] == 'Recycle')['IsEnabled'])
        self.assertTrue(task['ContinueTraining'])

    def test_missing_room_does_not_get_invented_or_reported_as_unreadable(self):
        self.data['Configurations']['Default']['TaskQueue'][0]['RoomList'] = [{'Room': 'Training'}]
        self.gui.write_text(json.dumps(self.data), encoding='utf-8')
        before = self.gui.read_bytes()
        code, output = self.run_cli()
        self.assertNotEqual(code, 0)
        self.assertIn('Recycle', output)
        self.assertNotIn('maa config unreadable', output)
        self.assertEqual(self.gui.read_bytes(), before)

    def test_configured_installation_folder_can_have_any_version_label(self):
        for label in ('MAA-v5.0.0-win-x64', 'MAA-v99.0.0-win-x64', 'MAA-preview'):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp:
                self.root = Path(temp) / label
                (self.root / 'config').mkdir(parents=True)
                (self.root / 'MAA.exe').touch()
                path = self.root / 'config/gui.new.json'
                path.write_text(json.dumps(self.data), encoding='utf-8')
                code, output = self.run_cli()
                self.assertEqual(code, 0, output)
                queue = json.loads(path.read_text(encoding='utf-8'))['Configurations']['Default']['TaskQueue']
                self.assertTrue(next(r['IsEnabled'] for r in queue[0]['RoomList'] if r['Room'] == 'Recycle'))

    def test_backup_error_remains_specific_and_leaves_original_untouched(self):
        before = self.gui.read_bytes()
        code, output = self.run_cli(write_error=True)
        self.assertNotEqual(code, 0)
        self.assertIn('备份失败（测试）', output)
        self.assertEqual(self.gui.read_bytes(), before)
