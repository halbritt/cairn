"""Find live host processes carrying an inherited environment marker.

A process-group scan misses work that calls setsid or double-forks away from
the tool's group. Environment is inherited across both, so scanning every
/proc/PID/environ for an exact NAME=VALUE entry also finds those escapees.

Limits: a process that replaces its environment (for example exec with an
empty environment) no longer carries the marker, and processes of other users
are outside this scan. A same-user process whose environ cannot be read
(non-dumpable, such as agents and sandboxes) makes coverage unknown, so the
result is never reported clear, unless it started before `since`: a process
older than the marked work cannot descend from it.
"""
import os
from pathlib import Path


def _stat(pid):
    return _stat_at('/proc', pid)


def _stat_at(proc, pid):
    raw = Path(f'{proc}/{pid}/stat').read_text()
    fields = raw[raw.rindex(')') + 2:].split()
    # fields[0] is state (field 3); pgrp, session and start time are fields 5, 6 and 22.
    return dict(state=fields[0], pgid=int(fields[2]), sid=int(fields[3]), start=int(fields[19]))


def start_ticks(pid='self'):
    """Boot-relative start time of a process, in clock ticks, as in /proc."""
    return _stat(pid)['start']


def markers(name, value, since=0, proc='/proc'):
    """Return live same-user processes whose environment has NAME=VALUE.

    Returns {clear, coverage, processes, unknown}. clear is true only when
    every same-user process started at or after `since` (clock ticks, from
    start_ticks taken before the marked work began) was read and none carries
    the marker. Zombies have exited and are not counted.
    """
    if not name or '=' in name or not value:
        raise ValueError('marker name and value must be nonempty and name must not contain =')
    needle = f'{name}={value}'.encode()
    uid = os.getuid()
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    processes, unknown = [], []
    for entry in os.scandir(proc):
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            if entry.stat(follow_symlinks=False).st_uid != uid:
                continue
            with open(f'{proc}/{pid}/environ', 'rb') as source:
                environment = source.read().split(b'\0')
            if needle not in environment:
                continue
            status = _stat(pid)
            if status['state'] == 'Z':
                continue
            cmd = Path(f'{proc}/{pid}/cmdline').read_bytes().replace(b'\0', b' ').strip()
            processes.append(dict(pid=pid, start=status['start'], boot=boot, pgid=status['pgid'],
                                  sid=status['sid'], cmd=cmd.decode('utf-8', 'replace')[:200]))
        except (FileNotFoundError, ProcessLookupError):
            continue  # exited during the scan
        except PermissionError:
            # A zombie's environ is unreadable too, but it has already exited.
            try:
                status = _stat_at(proc, pid)
                if status['state'] != 'Z' and status['start'] >= since:
                    unknown.append(pid)
            except (FileNotFoundError, ProcessLookupError):
                continue
            except (PermissionError, ValueError, IndexError):
                unknown.append(pid)
    coverage = 'unknown' if unknown else 'complete'
    return dict(clear=coverage == 'complete' and not processes, coverage=coverage,
                processes=processes, unknown=unknown)
