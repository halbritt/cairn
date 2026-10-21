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
import contextlib
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
import unittest.mock
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
        self.close_errors, self.kills, self.closes = list(close_errors), 0, 0
        info = os.stat(self.root)
        self.identity = (info.st_dev, info.st_ino)

    def kill(self):
        self.kills += 1
        if self.kill_error:
            raise OSError('synthetic kill failure')

    def populated(self):
        if self.events_error:
            raise owned.Unavailable('cgroup_release_unknown')
        return self.population.pop(0) if len(self.population) > 1 else self.population[0]

    def close(self):
        self.closes += 1
        if 'cgroup.procs' in self.files:
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
        observed = leaf.finish(time.monotonic() + 0.3)
        custody = json.loads((run / 'custody.json').read_text())
        return writer.result(run_id='synthetic.CURRENT.s0', workspace=self.root / 'work', leaf=leaf,
                             observed=observed, custody=custody), out, code



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

    def test_status_pipe_failure_closes_the_acquired_group_and_keeps_the_original_error(self):
        groups = []

        def factory(delegated, controls):
            groups.append(FakeGroup(delegated, controls, close_errors=('cgroup_directory_removal_unknown',)))
            return groups[-1]
        with patch.object(writer.os, 'pipe2', side_effect=OSError(24, 'synthetic descriptor exhaustion')):
            with self.assertRaises(OSError) as caught:
                writer.WriterLeaf(str(self.root), {}, group_factory=factory)
        self.assertEqual(caught.exception.errno, 24)
        self.assertEqual(groups[0].closes, 1)
        self.assertIn('writer cleanup: cgroup_directory_removal_unknown', caught.exception.__notes__)

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
        self.assertEqual(status, b'member %d 0::%s\n' % (target['pid'], CURRENT.encode()))

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
        self.assertEqual((result['migration_control'], result['complete_descendant_custody']),
                         (dict(state='unknown', reasons=['launch_evidence_absent'], argument=writer.ARGUMENT), False))
        self.assertEqual((result['writer_quiescence'], result['final_artifact_custody']),
                         (dict(state='not_established', reason='init_unbound'), 'not_established'))
        # The owner's held identity, the selected parent and the membership the joined process observed.
        info = os.stat(self.root)
        self.assertEqual(result['leaf'], dict(parent=str(self.root), relative=CURRENT,
                                              held_identity=[info.st_dev, info.st_ino],
                                              owner_namespaces={name: os.stat('/proc/self/ns/' + name).st_ino
                                                                for name in ('pid', 'mnt')}))
        self.assertEqual(result['bootstrap']['observed_cgroup'], '0::' + CURRENT)
        self.assertIsInstance(result['bootstrap']['pid'], int)

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



class MigrationEvidenceTests(Base):
    """The in-sandbox probe in a real bwrap filesystem sandbox (inert command; synthetic group, real cgroupfs device)."""

    def leaf_with_device(self):
        group = FakeGroup(str(self.root))
        group.fd = os.open('/sys/fs/cgroup', os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, group.fd)
        return writer.WriterLeaf(str(self.root), {}, group_factory=lambda delegated, controls: group)

    def sandboxed(self, leaf, inner):
        import trial_task_eval as task
        provider, (source, target, _) = leaf.probe(inner)
        command = task.runtime_sandbox() + ['--ro-bind', str(source), target, *provider]
        run = Path(tempfile.mkdtemp(dir=self.root))
        out, err, code, _ = capture(leaf.wrap(command), 20, run / 'timing.json', custody_path=run / 'custody.json',
                                    pass_fds=leaf.pass_fds)
        observed = leaf.finish(time.monotonic() + 0.3)
        return writer.result(run_id='synthetic.CURRENT.s0', workspace=self.root / 'work', leaf=leaf, observed=observed,
                             custody=json.loads((run / 'custody.json').read_text())), out, code

    @unittest.skipUnless(__import__('shutil').which('bwrap'), 'needs the local filesystem sandbox')
    def test_probe_reports_launch_evidence_then_execs_the_unchanged_command(self):
        result, out, code = self.sandboxed(self.leaf_with_device(), ['/usr/bin/echo', 'inert'])
        self.assertEqual((code, out), (0, b'inert\n'))
        evidence = result['launch_evidence']
        self.assertEqual(set(evidence['caps'].values()), {writer.ZERO_CAPS})
        self.assertFalse(set(evidence['mount_types']) & writer.CGROUP_TYPES)
        self.assertEqual(evidence['cgroupfs_descriptors'], [])
        self.assertNotEqual(evidence['namespaces']['pid'], result['leaf']['owner_namespaces']['pid'])
        self.assertNotEqual(evidence['namespaces']['mnt'], result['leaf']['owner_namespaces']['mnt'])
        expected = [] if evidence['max_user_namespaces'] == 0 else ['user_namespace_creation_available']
        self.assertEqual(result['migration_control'], dict(state=writer.CONDITIONS_OBSERVED if not expected else 'unknown',
                                                           reasons=expected, argument=writer.ARGUMENT))

    def test_controlled_requires_every_observation(self):
        bootstrap = dict(state='member', pid=4242)
        good = dict(caps=dict.fromkeys(('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'), writer.ZERO_CAPS),
                    no_new_privs='1', mount_types=['devtmpfs', 'proc', 'tmpfs'], cgroupfs_descriptors=[], pid=2,
                    namespaces=dict(pid=11, mnt=12), cgroup='0::/leaf', max_user_namespaces=0)
        owner = dict(pid=21, mnt=22)
        check = lambda evidence, **kw: writer.migration_control(evidence, bootstrap=kw.get('bootstrap', bootstrap),
                                                                relative='/leaf', cgroup_dev=kw.get('dev', '0:30'),
                                                                owner_namespaces=kw.get('owner', owner))
        self.assertEqual(check(good), dict(state=writer.CONDITIONS_OBSERVED, reasons=[], argument=writer.ARGUMENT))
        # Observed conditions never satisfy a consumer that requires proved migration control.
        with self.assertRaisesRegex(writer.WriterUnavailable, '^migration_control_not_proved:migration_conditions_observed$'):
            writer.require_proved_migration_control(dict(migration_control=check(good)))
        self.assertNotEqual(writer.CONDITIONS_OBSERVED, writer.PROVED)
        for change, reason in ((dict(caps=dict(good['caps'], CapBnd='000001ffffffffff')), 'capabilities_present'),
                               (dict(no_new_privs='0'), 'no_new_privs_absent'),
                               (dict(mount_types=['cgroup2', 'proc']), 'cgroupfs_mount_visible'),
                               (dict(cgroupfs_descriptors=[7]), 'cgroupfs_descriptor_open'),
                               # A different pid in the SAME namespace is not namespace evidence.
                               (dict(pid=7, namespaces=dict(pid=21, mnt=12)), 'pid_namespace_unobserved'),
                               (dict(namespaces=dict(pid=11)), 'pid_namespace_unobserved'),
                               (dict(namespaces=dict(pid=11, mnt=22)), 'mount_namespace_unobserved'),
                               (dict(cgroup='0::/elsewhere'), 'probe_membership_mismatch'),
                               (dict(max_user_namespaces=514295), 'user_namespace_creation_available')):
            with self.subTest(reason=reason):
                self.assertEqual(check(dict(good, **change)),
                                 dict(state='unknown', reasons=[reason], argument=writer.ARGUMENT))
        self.assertEqual(check(None)['reasons'], ['launch_evidence_absent'])
        self.assertEqual(check(good, dev=None)['reasons'], ['launch_evidence_absent'])
        self.assertEqual(check(good, bootstrap=dict(state='unobserved'))['reasons'], ['launch_evidence_absent'])
        self.assertEqual(check(good, owner=None)['reasons'], ['launch_evidence_absent'])

    def test_probe_outside_the_sandbox_is_not_controlled(self):
        result, out, code = self.sandboxed_outside()
        self.assertEqual(result['migration_control']['state'], 'unknown')
        self.assertIn('pid_namespace_unobserved', result['migration_control']['reasons'])

    def sandboxed_outside(self):
        leaf = self.leaf_with_device()
        provider, _ = leaf.probe(['/usr/bin/true'])
        provider[provider.index(writer.PROBE_TARGET)] = str(writer.PROBE)
        run = Path(tempfile.mkdtemp(dir=self.root))
        out, err, code, _ = capture(leaf.wrap(provider), 20, run / 'timing.json', custody_path=run / 'custody.json',
                                    pass_fds=leaf.pass_fds)
        observed = leaf.finish(time.monotonic() + 0.3)
        return writer.result(run_id='synthetic.CURRENT.s0', workspace=self.root / 'work', leaf=leaf, observed=observed,
                             custody=json.loads((run / 'custody.json').read_text())), out, code


@unittest.skipUnless(__import__('shutil').which('bwrap'), 'needs the local filesystem sandbox')
class NamespaceInitTests(Base):
    """The bwrap pid-namespace init bound by pidfd during capture (ordinary sandbox, inert commands)."""

    leaf_with_device = MigrationEvidenceTests.leaf_with_device

    def run_bound(self, inner, *, observe=True, writable=None):
        import trial_task_eval as task
        leaf = self.leaf_with_device()
        provider, (source, target, _) = leaf.probe(inner)
        command = task.runtime_sandbox() + ['--ro-bind', str(source), target]
        if writable is not None:
            command += ['--bind', str(writable), str(writable)]
        command += provider
        run = Path(tempfile.mkdtemp(dir=self.root))
        out, err, code, _ = capture(leaf.wrap(command), 20, run / 'timing.json', custody_path=run / 'custody.json',
                                    pass_fds=leaf.pass_fds, observer=leaf.observe_init if observe else None)
        observed = leaf.finish(time.monotonic() + 5)
        return writer.result(run_id='synthetic.CURRENT.s0', workspace=self.root / 'work', leaf=leaf, observed=observed,
                             custody=json.loads((run / 'custody.json').read_text()))

    def test_monitor_init_and_actor_are_distinct_and_the_init_exit_is_observed(self):
        result = self.run_bound(['/usr/bin/python3', '-c', 'import time; time.sleep(0.6)'])
        init = result['init']
        self.assertEqual(init['state'], 'bound')
        self.assertEqual(init['monitor_pid'], result['bootstrap']['pid'])  # the launcher execs into the monitor
        self.assertNotEqual(init['host_pid'], init['monitor_pid'])
        self.assertEqual(init['ns_pids'][-1], '1')  # the namespace init
        self.assertEqual(result['launch_evidence']['pid'], 2)  # the actor, as seen in its namespace
        self.assertEqual(result['launch_evidence']['namespaces']['pid'], init['pid_namespace']['inode'])
        self.assertEqual(result['init_exit'], 'exited')
        self.assertEqual(result['writer_quiescence'],
                         dict(state='namespace_init_exited', basis='installed_kernel_source_zap_before_pidfd_notify'))
        self.assertEqual(result['final_artifact_custody'], 'not_established')

    ESCAPES = {
        'double_fork': "if os.fork() == 0:\n    if os.fork() == 0:\n        write()\n    os._exit(0)\n",
        'setsid': "if os.fork() == 0:\n    os.setsid()\n    write()\n",
        'after_pipe_close': "if os.fork() == 0:\n    write()\n",
    }
    WRITE = ("import os, sys, time\n"
             "def write():\n"
             "    for fd in (0, 1, 2):\n"
             "        os.close(fd)  # the streams reach EOF while this writer continues\n"
             "    for _ in range(300):\n"
             "        open(sys.argv[1], 'a').write('x'); time.sleep(.01)\n"
             "    os._exit(0)\n")

    def test_escaped_writers_have_stopped_once_the_init_exit_is_observed(self):
        writable = self.root / 'writable'
        writable.mkdir()
        for name, spawn in self.ESCAPES.items():
            with self.subTest(writer=name):
                output = writable / name
                program = self.WRITE + spawn + "time.sleep(0.4)\n"  # the actor exits while its writer runs
                result = self.run_bound(['/usr/bin/python3', '-c', program, str(output)], writable=writable)
                self.assertEqual(result['writer_quiescence']['state'], 'namespace_init_exited', result)
                before = output.stat().st_size
                self.assertGreater(before, 0)  # the escaped writer really was writing
                self.assertLess(before, 300)  # and was stopped before it finished
                time.sleep(0.4)
                self.assertEqual(output.stat().st_size, before)  # no actor-origin writer remains

    def test_late_or_missing_binding_never_establishes_quiescence(self):
        result = self.run_bound(['/usr/bin/true'], observe=False)
        self.assertEqual(result['writer_quiescence'], dict(state='not_established', reason='init_unbound'))

    def test_ambiguous_or_wrong_process_is_never_bound(self):
        two = subprocess.Popen(['/usr/bin/python3', '-c', 'import os, time\nfor _ in range(2):\n    if os.fork() == 0:\n        time.sleep(5); os._exit(0)\ntime.sleep(5)'])
        one = subprocess.Popen(['/usr/bin/python3', '-c', 'import os, time\nif os.fork() == 0:\n    time.sleep(5); os._exit(0)\ntime.sleep(5)'])
        try:
            time.sleep(0.5)
            for monitor, expected in ((two, dict(state='ambiguous', children=2)),
                                      (one, dict(state='ambiguous', reason='not_the_namespace_init'))):
                leaf = self.leaf_with_device()
                leaf.observe_init(monitor)
                self.assertEqual(leaf.init, expected)
                self.assertIsNone(leaf.init_pidfd)
                leaf.close()
        finally:
            for process in (two, one):
                process.kill()
                process.wait()

    def test_quiescence_requires_the_bound_namespace_and_an_observed_exit(self):
        bound = dict(state='bound', pid_namespace=dict(inode=11, dev=3))
        evidence = dict(namespaces=dict(pid=11, mnt=12))
        q = writer.writer_quiescence
        self.assertEqual(q(dict(init=bound, init_exit='exited'), evidence)['state'], 'namespace_init_exited')
        self.assertEqual(q(dict(init=bound, init_exit='exited'), dict(namespaces=dict(pid=99, mnt=12))),
                         dict(state='not_established', reason='actor_namespace_unbound'))
        self.assertEqual(q(dict(init=bound, init_exit='not_exited'), evidence),
                         dict(state='not_established', reason='init_not_exited'))
        self.assertEqual(q(dict(init=dict(state='ambiguous'), init_exit='exited'), evidence),
                         dict(state='not_established', reason='init_ambiguous'))

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

    def failing_run(self, **patches):
        import trial_task_eval as task
        groups = []
        factory = lambda delegated, controls: groups.append(FakeGroup(delegated, controls, populated=(True, False))) or groups[-1]

        def inert(root, cwd, binds, env, argv, harness, **kwargs):
            hookstate = next(src for src, target, _ in binds if str(target) == '/tmp/trial/hookstate')
            return [sys.executable, '-c', self.boundary.ACTOR, str(hookstate), '0.1', 'Done.']
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(task, 'load_corpus', return_value=[]))
            stack.enter_context(patch.object(task, 'sandbox_command', side_effect=inert))
            stack.enter_context(patch.object(writer.owned, 'OwnedCgroup', side_effect=factory))
            grade = stack.enter_context(patch.object(task, 'grade', side_effect=task.grade))
            for target, name, value in patches.values():
                stack.enter_context(patch.object(target, name, value))
            try:  # keep the traceback (assertRaises would strip it)
                task.run_agent(self.case, 'CURRENT', 0, 0, self.args, self.stores, self.out)
            except Exception as raised:
                error = raised
            else:
                self.fail('the injected failure did not propagate')
        grade.assert_not_called()
        return error, groups[0] if groups else None

    def test_failures_after_capture_still_release_close_and_record_without_success(self):
        import trial_task_eval as task
        real_capture = task.capture

        class Undecodable(bytes):
            def decode(self, *args, **kwargs):
                raise UnicodeDecodeError('utf-8', b'x', 0, 1, 'synthetic decode failure')

        def undecodable(*args, **kwargs):
            out, err, code, timings = real_capture(*args, **kwargs)
            return Undecodable(out), err, code, timings
        real_write = Path.write_bytes

        def failing_storage(path, data):
            if path.name == 'stream.jsonl':
                raise OSError(28, 'synthetic storage failure')
            return real_write(path, data)
        cases = dict(
            decode=(dict(capture=(task, 'capture', undecodable)), UnicodeDecodeError),
            storage=(dict(write=(Path, 'write_bytes', failing_storage)), OSError),
            custody=(dict(custody=(task, 'read_custody', lambda path: (_ for _ in ()).throw(ValueError('custody_shape')))),
                     ValueError))
        for name, (patches, kind) in cases.items():
            with self.subTest(failure=name):
                import shutil
                shutil.rmtree(self.out)
                self.out.mkdir(mode=0o700)
                error, group = self.failing_run(**patches)
                self.assertIsInstance(error, kind)
                self.assertEqual(group.closes, 1)
                self.assertGreaterEqual(group.kills, 1)
                recorded = json.loads((self.out / 'runs/synthetic.CURRENT.s0/writer-release.json').read_text())
                self.assertEqual(recorded['state'], 'attempt_failed')
                self.assertEqual(recorded['release'], 'released')  # observed, but never claimed as success
                self.assertEqual(recorded['bootstrap']['state'], 'member')

    def test_receipt_write_failure_keeps_the_primary_error_with_observations(self):
        import trial_task_eval as task
        real_write = Path.write_text

        def failing_receipt(path, data, *args, **kwargs):
            if path.name == 'writer-release.json':
                raise OSError(28, 'synthetic receipt failure')
            return real_write(path, data, *args, **kwargs)

        def malformed(path):
            raise ValueError('custody_shape')
        error, group = self.failing_run(custody=(task, 'read_custody', malformed),
                                        write=(Path, 'write_text', failing_receipt))
        self.assertEqual(str(error), 'custody_shape')
        frames = []
        tb = error.__traceback__
        while tb is not None:
            frames.append(tb.tb_frame.f_code.co_name)
            tb = tb.tb_next
        self.assertEqual(frames[-1], 'malformed')  # the original traceback, not the receipt failure
        notes = '\n'.join(error.__notes__)
        self.assertIn('writer_release: {"bootstrap": "member", "close_errors": [], "release": "released", '
                      '"state": "attempt_failed"}', notes)
        self.assertIn('writer_release_receipt_unwritten: OSError', notes)
        self.assertEqual(group.closes, 1)

    def test_uncertain_cleanup_on_a_failed_attempt_is_unknown_never_released(self):
        import trial_task_eval as task
        groups = []
        factory = lambda delegated, controls: groups.append(FakeGroup(
            delegated, controls, events_error=True, close_errors=('cgroup_directory_removal_unknown',))) or groups[-1]

        def malformed(path):
            raise ValueError('custody_shape')
        error, _ = self.failing_run(custody=(task, 'read_custody', malformed),
                                    group=(writer.owned, 'OwnedCgroup', unittest.mock.Mock(side_effect=factory)))
        recorded = json.loads((self.out / 'runs/synthetic.CURRENT.s0/writer-release.json').read_text())
        self.assertEqual((recorded['state'], recorded['release'], recorded['close_errors']),
                         ('attempt_failed', 'events_unknown', ['cgroup_directory_removal_unknown']))
        self.assertIn('"release": "events_unknown"', '\n'.join(error.__notes__))

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
