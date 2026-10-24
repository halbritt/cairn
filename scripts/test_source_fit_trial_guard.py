"""Prospective admission and terminal evidence guards for source-fit trials."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from check_source_fit_panel import PANEL
from run_source_fit import can_admit_next, direct_context, next_run_reserve, parse_stream, trial


class SourceFitTrialGuardTests(unittest.TestCase):
    PLAN = dict(maximum_runs=15, maximum_total_reported_tokens=100000)

    def test_next_run_is_refused_before_reported_ceiling(self):
        rows = [dict(tokens=28115), *[dict(tokens=14000) for _ in range(2)]]
        total = sum(row['tokens'] for row in rows)
        self.assertEqual(next_run_reserve(rows), 32000)
        self.assertTrue(can_admit_next(total, rows, self.PLAN))
        rows.extend([dict(tokens=14000), dict(tokens=14000)])
        total += 28000
        self.assertFalse(can_admit_next(total, rows, self.PLAN))
        self.assertFalse(can_admit_next(94425, rows, self.PLAN))

    def test_observed_spike_raises_reserve_and_run_count_is_bounded(self):
        self.assertEqual(next_run_reserve([dict(tokens=41000)]), 43000)
        self.assertFalse(can_admit_next(58000, [dict(tokens=41000)], self.PLAN))
        self.assertFalse(can_admit_next(0, [dict(tokens=1)] * 15, self.PLAN))

    def test_terminal_requires_one_resolved_model_and_complete_usage(self):
        assistant = dict(type='assistant', message=dict(model='claude-sonnet-5-5',
            content=[dict(type='tool_use', name='mcp__cairn__cairn_pull')]))
        result = dict(type='result', result='Answer', is_error=False, usage=dict(
            input_tokens=1, cache_read_input_tokens=2,
            cache_creation_input_tokens=3, output_tokens=4))
        stream = '\n'.join(json.dumps(row) for row in (assistant, result))
        parsed = parse_stream(stream)
        self.assertEqual(parsed['tokens'], 10)
        self.assertEqual(parsed['tool_calls'], ['mcp__cairn__cairn_pull'])
        with self.assertRaisesRegex(RuntimeError, 'native_usage_missing'):
            parse_stream('\n'.join(json.dumps(row) for row in
                         (assistant, dict(result, usage={'input_tokens': 1}))))
        with self.assertRaisesRegex(RuntimeError, 'resolved_model_missing_or_multiple'):
            parse_stream(json.dumps(result))

    def test_direct_control_omits_local_only_source(self):
        panel = json.loads(PANEL.read_text())
        case = next(case for case in panel['cases'] if case['id'] == 'inaccessible_source')
        self.assertEqual(direct_context(panel, case), '')
        mixed = next(case for case in panel['cases'] if case['id'] == 'buried_live_qualification')
        self.assertIn('Current qualification (2026-10-20)', direct_context(panel, mixed))

    def test_campaign_stops_before_next_native_admission(self):
        panel = json.loads(PANEL.read_text())
        panel['cases'] = [dict(id='first', task='First?', arms=['current'],
                               required_answer=['known']),
                          dict(id='second', task='Second?', arms=['current'],
                               required_answer=['known'])]
        panel['provider_plan']['maximum_total_reported_tokens'] = 50000
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            def config(*_args):
                path = output / 'mcp.json'
                path.write_text('{}')
                return path
            result = dict(model='claude-sonnet-5-5', usage=dict(output_tokens=100,
                input_tokens=100, cache_read_input_tokens=100,
                cache_creation_input_tokens=19700), tokens=20000,
                answer='known', tool_calls=[])
            with patch('run_source_fit.subscription_status', return_value=dict(
                    auth_method='claude.ai', subscription_type='max')), \
                 patch('run_source_fit.native_call', return_value=result) as native, \
                 patch('run_source_fit.mcp_file', side_effect=config):
                trial(panel, dict(panel_sha256='fixture'),
                      {('first', 'current'): '', ('second', 'current'): ''},
                      dict(current=object()), [], output)
            self.assertEqual(native.call_count, 1)
            saved = json.loads((output / 'result.json').read_text())
            self.assertEqual(saved['status'], 'next_run_reserve_exhausted')
            self.assertEqual(len(saved['runs']), 1)
            self.assertEqual(saved['reported_tokens'], 20000)


if __name__ == '__main__':
    unittest.main()
