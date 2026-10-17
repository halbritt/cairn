"""Pure receipt-preservation regression; all process facts are controlled data."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import check_trial_sdk_native_process_owner as checker


class ReceiptTests(unittest.TestCase):
    def test_source_refusal_preserves_controlled_outer_wait_and_cleanup_facts(self):
        controlled = {'test_pid': 1234, 'test_wait': {'pid': 1234, 'raw_status': 0, 'returncode': 0},
                      'waits': [{'pid': 1234, 'raw_status': 0, 'returncode': 0}],
                      'group_kill': 'accepted', 'group_children_empty': True,
                      'pipes': {name: {'close_attempts': 1, 'closed_known': True}
                                for name in ('stdout', 'stderr')},
                      'errors': [], 'live_deadline_exceeded': False}
        # This is deliberately not a successful 11-case/9-child host observation.
        logs = {'stdout': json.dumps({'owned_children': [], 'fd_counts': [4, 4],
                                     'test_count': 11, 'successful': True}).encode(),
                'stderr': b''}
        completed = False
        original_read = checker.read
        def source_read(path):
            if completed:
                raise PermissionError('controlled post-run source refusal')
            return original_read(path)
        def controlled_supervision(snapshot):
            nonlocal completed
            self.assertEqual(len(list(snapshot.iterdir())), 9)
            completed = True
            return controlled, logs
        with tempfile.TemporaryDirectory(prefix='cairn-owner-receipt-', dir='/tmp') as temp:
            out = Path(temp) / 'attempt'
            with patch.object(sys, 'argv', ['check', '--output', str(out)]), \
                    patch.object(checker, 'read', side_effect=source_read), \
                    patch.object(checker, 'supervise', side_effect=controlled_supervision), \
                    patch.object(checker.subprocess, 'Popen', side_effect=AssertionError('process effect')), \
                    patch.object(checker.ctypes, 'CDLL', side_effect=AssertionError('kernel effect')), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(checker.main(), 1)
            result = json.loads((out / 'RESULT.json').read_bytes())
            before_verification = json.loads((out / 'OUTER-OBSERVATION.json').read_bytes())
            self.assertFalse(result['ok'])
            self.assertFalse(result['pins_stable'])
            self.assertEqual(result['errors'], ['pins:PermissionError'])
            self.assertEqual(result['test_wait'], controlled['test_wait'])
            self.assertEqual(result['waits'], controlled['waits'])
            self.assertEqual(result['pipes'], controlled['pipes'])
            self.assertTrue(result['group_children_empty'])
            self.assertEqual(before_verification['errors'], [])
            self.assertEqual(before_verification['test_wait'], result['test_wait'])
            self.assertEqual(before_verification['waits'], result['waits'])


if __name__ == '__main__':
    unittest.main()
