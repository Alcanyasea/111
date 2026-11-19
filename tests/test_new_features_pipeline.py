"""New MAA adapters use fake paths/processes; production entrypoints never run."""
import tempfile
import unittest
from pathlib import Path

from tests.ps_harness import run_ps
from tests.test_master_pipeline import PIPELINE_SETUP


SETUP = PIPELINE_SETUP + r'''
$accountTasksPy = 'account_tasks.py'
$combatReportPy = 'combat_report.py'
$script:events = [Collections.Generic.List[object]]::new()
function Invoke-Plugin {
    param($pyPath, $tag, $name, $argList, $missNote)
    $script:events.Add(@{path=$pyPath; args=@($argList)})
    return $true
}
function Run-MAA {
    $script:events.Add(@{path='MAA'; args=@()})
    $script:ranMaa = $true
    return $true
}
'''


class NewFeaturesPipelineTest(unittest.TestCase):
    def case(self, body):
        with tempfile.TemporaryDirectory() as tmp:
            return run_ps(self, Path(tmp),
                          ['scripts/master.ps1', 'scripts/master_pipeline.ps1'],
                          SETUP + body)

    def test_account_overlay_applied_after_base_before_report_and_maa(self):
        result = self.case(r'''
$result = Invoke-AccountRun $acc 1 $false
@{ok=$result.OK; events=@($script:events)} | ConvertTo-Json -Depth 8
''')
        self.assertTrue(result['ok'])
        paths = [e['path'] for e in result['events']]
        overlay = next(i for i, e in enumerate(result['events'])
                       if e['path'] == 'account_tasks.py' and e['args'][0] == 'apply')
        self.assertLess(paths.index('fia.py'), overlay)
        self.assertLess(overlay, paths.index('MAA'))
        reports = [e for e in result['events'] if e['path'] == 'combat_report.py']
        self.assertEqual([e['args'][0] for e in reports], ['boundary', 'collect'])
        self.assertLess(paths.index('combat_report.py'), paths.index('MAA'))
        self.assertEqual(paths[-1], 'account_tasks.py')
        for event in reports:
            args = event['args']
            self.assertEqual(args[args.index('--account') + 1], 'a1')
            self.assertEqual(args[args.index('--server') + 1], 'official')
        self.assertEqual(reports[0]['args'][1:], reports[1]['args'][1:])

    def test_preflight_blocks_all_configuration_writes(self):
        result = self.case(r'''
function Invoke-Plugin {
    param($pyPath, $tag, $name, $argList)
    $script:events.Add(@{path=$pyPath; args=@($argList)})
    if ($pyPath -eq 'account_tasks.py' -and $argList[0] -eq 'preflight') {
        $script:LastPluginError = 'MAA is running'
        return $false
    }
    return $true
}
$result = Invoke-AccountRun $acc 1 $false
@{result=$result; events=@($script:events); ran=$script:ranMaa} | ConvertTo-Json -Depth 8
''')
        self.assertFalse(result['ran'])
        self.assertEqual(len(result['events']), 1)
        self.assertEqual(result['events'][0]['args'][0], 'preflight')
        self.assertIn('MAA is running', result['result']['Reason'])

    def test_overlay_failure_blocks_maa(self):
        result = self.case(r'''
function Invoke-Plugin {
    param($pyPath)
    if ($pyPath -eq 'account_tasks.py') {
        $script:LastPluginError = 'invalid account plan'
        return $false
    }
    return $true
}
$result = Invoke-AccountRun $acc 1 $false
@{result=$result; ran=$script:ranMaa} | ConvertTo-Json -Depth 5
''')
        self.assertFalse(result['ran'])
        self.assertFalse(result['result']['OK'])
        self.assertIn('invalid account plan', result['result']['Reason'])

    def test_collect_has_no_account_plans_or_report(self):
        result = self.case(r'''
$InfrastCollect = $true
$result = Invoke-AccountRun $acc 1 $false
@{ok=$result.OK; events=@($script:events)} | ConvertTo-Json -Depth 8
''')
        self.assertTrue(result['ok'])
        self.assertEqual([e['path'] for e in result['events']], ['collect.py', 'MAA'])

    def test_report_errors_do_not_change_gameplay_failure(self):
        result = self.case(r'''
function Invoke-Plugin {
    param($pyPath, $tag, $name, $argList)
    if ($pyPath -eq 'combat_report.py') {
        $script:events.Add(@{path=$pyPath; args=@($argList)})
        throw 'report unavailable'
    }
    return $true
}
function Run-MAA { $script:LastMaaStatus = 'stall'; return $false }
$result = Invoke-AccountRun $acc 1 $false
@{result=$result; events=@($script:events)} | ConvertTo-Json -Depth 8
''')
        self.assertFalse(result['result']['OK'])
        self.assertEqual(result['result']['Reason'], 'MAA 无进展超时')
        self.assertEqual([e['args'][0] for e in result['events']
                          if e['path'] == 'combat_report.py'], ['boundary', 'collect'])

    def test_attempts_have_distinct_report_ids(self):
        result = self.case(r'''
$null = Invoke-AccountRun $acc 1 $false
$null = Invoke-AccountRun $acc 1 $true
@{events=@($script:events)} | ConvertTo-Json -Depth 8
''')
        boundaries = [e['args'] for e in result['events']
                      if e['path'] == 'combat_report.py' and e['args'][0] == 'boundary']
        self.assertEqual(len(boundaries), 2)
        ids = [args[args.index('--run-id') + 1] for args in boundaries]
        self.assertNotEqual(*ids)

    def test_maa_exception_still_collects_and_records_failure(self):
        result = self.case(r'''
function Run-MAA { throw 'fake launch failure' }
$result = Invoke-AccountRun $acc 1 $false
@{result=$result; events=@($script:events)} | ConvertTo-Json -Depth 8
''')
        self.assertFalse(result['result']['OK'])
        reports = [e for e in result['events'] if e['path'] == 'combat_report.py']
        self.assertEqual([e['args'][0] for e in reports], ['boundary', 'collect'])


if __name__ == '__main__':
    unittest.main()
