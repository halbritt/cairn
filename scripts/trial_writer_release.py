"""Owned writer leaf and release result for the original legacy actor (CAIRN-107).

A root-selected delegated cgroup parent and an explicit resource policy go
in. Every attempt then gets an exclusive new leaf
(``trial_owned_workers.OwnedCgroup``) and runs as follows:

1. ``trial_writer_launcher`` joins the leaf and verifies membership before it
   execs the unchanged actor command. It adds no rlimits, environment or
   namespaces.
2. After the actor's capture, ``release`` kills the leaf through the held
   ``cgroup.kill`` descriptor and waits until the held ``cgroup.events``
   descriptor reports ``populated 0``.
3. The result is bound to the exact attempt and workspace.

Kept separate:

- the real ``waitpid`` status and the stream EOF, as captured;
- the release outcome: ``released``, ``not_released``, ``kill_unknown``
  and ``events_unknown`` are each distinct;
- ``close`` errors, which never become a release.

Migration control is decided from launch evidence. A probe
(``trial_writer_probe``) runs inside the sandbox as the exact process that
becomes the actor, reports its capabilities, mount types, descriptors, pid
namespace, cgroup line and user-namespace limit, and execs the unchanged
command. ``migration_control`` is ``migration_conditions_observed`` (never
``migration_control_proved``) only when that evidence shows the actor has no cgroupfs view and no means to gain one. Otherwise it is
``unknown`` with reasons. The common gap is user namespace creation being
available, because the legacy route adds no user namespace.

Even ``migration_conditions_observed`` plus ``released`` is not writer quiescence or
final-artifact custody. ``complete_descendant_custody`` stays ``False``.
Unavailable containment refuses before launch; nothing is assumed.
"""
import copy
import errno
import fcntl
import hashlib
import json
import os
import shutil
import stat
import select
import signal
from pathlib import Path
import time

import trial_owned_workers as owned

SCHEMA = 'cairn.legacy-writer-release/1'
LAUNCHER = Path(__file__).resolve().with_name('trial_writer_launcher.py')
PROBE = Path(__file__).resolve().with_name('trial_writer_probe.py')
PROBE_TARGET = '/tmp/trial/writer-probe.py'
ZERO_CAPS = '0000000000000000'
CGROUP_TYPES = frozenset(('cgroup', 'cgroup2', 'sysfs'))
# These observations are a proposed sufficiency argument for root review, not a proof. They never set
# writer quiescence or final-artifact custody.
ARGUMENT = 'proposed_sufficiency_not_proof'
CONDITIONS_OBSERVED = 'migration_conditions_observed'  # observed launch conditions held
PROVED = 'migration_control_proved'  # reserved for an accepted proof; never emitted here
CONTROL_FIELDS = frozenset(owned.CONTROLS)


class WriterUnavailable(Exception):
    pass


def check_controls(controls):
    """A selected policy: only known control fields, with integer or string values."""
    if not (isinstance(controls, dict) and set(controls) <= CONTROL_FIELDS
            and all(type(v) in (int, str) and v != '' for v in controls.values())):
        raise WriterUnavailable('writer_controls_invalid')
    return dict(controls)


REVIEWED_SCHEMA = 'cairn.writer-reviewed-identity/2'
BASIS = 'reviewed_identity_source_zap_before_pidfd_notify'


def file_sha256(path):
    digest = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError('not_regular')
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def runtime_identity():
    """Observe candidate runtime identities at attempt start; this is not execution binding.

    Kernel: ``uname -r`` and the /proc/version build string. bwrap: its realpath on the
    owner's PATH and a hash of its current bytes. Reviewed execution separately seals
    and checks a copy before descriptor execution. Missing values stay None.
    """
    identity = dict(kernel=dict(release=None, version=None), bwrap=dict(path=None, sha256=None))
    try:
        identity['kernel']['release'] = os.uname().release
        identity['kernel']['version'] = Path('/proc/version').read_text().strip()
    except OSError:
        pass
    try:
        resolved = shutil.which('bwrap')
        if resolved is not None:
            path = os.path.realpath(resolved)
            identity['bwrap'] = dict(path=path, sha256=file_sha256(path))
    except (OSError, ValueError):
        pass
    return identity


def check_reviewed(reviewed):
    """Root-selected reviewed pins: kernel release and build string, bwrap path and sha256, plus the
    reviewed source scope they are bound to. Closed shape; refuses anything else."""
    if reviewed is None:
        return None
    if not (isinstance(reviewed, dict) and set(reviewed) == {'schema', 'kernel', 'bwrap', 'source_scope', 'execution'}
            and reviewed['schema'] == REVIEWED_SCHEMA and reviewed['execution'] == 'sealed_memfd'
            and isinstance(reviewed['kernel'], dict) and set(reviewed['kernel']) == {'release', 'version'}
            and isinstance(reviewed['bwrap'], dict) and set(reviewed['bwrap']) == {'path', 'sha256'}
            and all(type(v) is str and v for v in (*reviewed['kernel'].values(), *reviewed['bwrap'].values()))
            and isinstance(reviewed['source_scope'], dict)):
        raise WriterUnavailable('writer_reviewed_identity_invalid')
    return copy.deepcopy(reviewed)


def sealed_executable(path, expected_sha256):
    """Copy an ordinary ELF, seal it, then hash the immutable bytes actually used by exec.

    The reviewed mode explicitly selects descriptor execution. Privilege-bearing files,
    scripts and noexec sources refuse; there is no pathname-exec fallback.
    """
    image = None
    try:
        source = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(source, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_mode & (stat.S_ISUID | stat.S_ISGID)
                    or not info.st_mode & 0o111 or not os.access(path, os.X_OK)
                    or os.fstatvfs(stream.fileno()).f_flag & os.ST_NOEXEC):
                raise WriterUnavailable('writer_executable_policy')
            try:
                os.getxattr(stream.fileno(), 'security.capability')
            except OSError as error:
                if error.errno != errno.ENODATA:
                    raise
            else:
                raise WriterUnavailable('writer_executable_policy')
            if stream.read(4) != b'\x7fELF':
                raise WriterUnavailable('writer_executable_not_elf')
            stream.seek(0)
            image = os.memfd_create('cairn-reviewed-bwrap', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
            with os.fdopen(os.dup(image), 'wb') as target:
                shutil.copyfileobj(stream, target)
        os.fchmod(image, 0o500)
        seals = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
        fcntl.fcntl(image, fcntl.F_ADD_SEALS, seals)
        os.lseek(image, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        while block := os.read(image, 1024 * 1024):
            digest.update(block)
        if digest.hexdigest() != expected_sha256:
            raise WriterUnavailable('writer_executable_mismatch')
        return image
    except BaseException:
        if image is not None:
            os.close(image)
        raise


class WriterLeaf:
    def __init__(self, delegated, controls, *, group_factory=None, reviewed=None):
        self.controls = check_controls(controls)
        self.reviewed = check_reviewed(reviewed)
        # Recorded at attempt start, before anything is launched.
        self.runtime = runtime_identity()
        self.parent = str(delegated)
        self.status_read = self.status_write = self.evidence_read = self.evidence_write = None
        self.cgroup_dev = None
        self.owner_namespaces = None
        self.init = dict(state='unbound')  # the sandbox pid-namespace init, bound during capture
        self.init_pidfd = None
        self.closed = None
        self.executable_fd = None
        self.execution_binding = None
        try:
            self.group = (group_factory or owned.OwnedCgroup)(delegated, controls=self.controls)
        except owned.Unavailable:
            raise WriterUnavailable('writer_containment_unavailable') from None
        # Staged acquisition: anything acquired is released if a later step fails.
        try:
            if self.reviewed is not None:
                if not identity_reviewed(self.runtime, self.reviewed):
                    raise WriterUnavailable('writer_runtime_identity_mismatch')
                self.executable_fd = sealed_executable(self.runtime['bwrap']['path'],
                                                       self.reviewed['bwrap']['sha256'])
            self.identity = list(self.group.identity)
            # The owner's own namespaces at launch: the probe must report different ones.
            self.owner_namespaces = {name: os.stat('/proc/self/ns/' + name).st_ino for name in ('pid', 'mnt')}
            self.status_read, self.status_write = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
            self.evidence_read, self.evidence_write = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
            info = os.fstat(self.group.fd) if getattr(self.group, 'fd', None) is not None else None
            # The host cgroupfs device, from the held leaf directory (synthetic groups have none).
            self.cgroup_dev = None if info is None else '%d:%d' % (os.major(info.st_dev), os.minor(info.st_dev))
        except BaseException as error:
            observations = self.close()
            if observations:  # keep the original failure; attach cleanup observations
                error.add_note('writer cleanup: ' + ','.join(observations))
            raise

    @property
    def pass_fds(self):
        return (self.group.files['cgroup.procs'], self.status_write, self.evidence_write) + (
            () if self.executable_fd is None else (self.executable_fd,))

    def probe(self, provider):
        """The in-sandbox launch-evidence probe in front of the unchanged provider argv, and its bind."""
        return (['/usr/bin/python3', '-B', PROBE_TARGET, str(self.evidence_write), self.cgroup_dev or 'none', '--',
                 *provider], (PROBE, PROBE_TARGET, 'ro'))

    def observe_init(self, monitor):
        """Capture observer: bind the bwrap pid-namespace init (the monitor's only child) by pidfd.

        bubblewrap 0.9.0 (bubblewrap.c main, do_init, monitor_child): with --unshare-pid the outer
        process is the monitor; it clones a child into the new pid namespace, which forks again and
        stays as pid 1 (do_init), while the forked process execs the command as pid 2 (the actor).
        The binding is the init's held pidfd plus its pid-namespace inode/dev, verified AFTER the
        pidfd is open (parent is the monitor, NSpid ends in 1), so a reused numeric pid cannot pass.
        More than one child is ambiguous and is never bound. Never raises.
        """
        if self.init['state'] != 'unbound':
            return
        try:
            children = Path(f'/proc/{monitor.pid}/task/{monitor.pid}/children').read_text().split()
        except OSError:
            return  # not yet observable; a binding that never happens stays unbound
        if not children:
            return
        if len(children) != 1:
            self.init = dict(state='ambiguous', children=len(children))
            return
        pid = int(children[0])
        try:
            pidfd = os.pidfd_open(pid)
        except OSError:
            return
        try:
            fields = Path(f'/proc/{pid}/stat').read_text().rsplit(') ', 1)[1].split()
            nspid = [line.split()[1:] for line in Path(f'/proc/{pid}/status').read_text().splitlines()
                     if line.startswith('NSpid:')]
            namespace = os.stat(f'/proc/{pid}/ns/pid')
            signal.pidfd_send_signal(pidfd, 0)  # still the same live process after the reads
        except (OSError, IndexError, ValueError):
            os.close(pidfd)
            return
        if int(fields[1]) != monitor.pid or not nspid or nspid[0][-1] != '1' or len(nspid[0]) < 2:
            os.close(pidfd)
            self.init = dict(state='ambiguous', reason='not_the_namespace_init')
            return
        self.init_pidfd = pidfd
        self.init = dict(state='bound', host_pid=pid, monitor_pid=monitor.pid, ns_pids=nspid[0],
                         pid_namespace=dict(inode=namespace.st_ino, dev=namespace.st_dev))

    def init_exit(self, deadline):
        """Wait on the held init pidfd; readiness only after the kernel zapped and reaped the namespace."""
        if self.init_pidfd is None:
            return 'unbound'
        left = max(0.0, deadline - time.monotonic())
        try:
            ready = select.select([self.init_pidfd], [], [], left)[0]
        except OSError:
            return 'exit_unknown'
        return 'exited' if ready else 'not_exited'

    def launch_evidence(self):
        """The probe's report, read once after capture; anything else is absent evidence."""
        if self.evidence_write is not None:
            os.close(self.evidence_write)
            self.evidence_write = None
        try:
            data = os.read(self.evidence_read, 65536)
            document = json.loads(data.split(b'\n', 1)[0])
        except (OSError, ValueError):
            return None
        return document if isinstance(document, dict) else None

    def wrap(self, argv):
        self.execution_binding = None
        prefix = []
        if self.executable_fd is not None:
            if not argv or argv[0] not in ('bwrap', self.runtime['bwrap']['path']):
                raise WriterUnavailable('writer_executable_command_mismatch')
            prefix = ['--exec-fd', str(self.executable_fd)]
            self.execution_binding = dict(mode='sealed_memfd', sha256=self.runtime['bwrap']['sha256'])
        elif argv and argv[0] == 'bwrap' and self.runtime['bwrap']['path'] is not None:
            argv = [self.runtime['bwrap']['path'], *argv[1:]]
        return ['/usr/bin/python3', '-B', str(LAUNCHER), str(self.group.files['cgroup.procs']), self.group.relative,
                str(self.status_write), *prefix, *argv]

    def bootstrap(self):
        """Read the launcher's membership report; anything else is not membership."""
        if self.status_write is not None:
            os.close(self.status_write)
            self.status_write = None
        try:
            data = os.read(self.status_read, 256)
        except BlockingIOError:
            data = b''
        except OSError:
            return dict(state='unobserved')
        line = data.split(b'\n', 1)[0].split()
        if (len(line) == 3 and line[0] == b'member' and line[1].isdigit()
                and line[2] == b'0::' + self.group.relative.encode()):
            # Launcher-observed /proc/self/cgroup before exec; the process is not re-read later.
            return dict(state='member', pid=int(line[1]), observed_cgroup=line[2].decode())
        return dict(state='unobserved')

    def release(self, deadline):
        """Kill the leaf through held descriptors until populated 0; never infer release."""
        try:
            self.group.kill()
        except (OSError, owned.Unavailable):
            return 'kill_unknown'
        while True:
            try:
                if not self.group.populated():
                    return 'released'
            except (OSError, owned.Unavailable, ValueError):
                return 'events_unknown'
            if time.monotonic() >= deadline:
                return 'not_released'
            try:
                self.group.kill()
            except (OSError, owned.Unavailable):
                return 'kill_unknown'
            time.sleep(.005)

    def close(self):
        if self.closed is not None:
            return self.closed
        errors = []
        for name in ('status_read', 'status_write', 'evidence_read', 'evidence_write', 'init_pidfd', 'executable_fd'):
            fd = getattr(self, name)
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    errors.append('writer_status_close_unknown')
                setattr(self, name, None)
        group = getattr(self, 'group', None)
        if group is not None:
            try:
                errors.extend(group.close())
            except OSError:
                errors.append('cgroup_close_unknown')
        self.closed = sorted(set(errors))
        return self.closed

    def finish(self, deadline):
        """Bootstrap, release and close exactly once; never raises, always returns observations."""
        observed = dict(bootstrap=dict(state='unobserved'), release='release_unknown', close_errors=[],
                        launch_evidence=None, init=dict(self.init), init_exit='unbound',
                        execution_binding=self.execution_binding)
        try:
            observed['bootstrap'] = self.bootstrap()
        except Exception:
            pass
        try:
            observed['launch_evidence'] = self.launch_evidence()
        except Exception:
            pass
        try:
            observed['release'] = self.release(deadline)
        except Exception:
            observed['release'] = 'release_unknown'
        try:
            observed['init_exit'] = self.init_exit(deadline)
        except Exception:
            observed['init_exit'] = 'exit_unknown'
        finally:
            try:
                observed['close_errors'] = self.close()
            except Exception:
                observed['close_errors'] = ['writer_close_unknown']
        return observed


def migration_control(evidence, *, bootstrap, relative, cgroup_dev, owner_namespaces):
    """CONDITIONS_OBSERVED only when the launch evidence shows no cgroupfs view or means to gain one.

    That is distinct from PROVED migration control, which this function never returns.

    Required observations:
    - every capability set is empty and NoNewPrivs is 1;
    - no cgroup, cgroup2 or sysfs mount is visible;
    - no descriptor is on the host cgroupfs device;
    - the probe's pid and mount namespace inodes differ from the owner's own (observed by the owner at
      leaf creation); a different pid alone is not namespace evidence;
    - its cgroup line names the leaf;
    - user namespace creation is disabled (max_user_namespaces == 0).
    Anything absent or different stays 'unknown', with the reasons.
    """
    if (not isinstance(evidence, dict) or cgroup_dev is None or bootstrap.get('state') != 'member'
            or not isinstance(owner_namespaces, dict)):
        return dict(state='unknown', reasons=['launch_evidence_absent'], argument=ARGUMENT)
    reasons = []
    caps = evidence.get('caps') if isinstance(evidence.get('caps'), dict) else {}
    if set(caps) != {'CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'} or set(caps.values()) != {ZERO_CAPS}:
        reasons.append('capabilities_present')
    if evidence.get('no_new_privs') != '1':
        reasons.append('no_new_privs_absent')
    types = evidence.get('mount_types')
    if not isinstance(types, list) or set(types) & CGROUP_TYPES:
        reasons.append('cgroupfs_mount_visible')
    if evidence.get('cgroupfs_descriptors') != []:
        reasons.append('cgroupfs_descriptor_open')
    namespaces = evidence.get('namespaces') if isinstance(evidence.get('namespaces'), dict) else {}
    if (set(namespaces) != {'pid', 'mnt'} or not all(type(v) is int for v in namespaces.values())
            or namespaces['pid'] == owner_namespaces.get('pid')):
        reasons.append('pid_namespace_unobserved')
    elif namespaces['mnt'] == owner_namespaces.get('mnt'):
        reasons.append('mount_namespace_unobserved')
    if evidence.get('cgroup') != '0::' + relative:
        reasons.append('probe_membership_mismatch')
    if evidence.get('max_user_namespaces') != 0:
        reasons.append('user_namespace_creation_available')
    return dict(state='unknown' if reasons else CONDITIONS_OBSERVED, reasons=reasons, argument=ARGUMENT)


def require_proved_migration_control(result):
    """Gate for a consumer that needs PROVED migration control; observed conditions never satisfy it.

    This module never emits PROVED: launch observations are a proposed sufficiency argument only.
    """
    state = (result.get('migration_control') or {}).get('state') if isinstance(result, dict) else None
    if state != PROVED:
        raise WriterUnavailable('migration_control_not_proved:' + str(state))
    return True


def identity_reviewed(runtime, reviewed):
    """True only when every recorded identity equals the root-selected reviewed pin exactly."""
    if not isinstance(reviewed, dict) or not isinstance(runtime, dict):
        return False
    return (runtime.get('kernel') == reviewed['kernel'] and runtime.get('bwrap') == reviewed['bwrap']
            and None not in (*runtime['kernel'].values(), *runtime['bwrap'].values()))


def writer_quiescence(observed, evidence, *, runtime=None, reviewed=None):
    """'namespace_init_exited' only when the bound init's exit was observed for the actor's namespace.

    Installed source (Ubuntu linux 6.8.0-146.146): do_exit -> exit_notify -> forget_original_parent
    -> find_child_reaper, which for the namespace's last init thread runs zap_pid_ns_processes
    (SIGKILL every other pid, kernel_wait4 until ECHILD, then wait until pid_allocated == init_pids)
    BEFORE exit_state is set and do_notify_parent -> do_notify_pidfd wakes pidfd pollers;
    pidfd_poll reports readiness only when thread_group_exited. A ready init pidfd therefore means
    no other process of that namespace (or of namespaces nested in it) remains.

    That ordering was reviewed only for the exact identities in the root-selected ``reviewed`` pin
    (kernel release and build string; the bwrap executable's path and bytes). Any other or missing
    runtime identity gives 'runtime_identity_unreviewed'; nothing here holds universally.
    """
    if not identity_reviewed(runtime, reviewed):
        return dict(state='not_established', reason='runtime_identity_unreviewed')
    if observed.get('execution_binding') != dict(mode='sealed_memfd', sha256=runtime['bwrap']['sha256']):
        return dict(state='not_established', reason='executable_unbound')
    init = observed.get('init') or {}
    if init.get('state') != 'bound':
        return dict(state='not_established', reason='init_' + str(init.get('state', 'unbound')))
    namespaces = evidence.get('namespaces') if isinstance(evidence, dict) else None
    if not isinstance(namespaces, dict) or namespaces.get('pid') != init['pid_namespace']['inode']:
        return dict(state='not_established', reason='actor_namespace_unbound')
    if observed.get('init_exit') != 'exited':
        return dict(state='not_established', reason='init_' + str(observed.get('init_exit')))
    return dict(state='namespace_init_exited', basis=BASIS, scope=reviewed['source_scope'])


def result(*, run_id, workspace, leaf, observed, custody, attempt_failed=False):
    """The owner-derived result for one attempt; each outcome stays distinct.

    ``released`` means only that this held leaf was observed empty after the
    owner's kill. It is not writer quiescence and not final-artifact custody:
    migration control is unknown and descendant custody stays incomplete.
    """
    bootstrap, release, close_errors = observed['bootstrap'], observed['release'], observed['close_errors']
    if attempt_failed:
        state = 'attempt_failed'  # a failed attempt never yields a successful release claim
    elif bootstrap.get('state') != 'member':
        state = 'bootstrap_unobserved'
    elif release != 'released':
        state = release
    elif close_errors:
        state = 'close_unknown'
    else:
        state = 'released'
    custody = custody if isinstance(custody, dict) else {}
    return dict(schema=SCHEMA, attempt=dict(run_id=run_id, workspace=str(workspace)), state=state,
                leaf=dict(parent=leaf.parent, relative=leaf.group.relative, held_identity=leaf.identity,
                          owner_namespaces=leaf.owner_namespaces),
                bootstrap=bootstrap, release=release, close_errors=list(close_errors), controls=dict(leaf.controls),
                waitpid=dict(observed_wait_status=custody.get('observed_wait_status'),
                             observed_returncode=custody.get('observed_returncode'),
                             leader_reaped=custody.get('leader_reaped')),
                stream_eof=dict(stdout=custody.get('stdout_eof'), stderr=custody.get('stderr_eof')),
                launch_evidence=observed.get('launch_evidence'),
                migration_control=migration_control(observed.get('launch_evidence'), bootstrap=bootstrap,
                                                    relative=leaf.group.relative, cgroup_dev=leaf.cgroup_dev,
                                                    owner_namespaces=leaf.owner_namespaces),
                complete_descendant_custody=False,
                init=observed.get('init'), init_exit=observed.get('init_exit'),
                runtime_identity=leaf.runtime, reviewed_identity=leaf.reviewed,
                execution_binding=observed.get('execution_binding'),
                writer_quiescence=writer_quiescence(observed, observed.get('launch_evidence'),
                                                    runtime=leaf.runtime, reviewed=leaf.reviewed),
                final_artifact_custody='not_established')
