"""Synthetic legacy attempt streams and hook observations; no case, provider or model."""
import hashlib
import json
import unittest

import trial_legacy_limits as limits
import trial_paired_request as paired

PROMPT = 'Synthetic task.\n'
INSTRUCTION = '# Synthetic memory instruction\n'


def line(event):
    return json.dumps(event) + '\n'


def tool_use(ident, name, **arguments):
    return line(dict(type='assistant', message=dict(content=[dict(type='tool_use', id=ident, name=name,
                                                                   input=arguments)])))


def tool_result(ident, text):
    return line(dict(type='user', message=dict(content=[dict(type='tool_result', tool_use_id=ident,
                                                              content=text)])))


def answer(text):
    return (line(dict(type='assistant', message=dict(content=[dict(type='text', text=text)])))
            + line(dict(type='result', result=text, is_error=False, num_turns=1)))


def hook(event, stdout=100, seconds=0.5, searches=1, pulls=0, exit_code=0):
    return dict(schema='cairn.task-hook-observation/1', event=event, exit_code=exit_code, stdout_bytes=stdout,
                stderr_bytes=0, process_seconds=seconds,
                memory_call_attempts={'search': searches, 'pull': pulls, 'pull-evidence': 0, 'history': 0, 'other': 0})


HOOKS = [hook('SessionStart'), hook('UserPromptSubmit')]


def rows(stream, seconds):
    """trial_native_capture.capture timing rows for each LF-terminated line."""
    lines = [part + '\n' for part in stream.split('\n')[:-1]]
    return [dict(line=i + 1, received_seconds=value, bytes=len(text.encode()),
                 sha256=hashlib.sha256(text.encode()).hexdigest()) for i, (text, value) in enumerate(zip(lines, seconds))]


class LegacyLimitTests(unittest.TestCase):
    def assess(self, stream, hooks=HOOKS, **kwargs):
        return limits.assess(stream, hooks, input_bytes=limits.input_charge(PROMPT, INSTRUCTION), **kwargs)

    def test_values_come_from_the_original_limits(self):
        self.assertIs(limits.LIMITS, paired.ORIGINAL_LIMITS)
        self.assertEqual(limits.hook_timeout_seconds(), 5)
        self.assertEqual(limits.mcp_tool_room_arguments(), ['--tokens', '32000', '--max-tokens', '32000'])

    def test_task_wire_and_framing_are_charged_against_selected_input(self):
        wire = len((json.dumps(dict(type='user', message=dict(role='user', content=PROMPT)),
                               ensure_ascii=False) + '\n').encode())
        charge = wire + 512 + len(INSTRUCTION.encode())
        self.assertEqual(limits.input_charge(PROMPT, INSTRUCTION), charge)
        self.assertEqual(limits.hook_context_bytes(PROMPT, INSTRUCTION), 9500 - charge)
        result = self.assess(answer('done'))
        self.assertEqual(result['status'], 'within_original_limits')
        self.assertEqual(result['total_bytes'], charge + 200)

    def test_memory_bytes_over_the_selected_input_fail(self):
        result = self.assess(answer('done'), hooks=[hook('SessionStart', stdout=9000), hook('UserPromptSubmit')])
        self.assertEqual(result['failures'], ['selected_input_exceeded_9500_bytes'])

    def test_native_search_and_pull_counts_include_hook_calls(self):
        stream = (tool_use('toolu_1', 'mcp__cairn__cairn_search') + tool_result('toolu_1', 'r')
                  + tool_use('toolu_2', 'mcp__cairn__cairn_search') + tool_result('toolu_2', 'r')
                  + tool_use('toolu_3', 'mcp__cairn__cairn_search') + tool_result('toolu_3', 'r') + answer('done'))
        result = self.assess(stream, timings=rows(stream, [0] * 99))
        self.assertIn('memory_call_limit', result['failures'])
        self.assertEqual((result['native_searches'], result['hook_searches']), (3, 2))
        pulls = [hook('SessionStart', pulls=3), hook('UserPromptSubmit', pulls=2)]
        self.assertEqual(self.assess(answer('done'), hooks=pulls)['failures'], ['memory_call_limit'])

    def test_final_answer_over_8192_bytes_fails(self):
        self.assertEqual(self.assess(answer('x' * 8192))['status'], 'within_original_limits')
        self.assertEqual(self.assess(answer('x' * 8193))['failures'], ['final_answer_exceeded_8192_bytes'])

    def test_hook_over_five_seconds_or_failing_fails(self):
        self.assertEqual(self.assess(answer('d'), hooks=[hook('SessionStart', seconds=5.01), hook('UserPromptSubmit')]
                                     )['failures'], ['hook_exceeded_5_seconds'])
        self.assertEqual(self.assess(answer('d'), hooks=[hook('SessionStart', exit_code=1), hook('UserPromptSubmit')]
                                     )['failures'], ['hook_failed'])

    def test_single_memory_result_over_tool_room_fails(self):
        stream = tool_use('toolu_1', 'mcp__cairn__cairn_pull') + tool_result('toolu_1', 'y' * 32001) + answer('d')
        result = self.assess(stream, timings=rows(stream, [0] * 99))
        self.assertIn('tool_room_exceeded_32000_bytes', result['failures'])

    def test_native_memory_without_timing_is_unknown_never_within_limits(self):
        stream = tool_use('toolu_1', 'mcp__cairn__cairn_search') + tool_result('toolu_1', 'r') + answer('d')
        result = self.assess(stream)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['unknown'], ['last_native_memory_timing_unobserved'])

    def test_last_native_memory_after_thirty_seconds_fails(self):
        stream = tool_use('toolu_1', 'mcp__cairn__cairn_search') + tool_result('toolu_1', 'r') + answer('d')
        self.assertEqual(self.assess(stream, timings=rows(stream, [1, 30, 31, 31]))['status'], 'within_original_limits')
        self.assertEqual(self.assess(stream, timings=rows(stream, [1, 30.5, 31, 31]))['failures'],
                         ['last_memory_exceeded_30_seconds'])

    def test_missing_or_malformed_hook_observations_are_unknown(self):
        self.assertEqual(self.assess(answer('d'), hooks=[hook('SessionStart')])['unknown'],
                         ['required_hook_observations_missing'])
        broken = dict(hook('UserPromptSubmit'), stdout_bytes=None)
        self.assertIn('hook_measurement_missing', self.assess(answer('d'), hooks=[hook('SessionStart'), broken])['unknown'])
        unfinished = tool_use('toolu_1', 'mcp__cairn__cairn_search') + answer('d')
        self.assertIn('unfinished_memory_calls', self.assess(unfinished, timings=rows(unfinished, [0] * 9))['unknown'])


    def test_absent_malformed_or_truncated_evidence_is_never_within_limits(self):
        complete = answer('done')
        cases = {
            'empty': ('', 'terminal_event_missing'),
            'broken': ('{broken\n', 'stream_malformed'),
            'truncated': (complete[:-5], 'stream_malformed'),
            'no_terminal': (complete.splitlines(keepends=True)[0], 'terminal_event_missing'),
            'string_message': (line(dict(type='assistant', message='text')) + complete, 'stream_shape_unknown'),
            'string_content': (line(dict(type='assistant', message=dict(content='text'))) + complete, 'stream_shape_unknown'),
            'non_object_line': ('[1, 2]\n' + complete, 'stream_shape_unknown'),
        }
        for name, (stream, reason) in cases.items():
            with self.subTest(name=name):
                result = self.assess(stream)
                self.assertNotEqual(result['status'], 'within_original_limits')
                self.assertIn(reason, result['unknown'] + result['failures'])

    def test_timings_must_be_finite_nonnegative_and_complete(self):
        stream = tool_use('toolu_1', 'mcp__cairn__cairn_search') + tool_result('toolu_1', 'r') + answer('d')
        good = rows(stream, [1, 2, 3, 3])
        tampered = [dict(r) for r in good]
        tampered[1]['sha256'] = '0' * 64
        shifted = [dict(r, line=r['line'] + 1) for r in good]
        for timings in (rows(stream, [1, float('nan'), 2, 2]), rows(stream, [1, float('inf'), 2, 2]),
                        rows(stream, [1, -1, 2, 2]), rows(stream, [1, 2, 3]), rows(stream, [1, None, 2, 2]),
                        rows(stream, [1, True, 2, 2]), rows(stream, [1, '2', 2, 2]), 'not a list', [1, 2, 3, 3],
                        tampered, shifted):
            with self.subTest(timings=timings):
                result = self.assess(stream, timings=timings)
                self.assertEqual(result['status'], 'unknown')
                self.assertIn('stream_timing_invalid', result['unknown'])

    def test_malformed_hook_rows_or_container_never_raise(self):
        for hooks in (None, 'rows', [None, 1], [dict(event='SessionStart', memory_call_attempts='x')]):
            with self.subTest(hooks=hooks):
                result = self.assess(answer('d'), hooks=hooks)
                self.assertEqual(result['status'], 'unknown')
        self.assertEqual(self.assess(None)['status'], 'unknown')


    def test_unicode_line_separators_inside_json_do_not_split_lines(self):
        text = 'answer with \u2028 separator'
        stream = (json.dumps(dict(type='assistant', message=dict(content=[dict(type='text', text=text)])), ensure_ascii=False)
                  + '\n' + json.dumps(dict(type='result', result=text), ensure_ascii=False) + '\n')
        self.assertEqual(self.assess(stream, timings=rows(stream, [1, 2]))['status'], 'within_original_limits')


if __name__ == '__main__':
    unittest.main()
