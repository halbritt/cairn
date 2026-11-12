"""PROPOSED ONLY: four selected-bound crash cells; requires criteria admission.

No import or execution of this file is authorized by the preparation request.
The existing installed fixture, selector program and normal retry comparisons
are reused unchanged. The only signals target measured, test-owned pidfds.
"""
import base64
import copy
import ctypes
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import select
import shlex
import signal
import subprocess
import time
import unittest
import uuid

import test_f03_bound_capture as bound
import test_f04_activation as fixture


def children(pid):
    result = set()
    for task in Path('/proc', str(pid), 'task').iterdir():
        try:
            result.update(map(int, (task/'children').read_text().split()))
        except FileNotFoundError:
            continue
    return result


def measure(pid):
    if type(pid) is not int or pid <= 1:
        raise AssertionError('invalid target PID; no signal is permitted')
    proc = Path('/proc', str(pid))
    raw = (proc/'stat').read_text()
    fields = raw[raw.rfind(')')+2:].split()
    return dict(pid=pid, ppid=int(fields[1]), state=fields[0], start=int(fields[19]),
                boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                uid=proc.stat().st_uid)


class Ref:
    def __init__(self, pid, parent):
        if type(pid) is not int or pid <= 1:
            raise AssertionError('invalid target PID; no pidfd is opened')
        self.fd = os.pidfd_open(pid, 0)
        try:
            self.identity = measure(pid)
            assert self.identity['ppid'] == parent
            assert self.identity['uid'] == os.getuid()
            self.pid = pid
        except BaseException:
            os.close(self.fd)
            raise

    def alive(self):
        return not select.select([self.fd], [], [], 0)[0]

    def send(self, sig):
        assert type(self.pid) is int and self.pid > 1
        if not self.alive():
            return
        now = measure(self.pid)
        assert all(now[key] == self.identity[key] for key in ('pid', 'start', 'boot', 'uid'))
        # The descriptor, never this numeric PID, selects the signal target.
        signal.pidfd_send_signal(self.fd, sig, None, 0)

    def stop(self):
        self.send(signal.SIGSTOP)
        expires = time.monotonic()+2
        while self.alive() and measure(self.pid)['state'] not in ('T', 't'):
            if time.monotonic() >= expires:
                raise AssertionError('owned process did not stop')
            time.sleep(0.01)

    def reap_adopted(self):
        expires = time.monotonic()+5
        while True:
            result = os.waitid(os.P_PIDFD, self.fd, os.WEXITED | os.WNOHANG)
            if result is not None:
                assert result.si_pid == self.pid
                return result
            if time.monotonic() >= expires:
                raise AssertionError('owned adopted descendant did not reap')
            time.sleep(0.01)


class OwnedHook:
    def __init__(self, process, baseline):
        self.process, self.baseline = process, baseline
        self.refs = [Ref(process.pid, os.getpid())]

    def freeze(self):
        def visit(parent):
            if not parent.alive():
                return
            parent.stop()
            # Stopped parents cannot fork/reap while descendants are pinned.
            for pid in sorted(children(parent.pid)):
                existing = next((ref for ref in self.refs if ref.pid == pid), None)
                ref = existing or Ref(pid, parent.pid)
                if existing is None:
                    self.refs.append(ref)
                visit(ref)
        visit(self.refs[0])

    def close(self):
        errors = []
        try:
            try:
                if self.refs[0].alive():
                    self.freeze()
            except BaseException as exc:
                errors.append(exc)
            try:
                self.refs[0].send(signal.SIGKILL)
            except BaseException as exc:
                errors.append(exc)
            try:
                self.process.wait(timeout=5)
            except BaseException as exc:
                errors.append(exc)
            # Parent-first reaping makes each following orphan our child.
            for ref in self.refs[1:]:
                try:
                    ref.send(signal.SIGKILL)
                    ref.reap_adopted()
                except BaseException as exc:
                    errors.append(exc)
            unknown = children(os.getpid())-self.baseline
            if unknown:
                errors.append(AssertionError('unproved descendants remain; no signal sent to '+str(sorted(unknown))))
        finally:
            for ref in self.refs:
                os.close(ref.fd)
        if errors:
            raise BaseExceptionGroup('owned cleanup incomplete', errors)


@contextmanager
def subreaper():
    assert hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal') and hasattr(os, 'P_PIDFD')
    libc = ctypes.CDLL(None, use_errno=True)
    old = ctypes.c_int()
    assert libc.prctl(37, ctypes.byref(old), 0, 0, 0) == 0
    assert libc.prctl(36, 1, 0, 0, 0) == 0
    try:
        yield
    finally:
        assert libc.prctl(36, old.value, 0, 0, 0) == 0


class BoundCaptureCrashTests(bound.BoundProviderCaptureTests):
    def snapshot(self):
        directory = Path(self.f.memory['state_dir'])
        return {path.name: dict(mode=path.stat().st_mode & 0o777, raw=path.read_bytes().hex())
                for path in sorted(directory.iterdir()) if path.suffix != '.lock'} if directory.exists() else {}

    def probe(self, suffix, identity=None):
        path = Path(self.f.memory['state_dir'])/(fixture.SESSION+suffix)
        before = path.stat()  # Never create a substitute for a missing hook lock.
        current = (before.st_dev, before.st_ino, before.st_mode & 0o777)
        if identity is not None:
            self.assertEqual(current, identity)
        with path.open('rb') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False, current
            fcntl.flock(stream, fcntl.LOCK_UN)
        return True, current

    def normalized_calls(self, calls, side):
        result = []
        for operation, original, headers in calls:
            body = copy.deepcopy(original)
            if operation != 'version':
                request = body.pop('request_id')
                self.assertEqual(str(uuid.UUID(request)), request)
                self.assertNotEqual(uuid.UUID(request).int, 0)
            self.assertIn(operation, ('version', 'index'))
            self.assertEqual(headers['Authorization'], 'Bearer bound-memory-synthetic-token')
            token = headers['Cairn-Client-Diagnostics']
            origin = json.loads(base64.urlsafe_b64decode(token+'='*(-len(token)%4)))['origin']
            expected = dict(component='lifecycle-memory', basis='reported')
            if side == 'go':
                expected['implementation_id'] = self.f.receipt['binary']['sha256']
            self.assertEqual(origin, expected)
            result.append((operation, fixture.scalar_tree(body)))
        self.assertEqual([op for op, _, _ in calls if op != 'version'], ['index']*3)
        self.assertGreaterEqual(sum(op == 'version' for op, _, _ in calls), 1)
        return result

    def crash(self, side, seed):
        self.reset_state(seed)
        before = self.snapshot()
        self.calls.clear(); self.report_locks.clear()
        self.spec = dict(delay=60)
        self.before_run(side)
        self.assertEqual(self.f.receipt_sha, self.f.installed['receipt']['sha256'])
        launcher = self.f.launcher('memory', 150) if side == 'source' else self.go_launcher()
        raw_launcher = launcher.read_bytes()
        words = shlex.split(raw_launcher.decode().split('\n', 1)[1])
        self.assertEqual(words[0], 'exec'); self.assertEqual(words[-2:], ['--', '$@'])
        vector = words[1:-2]
        self.assertEqual(vector[vector.index('--hook-timeout')+1], '150')
        self.assertEqual(vector[vector.index('--receipt-sha256')+1], self.f.receipt_sha)
        self.assertEqual(launcher.parent.name, fixture.sha(raw_launcher))
        self.assertEqual(launcher.stat().st_mode & 0o777, 0o500)
        baseline = children(os.getpid())
        process = subprocess.Popen([str(launcher)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, cwd=self.f.root, env=self.f.env())
        try:
            owned = OwnedHook(process, baseline)
        except BaseException as primary:
            errors = [primary]
            try:
                process.stdin.close()  # No event bytes have been written.
            except BaseException as exc:
                errors.append(exc)
            process.stdin = None
            try:
                process.communicate(timeout=3)
            except BaseException as exc:
                errors.append(exc)
            finally:
                for stream in (process.stdout, process.stderr):
                    try:
                        stream.close()
                    except BaseException as exc:
                        errors.append(exc)
            try:
                if process.poll() is None:
                    errors.append(AssertionError('unowned hook remains; no signal sent'))
            except BaseException as exc:
                errors.append(exc)
            if len(errors) == 1:
                raise
            raise BaseExceptionGroup('unowned hook custody/cleanup diagnostics', errors)
        failure = None; observed = None; scratch = None
        try:
            process.stdin.write(self.f.raw); process.stdin.close()
            log = self.root/'provider.calls'
            expires = time.monotonic()+10
            while True:
                logged = log.read_bytes() if log.exists() else b''
                if logged.endswith(b'\n'):
                    break
                self.assertIsNone(process.poll(), 'hook exited before complete selector readiness')
                self.assertLess(time.monotonic(), expires, 'selector readiness deadline')
                time.sleep(0.01)
            lines = logged.splitlines()
            self.assertEqual(len(lines), 1)
            call = json.loads(lines[0])
            selector = Ref(call['pid'], process.pid)  # Log value is only a hint.
            owned.refs.append(selector)
            self.assertEqual(Path('/proc', str(selector.pid), 'cmdline').read_bytes().split(b'\0')[1],
                             os.fsencode(self.root/'selector'))
            self.assertEqual(children(selector.pid), set(), 'the pinned synthetic selector forks no children')
            owned.freeze()  # Stable ancestry/pidfds before intentional parent death.
            actual_argv = Path('/proc', str(process.pid), 'cmdline').read_bytes().rstrip(b'\0').split(b'\0')
            self.assertEqual(actual_argv, [os.fsencode(word) for word in vector+['--']])
            self.assertEqual(set(children(process.pid)), {selector.pid})
            self.assertEqual(call['child'], '1'); self.assertIsNone(call['claudecode'])
            self.assertEqual(call['entries'], [])
            work = Path('/proc', str(selector.pid), 'cwd').resolve(strict=True)
            self.assertEqual(work, Path(call['cwd'])); self.assertTrue(work.name.startswith('cairn-selection-'))
            info = work.stat(); self.assertEqual((info.st_uid, info.st_mode & 0o777), (os.getuid(), 0o700))
            scratch = (work, info.st_dev, info.st_ino)
            self.assertEqual(self.snapshot(), before)
            shared, shared_id = self.probe('.lock'); capture, capture_id = self.probe('.capture.lock')
            self.assertTrue(shared); self.assertFalse(capture)
            owned.refs[0].send(signal.SIGKILL)
            process.wait(timeout=5)
            self.assertEqual(process.returncode, -signal.SIGKILL)
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(self.probe('.lock', shared_id)[0], True)
            self.assertEqual(self.probe('.capture.lock', capture_id)[0], True)
            self.assertFalse(any(op in ('create', 'revise', 'capture-observation') for op, _, _ in self.calls))
            self.assertEqual(self.writes, [])
            observed = dict(side=side, input=self.f.raw.hex(), receipt=self.f.receipt,
                            launcher=raw_launcher.decode(), launcher_sha256=fixture.sha(raw_launcher),
                            hook=owned.refs[0].identity, selector=selector.identity, provider=call,
                            calls=copy.deepcopy(self.calls), state=before,
                            lock_identities=dict(shared=shared_id, capture=capture_id))
        except BaseException as exc:
            failure = exc
        finally:
            try:
                owned.close()
                if scratch is not None:
                    work, device, inode = scratch
                    info = work.stat()
                    self.assertEqual((info.st_dev, info.st_ino), (device, inode))
                    self.assertEqual(list(work.iterdir()), [])
                    work.rmdir()  # Only the kernel-observed empty owned selector cwd.
            except BaseException as exc:
                failure = BaseExceptionGroup('primary/cleanup diagnostics', [failure, exc]) if failure else exc
            try:
                if not process.stdin.closed:
                    process.stdin.close()
            except BaseException as exc:
                failure = BaseExceptionGroup('primary/stdin diagnostics', [failure, exc]) if failure else exc
            process.stdin = None  # communicate must not flush a closed input pipe.
            try:
                stdout, stderr = process.communicate(timeout=3)
            except BaseException as exc:
                failure = BaseExceptionGroup('primary/stream diagnostics', [failure, exc]) if failure else exc
            finally:
                process.stdout.close(); process.stderr.close()
        if failure:
            raise failure
        self.assertEqual((process.returncode, stdout, stderr), (-signal.SIGKILL, b'', b''))
        observed.update(exit=process.returncode, stdout=stdout.hex(), stderr=stderr.hex(), cleaned=True)
        print('BOUND_CAPTURE_CRASH_RAW='+json.dumps(observed, ensure_ascii=True), flush=True)
        return (process.returncode, stdout, stderr), self.snapshot(), self.normalized_calls(observed['calls'], side), observed['provider']['body']

    def exercise_cell(self, harness, event):
        for config in (self.f.memory, self.f.coord):
            config['harness'] = harness
        self.f.publish_configuration()
        messages = [dict(role='user' if i % 2 == 0 else 'assistant',
                         text='Preserve the postgres retention decision and update its operator guide.') for i in range(6)]
        transcript = self.root/(harness+'-crash-transcript.jsonl')
        if harness == 'codex':
            rows = [dict(type='response_item', payload=dict(type='message', role=m['role'],
                    content=[dict(type='input_text' if m['role']=='user' else 'output_text', text=m['text'])])) for m in messages]
        else:
            rows = [dict(type=m['role'], uuid='11111111-1111-4111-8111-'+str(i+1).zfill(12),
                    message=dict(content=[dict(type='text', text=m['text'])])) for i,m in enumerate(messages)]
        fixture.write(transcript, b''.join(fixture.encoded(row)+b'\n' for row in rows))
        self.event(event, transcript_path=str(transcript))
        self.selection['memories'] = [self.note()]
        with subreaper():
            for existing in (False, True):
                with self.subTest(harness=harness, event=event, seeded=existing):
                    state = dict(binding=[None, ''], seen=dict(retained=2), opaque=['日本語', 900719925474099312345],
                                 captured_digest='1'*64, captured_messages=0,
                                 last_capture=dict(at=123.25, outcome='prior', records=[], selector_calls=0),
                                 last_recall=dict(outcome='retained'))
                    if harness == 'codex':
                        marker = dict(path=str(transcript.resolve()), offset=0)
                        state.update(codex_capture_marker=marker, capture_snapshot_marker=marker)
                    seed = {fixture.SESSION+'.json': fixture.encoded(state)} if existing else {}
                    source = self.crash('source', seed)
                    candidate = self.crash('go', seed)
                    self.assertEqual(candidate, source)
                    self.reset_state(seed); self.spec = {}
                    self.assertEqual(self.pair().returncode, 0)  # Existing full normal comparison, unchanged.
                    self.assertEqual(len(self.writes), 1)
                    self.assertEqual(self.writes[0]['draft']['kind'], 'decision')
                    self.assertEqual(self.writes[0]['draft']['body'],
                                     self.f.root.name+': Retention policy\n\nUse thirty days for postgres retention.')
                    saved = self.state()[fixture.SESSION+'.json']
                    self.assertEqual(saved['last_capture']['outcome'], 'saved')
                    self.assertEqual(saved['last_capture']['records'], [self.writes[0]['record_id']])
                    if existing:
                        self.assertEqual(saved['seen'], state['seen'])
                        self.assertEqual(saved['opaque'], state['opaque'])
                        self.assertEqual(saved['last_recall'], state['last_recall'])
                    self.assertEqual(saved['captured_messages'], 6)
                    self.assertRegex(saved['captured_digest'], '^[0-9a-f]{64}$')
                    if harness == 'codex':
                        committed = dict(path=str(transcript.resolve()), offset=transcript.stat().st_size)
                        self.assertEqual(saved['codex_capture_marker'], committed)
                        self.assertEqual(saved['capture_snapshot_marker'], committed)

    def test_codex_precompact(self): self.exercise_cell('codex', 'PreCompact')
    def test_codex_stop(self): self.exercise_cell('codex', 'Stop')
    def test_claude_precompact(self): self.exercise_cell('claude', 'PreCompact')
    def test_claude_session_end(self): self.exercise_cell('claude', 'SessionEnd')


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(BoundCaptureCrashTests(name) for name in (
        'test_codex_precompact', 'test_codex_stop', 'test_claude_precompact', 'test_claude_session_end'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
