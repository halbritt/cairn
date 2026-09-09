import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from trial_agent_env import command_base
from trial_native_executor import require_fixture
from trial_native_recurrence import SCENARIO
from trial_native_runtime import materialize_source


class NativeExecutorBoundaryTests(unittest.TestCase):
    def test_explicit_toolchain_does_not_resolve_go_from_supervisor_path(self):
        # The supervisor's PATH selected Go 1.23 while this source requires
        # Go 1.25. Supplying the controller's paths must bypass that lookup.
        with patch('trial_agent_env.subprocess.check_output', side_effect=AssertionError('unexpected Go lookup')):
            command = command_base(Path('/opencode'), Path('/cairn'), Path('/store'), Path('/work'),
                Path('/home'), Path('/config'), Path('/cache'), False,
                go_paths=['/pinned/go', '/pinned/modules'])
        self.assertIn('/pinned/go', command)
        self.assertIn('/pinned/modules', command)

    def test_sealed_tree_must_match_before_source_is_written(self):
        with tempfile.TemporaryDirectory() as root:
            work = Path(root)
            (work / 'inputs').mkdir()
            (work / 'inputs/01-base').write_text(json.dumps(dict(schema_version=1, files={'file.txt': 'changed'})))
            expected = [dict(path='file.txt', sha256=hashlib.sha256(b'original').hexdigest())]
            with self.assertRaisesRegex(ValueError, 'differs'):
                materialize_source(work, expected)
            self.assertFalse((work / 'source').exists())

    def test_sealed_paths_cannot_escape_source_even_with_matching_digest(self):
        for name in ('../escaped', '/tmp/escaped', 'a/../escaped'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as root:
                work = Path(root)
                (work / 'inputs').mkdir()
                (work / 'inputs/01-base').write_text(json.dumps(dict(schema_version=1, files={name: 'body'})))
                with self.assertRaisesRegex(ValueError, 'path'):
                    materialize_source(work, [dict(path=name, sha256=hashlib.sha256(b'body').hexdigest())])
                self.assertFalse((work / 'source').exists())

    def test_unchanged_source_preserves_exact_text(self):
        with tempfile.TemporaryDirectory() as root:
            work = Path(root)
            (work / 'inputs').mkdir()
            body = 'unicode: café\n<&>\n'
            (work / 'inputs/01-base').write_text(json.dumps(dict(schema_version=1, files={'a/file.txt': body})))
            source = materialize_source(work, [dict(path='a/file.txt', sha256=hashlib.sha256(body.encode()).hexdigest())])
            self.assertEqual((source / 'a/file.txt').read_bytes(), body.encode())

    def test_model_gate_rejects_changed_code_or_incomplete_native_fixture(self):
        scenario = json.loads(SCENARIO.read_text())
        pins = {'driver_binary_sha256': 'tested-driver'}
        fixture = dict(execution_kind='fixture', scenario_sha256=hashlib.sha256(SCENARIO.read_bytes()).hexdigest(),
            execution_pins=pins, arms=[dict(mode=mode, change_set_checked=True, process_exit=0, invocations=1,
                provider_requests=0, gate=dict(gate_actions=['fail'], passed=False), exact_prompt_received=True)
                for mode in scenario['arms']])
        fixture['arms'][-1]['host_observation'] = dict(claim_confirmed=True, invocation_started=True,
            delivery_observation_id='delivery', outcome_observation_id='outcome')
        require_fixture(fixture, scenario, pins)
        with self.assertRaises(ValueError):
            require_fixture(fixture, scenario, {'driver_binary_sha256': 'untested-driver'})
        for mutation in ('missing_arm', 'no_delivery', 'rerun', 'unknown_exit', 'accepted_fixture', 'wrong_prompt'):
            candidate = copy.deepcopy(fixture)
            if mutation == 'missing_arm':
                candidate['arms'].pop(0)
            elif mutation == 'no_delivery':
                candidate['arms'][-1]['host_observation']['delivery_observation_id'] = ''
            elif mutation == 'rerun':
                candidate['arms'][0]['invocations'] = 2
            elif mutation == 'unknown_exit':
                candidate['arms'][0]['process_exit'] = None
            elif mutation == 'accepted_fixture':
                candidate['arms'][0]['gate']['passed'] = True
            else:
                candidate['arms'][0]['exact_prompt_received'] = False
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                require_fixture(candidate, scenario, pins)


if __name__ == '__main__':
    unittest.main()
