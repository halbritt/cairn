"""Tool workloads and a ground-truth process observer for the CAIRN-2 trial.

The owned real-model OpenCode cancellation trial asks the model to run
`workload MODE LEDGER` as its tool call, cancels the Cairn request, and then
compares Cairn's reconciliation with `observe LEDGER`. The observer reads
/proc directly and never consults Cairn, so a false clear scan is visible.

Modes:
  tree      Two children and a grandchild in the tool's process group; all
            stop on SIGTERM. The expected clean cancellation.
  stubborn  As tree, but one child ignores SIGTERM. A stop that never
            escalates to SIGKILL must leave the hold in place.
  escaped   As tree, plus a TERM-ignoring process that leaves the session
            and process group (setsid and double fork). Killing the tool's
            group misses it; Cairn must not confirm cancellation while it
            survives.

Every spawned process appends {pid, start, role, pgid, sid} to the ledger.
Identity is pid plus /proc start time, so a reused pid is reported as gone.
"""
import json
import os
from pathlib import Path
import signal
import sys
import time

MODES = ('tree', 'stubborn', 'escaped')


def stat_fields(pid):
    """Fields of /proc/PID/stat after the command name, or None if gone."""
    try:
        raw = Path(f'/proc/{pid}/stat').read_text()
    except (FileNotFoundError, ProcessLookupError):
        return None
    return raw[raw.rindex(')') + 2:].split()


def identity(pid):
    fields = stat_fields(pid)
    # fields[0] is state (field 3); start time is field 22.
    return None if fields is None else dict(state=fields[0], start=int(fields[19]))


def record(ledger, role):
    me = identity(os.getpid())
    line = json.dumps(dict(pid=os.getpid(), start=me['start'], role=role,
                           pgid=os.getpgid(0), sid=os.getsid(0)))
    with open(ledger, 'a') as out:
        out.write(line + '\n')
        out.flush()
        os.fsync(out.fileno())


def linger(ledger, role, ignore_term=False):
    if ignore_term:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    record(ledger, role)
    while True:
        time.sleep(3600)


def spawn(ledger, role, ignore_term=False, escape=False, grandchild=None):
    pid = os.fork()
    if pid:
        if escape:
            os.waitpid(pid, 0)  # reap the intermediate; the escapee is reparented
        return
    try:
        if escape:
            os.setsid()
            if os.fork():
                os._exit(0)
        if grandchild:
            spawn(ledger, grandchild)
        linger(ledger, role, ignore_term)
    finally:
        os._exit(0)


def workload(mode, ledger):
    if mode not in MODES:
        raise SystemExit(f'unknown mode {mode!r}; expected one of {", ".join(MODES)}')
    record(ledger, 'tool')
    spawn(ledger, 'child-a', grandchild='grandchild')
    spawn(ledger, 'child-b', ignore_term=(mode == 'stubborn'))
    if mode == 'escaped':
        spawn(ledger, 'escapee', ignore_term=True, escape=True)
    print(f'cairn-trial workload {mode} running; ledger {ledger}', flush=True)
    while True:
        time.sleep(3600)


def entries(ledger):
    return [json.loads(line) for line in Path(ledger).read_text().splitlines() if line.strip()]


def observe(ledger):
    processes = []
    for entry in entries(ledger):
        current = identity(entry['pid'])
        alive = bool(current and current['start'] == entry['start'] and current['state'] != 'Z')
        processes.append(dict(entry, alive=alive))
    return dict(processes=processes, survivors=[p['role'] for p in processes if p['alive']])


def kill_verified(entry):
    """SIGKILL the ledger process only if it is still that exact process.

    The pidfd pins one process before its start time is rechecked, so a pid
    reused after observation can never direct the signal at another process.
    """
    try:
        pidfd = os.pidfd_open(entry['pid'])
    except ProcessLookupError:
        return
    try:
        current = identity(entry['pid'])
        if current and current['start'] == entry['start']:
            signal.pidfd_send_signal(pidfd, signal.SIGKILL)
    except ProcessLookupError:
        pass
    finally:
        os.close(pidfd)


def cleanup(ledger):
    """SIGKILL surviving ledger processes whose identity still matches."""
    for process in observe(ledger)['processes']:
        if process['alive']:
            kill_verified(process)
    deadline = time.monotonic() + 5
    while observe(ledger)['survivors'] and time.monotonic() < deadline:
        time.sleep(0.05)
    return observe(ledger)


def main(argv):
    if len(argv) == 3 and argv[0] == 'workload':
        workload(argv[1], argv[2])
    elif len(argv) == 2 and argv[0] in ('observe', 'cleanup'):
        result = (observe if argv[0] == 'observe' else cleanup)(argv[1])
        print(json.dumps(result, indent=2))
        return 1 if result['survivors'] else 0
    else:
        raise SystemExit('usage: opencode_cancel_trial.py workload tree|stubborn|escaped LEDGER\n'
                         '       opencode_cancel_trial.py observe|cleanup LEDGER')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
