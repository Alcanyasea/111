import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tests.test_base_schedule import bs
from tests.test_infrast_collect_plan import ic

class NewFacilitiesTest(unittest.TestCase):
    def schedule(self):
        value = bs.default_base_schedule()
        value['batches']['4点'].update(recycle=['芬', '克洛丝'], training={'operatorsA':['杜宾','艾丽妮'], 'operatorsB':['银灰']}, continue_training=True)
        return value

    def test_roles_round_trip(self):
        doc = bs.build_plan_document(self.schedule())
        rooms = doc['plans'][0]['rooms']
        self.assertEqual(rooms['recycle'][0]['operators'], ['芬','克洛丝'])
        self.assertEqual(rooms['training'][0], {'operatorsA':['杜宾','艾丽妮'], 'operatorsB':['银灰']})
        imported = bs._convert_import_plan(doc['plans'][0], '333')
        self.assertEqual(imported['training'], self.schedule()['batches']['4点']['training'])
        self.assertEqual(imported['recycle'], ['芬','克洛丝'])
        self.assertNotIn('continue_training', doc['plans'][0])

    def test_empty_fields_keep_legacy_plan(self):
        rooms = bs.build_plan_document({})['plans'][0]['rooms']
        self.assertNotIn('training', rooms)
        self.assertNotIn('recycle', rooms)
        self.assertFalse(bs.normalize({})['batches']['4点']['continue_training'])

    def test_continue_training_export_is_console_metadata(self):
        doc = bs.build_plan_document(self.schedule())
        imported = bs._convert_import_plan(doc['plans'][0], '333')
        self.assertTrue(imported['continue_training'])

    def test_collect_recycle_is_skip_only_when_supported(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(ic, 'PLUGIN_DIR', Path(tmp)):
            doc = json.loads(ic.write_collect_plan(include_recycle=True).read_text(encoding='utf-8'))
            room = doc['plans'][0]['rooms']['recycle'][0]
            self.assertTrue(room['skip'])
            self.assertFalse(room['autofill'])
            self.assertNotIn('product', room)

    def test_new_facilities_require_capability_before_any_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'config').mkdir()
            path = root/'config'/'gui.new.json'
            data = {'Current':'Default', 'Configurations':{'Default':{'TaskQueue':[{'$type':'InfrastTask','ContinueTraining':False,'RoomList':[{'Room':'Training','IsEnabled':False}]}]}}}
            path.write_text(json.dumps(data),encoding='utf-8')
            before = path.read_bytes()
            self.assertIsNone(bs.apply_maa_config(root, Path(tmp)/'plan.json', required_rooms=('Recycle',), log=lambda _:None))
            self.assertEqual(path.read_bytes(),before)
            bs.apply_maa_config(root, Path(tmp)/'plan.json', required_rooms=('Training',), continue_training=True)
            task = json.loads(path.read_text(encoding='utf-8'))['Configurations']['Default']['TaskQueue'][0]
            self.assertTrue(task['ContinueTraining'])
            self.assertTrue(next(r for r in task['RoomList'] if r['Room']=='Training')['IsEnabled'])

    def test_continue_training_restores_original_for_next_unmanaged_account(self):
        for original in (None, False, True):
            with self.subTest(original=original), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); (root/'config').mkdir()
                task = {'$type':'InfrastTask'}
                if original is not None: task['ContinueTraining'] = original
                path = root/'config'/'gui.new.json'
                path.write_text(json.dumps({'Current':'Default','Configurations':{'Default':{'TaskQueue':[task]}}}),encoding='utf-8')
                bs.apply_maa_config(root,root/'a.json',continue_training=True)
                bs.apply_maa_config(root,root/'b.json')
                result = json.loads(path.read_text(encoding='utf-8'))['Configurations']['Default']['TaskQueue'][0]
                self.assertEqual(result.get('ContinueTraining'),original)
                self.assertNotIn('MaaConsoleTrainingOriginal',result)

    def test_base_and_collect_transaction_failures_leave_config_untouched(self):
        for plugin in (bs, ic):
            with self.subTest(plugin=plugin.__name__), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); (root/'config').mkdir()
                path = root/'config'/'gui.new.json'
                path.write_text(json.dumps({'Current':'Default','Configurations':{'Default':{'TaskQueue':[{'$type':'InfrastTask'}]}}}),encoding='utf-8')
                before = path.read_bytes()
                with patch.object(plugin,'write_transaction',side_effect=OSError('backup failed')), patch.object(ic,'PLUGIN_DIR',root):
                    result = bs.apply_maa_config(root,root/'plan.json',log=lambda _:None) if plugin is bs else ic.apply_collect(root)
                self.assertEqual(path.read_bytes(),before)
                if plugin is bs: self.assertIsNone(result)
                else: self.assertFalse(result[0])

    def test_new_version_does_not_require_serialized_default_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'config').mkdir()
            path = root/'config'/'gui.new.json'
            path.write_text(json.dumps({'Current':'Default','Configurations':{'Default':{'TaskQueue':[{'$type':'InfrastTask','RoomList':[{'Room':'Training'}]}]}}}),encoding='utf-8')
            result = bs.apply_maa_config(root,root/'plan.json',continue_training=True,required_rooms=('Training',))
            self.assertIn('gui.new.json',result)
            self.assertTrue((root/'config'/'maa-console-training.json').exists())

    def test_import_skip_and_explicit_empty_new_rooms_survive_regeneration(self):
        for entries in ([], [{'skip':True,'operatorsA':['do not select']} ]):
            with self.subTest(entries=entries):
                imported = bs._convert_import_plan({'rooms':{'training':entries,'recycle':[]}},'333')
                doc = bs.build_plan_document({'batches':{'4点':imported}})
                rooms = doc['plans'][0]['rooms']
                self.assertEqual(rooms['recycle'],[])
                if entries: self.assertTrue(rooms['training'][0]['skip'])
                else: self.assertEqual(rooms['training'],[])

    def test_new_rooms_restore_original_enabled_and_presence_for_next_account(self):
        for original_rooms in ([{'Room':'Training','IsEnabled':False},{'Room':'Recycle','IsEnabled':False}], [{'Room':'Training','IsEnabled':False},{'Room':'Recycle'}]):
            with self.subTest(rooms=original_rooms), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);(root/'config').mkdir()
                path=root/'config'/'gui.new.json'
                task={'$type':'InfrastTask','RoomList':original_rooms,'ContinueTraining':True}
                path.write_text(json.dumps({'Current':'Default','Configurations':{'Default':{'TaskQueue':[task]}}}),encoding='utf-8')
                bs.apply_maa_config(root,root/'a.json',required_rooms=('Training','Recycle'),continue_training=True)
                bs.apply_maa_config(root,None)
                result=json.loads(path.read_text(encoding='utf-8'))['Configurations']['Default']['TaskQueue'][0]
                for original in original_rooms:
                    self.assertEqual(next(r for r in result['RoomList'] if r['Room']==original['Room']),original)
                self.assertTrue(result['ContinueTraining'])

    def test_new_rooms_restore_unrequested_room_when_next_account_requests_other(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'config').mkdir()
            path=root/'config'/'gui.new.json'
            task={'$type':'InfrastTask','RoomList':[{'Room':'Training','IsEnabled':False},{'Room':'Recycle','IsEnabled':False}]}
            path.write_text(json.dumps({'Current':'Default','Configurations':{'Default':{'TaskQueue':[task]}}}),encoding='utf-8')
            bs.apply_maa_config(root,root/'a.json',required_rooms=('Training',))
            bs.apply_maa_config(root,root/'b.json',required_rooms=('Recycle',))
            rooms={r['Room']:r for r in json.loads(path.read_text())['Configurations']['Default']['TaskQueue'][0]['RoomList']}
            self.assertFalse(rooms['Training']['IsEnabled'])
            self.assertTrue(rooms['Recycle']['IsEnabled'])

    def test_unknown_tasks_disabled_and_default_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'config').mkdir()
            queue = [{'$type':'StartUpTask','IsEnable':True}, {'$type':'InfrastTask'}, {'$type':'FutureTask','IsEnable':True,'secret':{'x':1}}]
            data = {'Current':'Default','Configurations':{'Default':{'TaskQueue':queue}}}
            path = root/'config'/'gui.new.json'; path.write_text(json.dumps(data),encoding='utf-8')
            with patch.object(ic,'PLUGIN_DIR',root):
                ic.apply_collect(root)
                ic.apply_collect(root)
            result = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(result['Configurations']['Default'],data['Configurations']['Default'])
            unknown = result['Configurations']['收菜']['TaskQueue'][-1]
            self.assertFalse(unknown['IsEnable'])
            self.assertEqual(unknown['secret'],{'x':1})

if __name__ == '__main__': unittest.main()
