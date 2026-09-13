import copy
import json
from pathlib import Path
import tempfile
import unittest

from trial_native_recurrence import GATE_TEST, SCENARIO, gate_cases, require_calibration


class NativeRecurrenceCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.scenario = json.loads(SCENARIO.read_text())
        names = ['explicit_without_home', 'explicit_with_unusable_defaults', 'home_defaults',
                 'cairn_home_defaults', 'only_token_explicit', 'only_socket_explicit', 'empty_token',
                 'empty_socket', 'invalid_flag_without_home', 'missing_default_without_home', 'bad_explicit_token']
        failures = self.scenario['calibration']['baseline_failed']
        self.baseline = dict(gate=dict(passed=False, gate_actions=['fail']),
                             cases={n: 'fail' if n in failures else 'pass' for n in names})
        self.reference = dict(gate=dict(passed=True, gate_actions=['pass']), cases=dict.fromkeys(names, 'pass'))

    def test_reference_cannot_replace_the_required_failing_baseline(self):
        require_calibration(self.scenario, self.baseline, self.reference)
        with self.assertRaisesRegex(ValueError, 'Baseline defect differs'):
            require_calibration(self.scenario, self.reference, self.reference)

    def test_incomplete_skipped_or_rejected_reference_is_not_calibration(self):
        for change in ('missing', 'skip', 'failed_existing_tests'):
            reference = copy.deepcopy(self.reference)
            if change == 'missing':
                del reference['cases']['bad_explicit_token']
            elif change == 'skip':
                reference['cases']['bad_explicit_token'] = 'skip'
            else:
                reference['gate']['passed'] = False
            with self.subTest(change=change), self.assertRaises(ValueError):
                require_calibration(self.scenario, self.baseline, reference)

    def test_skipped_baseline_preservation_case_is_not_a_known_defect(self):
        self.baseline['cases']['bad_explicit_token'] = 'skip'
        with self.assertRaisesRegex(ValueError, 'skipped'):
            require_calibration(self.scenario, self.baseline, self.reference)

    def test_rerun_cannot_overwrite_an_earlier_gate_failure(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root) / 'gate.jsonl'
            log.write_text('\n'.join(json.dumps(dict(Test=GATE_TEST + '/empty_token', Action=a))
                                     for a in ('run', 'fail', 'run', 'pass')) + '\n')
            with self.assertRaisesRegex(ValueError, 'more than once'):
                gate_cases(log)


if __name__ == '__main__':
    unittest.main()
