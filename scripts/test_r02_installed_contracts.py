"""F-04 contract oracles through the real R-02 installed file producer."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from test_f04_activation import ROOT, SESSION, compat, encoded, overwrite_fixture, sha
import test_f04_deadlines as deadlines
import test_f04_failures as failures
import test_f04_state as state
import test_lifecycle_generation_install as retained
from test_r02_compatibility_install import InstalledFixture, InstalledTools


class InstalledContractTools(InstalledTools):
    def fixture(self, name, **kwargs):
        return InstalledFixture(self.root / name, self.binary, stager=self.stager, **kwargs)


class R02InstalledStateTests(InstalledContractTools, state.F04StateTests):
    """Unchanged ledger/locking/crash oracles; only the file producer changes."""


class R02InstalledFailureTests(failures.F04FailureTests):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stager = Path(cls.build.name) / 'stager'
        subprocess.run(['go', 'build', '-buildvcs=false', '-o', str(cls.stager), './cmd/cairn-stage-lifecycle'],
                       cwd=ROOT, check=True)
        cls.stager.chmod(0o500)
        cls.images = {}

    def fixture(self, name, mode, **kwargs):
        if mode not in self.images:
            image = Path(self.build.name) / ('image-' + mode)
            subprocess.run(['go', 'build', '-buildvcs=false', '-ldflags', '-X main.testMode=' + mode,
                            '-o', str(image), './cmd/f04-failure'], cwd=self.build.name, check=True)
            image.chmod(0o500)
            self.images[mode] = image
        f = InstalledFixture(self.root / name, self.images[mode], stager=self.stager, **kwargs)
        self.assertEqual(f.binary.name, 'cairn')
        self.assertEqual(f.receipt['binary']['sha256'], sha(self.images[mode].read_bytes()))
        return f


class R02InstalledDeadlineTests(InstalledContractTools, deadlines.F04DeadlineTests):
    def test_missing_invalid_or_wrong_row_timeout_refuses_before_activation(self):
        # The producer emits only its selected 13/150 and 2/15 L vectors.
        # Wrong-event calls use those actual Ls. Invalid/missing caller Hs are
        # adversarial vectors over the installed independent receipt selection.
        for role, budget in (('memory', 12), ('coordination', 13), ('memory', 150), ('coordination', 2), ('memory', None)):
            with self.subTest(role=role, budget=budget):
                f = self.fixture(role + str(budget))
                f.raw = encoded(dict(f.event, timeout=150, deadline=time.monotonic() + 10000))
                if budget in (150, 2):
                    result = f.public(role, budget)
                else:
                    config = f.memory_path if role == 'memory' else f.coord_path
                    argv = compat.launch_vector(sys.executable, f.receipt_path, f.receipt_sha, role,
                                                role + '-hook.py', config, budget or 13, f.launch_root)
                    if budget is None:
                        offset = argv.index('--hook-timeout')
                        del argv[offset:offset + 2]
                    result = subprocess.run(argv, input=f.raw, capture_output=True, cwd=f.root, env=f.env(), timeout=5)
                self.assertEqual((result.returncode, result.stdout), (1, b''))
                self.assertIn(b'compatibility_preflight', result.stderr)
                self.assertEqual(f.api_calls(), [])
                self.assertFalse(Path(f.memory['state_dir']).exists())

    def test_expiration_during_checked_bootstrap_prevents_any_child_launch(self):
        for role, budget, event in (('memory', 13, 'UserPromptSubmit'), ('coordination', 2, 'PostToolUse')):
            f = self.fixture('prelaunch-' + role)
            source = f.sources / 'lifecycle_compatibility.py'
            # TEST-only selected source sleeps in the checked bootstrap, never
            # while the installer imports its material writer. The actual R02
            # publisher guards, pins, seals and renders this whole new closure.
            source.write_bytes(source.read_bytes() + (
                "\nif __name__ == 'cairn_checked_bootstrap':\n"
                " import time as _fixture_clock\n _fixture_clock.sleep(" + str(budget + 0.05) + ")\n").encode())
            f.publish_configuration()
            f.raw = encoded(dict(f.event, hook_event_name=event, prompt='ordinary prompt'))
            result, elapsed, trace = self.traced(f, role, budget)
            self.assertEqual((result.returncode, result.stdout), (1, b''))
            self.assertIn(b'OSError' if role == 'memory' else b'TimeoutError', result.stderr)
            self.assertGreaterEqual(elapsed, budget)
            self.assertLess(elapsed, budget + 2)
            self.assertFalse(any('execve("/proc/self/fd/' in row for row in trace))
            self.assertEqual(f.api_calls(), [])
            self.assertFalse(Path(f.memory['state_dir']).exists())


class R02InstalledAdmissionTests(InstalledContractTools, unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        selected_stager = patch.object(retained, 'STAGER', str(self.stager))
        selected_stager.start()
        self.addCleanup(selected_stager.stop)

    def test_source_exclusions_precede_unopened_invalid_binding_data(self):
        cases = ('lifecycle-disabled', 'child', 'coordination-disabled', 'wake-context', 'other-home',
                 'memory-marker', 'coordination-marker', 'unsupported', 'noncanonical-uuid')
        for case in cases:
            f = self.fixture(case)
            env = f.env()
            if case == 'lifecycle-disabled': env['CAIRN_LIFECYCLE_DISABLED'] = '1'
            elif case == 'child': env['CAIRN_LIFECYCLE_CHILD'] = '1'
            elif case == 'coordination-disabled': env['CAIRN_COORDINATION_DISABLED'] = '1'
            elif case == 'wake-context': env['CAIRN_WAKE_CONTEXT'] = 'fixture-context'
            elif case == 'other-home':
                f.memory['config_home'] = f.coord['config_home'] = str(f.root / 'unselected-home')
                f.publish_configuration()
                env['CODEX_HOME'] = str(f.root / 'selected-home')
            elif case.endswith('marker'):
                (f.root / ('.cairn-no-memory' if case == 'memory-marker' else '.cairn-no-coordination')).touch()
            elif case == 'unsupported': f.raw = encoded(dict(f.event, hook_event_name='Stop'))
            else: f.raw = encoded(dict(f.event, session_id=SESSION.upper().replace('11111111', 'AAAAAAAA', 1)))
            overwrite_fixture(f.binding, b'not JSON and must remain unread')
            for role in ('memory', 'coordination'):
                with self.subTest(case=case, role=role):
                    result = subprocess.run(f.activation_argv(role), input=f.raw, capture_output=True,
                                            cwd=f.root, env=env, timeout=15)
                    if case == 'noncanonical-uuid': self.assertEqual(result.returncode, 1)
                    else: self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'{"enabled":false}\n', b''))
                    self.assertFalse(Path(f.memory['state_dir']).exists())
                    self.assertEqual(f.api_calls(), [])

    def test_actual_private_three_and_installed_legacy_two_refuse_each_others_binding(self):
        for harness in ('codex', 'claude'):
            for enabled in (False, True):
                f = self.fixture(harness + str(enabled), harness=harness, enabled=enabled)
                old = retained.GenerationInstallation()
                old.harness = harness
                old.setUp()
                self.addCleanup(old.doCleanups)
                before = old.inspect(old.old)
                old.legacy_frontends(old.launches)
                old.validate_legacy()
                # Keep the immutable installed /2 bundle/configuration/argv
                # unchanged while invoking its complete frontends on /3.
                for role in ('memory', 'coordination'):
                    with self.subTest(harness=harness, enabled=enabled, role=role):
                        config = f.memory_path if role == 'memory' else f.coord_path
                        argv = list(old.launches[role])
                        argv[argv.index('--config') + 1] = str(config)
                        raw = encoded(dict(f.event, hook_event_name='UserPromptSubmit' if role == 'memory' else 'SessionStart',
                                           prompt=f.wake() if role == 'memory' else '', host_pid=os.getpid()))
                        refused = subprocess.run(argv, input=raw, capture_output=True, cwd=f.root, env=f.env(), timeout=18)
                        expected = (b'Cairn lifecycle diagnostic: phase=activation error_class=ValueError\n'
                                    b'Cairn lifecycle: invalid lifecycle input or unavailable local file; use native tools or an explicit handoff.\n'
                                    if role == 'memory' else
                                    b'Cairn coordination: ValueError; session presence unavailable\n')
                        self.assertEqual((refused.returncode, refused.stdout, refused.stderr), (1, b'', expected))
                        # An independently pinned adversarial receipt permits
                        # selection of the actual older config/path, but cannot
                        # promote its /2 bytes to the private image's /3 ABI.
                        receipt = copy.deepcopy(f.receipt)
                        descriptor = lambda path: dict(path=str(path), sha256=sha(path.read_bytes()))
                        receipt['memory_config'] = descriptor(old.old_dir / 'memory.json')
                        receipt['coordination_configs'] = [descriptor(old.old_dir / 'coordination.json')]
                        receipt['binding'] = dict(descriptor(old.old_dir / 'binding.json'), schema='cairn.inbox-recall-binding/3')
                        receipt_path = f.root / 'incompatible-receipt.json'
                        receipt_path.write_bytes(encoded(receipt)); receipt_path.chmod(0o600)
                        selected = receipt['memory_config'] if role == 'memory' else receipt['coordination_configs'][0]
                        argv = [str(f.binary), 'lifecycle', 'binding', 'activation', '--entrypoint', role,
                                '--config', selected['path'], '--config-sha256', selected['sha256'],
                                '--binding-sha256', receipt['binding']['sha256'], '--receipt', str(receipt_path),
                                '--receipt-sha256', sha(receipt_path.read_bytes()), '--deadline', str(time.monotonic() + 13)]
                        refused = subprocess.run(argv, input=f.raw, capture_output=True, cwd=f.root, env=f.env(), timeout=15)
                        self.assertEqual((refused.returncode, refused.stdout, refused.stderr),
                                         (1, b'', b'{"error":{"class":"ValueError","reason":"activation","errno":null}}\n'))
                        self.assertFalse(Path(f.memory['state_dir']).exists())
                        self.assertEqual(f.api_calls(), [])
                self.assertEqual(old.inspect(old.old), before)
                old.assert_preserved()


if __name__ == '__main__':
    unittest.main()
