import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from tests.test_support import appconfig
from PySide6.QtWidgets import QApplication, QWidget
from pages.account_tasks_dialog import AccountTasksDialog


METADATA = {'items': {'30012': {'name': '固源岩', 'sortId': 1},
                      '30042': {'name': '异铁', 'sortId': 2}},
            'operators': {'阿米娅': {'role': 'Caster', 'rarity': 5}},
            'activities': [('测试活动', [{'stage': 'TEST-7', 'drop_id': '30042', 'target': 200}])],
            'errors': []}


class AccountTasksDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def dialog(self, settings=None, parent=None):
        account = {'id': 'test', 'label': '测试账号', 'server': 'official'}
        if settings is not None:
            account['maa_tasks'] = copy.deepcopy(settings)
        with patch('pages.account_tasks_dialog.load_metadata', return_value=copy.deepcopy(METADATA)), \
             patch('pages.account_tasks_dialog.maa_dir_for', return_value=Path('fake-only')):
            dialog = AccountTasksDialog({'paths': {}, 'accounts': [account]}, account, parent)
        self.addCleanup(dialog.deleteLater)
        return dialog, account

    def test_default_disabled_and_cancel_does_not_save(self):
        dialog, account = self.dialog()
        with patch.object(appconfig, 'save') as save:
            dialog.add_plan('depot', {'stage': '1-7', 'drop_id': '30012', 'target': 10})
            dialog.reject()
            save.assert_not_called()
        self.assertNotIn('maa_tasks', account)
        self.assertFalse(dialog.depot['enabled'].isChecked())
        self.assertFalse(dialog.progress['enabled'].isChecked())

    def test_rows_reorder_remove_and_save_named_material_id(self):
        dialog, account = self.dialog()
        for stage, identity in [('1-7', '30012'), ('S3-3', '30042')]:
            dialog.add_plan('depot', {'stage': stage, 'drop_id': identity, 'target': 100})
        dialog.move_plan('depot', -1)
        self.assertEqual([p['stage'] for p in dialog.plans('depot')], ['S3-3', '1-7'])
        dialog.remove_plan('depot')
        self.assertEqual([p['stage'] for p in dialog.plans('depot')], ['1-7'])
        dialog.depot['enabled'].setChecked(True)
        with patch.object(appconfig, 'save') as save:
            dialog._save()
            save.assert_called_once_with(dialog.cfg)
        plan = account['maa_tasks']['depot']['plans'][0]
        self.assertEqual(plan['drop_name'], '固源岩')
        self.assertEqual(plan['drop_id'], '30012')
        self.assertEqual((plan['medicine'], plan['stone']), (0, 0))

    def test_operator_metadata_role_and_mastery_validation(self):
        dialog, account = self.dialog()
        dialog.add_plan('progress')
        table = dialog.progress['table']
        table.cellWidget(0, 0).setCurrentText('阿米娅')
        self.assertEqual(table.cellWidget(0, 1).currentData(), 'Caster')
        table.cellWidget(0, 4).setValue(3)
        dialog.progress['enabled'].setChecked(True)
        with patch.object(appconfig, 'save') as save:
            dialog._save()
            save.assert_not_called()
        self.assertIn('精二', dialog.hint.text())
        self.assertNotIn('maa_tasks', account)
        table.cellWidget(0, 2).setCurrentIndex(2)
        table.cellWidget(0, 3).setValue(7)
        with patch.object(appconfig, 'save') as save:
            dialog._save()
            save.assert_called_once()
        self.assertEqual(account['maa_tasks']['progress']['plans'][0]['mastery'], [3, 0, 0])

    def test_activity_append_preserves_manual_plan_and_disable(self):
        dialog, _ = self.dialog()
        dialog.add_plan('depot', {'stage': '1-7', 'drop_id': '30012', 'target': 20})
        dialog._import_activity()
        self.assertEqual([p['stage'] for p in dialog.plans('depot')], ['1-7', 'TEST-7'])
        self.assertFalse(dialog.depot['enabled'].isChecked())

    def test_unknown_fields_roundtrip_and_persistence_failure_rollback(self):
        settings = {'future': {'keep': True}, 'depot': {'enabled': False, 'future_option': 5,
                    'plans': [{'stage': '1-7', 'drop_id': '30012', 'target': 100, 'future_plan': 'keep'}]}}
        dialog, account = self.dialog(settings)
        value = dialog.value()
        self.assertEqual(value['future'], {'keep': True})
        self.assertEqual(value['depot']['future_option'], 5)
        self.assertEqual(value['depot']['plans'][0]['future_plan'], 'keep')
        with patch.object(appconfig, 'save', side_effect=OSError('blocked')):
            dialog._save()
        self.assertEqual(account['maa_tasks'], settings)
        self.assertIn('blocked', dialog.hint.text())

    def test_offscreen_render_with_fake_metadata(self):
        owner = QWidget()
        owner.resize(1000, 560)
        owner.show()
        self.addCleanup(owner.close)
        dialog, _ = self.dialog(parent=owner)
        dialog.add_plan('depot', {'stage': '1-7', 'drop_id': '30012', 'target': 100})
        dialog.add_plan('progress', {'name': '阿米娅', 'role': 'Caster', 'elite': 2, 'skill_level': 7, 'mastery': [0, 0, 0]})
        dialog.show()
        self.app.processEvents()
        for index in (0, 1):
            dialog.tabs.setCurrentIndex(index)
            self.app.processEvents()
            pixmap = dialog.grab()
            self.assertFalse(pixmap.isNull())
            self.assertGreaterEqual(dialog.panel.width(), 880)
            self.assertTrue(owner.rect().contains(dialog.panel.geometry()))
            self.assertTrue(dialog.save_button.isVisibleTo(dialog))
        dialog.close()


if __name__ == '__main__':
    unittest.main()
