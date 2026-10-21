"""Single-pass observations preserve timing failures and original charges."""
import hashlib
import json
import unittest

from trial_native_observation import measure_lines


class LineObservationTests(unittest.TestCase):
    def run_lines(self, lines, timings):
        return measure_lines(lines, timings, (), input_bytes=500,
                             expects_hooks=False, model='fixture', prompt='task')

    def test_single_pass_interleaving_and_repeated_error_charge(self):
        result = dict(type='tool_result', tool_use_id='call', is_error=True,
                      content='é' * 2300)
        events = [dict(type='system', subtype='init', model='fixture'),
                  dict(type='user', message=dict(content='task')),
                  dict(type='assistant', message=dict(content=[dict(
                      type='tool_use', id='call', name='mcp__cairn__cairn_pull', input={})])),
                  dict(type='user', message=dict(content=[result])),
                  dict(type='user', message=dict(content=[result])),
                  dict(type='result', permission_denials=[], result='done')]
        state = {'line': 0, 'timing': 0, 'hash': None}

        def lines():
            for event in events:
                self.assertEqual(state['line'], state['timing'])
                raw = json.dumps(event, ensure_ascii=False) + '\n'
                state['line'] += 1
                state['hash'] = hashlib.sha256(raw.encode()).hexdigest()
                yield raw

        def timings():
            for _ in events:
                self.assertEqual(state['line'], state['timing'] + 1)
                state['timing'] += 1
                yield dict(sha256=state['hash'], received_seconds=31)

        observed = self.run_lines(lines(), timings())
        charge = 2 * len(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode())
        self.assertEqual(observed['native_memory_result_bytes'], charge)
        self.assertEqual(observed['total_bytes'], 500 + charge)
        self.assertEqual(observed['total_actual_pull_calls'], 1)
        self.assertIn('selected_input_exceeded_9500_bytes', observed['failures'])
        self.assertIn('last_memory_exceeded_30_seconds', observed['failures'])

    def test_missing_and_surplus_timing_remain_unknown(self):
        raw = '{"type":"result","permission_denials":[]}\n'
        timing = dict(sha256=hashlib.sha256(raw.encode()).hexdigest(), received_seconds=1)
        for timings in ([], [timing, timing]):
            with self.subTest(count=len(timings)):
                observed = self.run_lines(iter([raw]), iter(timings))
                self.assertIn('stream_timing_incomplete', observed['unknown'])
                self.assertIn('action_timing_unknown', observed['public_actions']['unknown'])
        missing = self.run_lines(iter([raw]), iter([]))
        self.assertIn('stream_timing_mismatch', missing['unknown'])

    def test_iterator_failure_does_not_return_partial_success(self):
        def lines():
            yield '{}\n'
            raise OSError('synthetic read failure')
        with self.assertRaises(OSError):
            self.run_lines(lines(), iter([{}]))


if __name__ == '__main__':
    unittest.main()
