"""The baseline runner must reject false green results without native prerequisites."""
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

import check_hybrid_release_baseline as baseline


class RunnerBoundaryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.number = 0

    def fixture(self, body, names=('Fixture.test_selected',)):
        self.number += 1
        source = self.root / ('fixture_%d.py' % self.number)
        source.write_text('import os,sys,json,time,subprocess,unittest\nfrom pathlib import Path\n' + body)
        evidence = baseline.Evidence('source-only', 'closure-smoke', ('REL-BASE',),
                                     ('fixture:self-check',), 'Hermetic runner failure fixture')
        return baseline.Suite('fixture', str(source), baseline.digest(source.read_bytes()),
                              {name: evidence for name in names})

    def run_fixture(self, suite, timeout=20):
        scratch = self.root / ('run_%d' % self.number)
        return baseline.run_suite(suite, scratch, timeout=timeout)

    def test_loader_executes_no_main_module_and_records_effect_and_stream(self):
        marker = self.root / 'selected-effect'
        suite = self.fixture('class Fixture(unittest.TestCase):\n'
                             ' def test_selected(self):\n'
                             '  Path(%r).write_bytes(b"observed")\n'
                             '  print("selected \\u03b2")\n' % str(marker))
        direct = subprocess.run([sys.executable, '-B', suite.path], capture_output=True)
        self.assertEqual((direct.returncode, direct.stdout, direct.stderr), (0, b'', b''))
        self.assertFalse(marker.exists(), 'exit zero did not run the test')
        outcome = self.run_fixture(suite)
        self.assertEqual(outcome['status'], 'passed', outcome)
        self.assertEqual(marker.read_bytes(), b'observed')
        self.assertIn('selected β'.encode(), base64.b64decode(outcome['stdout']['base64']))
        self.assertEqual([(r['id'], r['outcome']) for r in outcome['observation']['cases']],
                         [('Fixture.test_selected', 'passed')])

    def test_skip_expected_failure_and_unexpected_success_fail_required_coverage(self):
        variants = [('@unittest.skip("fixture")', 'pass', 'skipped'),
                    ('@unittest.expectedFailure', 'self.fail("fixture")', 'expected_failure'),
                    ('@unittest.expectedFailure', 'pass', 'unexpected_success')]
        for decorator, statement, expected in variants:
            with self.subTest(outcome=expected):
                suite = self.fixture('class Fixture(unittest.TestCase):\n %s\n'
                                     ' def test_selected(self):\n  %s\n' % (decorator, statement))
                outcome = self.run_fixture(suite)
                self.assertEqual(outcome['status'], 'failed', outcome)
                self.assertEqual(outcome['observation']['cases'][0]['outcome'], expected)

    def test_result_multiset_counts_and_malformed_reports_fail_even_on_exit_zero(self):
        passed = dict(id='Fixture.test_selected', outcome='passed', seconds=0)
        variants = {
            'zero': {'cases': [], 'tests_run': 0},
            'missing': {'cases': [dict(passed, id='Fixture.test_missing')], 'tests_run': 1},
            'duplicate': {'cases': [passed, passed], 'tests_run': 2},
            'unselected': {'cases': [passed, dict(passed, id='Fixture.test_other')], 'tests_run': 2},
            'bool_count': {'cases': [passed], 'tests_run': True},
            'malformed': '{not-json',
            'nonzero': {'cases': [passed], 'tests_run': 1},
        }
        for name, report in variants.items():
            with self.subTest(report=name):
                encoded = report if isinstance(report, str) else json.dumps(report)
                exit_code = 7 if name == 'nonzero' else 0
                suite = self.fixture('class Fixture(unittest.TestCase):\n'
                                     ' def test_selected(self):\n'
                                     '  report=Path(sys.argv[sys.argv.index("--report")+1])\n'
                                     '  report.write_text(%r)\n  os._exit(%d)\n' % (encoded, exit_code))
                outcome = self.run_fixture(suite)
                self.assertEqual(outcome['returncode'], exit_code)
                self.assertEqual(outcome['status'], 'failed', outcome)

    def test_missing_named_test_and_nonzero_child_cannot_pass(self):
        suite = self.fixture('class Fixture(unittest.TestCase):\n def test_other(self): pass\n')
        self.assertEqual(self.run_fixture(suite)['status'], 'failed')
        suite = self.fixture('class Fixture(unittest.TestCase):\n'
                             ' def test_selected(self): os._exit(7)\n')
        outcome = self.run_fixture(suite)
        self.assertEqual((outcome['status'], outcome['returncode']), ('failed', 7))

    def test_timeout_stops_and_joins_the_owned_child_group(self):
        pidfile = self.root / 'descendant.pid'
        suite = self.fixture('class Fixture(unittest.TestCase):\n'
                             ' def test_selected(self):\n'
                             '  child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])\n'
                             '  Path(%r).write_text(str(child.pid))\n'
                             '  time.sleep(60)\n' % str(pidfile))
        outcome = self.run_fixture(suite, timeout=2)
        self.assertEqual(outcome['status'], 'failed')
        self.assertIn('timeout', outcome['error'])
        self.assertTrue(pidfile.exists(), 'fixture did not start its descendant')
        status = Path('/proc') / pidfile.read_text() / 'stat'
        deadline = time.monotonic() + 3
        while status.exists():
            try:
                state = status.read_text().rsplit(') ', 1)[1].split()[0]
            except FileNotFoundError:
                break
            if state in ('Z', 'X'):
                break
            if time.monotonic() >= deadline:
                self.fail('owned descendant still running after group cleanup')
            time.sleep(0.02)

    def test_missing_prerequisite_is_missing_coverage_before_child_launch(self):
        selected = next(s for s in baseline.SUITES if s.path == 'scripts/test_installed_lifecycle_bundle.py')
        with patch.dict(os.environ, {'PATH': ''}):
            outcome = baseline.run_suite(selected, self.root / 'unlaunched')
        self.assertEqual(outcome['status'], 'missing_coverage', outcome)
        self.assertIn('bwrap', outcome['missing'])
        self.assertNotIn('command', outcome)

    def test_suite_hash_drift_refuses_before_module_effect(self):
        marker = self.root / 'must-not-import'
        suite = self.fixture('class Fixture(unittest.TestCase):\n def test_selected(self): pass\n')
        with Path(suite.path).open('a') as source:
            source.write('Path(%r).touch()\n' % str(marker))
        outcome = self.run_fixture(suite)
        self.assertEqual(outcome['status'], 'missing_coverage')
        self.assertFalse(marker.exists())

    def test_child_rechecks_hash_after_parent_check_and_before_execution(self):
        marker = self.root / 'must-not-execute-changed-bytes'
        suite = self.fixture('class Fixture(unittest.TestCase):\n def test_selected(self): pass\n')
        original = subprocess.Popen

        def replace_before_spawn(*args, **kwargs):
            with Path(suite.path).open('a') as source:
                source.write('Path(%r).touch()\n' % str(marker))
            return original(*args, **kwargs)

        with patch.object(subprocess, 'Popen', new=replace_before_spawn):
            outcome = self.run_fixture(suite)
        self.assertEqual((outcome['status'], outcome['returncode']), ('missing_coverage', 2))
        self.assertFalse(marker.exists())

    def test_unsupported_expectations_do_not_count_as_supported_operations(self):
        suite = self.fixture('class Fixture(unittest.TestCase):\n def test_selected(self): pass\n')
        unsupported = baseline.Evidence('fake-boundary', 'unsupported-expectation',
                                        ('REL-BASE',), ('fixture:unsupported',), 'Expected no operation')
        suite = baseline.Suite(suite.profile, suite.path, suite.sha256,
                               {name: unsupported for name in suite.cases})
        outcome = self.run_fixture(suite)
        self.assertEqual(outcome['status'], 'passed', outcome)
        summary = baseline.coverage_summary([outcome])
        self.assertEqual(summary['passed_supported_expectation_tests'], 0)
        self.assertEqual(summary['validated_tests_by_row_kind'], {'unsupported-expectation': 1})

    def test_inherited_account_and_disable_sentinels_do_not_reach_child(self):
        suite = self.fixture('class Fixture(unittest.TestCase):\n'
                             ' def test_selected(self):\n'
                             '  for key in ("HERDR_SOCKET_PATH","CAIRN_LIFECYCLE_DISABLED","OWNER_API_TOKEN"):\n'
                             '   self.assertNotIn(key,os.environ)\n'
                             '  self.assertEqual(Path(os.environ["CODEX_HOME"]).parent,Path.home())\n')
        with patch.dict(os.environ, dict(HERDR_SOCKET_PATH='synthetic-sentinel',
                                        CAIRN_LIFECYCLE_DISABLED='1', OWNER_API_TOKEN='synthetic-only')):
            outcome = self.run_fixture(suite)
        self.assertEqual(outcome['status'], 'passed', outcome)

    def test_observer_preserves_return_identity_kwargs_and_boundary_exceptions(self):
        observations, seen = [], []
        completed = subprocess.CompletedProcess(['fixture'], 0, b'output', b'error')
        environment = {'SYNTHETIC': 'fixture'}

        def boundary(*args, **kwargs):
            seen.append((args, kwargs))
            return completed

        observed = baseline.observe_run(boundary, observations, lambda: 'fixture')
        self.assertIs(observed(['fixture'], env=environment, input=b'exact', check=True), completed)
        self.assertEqual(seen[0], ((['fixture'],), dict(env=environment, input=b'exact', check=True)))
        self.assertIs(seen[0][1]['env'], environment)
        for error in (subprocess.CalledProcessError(4, ['fixture'], output=b'out', stderr=b'err'),
                      subprocess.TimeoutExpired(['fixture'], 0.1, output=b'partial')):
            with self.subTest(error=type(error).__name__):
                def refusing(*args, **kwargs):
                    raise error
                observed = baseline.observe_run(refusing, observations, lambda: 'fixture')
                with self.assertRaises(type(error)) as caught:
                    observed(['fixture'], timeout=0.1, check=True)
                self.assertIs(caught.exception, error)

    def test_real_subprocess_outcomes_match_with_and_without_observer(self):
        source = '''
class Fixture(unittest.TestCase):
 def test_success(self):
  result=subprocess.run([sys.executable,'-c','print("fixture")'],capture_output=True,check=True)
  self.assertEqual(result.stdout,b'fixture\\n')
 def test_refusal(self):
  with self.assertRaises(subprocess.CalledProcessError):
   subprocess.run([sys.executable,'-c','raise SystemExit(4)'],capture_output=True,check=True)
 def test_timeout(self):
  with self.assertRaises(subprocess.TimeoutExpired):
   subprocess.run([sys.executable,'-c','import time; time.sleep(60)'],capture_output=True,timeout=0.05)
'''
        observations = []
        outcomes = []
        original = subprocess.run
        for enabled in (False, True):
            module = types.ModuleType('transparency_fixture')
            exec('import subprocess,sys,unittest\n' + source, module.__dict__)
            suite = unittest.TestLoader().loadTestsFromTestCase(module.Fixture)
            selected_run = baseline.observe_run(original, observations, lambda: 'fixture') if enabled else original
            with patch.object(subprocess, 'run', new=selected_run):
                result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=baseline.CaseResults).run(suite)
            outcomes.append([(r['id'], r['outcome']) for r in result.records])
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertTrue(all(outcome == 'passed' for _, outcome in outcomes[0]))
        self.assertEqual(len(observations), 3)


if __name__ == '__main__':
    unittest.main()
