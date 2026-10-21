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
command. ``migration_control`` is ``controlled`` only when that evidence
shows the actor has no cgroupfs view and no means to gain one. Otherwise it is
``unknown`` with reasons. The common gap is user namespace creation being
available, because the legacy route adds no user namespace.

Even ``controlled`` plus ``released`` is not writer quiescence or
final-artifact custody. ``complete_descendant_custody`` stays ``False``.
Unavailable containment refuses before launch; nothing is assumed.
"""
import json
import os
from pathlib import Path
import time

import trial_owned_workers as owned

SCHEMA = 'cairn.legacy-writer-release/1'
LAUNCHER = Path(__file__).resolve().with_name('trial_writer_launcher.py')
PROBE = Path(__file__).resolve().with_name('trial_writer_probe.py')
PROBE_TARGET = '/tmp/trial/writer-probe.py'
ZERO_CAPS = '0000000000000000'
CGROUP_TYPES = frozenset(('cgroup', 'cgroup2', 'sysfs'))
CONTROL_FIELDS = frozenset(owned.CONTROLS)


class WriterUnavailable(Exception):
    pass


def check_controls(controls):
    """A selected policy: only known control fields, with integer or string values."""
    if not (isinstance(controls, dict) and set(controls) <= CONTROL_FIELDS
            and all(type(v) in (int, str) and v != '' for v in controls.values())):
        raise WriterUnavailable('writer_controls_invalid')
    return dict(controls)


class WriterLeaf:
    def __init__(self, delegated, controls, *, group_factory=None):
        self.controls = check_controls(controls)
        self.parent = str(delegated)
        self.status_read = self.status_write = self.evidence_read = self.evidence_write = None
        self.cgroup_dev = None
        self.closed = None
        try:
            self.group = (group_factory or owned.OwnedCgroup)(delegated, controls=self.controls)
        except owned.Unavailable:
            raise WriterUnavailable('writer_containment_unavailable') from None
        # Staged acquisition: anything acquired is released if a later step fails.
        try:
            self.identity = list(self.group.identity)
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
        return (self.group.files['cgroup.procs'], self.status_write, self.evidence_write)

    def probe(self, provider):
        """The in-sandbox launch-evidence probe in front of the unchanged provider argv, and its bind."""
        return (['/usr/bin/python3', '-B', PROBE_TARGET, str(self.evidence_write), self.cgroup_dev or 'none', '--',
                 *provider], (PROBE, PROBE_TARGET, 'ro'))

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
        return ['/usr/bin/python3', '-B', str(LAUNCHER), str(self.group.files['cgroup.procs']), self.group.relative,
                str(self.status_write), *argv]

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
        for name in ('status_read', 'status_write', 'evidence_read', 'evidence_write'):
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
                        launch_evidence=None)
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
        finally:
            try:
                observed['close_errors'] = self.close()
            except Exception:
                observed['close_errors'] = ['writer_close_unknown']
        return observed


def migration_control(evidence, *, bootstrap, relative, cgroup_dev):
    """'controlled' only from observed launch evidence that the actor has no cgroupfs view or means to gain one.

    Required observations:
    - every capability set is empty and NoNewPrivs is 1;
    - no cgroup, cgroup2 or sysfs mount is visible;
    - no descriptor is on the host cgroupfs device;
    - the probe runs in its own pid namespace (its pid differs from the joined host pid);
    - its cgroup line names the leaf;
    - user namespace creation is disabled (max_user_namespaces == 0).
    Anything absent or different stays 'unknown', with the reasons.
    """
    if not isinstance(evidence, dict) or cgroup_dev is None or bootstrap.get('state') != 'member':
        return dict(state='unknown', reasons=['launch_evidence_absent'])
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
    if type(evidence.get('pid')) is not int or evidence['pid'] == bootstrap.get('pid'):
        reasons.append('pid_namespace_unobserved')
    if evidence.get('cgroup') != '0::' + relative:
        reasons.append('probe_membership_mismatch')
    if evidence.get('max_user_namespaces') != 0:
        reasons.append('user_namespace_creation_available')
    return dict(state='unknown' if reasons else 'controlled', reasons=reasons)


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
                leaf=dict(parent=leaf.parent, relative=leaf.group.relative, held_identity=leaf.identity),
                bootstrap=bootstrap, release=release, close_errors=list(close_errors), controls=dict(leaf.controls),
                waitpid=dict(observed_wait_status=custody.get('observed_wait_status'),
                             observed_returncode=custody.get('observed_returncode'),
                             leader_reaped=custody.get('leader_reaped')),
                stream_eof=dict(stdout=custody.get('stdout_eof'), stderr=custody.get('stderr_eof')),
                launch_evidence=observed.get('launch_evidence'),
                migration_control=migration_control(observed.get('launch_evidence'), bootstrap=bootstrap,
                                                    relative=leaf.group.relative, cgroup_dev=leaf.cgroup_dev),
                complete_descendant_custody=False,
                writer_quiescence='not_established', final_artifact_custody='not_established')
