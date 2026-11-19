import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

class MaaConfigIOTest(unittest.TestCase):
    def test_backup_failure_prevents_all_writes(self):
        from plugins import maa_config_io as io
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp)/name for name in ('a.json','b.json')]
            for path in paths: path.write_text('{"old":true}',encoding='utf-8')
            before = [p.read_bytes() for p in paths]
            with patch.object(io.shutil,'copy2',side_effect=OSError('no backup')):
                with self.assertRaises(OSError): io.write_transaction([(p,{'new':True}) for p in paths])
            self.assertEqual([p.read_bytes() for p in paths],before)

    def test_second_write_failure_rolls_back_exact_bytes(self):
        from plugins import maa_config_io as io
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp)/name for name in ('a.json','b.json')]
            for path in paths: path.write_bytes(b'{ "old" : true }\r\n')
            before = [p.read_bytes() for p in paths]
            real_replace = io.os.replace
            def replace(source,target):
                if Path(target)==paths[1] and str(source).endswith('.pending'): raise OSError('write failure')
                return real_replace(source,target)
            with patch.object(io.os,'replace',side_effect=replace):
                with self.assertRaises(OSError): io.write_transaction([(p,{'new':True}) for p in paths])
            self.assertEqual([p.read_bytes() for p in paths],before)
            self.assertEqual(len(list(Path(tmp).glob('*.bak'))),2)
            self.assertFalse(list(Path(tmp).glob('*.pending')))
