"""Bounded native pipes and selected OS observations; no launch/admission policy."""
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time


def process_identity(pid):
    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(') ', 1)[1].split()
    return dict(pid=pid, state=fields[0], ppid=int(fields[1]), pgid=int(fields[2]),
                sid=int(fields[3]), start=int(fields[19]))


class OwnedProcesses:
    """The unreaped leader anchors the session; pidfds anchor signal targets."""
    def __init__(self, child):
        self.child, self.known, self.errors = child, {}, set()
        self.leader, self.leader_fd = None, None
        self.wait_status = None
        self.escaped = set()
        try:
            self.leader_fd = os.pidfd_open(child.pid)
        except (OSError, AttributeError):
            self.errors.add('leader_pidfd_unknown')
        try:
            self.leader = process_identity(child.pid)
            if self.leader_fd is not None:
                self.known[(child.pid, self.leader['start'])] = self.leader_fd
        except (OSError, ValueError, IndexError):
            self.errors.add('leader_identity_unknown')

    def leader_exited(self):
        try:
            return os.waitid(os.P_PID, self.child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
        except (OSError, AttributeError):
            self.errors.add('leader_exit_observation_unknown')
            return False

    def parent_bound(self, parent):
        for (pid, _), fd in self.known.items():
            if pid != parent:
                continue
            try:
                signal.pidfd_send_signal(fd, 0)
                return True
            except ProcessLookupError:
                return False
            except (OSError, AttributeError):
                self.errors.add('parent_identity_unknown')
                return False
        return False

    def bind_member(self, member):
        identity = (member['pid'], member['start'])
        if identity in self.known:
            return True
        if len(self.known) >= 256:
            self.errors.add('descendant_tracking_limit')
            return False
        fd = None
        try:
            fd = os.pidfd_open(member['pid'])
            current = process_identity(member['pid'])
            if current['start'] != member['start']:
                self.errors.add('descendant_identity_changed')
                return False
            # A first-observed escape requires a currently bound parent. An
            # already reparented escape does not gain ownership from start time.
            if current['sid'] != self.child.pid and not self.parent_bound(current['ppid']):
                self.errors.add('descendant_ancestry_unbound')
                return False
            signal.pidfd_send_signal(fd, 0)
            self.known[identity] = fd
            fd = None
            return True
        except (FileNotFoundError, ProcessLookupError):
            return False
        except (OSError, AttributeError, ValueError, IndexError):
            self.errors.add('descendant_pidfd_unknown')
            return False
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    self.errors.add('pidfd_close_unknown')

    def sample(self):
        if self.leader is None:
            return []
        snapshot = []
        try:
            with os.scandir('/proc') as entries:
                for number, entry in enumerate(entries):
                    if number >= 16384:
                        self.errors.add('process_scan_limit')
                        break
                    if not entry.name.isdigit():
                        continue
                    try:
                        snapshot.append(process_identity(int(entry.name)))
                    except (FileNotFoundError, ProcessLookupError):
                        continue
                    except (OSError, ValueError, IndexError):
                        self.errors.add('process_scan_unknown')
        except OSError:
            self.errors.add('process_scan_unknown')
        while True:
            parents = {pid for pid, _ in self.known}
            added = False
            for member in snapshot:
                if (member['pid'], member['start']) in self.known:
                    continue
                if member['start'] >= self.leader['start'] and (member['sid'] == self.child.pid or member['ppid'] in parents):
                    added = self.bind_member(member) or added
            if not added:
                break
        owned = [p for p in snapshot if (p['pid'], p['start']) in self.known]
        self.escaped.update((p['pid'], p['start']) for p in owned if p['sid'] != self.child.pid)
        return owned

    def signal_members(self, members, signum):
        for member in members:
            if member['state'] in ('Z', 'X'):
                continue
            fd = self.known.get((member['pid'], member['start']))
            if fd is None:
                self.errors.add('member_identity_unbound')
                continue
            try:
                current = process_identity(member['pid'])
                if current['start'] != member['start']:
                    self.errors.add('descendant_identity_changed')
                    continue
                signal.pidfd_send_signal(fd, signum)
            except (FileNotFoundError, ProcessLookupError):
                pass
            except (OSError, AttributeError, ValueError, IndexError):
                self.errors.add('member_signal_unknown')

    def cleanup(self):
        before = self.sample()
        for signum in (signal.SIGTERM, signal.SIGKILL):
            members = self.sample()
            self.signal_members(members, signum)
            if self.leader is None and self.leader_fd is not None:
                try:
                    signal.pidfd_send_signal(self.leader_fd, signum)
                except ProcessLookupError:
                    pass
                except (OSError, AttributeError):
                    self.errors.add('leader_signal_unknown')
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                members = self.sample()
                if not any(p['state'] not in ('Z', 'X') for p in members) and self.leader_exited():
                    break
                time.sleep(.02)
            else:
                continue
            break
        # No session scan or reconstructed signal is allowed after this reap.
        remaining = self.sample()
        try:
            waited, status = os.waitpid(self.child.pid, os.WNOHANG)
            if waited == self.child.pid:
                self.wait_status = status
                self.child.returncode = os.waitstatus_to_exitcode(status)
        except OSError:
            self.errors.add('leader_wait_unknown')
        if self.wait_status is None:
            self.errors.add('leader_wait_unknown')
        if any(p['state'] not in ('Z', 'X') for p in remaining):
            self.errors.add('observed_members_remain')
        if self.child.returncode is not None:
            remaining = [p for p in remaining if p['pid'] != self.child.pid]
        return dict(status='unknown' if self.errors else 'observed_clear',
                    errors=sorted(self.errors), members_before_cleanup=before,
                    members_after_cleanup=remaining, observed_escaped_count=len(self.escaped),
                    complete_descendant_custody=False,
                    scope='anchored_new_session_and_pidfd_bound_descendants',
                    limits=['unsampled_or_reparented_escape_unproven', 'procfs_io_not_hard_supervised',
                            'zombie_reaping_by_other_parents_unproven'])

    def close(self):
        for fd in set(self.known.values()) | ({self.leader_fd} if self.leader_fd is not None else set()):
            try:
                os.close(fd)
            except OSError:
                self.errors.add('pidfd_close_unknown')


def retain_metadata(path, value):
    with path.open('x') as output:
        json.dump(value, output, indent=2)
        output.flush()
        os.fsync(output.fileno())


def capture(command, timeout, timing_path, *, input_bytes=None, custody_path=None, pass_fds=(), observer=None):
    """Legacy four-tuple; classification is distinct from observed child status."""
    started = time.monotonic()
    deadline = started + timeout
    stdout, stderr, pending = bytearray(), bytearray(), bytearray()
    timings, sent, stderr_total = [], 0, 0
    classification, code, pipe_eof = 'completed', None, set()
    child = subprocess.Popen(command, stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                             pass_fds=pass_fds)
    owner = OwnedProcesses(child)
    def timing(line):
        timings.append(dict(line=len(timings)+1, received_seconds=time.monotonic()-started,
                            bytes=len(line), sha256=hashlib.sha256(line).hexdigest()))
    try:
        with selectors.DefaultSelector() as selector:
            for stream, name in ((child.stdout, 'stdout'), (child.stderr, 'stderr')):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            if input_bytes:
                os.set_blocking(child.stdin.fileno(), False)
                selector.register(child.stdin, selectors.EVENT_WRITE, 'stdin')
            elif child.stdin:
                child.stdin.close()
            while selector.get_map() or not owner.leader_exited():
                if observer is not None:
                    observer(child)  # owner observation point; it must not raise or block
                owner.sample()
                left = deadline - time.monotonic()
                if left <= 0:
                    classification, code = 'timeout', 124
                    break
                for key, _ in selector.select(min(.1, left)):
                    if key.data == 'stdin':
                        try:
                            sent += os.write(key.fd, memoryview(input_bytes)[sent:sent+65536])
                        except BlockingIOError:
                            continue
                        except BrokenPipeError:
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                            continue
                        if sent == len(input_bytes):
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                        continue
                    try:
                        block = os.read(key.fd, 65536)
                    except BlockingIOError:
                        continue
                    if not block:
                        pipe_eof.add(key.data)
                        selector.unregister(key.fileobj)
                        continue
                    if key.data == 'stderr':
                        stderr_total += len(block)
                        stderr.extend(block)
                        del stderr[:-20000]
                        continue
                    stdout.extend(block)
                    pending.extend(block)
                    while b'\n' in pending:
                        line, _, pending = pending.partition(b'\n')
                        timing(line+b'\n')
                        if len(timings) >= 65536:
                            classification, code = 'timing_limit', 125
                            break
                    if code is not None:
                        break
                    if len(pending) > 8*1024*1024 or len(stdout) > 64*1024*1024:
                        classification, code = 'output_limit', 125
                        break
                if code is not None:
                    break
            if pending and len(timings) < 65536:
                timing(pending)
    except BaseException:
        classification = 'observer_interrupted_or_io_error'
        raise
    finally:
        try:
            cleanup = owner.cleanup()
        finally:
            owner.close()
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        owner.errors.add('stream_close_unknown')
        cleanup['errors'] = sorted(owner.errors)
        cleanup['status'] = 'unknown' if owner.errors else 'observed_clear'
        if code is None:
            code = child.returncode if child.returncode is not None else 125
        metadata = dict(schema='cairn.native-process-custody/1', classification=classification,
                        legacy_exit_code=code, observed_returncode=child.returncode,
                        observed_wait_status=owner.wait_status, returncode_basis='waitpid_status_to_exitcode',
                        leader_reaped=owner.wait_status is not None, cleanup=cleanup,
                        input_bytes=0 if input_bytes is None else len(input_bytes), input_bytes_written=sent,
                        input_delivery_complete=sent == (0 if input_bytes is None else len(input_bytes)),
                        stdout_bytes=len(stdout), stderr_bytes=stderr_total,
                        stdout_eof='stdout' in pipe_eof, stderr_eof='stderr' in pipe_eof,
                        raw_stream_retained=False, hard_io_deadline_attested=False)
        if custody_path is not None:
            retain_metadata(custody_path, metadata)
        timing_path.write_text(json.dumps(timings, indent=2))
    return bytes(stdout), bytes(stderr), code, timings
