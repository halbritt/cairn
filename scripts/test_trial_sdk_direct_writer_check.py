"""Persistence failure regressions with constructed wait metadata; no child runs."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import check_trial_sdk_direct_writer as check


class RetainedObservationTests(unittest.TestCase):
    def setUp(self):
        self.pins = {name: check.digest(check.read(check.HERE / name)) for name in check.SOURCES}
        wait = {'pid': 1234, 'raw_status': 0, 'returncode': 0}
        self.outer = dict(test_pid=1234, test_wait=wait, waits=[wait], errors=[],
            group_children_empty=True, live_deadline_exceeded=False,
            pipes={name: {'close_attempts': 1, 'closed_known': True}
                   for name in ('stdout', 'stderr')})
        self.logs = {'stdout': json.dumps({'ok': True, 'final_wait': wait}).encode(), 'stderr': b''}

    def test_late_file_failures_keep_waits_and_named_error(self):
        actual_open = Path.open
        for name in ('OUTER-OBSERVATION.json', 'stdout.log', 'stderr.log', 'RESULT.json'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                out = Path(directory)
                def refuse(path, *args, **kwargs):
                    if path.parent == out and path.name == name:
                        raise OSError('constructed private persistence fault')
                    return actual_open(path, *args, **kwargs)
                with patch.object(Path, 'open', new=refuse):
                    result = check.finalize_check(out, self.pins, copy.deepcopy(self.outer), self.logs)
                expected = ('observation_write' if name == 'OUTER-OBSERVATION.json' else
                            'result_write' if name == 'RESULT.json' else 'log_write_' + name[:-4])
                self.assertFalse(result['ok'])
                self.assertIn(expected + ':OSError', result['outer']['errors'])
                self.assertEqual(result['outer']['test_wait'], self.outer['test_wait'])
                self.assertEqual(result['outer']['waits'], self.outer['waits'])
                if name != 'RESULT.json':
                    saved = json.loads((out / 'RESULT.json').read_bytes())
                    self.assertEqual(saved['outer']['test_wait'], self.outer['test_wait'])

    def test_late_source_open_failure_keeps_collected_observations(self):
        actual_open = os.open
        target = check.HERE / 'trial_sdk_direct_writer.py'
        def refuse(path, *args, **kwargs):
            if Path(path) == target:
                raise OSError('constructed private source failure')
            return actual_open(path, *args, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            with patch.object(os, 'open', side_effect=refuse):
                result = check.finalize_check(out, self.pins, copy.deepcopy(self.outer), self.logs)
            self.assertFalse(result['ok'])
            self.assertFalse(result['source_pins_stable'])
            self.assertIn('pins:OSError', result['outer']['errors'])
            self.assertEqual(result['outer']['test_wait'], self.outer['test_wait'])
            self.assertEqual(json.loads((out / 'OUTER-OBSERVATION.json').read_bytes()), self.outer)


if __name__ == '__main__':
    unittest.main()
