import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


spec = importlib.util.spec_from_file_location('recurrence_trial', Path(__file__).with_name('trial-opencode-recurrence.py'))
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


class ExplanationTest(unittest.TestCase):
    def test_selected_text_is_private_bounded_and_excludes_other_event_types(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace = [{'type': 'text', 'part': {'text': 'earlier prose'}},
                     {'type': 'reasoning', 'part': {'text': 'private reasoning'}},
                     {'type': 'tool_use', 'part': {'text': 'private tool output'}},
                     {'type': 'text', 'part': {'text': 'x' + 'é' * 5000}}]
            result = trial.retain_last_explanation(root, 'repo_only', trace)
            path = Path(result['path'])
            raw = path.read_bytes()
            self.assertEqual(raw.decode(), 'x' + 'é' * 4095)
            self.assertLessEqual(len(raw), 8192)
            self.assertTrue(result['truncated'])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(len(list(root.iterdir())), 1)

    def test_no_model_text_creates_no_diagnostic_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = trial.retain_last_explanation(root, 'repo_only', [
                {'type': 'tool_use', 'part': {'text': 'private tool output'}},
                {'type': 'reasoning', 'part': {'text': 'private reasoning'}}])
            self.assertIsNone(result)
            self.assertEqual(list(root.iterdir()), [])


class ReasoningBudgetTests(unittest.TestCase):
    def test_detects_reasoning_budget_exhaustion_with_empty_output(self):
        trace = [
            {'type': 'step_finish', 'part': {'reason': 'length', 'tokens': {'input': 78, 'output': 0, 'reasoning': 2048}}}
        ]
        summary = trial.trace_summary(trace, [])
        check = trial.detect_reasoning_budget_exhaustion(trace, summary)
        self.assertTrue(check['exhausted'])
        self.assertEqual(check['reason'], 'reasoning_budget_exhausted')

    def test_bounded_text_output_not_flagged_as_exhausted(self):
        trace = [
            {'type': 'text', 'part': {'text': '{"ok": true}'}},
            {'type': 'step_finish', 'part': {'reason': 'stop', 'tokens': {'input': 38, 'output': 20, 'reasoning': 0}}},
        ]
        summary = trial.trace_summary(trace, [])
        check = trial.detect_reasoning_budget_exhaustion(trace, summary)
        self.assertFalse(check['exhausted'])

    def test_assess_arm_records_reasoning_budget_exhausted_visibly(self):
        calls = []

        class FakeHost:
            def call(self, op, payload):
                calls.append((op, payload))
                if op == 'evidence':
                    return {'evidence_id': 'ev-1'}
                if op == 'assess-run':
                    return payload
                raise AssertionError(f'unexpected host op: {op}')

        result = {
            'arm': 'repo_only',
            'receipt': {'receipt_id': '00000000-0000-0000-0000-000000000001'},
            'gate': {'passed': False, 'actions': ['fail']},
            'changed_paths': [],
            'outside_scope': [],
            'patch_sha256': '0' * 64,
            'runtime_observed': True,
            'reasoning_budget_exhausted': True,
            'reasoning_budget_error': 'model exhausted output token budget inside reasoning with empty content (finish_reason=length)',
            'trace_summary': {'cited_record_ids': []},
            'selected_records': [],
        }
        with mock.patch.object(trial, 'invoke', return_value={'rows': [], 'more': False, 'interpretation': 'test'}):
            out = trial.assess_arm('/cairn', {}, result, [], FakeHost())
        self.assertEqual(out['assessment']['task_outcome'], 'unknown')
        self.assertEqual(out['assessment']['failure_domain'], 'binding')
        self.assertEqual(out['assessment']['failure_kind'], 'reasoning_budget_exhausted')

    def test_allow_thinking_env_off_switch_on_non_structured_turns(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(trial.ALLOW_THINKING_ENV, None)
            self.assertEqual(
                trial.resolve_thinking_options(disable_thinking=True),
                {'chat_template_kwargs': {'enable_thinking': False}},
            )
            os.environ[trial.ALLOW_THINKING_ENV] = '1'
            self.assertEqual(trial.resolve_thinking_options(disable_thinking=True), {})
            self.assertEqual(
                trial.resolve_thinking_options(disable_thinking=False, structured=True),
                {'chat_template_kwargs': {'enable_thinking': False}},
            )


if __name__ == '__main__':
    unittest.main()
