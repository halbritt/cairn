"""Owned writer leaf and release result. Real inert local processes; no case, oracle, model or provider.

Two kinds of test are kept apart:

- SYNTHETIC OWNER MACHINERY (always run): a FakeGroup stands in for the
  cgroup. Its "cgroup.procs" is a regular file and its population and kill are
  scripted, so these tests exercise the launcher ordering, the result mapping
  and the capture integration. They do NOT perform or observe cgroup
  membership, kill or release.
- REAL DELEGATED CGROUP (skipped unless the process already runs in an owned
  delegated fixture service): these exercise the actual OwnedCgroup leaf.
  They are not run, and not claimed, in an ordinary agent session.
"""
import json
import os
from pathlib import Path
import resource
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import trial_owned_workers as owned
import trial_writer_release as writer
from trial_native_capture import capture

LAUNCHER = writer.LAUNCHER
CURRENT = Path('/proc/self/cgroup').read_text().strip()[3:]


def delegation():
    try:
        from trial_external_supervision import fixture_service_delegation
        return fixture_service_delegation()
    except Exception:
        return None


class FakeGroup:
    """Synthetic stand-in: regular-file procs, scripted population and kill (no cgroup operations)."""

    def __init__(self, delegated, controls=None, *, populated=(False,), kill_error=False, events_error=False,
                 close_errors=(), relative=CURRENT):
        self.root = Path(delegated)
        self.controls = controls
        self.procs_path = self.root / 'cgroup.procs'
        self.files = {'cgroup.procs': os.open(self.procs_path, os.O_WRONLY | os.O_CREAT | os.O_CLOEXEC, 0o600)}
        self.relative = relative
        self.population, self.kill_error, self.events_error = list(populated), kill_error, events_error
        self.close_errors, self.kills = list(close_errors), 0

    def kill(self):
        self.kills += 1
        if self.kill_error:
            raise OSError('synthetic kill failure')

    def populated(self):
        if self.events_error:
            raise owned.Unavailable('cgroup_release_unknown')
        return self.population.pop(0) if len(self.population) > 1 else self.population[0]

    def close(self):
        os.close(self.files.pop('cgroup.procs'))
        return list(self.close_errors)


class Base(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def leaf(self, **fake):
        return writer.WriterLeaf(str(self.root), {}, group_factory=lambda delegated, controls: FakeGroup(
            delegated, controls, **fake))

    def wait_exact(self, pid, seconds=10):
        """Wait on an exact process handle (pidfd); kill through it if still alive at the deadline."""
        try:
            fd = os.pidfd_open(pid)
        except ProcessLookupError:
            return True
        try:
            if select.select([fd], [], [], seconds)[0]:
                return True
            signal.pidfd_send_signal(fd, signal.SIGKILL)
            select.select([fd], [], [], 5)
            return False
        finally:
            os.close(fd)


    def run_leaf(self, leaf, argv, timeout=10):
        command = leaf.wrap(argv)
        run = Path(tempfile.mkdtemp(dir=self.root))
        out, err, code, _ = capture(command, timeout, run / 'timing.json', custody_path=run / 'custody.json',
                                    pass_fds=leaf.pass_fds)
        bootstrap = leaf.bootstrap()
        try:
            released = leaf.release(time.monotonic() + 0.3)
        finally:
            closed = leaf.close()
        custody = json.loads((run / 'custody.json').read_text())
        return writer.result(run_id='synthetic.CURRENT.s0', workspace=self.root / 'work', bootstrap=bootstrap,
                             release=released, close_errors=closed, custody=custody, controls={}), out, code



class PolicyTests(Base):
    def owned_writes(self, controls):
        writes = {}

        def read(group, name):
            return 'domain\n' if name == 'cgroup.type' else (
                'populated 0\n' if name == 'cgroup.events' else writes.get(name, ''))

        def write(group, name, data):
            writes[name] = data.decode()
        opened = lambda group, name, flags: os.open(os.devnull, os.O_RDWR | os.O_CLOEXEC)
        with patch.object(owned.OwnedCgroup, 'read', read), patch.object(owned.OwnedCgroup, 'write', write), \
                patch.object(owned.OwnedCgroup, 'open', opened):
            group = owned.OwnedCgroup(str(self.root)) if controls == 'default' else owned.OwnedCgroup(str(self.root), controls)
            group.close()
        return writes

    def test_fixture_default_policy_is_unchanged(self):
        self.assertEqual(owned.CONTROLS, dict(memory_max=512*1024*1024, memory_swap_max=0, pids_max=32,
                                              cpu_max='100000 100000'))
        self.assertEqual(self.owned_writes('default'), {k.replace('_', '.'): str(v) for k, v in owned.CONTROLS.items()})

    def test_legacy_policy_is_applied_exactly(self):
        self.assertEqual(self.owned_writes({}), {})
        self.assertEqual(self.owned_writes(dict(pids_max=4096)), {'pids.max': '4096'})

    def test_selected_policy_accepts_only_known_controls(self):
        for bad in (None, [], dict(io_max='x'), dict(pids_max=None), dict(pids_max='')):
            with self.subTest(bad=bad), self.assertRaisesRegex(writer.WriterUnavailable, 'writer_controls_invalid'):
                writer.check_controls(bad)

    def test_unavailable_containment_refuses_before_launch(self):
        def unavailable(delegated, controls):
            raise owned.Unavailable('containment_or_resource_unavailable')
        with self.assertRaisesRegex(writer.WriterUnavailable, 'writer_containment_unavailable'):
            writer.WriterLeaf(str(self.root), {}, group_factory=unavailable)
        # An ordinary agent session has no delegated leaf to create: the real primitive refuses.
        if delegation() is None:
            with self.assertRaises(writer.WriterUnavailable):
                writer.WriterLeaf('/sys/fs/cgroup' + CURRENT, {})


TARGET = r'''
import json, os, resource, sys
Path = __import__('pathlib').Path
Path(sys.argv[1]).write_text(json.dumps(dict(pid=os.getpid(), env=dict(os.environ),
    core=list(resource.getrlimit(resource.RLIMIT_CORE)), fsize=list(resource.getrlimit(resource.RLIMIT_FSIZE)))))
'''


class LauncherTests(Base):
    def launch(self, relative=CURRENT, procs=None, env=None):
        procs_fd = procs if procs is not None else os.open(self.root / 'procs', os.O_WRONLY | os.O_CREAT, 0o600)
        status_read, status_write = os.pipe()
        report = self.root / 'target.json'

        def limits():  # values the fixture launcher would have overwritten
            resource.setrlimit(resource.RLIMIT_CORE, (4096, resource.getrlimit(resource.RLIMIT_CORE)[1]))
        done = subprocess.run(['/usr/bin/python3', '-B', str(LAUNCHER), str(procs_fd), relative, str(status_write),
                               '/usr/bin/python3', '-c', TARGET, str(report)],
                              pass_fds=(procs_fd, status_write), env=env or dict(PATH='/usr/bin:/bin', MARKER='kept'),
                              preexec_fn=limits, timeout=20)
        os.close(status_write)
        status = os.read(status_read, 256)
        os.close(status_read)
        if procs is None:
            os.close(procs_fd)
        return done.returncode, status, report

    def test_exec_happens_only_after_membership_is_written_and_verified(self):
        code, status, report = self.launch()
        target = json.loads(report.read_text())
        self.assertEqual(code, 0)
        self.assertEqual((self.root / 'procs').read_text(), str(target['pid']))
        self.assertEqual(status, b'member %d\n' % target['pid'])

    def test_legacy_rlimits_and_environment_pass_through_unchanged(self):
        code, _, report = self.launch(env=dict(PATH='/usr/bin:/bin', MARKER='kept', HOME='/legacy-home'))
        target = json.loads(report.read_text())
        self.assertEqual(target['core'][0], 4096)  # the fixture launcher would set 0
        self.assertEqual(target['fsize'], list(resource.getrlimit(resource.RLIMIT_FSIZE)))  # no 64 MiB cap
        self.assertEqual({k: target['env'][k] for k in ('MARKER', 'HOME', 'PATH')},
                         dict(MARKER='kept', HOME='/legacy-home', PATH='/usr/bin:/bin'))
        self.assertNotIn('LANG', target['env'])  # no replacement host environment

    def test_membership_mismatch_or_failed_join_never_execs(self):
        code, status, report = self.launch(relative='/not/this/leaf')
        self.assertEqual((code, status), (125, b''))
        self.assertFalse(report.exists())
        reader, closed = os.pipe()
        os.close(reader)  # writing the pid into a broken descriptor fails before any exec
        code, status, report = self.launch(procs=closed)
        self.assertEqual((code, status), (125, b''))
        self.assertFalse(report.exists())


class ReleaseMappingTests(Base):
    def test_member_then_release_is_the_only_released_state(self):
        result, out, code = self.run_leaf(self.leaf(populated=(True, False)), ['/usr/bin/echo', 'inert'])
        self.assertEqual((result['state'], result['release'], result['bootstrap']['state']), ('released', 'released', 'member'))
        self.assertEqual(out, b'inert\n')
        self.assertEqual(result['attempt'], dict(run_id='synthetic.CURRENT.s0', workspace=str(self.root / 'work')))
        # waitpid status and stream EOF are separate observations; descendant custody is never upgraded.
        self.assertEqual((result['waitpid']['observed_returncode'], result['waitpid']['leader_reaped']), (0, True))
        self.assertEqual(result['stream_eof'], dict(stdout=True, stderr=True))
        self.assertEqual((result['migration_control'], result['complete_descendant_custody']), ('unknown', False))

    def test_each_failure_keeps_its_own_state(self):
        cases = (
            (dict(kill_error=True), 'kill_unknown'),
            (dict(events_error=True), 'events_unknown'),
            (dict(populated=(True,)), 'not_released'),  # the release never arrives
            (dict(close_errors=('cgroup_directory_removal_unknown',)), 'close_unknown'),
            (dict(relative='/not/this/leaf'), 'bootstrap_unobserved'),
        )
        for fake, state in cases:
            with self.subTest(state=state):
                result, _, _ = self.run_leaf(self.leaf(**fake), ['/usr/bin/true'])
                self.assertEqual(result['state'], state)
                self.assertNotEqual(result['state'], 'released')

    def test_bootstrap_failure_never_runs_the_actor(self):
        marker = self.root / 'ran'
        result, _, code = self.run_leaf(self.leaf(relative='/not/this/leaf'), ['/usr/bin/touch', str(marker)])
        self.assertEqual((code, result['bootstrap']['state']), (125, 'unobserved'))
        self.assertFalse(marker.exists())

    def test_bootstrap_timeout_is_unobserved_and_the_actor_never_runs(self):
        fifo = self.root / 'procs-fifo'
        os.mkfifo(fifo)
        held = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
        try:
            while True:  # fill the pipe so the launcher's membership write blocks
                os.write(held, b'x' * 65536)
        except BlockingIOError:
            pass
        os.set_blocking(held, True)  # the shared description now blocks the launcher's write
        group = FakeGroup(str(self.root))
        os.close(group.files['cgroup.procs'])
        group.files['cgroup.procs'] = held
        leaf = writer.WriterLeaf(str(self.root), {}, group_factory=lambda delegated, controls: group)
        marker = self.root / 'ran'
        result, _, code = self.run_leaf(leaf, ['/usr/bin/touch', str(marker)], timeout=1)
        self.assertEqual(code, 124)
        self.assertEqual(result['state'], 'bootstrap_unobserved')
        self.assertFalse(marker.exists())


WRITERS = {
    'double_fork': r'''
import os, sys, time
if os.fork() == 0:
    if os.fork() == 0:
        open(sys.argv[1] + '.pid', 'w').write(str(os.getpid()))
        for _ in range(40):
            open(sys.argv[1], 'a').write('x'); time.sleep(.05)
    os._exit(0)
''',
    'setsid': r'''
import os, sys, time
if os.fork() == 0:
    os.setsid()
    open(sys.argv[1] + '.pid', 'w').write(str(os.getpid()))
    for _ in range(40):
        open(sys.argv[1], 'a').write('x'); time.sleep(.05)
    os._exit(0)
''',
    'after_pipe_close': r'''
import os, sys, time
if os.fork() == 0:
    for fd in (0, 1, 2):
        os.close(fd)  # the stream reaches EOF while this writer continues
    open(sys.argv[1] + '.pid', 'w').write(str(os.getpid()))
    for _ in range(40):
        open(sys.argv[1], 'a').write('x'); time.sleep(.05)
    os._exit(0)
''',
}


class EscapingWriterSyntheticTests(Base):
    """Without real delegation the owner cannot observe population: escaping writers are never 'released'."""

    def test_escaping_writers_are_never_reported_released_without_an_observed_leaf(self):
        for name, program in WRITERS.items():
            with self.subTest(writer=name):
                output = self.root / name
                result, _, _ = self.run_leaf(self.leaf(events_error=True),
                                             ['/usr/bin/python3', '-c', program, str(output)])
                self.assertEqual(result['state'], 'events_unknown')
                self.assertEqual(result['waitpid']['leader_reaped'], True)
                pid_file = Path(str(output) + '.pid')
                if pid_file.exists():
                    self.wait_exact(int(pid_file.read_text()))



class RunAgentIntegrationTests(Base):
    """The legacy original attempt through run_agent with the synthetic owner machinery (no cgroup operations)."""

    def setUp(self):
        super().setUp()
        import test_trial_original_limits_boundary as boundary
        self.boundary = boundary
        source = self.root / 'source'
        source.mkdir()
        (source / 'synthetic.txt').write_text('original\n')
        (source / 'setup.sh').write_text('git init -q\ngit add .\ngit commit -qm initial\n')
        self.case = dict(id='synthetic', workspace='synthetic', _prospective_workspace=str(source),
                         copy_to='repo', cwd='repo', category='synthetic', provenance={'type': 'synthetic'},
                         expected=[], wordings={'task': 'Make a synthetic change.'},
                         correct=[dict(type='unchanged', path='synthetic.txt')], mistake=[])
        hook = self.root / 'hook'
        hook.mkdir()
        (hook / 'memory.py').write_text('# synthetic hook\n')
        self.stores = {'CURRENT': dict(store=boundary.Store(self.root), hook=str(hook / 'memory.py'), configuration={})}
        self.out = self.root / 'output'
        self.out.mkdir(mode=0o700)
        self.delegated = self.root / 'delegated'
        self.delegated.mkdir()
        import types
        self.args = types.SimpleNamespace(harness='claude', model='inert', wording='task', timeout=20, max_turns=2,
                                          semantic_recall=[], original_limits=True, selector_model='inert',
                                          _original_writer=dict(delegated=str(self.delegated), controls={}))

    def run_agent(self, **fake):
        import trial_task_eval as task
        self.commands = []

        def inert(root, cwd, binds, env, argv, harness, **kwargs):
            hookstate = next(src for src, target, _ in binds if str(target) == '/tmp/trial/hookstate')
            command = [sys.executable, '-c', self.boundary.ACTOR, str(hookstate), '0.1', 'Done.']
            return command
        real_wrap = writer.WriterLeaf.wrap

        def wrap(leaf, argv):
            command = real_wrap(leaf, argv)
            self.commands.append(command)
            return command
        factory = lambda delegated, controls: FakeGroup(delegated, controls, **fake)
        with patch.object(task, 'load_corpus', return_value=[]), patch.object(task, 'sandbox_command', side_effect=inert), \
                patch.object(writer.owned, 'OwnedCgroup', side_effect=factory), \
                patch.object(writer.WriterLeaf, 'wrap', wrap), \
                patch.object(task, 'grade', side_effect=task.grade) as grade:
            record = task.run_agent(self.case, 'CURRENT', 0, 0, self.args, self.stores, self.out)
        return record, grade

    def test_released_attempt_records_the_owner_result_before_grading(self):
        record, grade = self.run_agent(populated=(True, False))
        self.assertEqual(self.commands[0][:3], ['/usr/bin/python3', '-B', str(LAUNCHER)])
        result = record['writer_release']
        self.assertEqual((result['state'], result['bootstrap']['state']), ('released', 'member'))
        self.assertEqual(result['attempt']['run_id'], 'synthetic.CURRENT.s0')
        self.assertTrue(result['attempt']['workspace'].endswith('/runs/synthetic.CURRENT.s0/work/repo'))
        self.assertEqual(record['outcome'], 'correct')
        grade.assert_called_once()

    def test_an_unreleased_leaf_is_never_graded(self):
        for fake, state in ((dict(populated=(True,)), 'not_released'), (dict(events_error=True), 'events_unknown'),
                            (dict(close_errors=('cgroup_directory_removal_unknown',)), 'close_unknown')):
            with self.subTest(state=state):
                import shutil
                shutil.rmtree(self.out)
                self.out.mkdir(mode=0o700)
                record, grade = self.run_agent(**fake)
                self.assertEqual(record['writer_release']['state'], state)
                self.assertEqual((record['outcome'], record['execution_failure']), ('harness_error', 'writer_release_' + state))
                grade.assert_not_called()

    def test_unavailable_containment_refuses_before_the_actor(self):
        import trial_task_eval as task
        launched = []

        def unavailable(delegated, controls):
            raise owned.Unavailable('containment_or_resource_unavailable')
        with patch.object(task, 'load_corpus', return_value=[]), \
                patch.object(task, 'sandbox_command', side_effect=lambda *a, **k: launched.append(1) or ['/usr/bin/true']), \
                patch.object(writer.owned, 'OwnedCgroup', side_effect=unavailable), \
                patch.object(task, 'capture', side_effect=AssertionError('the actor must not start')):
            with self.assertRaisesRegex(RuntimeError, '^original_writer_containment_unavailable'):
                task.run_agent(self.case, 'CURRENT', 0, 0, self.args, self.stores, self.out)

    def test_cli_requires_the_cgroup_and_its_controls_together_with_original_limits(self):
        import io
        import trial_task_eval as task
        controls = self.root / 'controls.json'
        controls.write_text(json.dumps(dict(schema='cairn.writer-controls/1', controls={})))
        digest = __import__('hashlib').sha256(controls.read_bytes()).hexdigest()
        for extra in (['--original-writer-cgroup', str(self.delegated)],
                      ['--original-writer-controls', str(controls), '--original-writer-controls-sha256', digest],
                      ['--original-writer-cgroup', str(self.delegated), '--original-writer-controls', str(controls),
                       '--original-writer-controls-sha256', digest]):
            with self.subTest(extra=extra), self.assertRaises(SystemExit), patch('sys.stderr', io.StringIO()):
                task.main(['agent', '--legacy-fixtures', '--output', str(self.root / 'never'), '--arms', *extra])
        self.assertFalse((self.root / 'never').exists())

@unittest.skipUnless(delegation(), 'needs an already delegated owned fixture service cgroup')
class RealDelegatedLeafTests(Base):
    """Real OwnedCgroup leaf: membership, kill and populated come from the kernel."""

    def test_escaping_writers_are_released_by_the_owned_leaf(self):
        parent = delegation()
        for name, program in WRITERS.items():
            with self.subTest(writer=name):
                output = self.root / name
                leaf = writer.WriterLeaf(parent, {})
                out, err, code, _ = capture(leaf.wrap(['/usr/bin/python3', '-c', program, str(output)]), 10,
                                            self.root / f'{name}.timing', custody_path=self.root / f'{name}.custody',
                                            pass_fds=leaf.pass_fds)
                bootstrap = leaf.bootstrap()
                released = leaf.release(time.monotonic() + 5)
                closed = leaf.close()
                self.assertEqual((bootstrap['state'], released, closed), ('member', 'released', []))
                pid_file = Path(str(output) + '.pid')
                if pid_file.exists():
                    self.assertTrue(self.wait_exact(int(pid_file.read_text()), 1))


if __name__ == '__main__':
    unittest.main()
