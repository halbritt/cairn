"""Actual R-02 material producer feeding the real F-04 activation processes."""
import hashlib
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from test_f04_activation import ROOT, SESSION, TURN, Fixture, encoded, compat
import test_f04_activation as activation
import test_f04_issued as issued
import test_lifecycle_generation_install as retained
from unittest.mock import patch


class InstalledFixture(Fixture):
    def __init__(self, root, binary, *, stager, **kwargs):
        self.stager = stager
        self.image_source = Path(binary)
        super().__init__(root, binary, **kwargs)
        self.install_root = root / 'release'
        self.install_root.mkdir(mode=0o700)
        self.package = root / 'package'
        self.package.mkdir(mode=0o700)
        self.installer = self.package / 'installer.py'
        shutil.copyfile(ROOT / 'scripts/stage_lifecycle_compatibility.py', self.installer)
        self.sources = self.package / 'sources'
        self.sources.mkdir(mode=0o700)
        for name in compat.MEMBERS:
            shutil.copyfile(ROOT / 'integrations/lifecycle' / name, self.sources / name)
            (self.sources / name).chmod(0o600)
        self.selected = None
        self.next_enabled = kwargs.get('enabled', True)
        self.publish_configuration()

    def installer_call(self, *args, body=None):
        return subprocess.run([sys.executable, '-I', '-S', '-B', str(self.installer), '--stager', str(self.stager), *args],
                              input=encoded(body) if body is not None else None, capture_output=True, cwd=self.root, timeout=40)

    def configuration_plan(self, *, enabled=None):
        if enabled is not None:
            self.next_enabled = enabled
        identity = str(uuid.uuid4())
        final = self.install_root / 'generations' / ('selection-' + identity)
        inputs = self.package / identity
        inputs.mkdir(mode=0o700)
        for role, config in [('memory', self.memory), ('coordination', self.coord)]:
            config['inbox_recall_binding'] = str(final / 'binding.json')
            path = inputs / (role + '.json')
            path.write_bytes(encoded(config))
            path.chmod(0o600)
        def descriptor(path): return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        plan = dict(id=identity, enabled=self.next_enabled, source_revision='f662f198c87e14acfc927552272bc4d3f6896602',
                    sources={name: descriptor(self.sources / name) for name in compat.MEMBERS}, binary=descriptor(self.image_source),
                    memory_config=descriptor(inputs / 'memory.json'), coordination_configs=[descriptor(inputs / 'coordination.json')],
                    aliases={'memory-hook.py': 'memory.py', 'coordination-hook.py': 'coordination.py', 'manifest.json': 'memory.py'},
                    python=sys.executable, shell='/bin/sh')
        return plan

    def publish_configuration(self, *, enabled=None):
        plan = self.configuration_plan(enabled=enabled)
        self.plan = plan
        result = self.installer_call('stage', '--root', str(self.install_root), body=plan)
        if result.returncode != 0:
            raise AssertionError('real R02 installer failed: ' + result.stderr.decode(errors='replace'))
        self.installed = json.loads(result.stdout)
        self.selected = self.installed['selection']
        final = self.install_root / 'generations' / ('selection-' + plan['id'])
        self.memory_path, self.coord_path = final / 'memory.json', final / 'coordination-0.json'
        self.binding, self.receipt_path = final / 'binding.json', final / 'receipt.json'
        self.receipt = json.loads(self.receipt_path.read_bytes())
        self.receipt_sha = self.installed['receipt']['sha256']
        self.binary = Path(self.receipt['binary']['path'])
        self.launch_root = self.install_root / 'launch'

    def launcher(self, role, timeout=None):
        selected_timeout = timeout if timeout is not None else (13 if role == 'memory' else 15)
        matches = [argv[0] for label, argv in self.installed['launchers'].items()
                   if label.startswith(role + ':') and label.endswith(':' + str(selected_timeout))]
        if len(matches) != 1:
            raise AssertionError('ambiguous installed launcher')
        return Path(matches[0])


class InstalledTools:
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.stager = Path(cls.build.name) / 'stager'
        cls.binary = Path(cls.build.name) / 'private-activation'
        for output, target in [(cls.stager, './cmd/cairn-stage-lifecycle'), (cls.binary, './cmd/cairn-lifecycle')]:
            subprocess.run(['go', 'build', '-buildvcs=false', '-o', str(output), target], cwd=ROOT, check=True)
            output.chmod(0o500)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()


class R02CompatibilityInstallationTests(InstalledTools, unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def fixture(self, name, **kwargs):
        return InstalledFixture(self.root / name, self.binary, stager=self.stager, **kwargs)

    def test_installed_complete_frontends_reach_real_activation_and_retained_handler(self):
        for harness in ('codex', 'claude'):
            for role in ('memory', 'coordination'):
                for enabled in (False, True):
                    with self.subTest(harness=harness, role=role, enabled=enabled):
                        f = self.fixture(harness + role + str(enabled), harness=harness, enabled=enabled)
                        if role == 'coordination':
                            f.event.update(hook_event_name='SessionStart', prompt='', host_pid=os.getpid())
                            f.raw = encoded(f.event)
                        result = f.public(role)
                        self.assertEqual((result.returncode, result.stderr), (0, b''), result.stdout)
                        if role == 'memory':
                            self.assertEqual(json.loads(result.stdout), {})
                            name = SESSION + ('.memory-budget.json' if harness == 'codex' else '.inbox-recall.json')
                            state = json.loads((Path(f.memory['state_dir']) / name).read_bytes())
                            self.assertEqual(list(state['inbox_activation'].values()), [enabled])
                        else:
                            self.assertIn('agent-fixture', json.loads(result.stdout)['hookSpecificOutput']['additionalContext'])
                            self.assertIn('agent-register', f.api.with_suffix('.calls').read_text().splitlines())
                        self.assertNotEqual(str(f.binary), f.memory['cairn'])
                        self.assertEqual(f.receipt['members']['manifest.json'], f.receipt['members']['memory.py'])

    def test_exact_reuse_keeps_published_inodes_and_inspection_rejects_alias_tampering(self):
        f = self.fixture('reuse')
        alias = Path(f.receipt['snapshot_root']) / 'manifest.json'
        before = alias.stat()
        result = f.installer_call('stage', '--root', str(f.install_root), body=f.plan)
        self.assertEqual((result.returncode, result.stderr), (0, b''))
        self.assertEqual(json.loads(result.stdout)['selection'], f.selected)
        self.assertEqual((before.st_dev, before.st_ino), (alias.stat().st_dev, alias.stat().st_ino))
        alias.chmod(0o600)
        alias.write_bytes(b'changed alias\n')
        alias.chmod(0o400)
        result = f.installer_call('inspect', '--path', f.selected['path'], '--sha256', f.selected['sha256'])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b'')
        result = f.public('memory')
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.assertIn(b'CompatibilityPreflightError', result.stderr)
        self.assertFalse(Path(f.memory['state_dir']).exists())

    def test_reverse_selects_the_exact_retained_legacy_generation_after_new_activation(self):
        legacy = retained.GenerationInstallation()
        with patch.object(retained, 'STAGER', str(self.stager)):
            legacy.setUp()
            self.addCleanup(legacy.doCleanups)
            before = legacy.legacy_frontends(legacy.launches)
            original = legacy.inspect(legacy.old)
            f = self.fixture('reverse')
            f.memory['state_dir'] = str(legacy.state)
            f.publish_configuration()
            result = f.public('memory')
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'{}\n', b''))
            # Explicit old pin, original argv and byte readback. No active
            # selector, cached callback, or /2 descriptor is rewritten.
            result = f.installer_call('inspect', '--legacy', '--path', legacy.old['path'], '--sha256', legacy.old['sha256'])
            self.assertEqual((result.returncode, result.stderr), (0, b''))
            reverse = json.loads(result.stdout)['launchers']
            self.assertEqual(reverse, legacy.launches)
            self.assertEqual(legacy.inspect(legacy.old), original)
            self.assertEqual(legacy.legacy_frontends(reverse), before)
            legacy.assert_preserved()

    def test_new_disabled_selection_does_not_change_cached_enabled_argv_or_unrelated_bytes(self):
        f = self.fixture('disabled', enabled=True)
        old_launcher = f.launcher('memory')
        old_selection = dict(f.selected)
        unrelated = f.root / 'unrelated.json'
        unrelated.write_bytes(b'{ "account": "retained", "other": [1,2] }\n')
        token = f.root / 'unread-token'
        token.write_bytes(b'synthetic token; never a staging member\n')
        token.chmod(0o000)
        f.memory['opaque'] = {'custom': [1, 'two', False]}
        f.publish_configuration(enabled=False)
        for launcher, session, expected in ((old_launcher, SESSION, True),
                                           (f.launcher('memory'), '99999999-9999-4999-8999-999999999999', False)):
            result = subprocess.run([str(launcher)], input=encoded(dict(f.event, session_id=session)),
                                    capture_output=True, env=f.env(), timeout=18)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'{}\n', b''))
            state = json.loads((Path(f.memory['state_dir']) / (session + '.memory-budget.json')).read_bytes())
            self.assertEqual(list(state['inbox_activation'].values()), [expected])
        self.assertNotEqual(old_selection, f.selected)
        self.assertEqual(json.loads(f.memory_path.read_bytes())['opaque'], f.memory['opaque'])
        self.assertEqual(unrelated.read_bytes(), b'{ "account": "retained", "other": [1,2] }\n')
        self.assertEqual(token.stat().st_mode & 0o777, 0)

    def test_missing_or_changed_installed_components_and_direct_member_refuse_before_effects(self):
        for kind in ('bootstrap', 'loader', 'helper', 'alias', 'binary', 'profile', 'direct'):
            with self.subTest(kind=kind):
                f = self.fixture(kind)
                snapshot = Path(f.receipt['snapshot_root'])
                if kind == 'direct':
                    result = subprocess.run([sys.executable, '-I', '-S', str(snapshot / 'memory.py'),
                                             '--config', str(f.memory_path)], input=f.raw, capture_output=True, timeout=15)
                else:
                    target = {'bootstrap': snapshot / 'lifecycle_compatibility.py', 'loader': snapshot / 'lifecycle_bundle.py',
                              'helper': snapshot / 'grant_ledger.py', 'alias': snapshot / 'manifest.json',
                              'binary': f.binary, 'profile': f.memory_path}[kind]
                    target.chmod(0o600)
                    target.write_bytes(b'changed selected input\n')
                    target.chmod(0o500 if kind == 'binary' else 0o400)
                    result = f.public('memory')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b'')
                self.assertFalse(Path(f.memory['state_dir']).exists())
                self.assertEqual(f.api_calls(), [])

    def test_qualified_shell_keeps_startup_files_unread_and_rejects_bash_startup(self):
        f = self.fixture('shell')
        marker = f.root / 'startup-read'
        startup = f.root / 'startup'
        startup.write_text('printf unexpected > ' + str(marker) + '\n')
        result = subprocess.run([str(f.launcher('memory'))], input=f.raw, capture_output=True, timeout=18,
                                env=dict(f.env(), ENV=str(startup), BASH_ENV=str(startup)))
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'{}\n', b''))
        self.assertFalse(marker.exists())
        plan = f.configuration_plan()
        plan['shell'] = '/bin/bash'
        result = f.installer_call('stage', '--root', str(f.install_root), body=plan)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'startup qualification', result.stderr)
        self.assertFalse((f.install_root / 'generations' / ('selection-' + plan['id'])).exists())

    def test_conflicting_install_refuses_without_changing_old_selection(self):
        f = self.fixture('conflict')
        plan = f.configuration_plan()
        locked = os.open(f.install_root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(locked, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = f.installer_call('stage', '--root', str(f.install_root), body=plan)
        finally:
            os.close(locked)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b'')
        result = f.installer_call('inspect', '--path', f.selected['path'], '--sha256', f.selected['sha256'])
        self.assertEqual((result.returncode, result.stderr), (0, b''))

    def fault_stager(self, f, *, abort=None, stop=False):
        """A test transport around the real CLI, never a publication substitute."""
        wrapper, count, stopped = f.root / 'fault-stager', f.root / 'call-count', f.root / 'stopped'
        wrapper.unlink(missing_ok=True)
        count.write_text('0')
        code = '''import json,os,pathlib,signal,subprocess,sys
real,count,stopped,abort,stop=VALUES
body=sys.stdin.buffer.read()
args=sys.argv[1:]
mutation=args[0] in ('--root','directory','generation')
number=int(pathlib.Path(count).read_text())+int(mutation)
pathlib.Path(count).write_text(str(number))
if mutation and stop:
 pathlib.Path(stopped).write_text(str(os.getpid()))
 os.kill(os.getpid(),signal.SIGSTOP)
if mutation and abort==[number,'before']: sys.exit(81)
result=subprocess.run([real,*args],input=body,capture_output=True)
if result.returncode: sys.stderr.buffer.write(result.stderr);sys.exit(result.returncode)
if mutation and abort==[number,'after']: sys.exit(82)
sys.stdout.buffer.write(result.stdout)
'''.replace('VALUES', repr((str(f.stager), str(count), str(stopped), abort, stop)))
        wrapper.write_text('#!' + sys.executable + '\n' + code)
        wrapper.chmod(0o500)
        return wrapper, stopped

    def test_every_component_publication_failure_reconciles_exactly_and_keeps_old_selection(self):
        f = self.fixture('publication')
        original_stager = f.stager
        # Binary, snapshot, four exact L/V selections, then configuration and
        # receipt generation. The Go tests interrupt each fsync within them.
        for number in range(1, 8):
            for side in ('before', 'after'):
                with self.subTest(component=number, side=side):
                    plan = f.configuration_plan(enabled=False)
                    wrapper, _ = self.fault_stager(f, abort=[number, side])
                    f.stager = wrapper
                    try:
                        failed = f.installer_call('stage', '--root', str(f.install_root), body=plan)
                    finally:
                        f.stager = original_stager
                    self.assertNotEqual(failed.returncode, 0)
                    self.assertEqual(failed.stdout, b'')
                    read = f.installer_call('inspect', '--path', f.selected['path'], '--sha256', f.selected['sha256'])
                    self.assertEqual((read.returncode, read.stderr), (0, b''))
                    reconciled = f.installer_call('stage', '--root', str(f.install_root), body=plan)
                    self.assertEqual((reconciled.returncode, reconciled.stderr), (0, b''))
                    pin = json.loads(reconciled.stdout)['selection']
                    again = f.installer_call('stage', '--root', str(f.install_root), body=plan)
                    self.assertEqual((again.returncode, again.stderr), (0, b''))
                    self.assertEqual(json.loads(again.stdout)['selection'], pin)
                    self.assertFalse(Path(f.memory['state_dir']).exists())
                    self.assertEqual(f.api_calls(), [])

    def test_live_competing_installer_refuses_and_interrupted_owner_joins_its_child(self):
        f = self.fixture('live-conflict')
        plan = f.configuration_plan()
        wrapper, stopped = self.fault_stager(f, stop=True)
        argv = [sys.executable, '-I', '-S', '-B', str(f.installer), '--stager', str(wrapper),
                'stage', '--root', str(f.install_root)]
        child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=f.root)
        try:
            child.stdin.write(encoded(plan)); child.stdin.close(); child.stdin = None
            deadline = time.monotonic() + 10
            while not stopped.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(stopped.exists(), 'first installer never acquired the root and reached its child')
            backend_pid = int(stopped.read_text())
            competing = f.installer_call('stage', '--root', str(f.install_root), body=plan)
            self.assertNotEqual(competing.returncode, 0)
            self.assertEqual(competing.stdout, b'')
            child.send_signal(signal.SIGTERM)
            stdout, stderr = child.communicate(timeout=10)
            self.assertNotEqual(child.returncode, 0)
            self.assertEqual(stdout, b'')
            self.assertIn(b'interrupted', stderr)
            with self.assertRaises(ProcessLookupError):
                os.kill(backend_pid, 0)
        finally:
            if child.poll() is None:
                child.terminate()
                child.communicate(timeout=10)
        result = f.installer_call('stage', '--root', str(f.install_root), body=plan)
        self.assertEqual((result.returncode, result.stderr), (0, b''))

    def test_invalid_input_pins_or_profile_paths_do_not_publish_a_generation(self):
        for kind in ('source-pin', 'source-link', 'missing-member', 'bad-alias', 'profile-path', 'no-coordinators'):
            with self.subTest(kind=kind):
                f = self.fixture('input-' + kind)
                plan = f.configuration_plan()
                if kind == 'source-pin':
                    plan['sources']['memory.py']['sha256'] = '0' * 64
                elif kind == 'source-link':
                    path = f.sources / 'memory.py'
                    other = path.with_name('other')
                    path.rename(other)
                    path.symlink_to(other)
                elif kind == 'missing-member':
                    del plan['sources']['grant_ledger.py']
                elif kind == 'bad-alias':
                    plan['aliases']['../escape'] = 'memory.py'
                elif kind == 'profile-path':
                    path = Path(plan['memory_config']['path'])
                    path.write_bytes(encoded(dict(f.memory, inbox_recall_binding='/unselected/binding.json')))
                    plan['memory_config']['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
                else:
                    plan['coordination_configs'] = []
                result = f.installer_call('stage', '--root', str(f.install_root), body=plan)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b'')
                self.assertFalse((f.install_root / 'generations' / ('selection-' + plan['id'])).exists())
                inspected = f.installer_call('inspect', '--path', f.selected['path'], '--sha256', f.selected['sha256'])
                self.assertEqual((inspected.returncode, inspected.stderr), (0, b''))
                self.assertEqual(f.api_calls(), [])


class R02InstalledIssuedTests(InstalledTools, issued.F04IssuedTests):
    """The unchanged F-04 command assertions over real installer output."""

    def fixture(self, root, **kwargs):
        return InstalledFixture(root, self.binary, stager=self.stager, **kwargs)


class R02InstalledActivationTests(InstalledTools, activation.F04ActivationTests):
    """The same public decoding, ledger and retained-handler expectations."""

    def fixture(self, name='go', **kwargs):
        return InstalledFixture(self.root / name, self.binary, stager=self.stager, **kwargs)
