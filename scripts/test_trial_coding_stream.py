"""Constructed composition tests; no provider, launch, persistence or grades."""
import hashlib
import json
import unittest
from unittest.mock import patch

from trial_coding_stream import CodingStream, coding_profile, hook_measurements, reported_usage, TOTAL, LINE
from trial_coding_launch import compile_launch
from selected_native.frames import FrameError
from test_trial_attempt_actions import SESSION, PRIVATE, init, call, result, event, wire


def composer(**changes):
    options = dict(task_id='T1', session_id=SESSION, model='fixture-model',
                   cli_version='fixture-cli', workspace=None,
                   required_commands={'make_check':'make check'}, prompt='new task', input_charge=700)
    options.update(changes)
    return CodingStream(**options)


def hook():
    return dict(event='SessionStart', stdout_sha256='a'*64, stdout_bytes=3,
                stderr_bytes=0, process_seconds=.1, exit_code=0,
                memory_call_attempts=dict(search=0, pull=0, history=0, other=0, **{'pull-evidence':0}))


class CodingStreamTests(unittest.TestCase):
    def test_failed_result_and_later_terminal_have_independent_whole_digest(self):
        stream = composer()
        values = [init(), call(), result([dict(tool_name='Bash', tool_use_id='toolu_check')]),
                  event('system', subtype='status', status=PRIVATE)]
        chunks = list(map(wire, values))
        for n, chunk in enumerate(chunks):
            stream.feed(chunk, n)
        report = stream.observations([])
        self.assertEqual(report['success_reader']['refusal'], 'native_result_failed')
        self.assertFalse(report['success_reader']['whole_stream_consumed'])
        self.assertEqual(report['success_reader']['consumed_prefix']['stream_sha256'],
                         hashlib.sha256(b''.join(chunks[:3])).hexdigest())
        expected = hashlib.sha256(b''.join(chunks)).hexdigest()
        self.assertEqual(report['full_stream']['stream_sha256'], expected)
        self.assertEqual(report['terminal']['stream_sha256'], expected)
        self.assertEqual(report['failed_attempt_actions']['stream_sha256'], expected)
        self.assertEqual(report['failed_attempt_actions']['native_reported_denials'][0]
                         ['declared_required_operation'], 'make_check')
        self.assertNotIn(PRIVATE, json.dumps(report))
        self.assertEqual(stream.reader.actions.actions, [])
        self.assertFalse(report['validity_decided'])
        self.assertEqual(stream.raw, b'')
        self.assertIsNone(stream.terminal.result)
        self.assertIsNone(stream.prompt)
        with self.assertRaisesRegex(ValueError, 'coding_stream_closed'):
            stream.feed(b'{}\n', 4)

    def test_failed_terminal_usage_is_reported_not_zero_fallback(self):
        failure = result()
        failure.update(total_cost_usd=.75, num_turns=8, modelUsage={'fixture-model':dict(
            provider='firstParty', inputTokens=12, outputTokens=34,
            cacheReadInputTokens=56, cacheCreationInputTokens=78)})
        stream = composer()
        stream.feed(wire(init())+wire(failure), 1)
        usage = stream.observations([])['reported_usage']
        self.assertEqual(usage['unknown'], [])
        self.assertEqual(usage['reported_cost_usd'], .75)
        self.assertEqual(usage['counters']['outputTokens'], 34)
        failure['modelUsage']['fixture-model']['provider'] = 'other'
        usage = reported_usage(failure, 'fixture-model')
        self.assertIsNone(usage['counters']['outputTokens'])
        self.assertFalse(usage['model_identity_matched'])
        self.assertIsNone(reported_usage({}, 'fixture-model')['reported_cost_usd'])

    def test_incomplete_transport_and_timing_are_visible(self):
        stream = composer()
        stream.feed(wire(init()), 5)
        stream.feed(b' ', float('nan'))
        stream.feed(b'{', 4)
        report = stream.observations([])
        self.assertIn('capture_timing_unknown', report['composition_unknown'])
        self.assertIn('unterminated_transport', report['composition_unknown'])
        self.assertIsNone(report['selected_input'])
        self.assertGreater(report['terminal']['pending_bytes'], 0)
        self.assertEqual(stream.terminal.buffer, b'')
        self.assertEqual(stream.reader.buffer, b'')

    def test_complete_stream_accounting_and_hook_correlation_without_synthetic_finish(self):
        stream = composer()
        values = [init(), event('user', message=dict(content='new task'))]
        rows = []
        for n, name in enumerate(('SessionStart', 'UserPromptSubmit')):
            values.extend([event('system', subtype='hook_started', hook_id=str(n), hook_event=name),
                           event('system', subtype='hook_response', hook_id=str(n), hook_event=name,
                                 stdout='{}\n', outcome='success', exit_code=0)])
            rows.append(dict(hook(), event=name, stdout_sha256=hashlib.sha256(b'{}\n').hexdigest()))
        final = dict.fromkeys(stream.profile.final_required_keys, None)
        values.append(event('result', subtype='success', is_error=False, result=json.dumps(final),
                            permission_denials=[], num_turns=1, total_cost_usd=.1,
                            modelUsage={'fixture-model':dict(provider='firstParty', inputTokens=1,
                                outputTokens=2, cacheReadInputTokens=0, cacheCreationInputTokens=0)}))
        chunks = list(map(wire, values))
        for n, chunk in enumerate(chunks):
            stream.feed(chunk, n)
        with patch.object(stream.reader, 'finish', side_effect=AssertionError('synthetic finish')):
            report = stream.observations(rows)
        self.assertIsNone(report['success_reader']['refusal'])
        self.assertTrue(report['success_reader']['whole_stream_consumed'])
        self.assertEqual(report['selected_input']['status'], 'within_observed_limits')
        self.assertEqual(report['selected_input']['total_bytes'], 706)
        self.assertEqual(report['accepted_prefix_hooks']['status'], 'observed')
        self.assertFalse(report['task_acceptance'])
        self.assertFalse(report['runtime_authenticated'])
        with self.assertRaisesRegex(FrameError, 'reader_unavailable'):
            stream.reader.finish(0)

    def test_transport_bounds_keep_hash_without_unbounded_terminal_buffers(self):
        stream = composer()
        chunk = b'x'*65536
        for n in range(TOTAL//len(chunk)+1):
            stream.feed(chunk, n)
        report = stream.observations([])
        self.assertIn('transport_bounds', report['composition_unknown'])
        self.assertTrue(report['terminal']['overflow'])
        self.assertEqual(report['terminal']['stream_sha256'], report['full_stream']['stream_sha256'])
        self.assertEqual(stream.raw, b'')
        self.assertEqual(stream.pending, b'')
        self.assertLessEqual(len(stream.reader.buffer), LINE+65536)

    def test_invalid_chunk_prevents_complete_digest_claim(self):
        stream = composer()
        stream.feed(wire(init()), 0)
        stream.feed(b'x'*65537, 1)
        report = stream.observations([])
        self.assertFalse(report['full_stream']['digest_complete'])
        self.assertIn('transport_chunk_unknown', report['composition_unknown'])
        self.assertEqual(stream.terminal.buffer, b'')

    def test_hook_projection_never_traverses_extra_private_payload(self):
        row = hook()
        cyclic = {}; cyclic['private'] = cyclic
        row['private_payload'] = cyclic
        self.assertEqual(hook_measurements([row]), [hook()])
        for bad in (dict(row, process_seconds=float('nan')), dict(row, stdout_bytes=True),
                    dict(row, event=PRIVATE), dict(row, memory_call_attempts={}), 'private'):
            stream = composer()
            stream.feed(wire(init()), 0)
            with patch('trial_coding_stream.measure') as measurement:
                report = stream.observations([bad])
                measurement.assert_not_called()
            self.assertIn('hook_observation_bounds', report['composition_unknown'])
            self.assertNotIn(PRIVATE, json.dumps(report))

    def test_measurement_error_is_unknown_without_private_exception_text(self):
        stream = composer()
        stream.feed(wire(init())+wire(result()), 1)
        with patch('trial_coding_stream.measure', side_effect=RuntimeError(PRIVATE)):
            report = stream.observations([])
        self.assertIsNone(report['selected_input'])
        self.assertIn('measurement_unknown', report['composition_unknown'])
        self.assertNotIn(PRIVATE, json.dumps(report))

    def test_action_interrupt_is_preserved_and_reraised(self):
        stream = composer()
        with patch.object(stream.failure_actions, 'feed', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                stream.feed(wire(init()), 0)
        self.assertIn('action_reader_interrupted', stream.unknown)
        self.assertEqual(stream.raw, b'')

    def test_profile_variants_and_compiled_input_lower_bound(self):
        for task, key in {'T1':'flag_basis', 'T2':'measured', 'T3':'ranking_effect',
                          'T4':'threshold', 'T5':'field_bounds', 'T7':'owner_decisions', 'T8':'verdict'}.items():
            profile = coding_profile(task)
            self.assertIn(key, profile.final_required_keys)
            self.assertEqual(profile.limits, (TOTAL, LINE, 2048))
            self.assertEqual(profile.final_max_bytes, 8192)
        with self.assertRaisesRegex(ValueError, 'coding_task_unavailable'):
            coding_profile('T6')
        launch = compile_launch(session_id=SESSION, model='fixture-model', effort='high',
                                prompt='new task', memory_instruction='memory instruction')
        charge = len(launch.input_message)+512+len(b'memory instruction')
        composer(input_charge=charge)
        with self.assertRaisesRegex(ValueError, 'coding_stream_input_charge'):
            composer(input_charge=len(b'new task')+512)


if __name__ == '__main__':
    unittest.main()
