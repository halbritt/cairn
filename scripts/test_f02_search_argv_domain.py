"""Actual Source/Go query-domain pairs; only API and selector are synthetic."""
import copy
import json
from pathlib import Path
import shutil
import unittest

import test_f03_capture_selection as capture
import test_f02_checked_ordinary as recall


def observed(result, state, calls, records=None, providers=None):
    return dict(exit=result.returncode, stdout=result.stdout.decode(), stderr=result.stderr.decode(),
                state=state, operations=[op for op, _, _ in calls],
                queries=[body['query'] for op, body, _ in calls if op == 'index'],
                records=copy.deepcopy(records), providers=providers)


class CaptureQueryDomainTests(unittest.TestCase):
    setUpClass = classmethod(capture.CaptureSelectionTests.setUpClass.__func__)
    tearDownClass = classmethod(capture.start_fixture.F02StartTests.tearDownClass.__func__)

    def setUp(self):
        self.case = capture.CaptureSelectionTests('runTest')
        self.case.binary, self.case.cli = self.binary, self.cli
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()

    def capture_pair(self, text):
        case = self.case
        case.configure('claude', 'PreCompact', messages=[
            dict(role='user', text=text),
            dict(role='assistant', text='Preserve the retention policy and next operator step.')])
        case.selection.update(checkpoint='Preserve the retention policy and next operator step.',
                              workstream='retention policy', memories=[case.note()])
        seed = b'{"hints":{},"sentinel":"preserve exact seed bytes"}\n'
        sides = []
        for side in ('source', 'go'):
            result, path = case.run_seeded_title(side, seed)
            log = case.root/'provider.calls'
            providers = [json.loads(row) for row in log.read_text().splitlines()] if log.exists() else []
            for provider in providers:
                self.assertFalse(Path('/proc', str(provider['pid'])).exists())
            row = observed(result, case.state(), case.calls, case.records, len(providers))
            row['state_unchanged'] = path.read_bytes() == seed
            row['reports'] = [dict((key, val) for key, val in body.items()
                                   if key not in ('request_id', 'duration_ms', 'selector_duration_ms'))
                              for op, body, _ in case.calls if op == 'capture-observation']
            row['locks'] = list(case.report_locks)
            sides.append(row)
        print('SEARCH_ARGV_CAPTURE_PAIR='+json.dumps(dict(text=text, source=sides[0], go=sides[1]), sort_keys=True), flush=True)
        self.assertEqual(sides[1], sides[0])
        return sides[1]

    def test_01_short_quoted_nul_capture_is_normalized_before_query(self):
        row = self.capture_pair('Keep "a\x00b" policy for postgres retention.')
        self.assertEqual((row['exit'], row['stderr'], row['providers']), (0, '', 1))
        self.assertFalse(row['state_unchanged'])
        self.assertEqual(row['operations'].count('create'), 2)
        self.assertEqual(len(row['records']), 2)
        self.assertFalse(any('\x00' in query for query in row['queries']))
        self.assertEqual(row['reports'][0]['outcome'], 'saved')
        self.assertTrue(all(row['locks'][0].values()))

    def test_03_retained_quoted_nul_capture_refuses_before_selector(self):
        row = self.capture_pair('Keep "alpha\x00beta" policy for postgres retention.')
        self.assertEqual((row['exit'], row['stdout'], row['providers']), (1, '', 0))
        self.assertIn('error_class=ValueError', row['stderr'])
        self.assertTrue(row['state_unchanged'])
        self.assertEqual(row['operations'], ['version', 'index', 'capture-observation'])
        self.assertEqual(row['records'], [])
        self.assertEqual(row['reports'][0]['outcome'], 'error')
        self.assertTrue(all(row['locks'][0].values()))


class RecallQueryDomainTests(unittest.TestCase):
    setUpClass = classmethod(recall.CheckedOrdinaryTests.setUpClass.__func__)
    tearDownClass = classmethod(recall.CheckedOrdinaryTests.tearDownClass.__func__)
    setUp = recall.CheckedOrdinaryTests.setUp
    close = recall.CheckedOrdinaryTests.close
    source = recall.CheckedOrdinaryTests.source
    go = recall.CheckedOrdinaryTests.go
    go_vector = recall.CheckedOrdinaryTests.go_vector
    selected_vector = recall.CheckedOrdinaryTests.selected_vector
    go_launcher = recall.CheckedOrdinaryTests.go_launcher
    state = recall.CheckedOrdinaryTests.state

    def recall_pair(self, prompt):
        self.f.event.update(hook_event_name='UserPromptSubmit', prompt=prompt)
        self.f.raw = recall.encoded(self.f.event)
        sides = []
        for launch in (self.source, self.go):
            shutil.rmtree(self.f.memory['state_dir'], ignore_errors=True)
            self.calls.clear()
            result = launch()
            sides.append(observed(result, self.state(), self.calls))
        print('SEARCH_ARGV_RECALL_PAIR='+json.dumps(dict(prompt=prompt, source=sides[0], go=sides[1]), sort_keys=True), flush=True)
        self.assertEqual(sides[1], sides[0])
        return sides[1]

    def test_02_short_quoted_nul_recall_is_normalized_before_query(self):
        row = self.recall_pair('Repair "a\x00b" policy for postgres retention.')
        self.assertEqual((row['exit'], row['stderr']), (0, ''))
        self.assertEqual(row['operations'], ['version', 'index'])
        self.assertFalse(any('\x00' in query for query in row['queries']))

    def test_04_retained_quoted_nul_recall_refuses_before_api(self):
        row = self.recall_pair('Repair "alpha\x00beta" policy for postgres retention.')
        self.assertEqual((row['exit'], row['stdout']), (1, ''))
        self.assertIn('error_class=ValueError', row['stderr'])
        self.assertEqual(row['operations'], [])
        self.assertEqual(row['state'], {})
