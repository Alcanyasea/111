import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from tests.test_support import appconfig
from PySide6.QtWidgets import QApplication
from pages import base_schedule_dialog as mod
from tests import test_base_new_facilities as fixtures
from remote_control.telegram_base_schedule import build_schedule_patch, format_account_detail, describe_schedule_changes

class NewFacilitiesDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def test_gui_preset_and_export_preserve_roles_and_custom_fia(self):
        cfg = {'schedule':{'times':[{'time':'04:00','enabled':True},{'time':'16:00','enabled':True}]}}
        schedule = fixtures.NewFacilitiesTest().schedule()
        schedule['batches']['4点']['fiammetta'] = {'enable':True,'target':'自定义干员'}
        acc = {'id':'fake','label':'fake','base_schedule':schedule}
        dlg = mod.BaseScheduleDialog(cfg,acc)
        try:
            captured = dlg._collect_bs()
            self.assertEqual(captured['batches']['4点'],mod.bsplugin.normalize(schedule)['batches']['4点'])
            dlg.edit_map['4点']['training']['operatorsA'].setText('杜宾，艾丽妮,温蒂')
            expected = ['杜宾','艾丽妮','温蒂']
            self.assertEqual(dlg._collect_bs()['batches']['4点']['training']['operatorsA'],expected)
            with patch.object(mod,'_PresetNameBox') as namebox, patch.object(appconfig,'save'), patch.object(mod.MessageBox,'exec',return_value=True):
                namebox.return_value.exec.return_value=True
                namebox.return_value.name_edit.text.return_value='新设施'
                dlg._on_preset_save()
                self.assertEqual(acc['base_schedule_presets'][0]['schedule']['batches']['4点']['training']['operatorsA'],expected)
            self.assertTrue({'琴柳','承曦格雷伊','温蒂','槐琥','至简','旅骨'}.issubset(mod.FIAMMETTA_TARGETS))
        finally: dlg.deleteLater(); self.app.processEvents()

    def test_remote_import_and_summary_preserve_new_fields(self):
        import json
        schedule = fixtures.NewFacilitiesTest().schedule()
        cfg = {'accounts':[{'id':'fake','label':'fake','base_schedule':{}}]}
        cfg['schedule']={'times':[{'time':'04:00','enabled':True},{'time':'16:00','enabled':True}]}
        doc = mod.bsplugin.build_plan_document(schedule)
        result,_,notes = build_schedule_patch(cfg,'fake',json.dumps(doc).encode())
        batch = result['base_schedule']['batches']['4点']
        self.assertEqual(batch['training'],schedule['batches']['4点']['training'])
        self.assertTrue(batch['continue_training'])
        account = {'id':'fake','label':'fake',**result}
        text = format_account_detail(mod.bsplugin,account)
        self.assertIn('回收站',text); self.assertIn('杜宾',text); self.assertIn('消耗材料',text)
        self.assertTrue(any('回收站' in line for line in describe_schedule_changes(
            mod.bsplugin, {}, result['base_schedule'])))
