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

Migration control is ``unknown``. The legacy route has no cgroup namespace,
so a same-user writer could move itself to another leaf it can write to.
``complete_descendant_custody`` therefore stays ``False``, and a released leaf
is not a final-artifact custody claim. Unavailable containment refuses
before launch. Nothing is assumed.
"""
import os
from pathlib import Path
import time

import trial_owned_workers as owned

SCHEMA = 'cairn.legacy-writer-release/1'
LAUNCHER = Path(__file__).resolve().with_name('trial_writer_launcher.py')
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
        try:
            self.group = (group_factory or owned.OwnedCgroup)(delegated, controls=self.controls)
        except owned.Unavailable:
            raise WriterUnavailable('writer_containment_unavailable') from None
        self.status_read, self.status_write = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
        self.closed = None

    @property
    def pass_fds(self):
        return (self.group.files['cgroup.procs'], self.status_write)

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
        if len(line) == 2 and line[0] == b'member' and line[1].isdigit():
            return dict(state='member', pid=int(line[1]))
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
        for name in ('status_read', 'status_write'):
            fd = getattr(self, name)
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    errors.append('writer_status_close_unknown')
                setattr(self, name, None)
        try:
            errors.extend(self.group.close())
        except OSError:
            errors.append('cgroup_close_unknown')
        self.closed = sorted(set(errors))
        return self.closed


def result(*, run_id, workspace, bootstrap, release, close_errors, custody, controls):
    """The owner-derived release result for one attempt; each outcome stays distinct."""
    if bootstrap.get('state') != 'member':
        state = 'bootstrap_unobserved'
    elif release != 'released':
        state = release
    elif close_errors:
        state = 'close_unknown'
    else:
        state = 'released'
    return dict(schema=SCHEMA, attempt=dict(run_id=run_id, workspace=str(workspace)), state=state,
                bootstrap=bootstrap, release=release, close_errors=list(close_errors), controls=dict(controls),
                waitpid=dict(observed_wait_status=custody.get('observed_wait_status'),
                             observed_returncode=custody.get('observed_returncode'),
                             leader_reaped=custody.get('leader_reaped')),
                stream_eof=dict(stdout=custody.get('stdout_eof'), stderr=custody.get('stderr_eof')),
                migration_control='unknown', complete_descendant_custody=False)
