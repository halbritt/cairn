#!/usr/bin/env python3
"""Fixture-only subreaper for one advice CLI and its actual descendants.

The normal path kills only the CLI controller, then observes Store-owned group
reconciliation. Failure cleanup uses retained pidfds for this owned tree.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import time


def observation(pid):
    raw = Path(f'/proc/{pid}/stat').read_text()
    fields = raw[raw.rfind(')') + 2:].split()
    return (dict(pid=pid, pgid=int(fields[2]), start_ticks=int(fields[19]),
                 boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip()), int(fields[1]))


def write_json(path, value):
    temporary = path.with_suffix('.pending')
    temporary.write_text(json.dumps(value, sort_keys=True) + '\n')
    temporary.chmod(0o600)
    temporary.replace(path)


class Supervisor:
    def __init__(self, channel, root):
        self.channel, self.root = channel, root
        self.cli = self.control = None
        self.handles, self.reaped = {}, {}
        self.anchors, self.parents = {}, {}
        self.killed_by_fixture = []
        self.errors = []
        self.closed = False
        self.close_result = None

    def anchor(self, process):
        # Popen's unreaped direct child cannot be recycled before this capture.
        observed, parent = observation(process.pid)
        if parent != os.getpid():
            raise RuntimeError('launched root is not the supervisor direct child')
        descriptor = os.pidfd_open(process.pid)
        try:
            again, parent_after = observation(process.pid)
            if again != observed or parent_after != os.getpid():
                raise RuntimeError('launched root changed during pidfd capture')
        except BaseException:
            os.close(descriptor)
            raise
        self.anchors[process.pid] = (descriptor, observed)

    def live_parent(self, pid):
        if pid == os.getpid():
            return True
        held = self.anchors.get(pid) or self.handles.get(pid)
        if held is None:
            raise RuntimeError('attempt to traverse an unpinned parent')
        descriptor, recorded = held
        if select.select([descriptor], [], [], 0)[0]:
            return False
        try:
            observed, _ = observation(pid)
        except (FileNotFoundError, ProcessLookupError):
            return False
        if observed != recorded:
            raise RuntimeError('pinned parent numeric PID now names a different process')
        return not select.select([descriptor], [], [], 0)[0]

    def capture(self, pid, parent):
        if not self.live_parent(parent):
            return False
        observed, ppid = observation(pid)
        allowed = (parent, os.getpid())
        if ppid not in allowed:
            raise RuntimeError('numeric child no longer belongs to the authenticated parent/subreaper')
        held = self.anchors.get(pid) or self.handles.get(pid)
        descriptor = held[0] if held else os.pidfd_open(pid)
        try:
            again, ppid_after = observation(pid)
            if again != observed or (held is not None and again != held[1]):
                raise RuntimeError('numeric child identity changed during capture')
            if ppid_after != ppid and not (ppid == parent and ppid_after == os.getpid()):
                raise RuntimeError('child parentage changed outside the owned adoption path')
            if ppid_after not in allowed:
                raise RuntimeError('child parentage does not prove ownership')
            if ppid_after != os.getpid() and not self.live_parent(parent):
                if held is None:
                    os.close(descriptor)
                return False
            exited = bool(select.select([descriptor], [], [], 0)[0])
        except BaseException:
            if held is None:
                os.close(descriptor)
            raise
        if held is None:
            if len(self.handles) >= 32:
                os.close(descriptor)
                raise RuntimeError('owned descendant identity bound exceeded')
            self.handles[pid] = (descriptor, observed)
            self.parents[pid] = dict(discovered_under=parent, before=ppid, after=ppid_after)
        # Retain an owned zombie too, so later adoption/reaping cannot leave an
        # untracked child. Its exited pidfd forbids descendant traversal.
        return not exited

    def children(self, parent):
        if not self.live_parent(parent):
            return []
        try:
            tasks = []
            for task in Path(f'/proc/{parent}/task').iterdir():
                if not task.name.isdecimal() or len(tasks) >= 256:
                    raise RuntimeError('owned process thread enumeration exceeded its bound')
                tasks.append(task)
        except FileNotFoundError:
            return []
        children = set()
        for task in tasks:
            try:
                with (task / 'children').open() as stream:
                    raw = stream.read(8193)
            except FileNotFoundError:
                continue  # A thread exited; the next bounded round can see adoption.
            if len(raw) > 8192:
                raise RuntimeError('owned thread child list exceeded its byte bound')
            children.update(int(value) for value in raw.split())
            if len(children) > 35:
                raise RuntimeError('owned process child union exceeded its process bound')
        # Discard a numeric list if its pinned parent exited during the read.
        if not self.live_parent(parent):
            return []
        return sorted(children)

    def no_waitable_children(self):
        # proc children can omit entries during concurrent exit. This dedicated
        # supervisor launches ordinary SIGCHLD children; ECHILD is a separate
        # kernel absence check. Do not signal or reap any PID returned by it.
        try:
            os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            return True
        return False

    def discover(self):
        pending, seen = [os.getpid()], set()
        while pending:
            parent = pending.pop()
            if parent in seen:
                continue
            seen.add(parent)
            if len(seen) > 35:
                raise RuntimeError('owned fixture tree exceeded its process bound')
            for pid in self.children(parent):
                if self.control is not None and pid == self.control.pid:
                    # Exclude only the still-matching pinned control, never a
                    # later process which happened to reuse its numeric PID.
                    current, ppid = observation(pid)
                    if current != self.anchors[pid][1] or ppid != os.getpid():
                        raise RuntimeError('control numeric PID no longer matches its pinned child')
                    continue
                try:
                    if self.capture(pid, parent):
                        pending.append(pid)
                except (ProcessLookupError, FileNotFoundError):
                    continue

    def reap(self):
        if self.cli is not None:
            self.cli.poll()
        if self.control is not None:
            self.control.poll()
        for pid, (_, recorded) in self.handles.items():
            if pid in self.reaped:
                continue
            try:
                observed, parent = observation(pid)
            except (FileNotFoundError, ProcessLookupError):
                continue
            if observed != recorded:
                raise RuntimeError('refusing to reap a reused numeric descendant PID')
            if parent != os.getpid():
                continue  # Not adopted until the genuine CLI/leader exits.
            try:
                found, status = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                continue
            if found:
                self.reaped[pid] = status

    def state(self, *, discover=True, reap=True):
        if discover:
            self.discover()
        if reap:
            self.reap()
        family = []
        for pid, (descriptor, recorded) in self.handles.items():
            family.append(dict(identity=recorded, parentage=self.parents[pid], exited=bool(select.select([descriptor], [], [], 0)[0]),
                               reaped=pid in self.reaped, proc_present=Path(f'/proc/{pid}').exists()))
        return dict(cli_pid=self.cli.pid if self.cli else None,
                    cli_exit=self.cli.poll() if self.cli else None,
                    control_identity=observation(self.control.pid)[0] if self.control and self.control.poll() is None else None,
                    family=family, killed_by_fixture=list(self.killed_by_fixture), errors=list(self.errors))

    def start(self, argv):
        if self.cli is not None or not argv or not Path(argv[0]).is_absolute():
            raise RuntimeError('one exact executable launch is required')
        # Files are bounded by the runner's own stdout/stderr contract. The
        # supervisor never sends the operator environment over its control socket.
        with (self.root / 'controller.stdout').open('wb') as out, (self.root / 'controller.stderr').open('wb') as err:
            self.cli = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
        self.anchor(self.cli)
        self.control = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(240)'],
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, start_new_session=True)
        self.anchor(self.control)
        return self.state()

    def stop_controller(self):
        self.discover()
        if self.cli is None or self.cli.poll() is not None:
            raise RuntimeError('live owned CLI required before crash injection')
        if not self.live_parent(self.cli.pid):
            raise RuntimeError('controller pidfd no longer names a live owned launch')
        signal.pidfd_send_signal(self.anchors[self.cli.pid][0], signal.SIGKILL)
        self.cli.wait(timeout=5)
        if self.cli.returncode != -signal.SIGKILL:
            raise RuntimeError('controller crash was not the requested SIGKILL')
        return self.state()

    def close(self):
        if self.closed:
            return self.close_result
        self.closed = True
        for label, process in (('controller', self.cli), ('independent control', self.control)):
            if process is not None and process.poll() is None:
                try:
                    held = self.anchors.get(process.pid)
                    if held is None:
                        # A failed immediate anchor still leaves an unreaped
                        # direct Popen child; validate and pin before signaling.
                        self.anchor(process)
                        held = self.anchors[process.pid]
                    try:
                        signal.pidfd_send_signal(held[0], signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=5)
                except BaseException as error:
                    self.errors.append(label + ' cleanup: ' + str(error))
        # Re-discover after each signal/reap round: a leader can fork just after
        # discovery, and killing it can newly adopt that child into this tree.
        deadline = time.monotonic() + 5
        children_remaining = []
        while time.monotonic() < deadline:
            try:
                self.discover()
                for pid, (descriptor, _) in self.handles.items():
                    if not select.select([descriptor], [], [], 0)[0]:
                        try:
                            signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                            if pid not in self.killed_by_fixture:
                                self.killed_by_fixture.append(pid)
                        except ProcessLookupError:
                            pass
                self.reap()
                children_remaining = self.children(os.getpid())
                if not children_remaining and self.no_waitable_children():
                    break
            except BaseException as error:
                self.errors.append('owned-tree cleanup: ' + str(error))
                break
            time.sleep(0.02)
        result = self.state(discover=False, reap=False)
        result['remaining_owned_children'] = self.children(os.getpid())
        result['no_waitable_children'] = self.no_waitable_children()
        result['cleanup_confirmed'] = (not self.errors and result['cli_exit'] is not None
            and result['control_identity'] is None and not result['remaining_owned_children']
            and result['no_waitable_children']
            and all(row['exited'] and not row['proc_present'] for row in result['family']))
        write_json(self.root / 'supervisor-final.json', result)
        self.close_result = result
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fd', type=int, required=True)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    if not args.root.is_dir() or args.root.stat().st_uid != os.getuid():
        raise RuntimeError('fixture-owned runtime directory required')
    if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        raise RuntimeError('pidfd APIs required; no numeric signal fallback')
    if not all(hasattr(os, name) for name in ('waitid', 'P_ALL', 'WEXITED', 'WNOHANG', 'WNOWAIT')):
        raise RuntimeError('nonconsuming child absence check required')
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), 'fixture subreaper setup failed')
    channel = socket.socket(fileno=args.fd)
    channel.settimeout(5)
    supervisor = Supervisor(channel, args.root)
    deadline, failure = time.monotonic() + 240, None
    try:
        while time.monotonic() < deadline:
            supervisor.discover()
            supervisor.reap()
            if not select.select([channel], [], [], 0.05)[0]:
                continue
            raw = channel.recv(32769)
            if not raw:
                raise RuntimeError('fixture controller disconnected before cleanup')
            if len(raw) > 32768:
                raise RuntimeError('oversize supervisor request')
            message = json.loads(raw)
            operation = message.get('operation')
            if operation == 'start':
                result = supervisor.start(message['argv'])
            elif operation == 'kill_controller':
                result = supervisor.stop_controller()
            elif operation == 'status':
                result = supervisor.state()
            elif operation == 'close':
                result = supervisor.close()
            else:
                raise RuntimeError('unsupported fixture supervisor operation')
            channel.sendall(json.dumps(dict(ok=True, data=result)).encode())
            if operation == 'close':
                break
        else:
            raise RuntimeError('fixture supervisor exceeded 240-second bound')
    except BaseException as error:
        failure = str(error)
        supervisor.errors.append(failure)
    finally:
        result = supervisor.close()
        for descriptor, _ in [*supervisor.handles.values(), *supervisor.anchors.values()]:
            os.close(descriptor)
        channel.close()
    return 0 if failure is None and result.get('cleanup_confirmed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
