import contextlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path

from plugins.combat_report import combat_report as cr


class CombatReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.maa = self.root / 'fake-maa'
        self.folder = self.maa / 'data' / 'DailyReport'
        self.folder.mkdir(parents=True)
        self.start = cr.timestamp('2026-11-12T19:55:00Z')
        self.end = cr.timestamp('2026-11-12T20:10:00Z')

    def session(self, start='2026-11-12T20:01:00Z', **values):
        result = dict(stage='1-7', startTime=start, endTime=None, times=2, rounds=1,
                      sanity=12, medicinesUsed=0, expiringMedicinesUsed=0,
                      stonesUsed=0, drops={'30012': 3})
        result.update(values)
        return result

    def write(self, sessions, filename='2026-11-13.json'):
        path = self.folder / filename
        path.write_text(json.dumps(dict(version=1, sessions=sessions)), encoding='utf-8')
        return path

    def boundary(self, account='A', run='r1'):
        return cr.capture(self.maa, account, 'official', run, self.start)

    def test_first_run_missing_source_can_capture_new_only(self):
        boundary = self.boundary()
        self.write([self.session(), self.session('2026-11-11T20:01:00Z')])
        report = cr.collect(boundary, cr.read_snapshot(self.maa), self.end)
        self.assertEqual('available', report['status'])
        self.assertEqual(1, len(report['records']))
        self.assertTrue(report['records'][0]['incomplete'])

    def test_account_b_does_not_receive_a_history(self):
        self.write([self.session('2026-11-12T19:40:00Z')])
        boundary = self.boundary('B')
        self.write([self.session('2026-11-12T19:40:00Z', times=10), self.session()])
        report = cr.collect(boundary, cr.read_snapshot(self.maa), self.end)
        self.assertEqual('B', report['account'])
        self.assertEqual(2, report['records'][0]['times'])
        self.assertEqual(1, len(report['records']))

    def test_positive_diff_open_session(self):
        self.write([self.session('2026-11-12T19:55:00Z', times=1, sanity=6, drops={'30012': 1})])
        boundary = self.boundary()
        self.write([self.session('2026-11-12T19:55:00Z')])
        row = cr.collect(boundary, cr.read_snapshot(self.maa), self.end)['records'][0]
        self.assertEqual(1, row['times'])
        self.assertEqual({'30012': 2}, row['drops'])

    def test_retries_count_distinct_sessions_and_deduplicate_repeated_report(self):
        boundary = self.boundary()
        self.write([self.session(), self.session('2026-11-12T20:02:00Z')])
        report = cr.collect(boundary, cr.read_snapshot(self.maa), self.end)
        summary = cr.summarize([report, report])
        self.assertEqual(4, summary['times'])
        self.assertEqual({'30012': 6}, summary['drops'])

    def test_game_day_timezone_midnight_and_four(self):
        self.assertEqual('2026-11-12', cr.game_day('2026-11-12T19:59:59Z'))
        self.assertEqual('2026-11-13', cr.game_day('2026-11-13T04:00:00+08:00'))
        self.assertEqual('2026-11-12', cr.game_day('2026-11-13T00:01:00+08:00'))

    def test_missing_malformed_old_and_interrupted_unavailable(self):
        for payload in ('{', '[]', '{"version":9,"sessions":[]}', '{"version":1,"sessions":[{}]}'):
            path = self.folder / '2026-11-13.json'
            path.write_text(payload, encoding='utf-8')
            boundary = self.boundary()
            self.write([self.session()])
            self.assertEqual('unavailable', cr.collect(boundary, cr.read_snapshot(self.maa), self.end)['status'])
        self.write([])
        self.assertEqual('unavailable', cr.collect(self.boundary(), cr.read_snapshot(self.maa), self.end)['status'])

    def test_missing_counter_is_unavailable_instead_of_zero(self):
        broken = self.session()
        del broken['sanity']
        self.write([broken])
        self.assertEqual('unavailable', cr.read_snapshot(self.maa)['status'])

    def test_end_before_start_is_unavailable(self):
        self.write([self.session(endTime='2026-11-12T20:00:00Z')])
        self.assertEqual('unavailable', cr.read_snapshot(self.maa)['status'])

    def test_source_unchanged_and_identity_filters(self):
        boundary = self.boundary()
        path = self.write([self.session()])
        original = path.read_bytes()
        report = cr.collect(boundary, cr.read_snapshot(self.maa), self.end)
        self.assertEqual(original, path.read_bytes())
        self.assertEqual(2, cr.summarize([report], 'official:A', '2026-11-13')['times'])
        self.assertEqual('unavailable', cr.summarize([report], 'official:B')['status'])

    def test_cli_repeat_collect_is_frozen_and_boundary_not_replaced(self):
        config = self.root / 'fixture.json'
        config.write_text('{}', encoding='utf-8')
        args = ['--config', str(config), '--account', 'A', '--server', 'official',
                '--run-id', '../run', '--maa-dir', str(self.maa), '--history-dir', str(self.root / 'history')]
        with contextlib.redirect_stdout(io.StringIO()):
            cr.main(['boundary'] + args)
        # Use a future start relative to the actual CLI boundary; then explicitly
        # adjust the fixture boundary so all data belongs to a controlled interval.
        bp = next((self.root / 'history' / 'combat_report').glob('*.boundary.json'))
        boundary = json.loads(bp.read_text(encoding='utf-8'))
        boundary['captured_at'] = self.start.isoformat()
        bp.write_text(json.dumps(boundary), encoding='utf-8')
        self.write([self.session()])
        def run():
            output = io.StringIO()
            class Clock(datetime):
                @classmethod
                def now(cls, tz=None):
                    return datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
            with contextlib.redirect_stdout(output), patch.object(cr, 'datetime', Clock):
                self.assertEqual(0, cr.main(['collect'] + args))
            return json.loads(output.getvalue())
        first = run()
        self.write([self.session(), self.session('2026-11-12T20:03:00Z')])
        self.assertEqual(first, run())
        self.assertEqual(1, len(first['records']))

    def test_prune_only_owned_files(self):
        folder = self.root / 'history' / 'combat_report'
        folder.mkdir(parents=True)
        payload = json.dumps(dict(version=1, captured_at='2020-01-01T00:00:00Z'))
        owned = folder / ('a' * 64 + '.json')
        other = folder / 'personal.json'
        owned.write_text(payload)
        other.write_text(payload)
        cr.prune_reports(self.root / 'history')
        self.assertFalse(owned.exists())
        self.assertTrue(other.exists())

    def test_old_corrupt_source_does_not_poison_current_capture(self):
        (self.folder / '2020-01-01.json').write_text('{', encoding='utf-8')
        self.write([])
        boundary = self.boundary()
        self.assertEqual('available', boundary['snapshot']['status'])
        self.write([self.session()])
        self.assertEqual('available', cr.collect(boundary, cr.read_snapshot(self.maa, boundary['captured_at']), self.end)['status'])

    def test_malformed_console_report_is_skipped(self):
        import copy
        boundary = self.boundary()
        self.write([self.session()])
        report = cr.collect(boundary, cr.read_snapshot(self.maa), self.end)
        folder = self.root / 'history' / 'combat_report'
        folder.mkdir(parents=True)
        path = folder / 'fixture.json'
        for key, value in [('times', True), ('sanity', -1), ('drops', {'x': '4'}),
                           ('game_day', '2026-02-31'), ('game_day', []),
                           ('incomplete', 'yes'), ('id', []), ('stage', 4)]:
            broken = copy.deepcopy(report)
            broken['records'][0][key] = value
            path.write_text(json.dumps(broken), encoding='utf-8')
            self.assertEqual([], cr.load_reports(self.root / 'history'), (key, value))
        for key, value in [('status', 'unknown'), ('status', 'unavailable'), ('captured_at', 'bad'),
                           ('item_names', {'x': []}), ('account', []), ('records', [None])]:
            broken = copy.deepcopy(report)
            broken[key] = value
            path.write_text(json.dumps(broken), encoding='utf-8')
            self.assertEqual([], cr.load_reports(self.root / 'history'), (key, value))

    def test_optional_names_read_from_fake_resource(self):
        resource = self.maa / 'resource'
        resource.mkdir()
        (resource / 'item_index.json').write_text(json.dumps({'30012': {'name': '固源岩'}, 'bad': 1}), encoding='utf-8')
        self.assertEqual({'30012': '固源岩'}, cr.read_item_names(self.maa, ['30012', 'bad', 'unknown']))


if __name__ == '__main__':
    unittest.main()
