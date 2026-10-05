"""Optional lifecycle invocation trace: fixed metadata, bounded, never behavioural.

Model-free: temporary fixtures, the fake CLI and synthetic bindings only.
"""
import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest
import uuid
from unittest.mock import patch

import test_inbox_recall_bridge as codex_fixture

memory = codex_fixture.memory

KEYS = {'entry': {'schema', 'kind', 'inv', 'at', 'pid', 'ppid'},
        'exit': {'schema', 'kind', 'inv', 'at', 'duration_ms', 'exit_code', 'phase', 'stage', 'outcome',
                 'error_class', 'event', 'session', 'turn', 'context_bytes', 'bound', 'entry_recorded'}}
REQUIRED = {'entry': {'schema', 'kind', 'inv', 'at', 'pid', 'ppid'},
            'exit': {'schema', 'kind', 'inv', 'at', 'duration_ms', 'outcome', 'entry_recorded'}}
ORDINARY = 'Repair the fixture validation while retaining the current scope.'


def records(path):
    """Test-only reader: complete, schema-valid lines are records; the rest is torn."""
    good, torn = [], 0
    raw = path.read_bytes() if path.exists() else b''
    lines = raw.split(b'\n')
    for n, line in enumerate(lines):
        if n == len(lines) - 1:
            torn += bool(line)  # no terminating newline
            continue
        try:
            value = json.loads(line)
        except ValueError:
            torn += bool(line)
            continue
        kind = value.get('kind') if isinstance(value, dict) else None
        if (kind in KEYS and value.get('schema') == 'cairn.hook-invocation/1'
                and set(value) <= KEYS[kind] and REQUIRED[kind] <= set(value) and len(line) < 512):
            good.append(value)
        else:
            torn += 1
    return good, torn


class InvocationTraceTests(unittest.TestCase):
    def setUp(self):
        self.case = codex_fixture.InboxRecallBridgeTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root = self.case.root
        self.trace_dir = self.root / 'invocation-trace'
        self.trace = self.trace_dir / 'trace.jsonl'
        self.event = dict(self.case.event, prompt=ORDINARY)

    def enable(self):
        self.trace_dir.mkdir(mode=0o700)

    def run_hook(self, event=None, *, env=None, config='memory.json', raw=None):
        return subprocess.run([sys.executable, str(self.root / 'memory.py'), '--config', str(self.root / config)],
                              input=raw if raw is not None else json.dumps(event or self.event).encode(),
                              capture_output=True, timeout=20, env=dict(os.environ, **(env or {})))

    def pair(self):
        good, torn = records(self.trace)
        self.assertEqual(torn, 0, self.trace.read_bytes())
        entries = [r for r in good if r['kind'] == 'entry']
        exits = [r for r in good if r['kind'] == 'exit']
        self.assertEqual((len(entries), len(exits)), (1, 1), good)
        self.assertEqual(entries[0]['inv'], exits[0]['inv'])
        return entries[0], exits[0]

    def stable_state(self):
        path = self.root / 'memory' / (self.event['session_id'] + '.json')
        state = json.loads(path.read_text()) if path.exists() else {}
        last = state.get('last_recall', {})
        return {k: last.get(k) for k in ('outcome', 'bytes', 'error_type', 'event')}

    # T1 actual two-record entry/exit through the real file path
    def test_normal_prompt_writes_entry_and_exit(self):
        self.enable()
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        entry, exit_ = self.pair()
        self.assertEqual(entry['pid'] > 0 and entry['ppid'] > 0, True)
        self.assertEqual(exit_['outcome'], 'completed')
        self.assertEqual(exit_['exit_code'], 0)
        self.assertEqual(exit_['phase'], 'handle')
        self.assertEqual(exit_['stage'], 'saved')
        self.assertEqual((exit_['event'], exit_['session'], exit_['turn']),
                         ('UserPromptSubmit', self.event['session_id'], self.event['turn_id']))
        self.assertIs(exit_['bound'], True)
        self.assertIs(exit_['entry_recorded'], True)
        context = json.loads(result.stdout).get('hookSpecificOutput', {}).get('additionalContext', '')
        self.assertEqual(exit_['context_bytes'], len(context.encode()))
        self.assertEqual(self.trace.stat().st_mode & 0o777, 0o600)

    # T2 deterministic differential contract: absent / enabled / broken
    def test_trace_does_not_change_observable_behaviour(self):
        outcomes = []
        for mode in ('absent', 'enabled', 'fifo', 'symlink', 'open_mode', 'full'):
            with self.subTest(mode):
                self.setUp()
                if mode == 'enabled':
                    self.enable()
                elif mode == 'fifo':
                    self.enable(); os.mkfifo(self.trace, 0o600)
                elif mode == 'symlink':
                    target = self.root / 'elsewhere'; target.mkdir(mode=0o700)
                    self.trace_dir.symlink_to(target)
                elif mode == 'open_mode':
                    self.trace_dir.mkdir(mode=0o755); self.trace_dir.chmod(0o755)
                elif mode == 'full':
                    self.enable()
                    with self.trace.open('wb') as out:
                        out.truncate(memory.TRACE_CAP)
                    self.trace.chmod(0o600)
                started = time.monotonic()
                result = self.run_hook()
                self.assertLess(time.monotonic() - started, 15)
                outcomes.append((mode, result.returncode, result.stdout, result.stderr, json.dumps(self.stable_state(), sort_keys=True)))
                if mode == 'symlink':
                    self.assertFalse(any((self.root / 'elsewhere').iterdir()))
                if mode == 'full':
                    self.assertEqual(self.trace.stat().st_size, memory.TRACE_CAP)
        baseline = outcomes[0][1:]
        for mode, *observed in outcomes[1:]:
            self.assertEqual(tuple(observed), baseline, mode)

    # T3 lock contention, bound and unbound
    def test_lock_contention_labels_without_changing_exit(self):
        for bound in (True, False):
            with self.subTest(bound=bound):
                self.setUp()
                self.enable()
                if not bound:
                    config = dict(self.case.mc); config.pop('inbox_recall_binding')
                    (self.root / 'memory.json').write_text(json.dumps(config))
                directory = self.root / 'memory'; directory.mkdir(mode=0o700, exist_ok=True)
                with (directory / (self.event['session_id'] + '.lock')).open('a') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    result = self.run_hook()
                entry, exit_ = self.pair()
                self.assertLess(entry['at'], exit_['at'] + 1)
                if bound:
                    self.assertEqual((result.returncode, exit_['outcome'], exit_['exit_code']), (1, 'lock_busy_error', 1))
                    self.assertEqual(exit_['error_class'], 'HookError')
                else:
                    self.assertEqual((result.returncode, result.stdout, exit_['outcome']), (0, b'{}\n', 'lock_busy_skip'))
                self.assertEqual(exit_['stage'], 'lock_wait')

    # T4 early returns
    def test_early_return_labels(self):
        cases = [
            ('early_return:disabled_env', dict(self.event), dict(CAIRN_LIFECYCLE_DISABLED='1')),
            ('early_return:stop_not_boundary', dict(self.event, hook_event_name='Stop', stop_hook_active=True), None),
            ('early_return:unsupported_event', dict(self.event, hook_event_name='Notification'), None),
        ]
        for label, event, env in cases:
            with self.subTest(label):
                self.setUp(); self.enable()
                result = self.run_hook(event, env=env)
                self.assertEqual((result.returncode, result.stdout), (0, b'{}\n'))
                self.assertEqual(self.pair()[1]['outcome'], label)
        with self.subTest('no_memory_marker'):
            self.setUp(); self.enable()
            (self.root / '.cairn-no-memory').write_text('')
            result = self.run_hook()
            self.assertEqual((result.returncode, result.stdout), (0, b'{}\n'))
            self.assertEqual(self.pair()[1]['outcome'], 'early_return:no_memory_marker')
        with self.subTest('stop_too_little_dialogue'):
            self.setUp(); self.enable()
            rollout = self.root / 'rollout.jsonl'; rollout.write_text('')
            result = self.run_hook(dict(self.event, hook_event_name='Stop', transcript_path=str(rollout)))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.pair()[1]['outcome'], 'early_return:stop_too_little_dialogue')
        with self.subTest('wake_coordinated'):
            self.setUp(); self.enable()
            result = self.run_hook(self.case.event)
            self.assertEqual((result.returncode, result.stdout), (0, b'{}\n'), result.stderr)
            self.assertEqual(self.pair()[1]['outcome'], 'early_return:wake_coordinated')

    # T5 failures before handle_event keep fixed labels and no text
    def test_pre_handle_failures(self):
        self.enable()
        (self.root / 'broken.json').write_text('{not json')
        result = self.run_hook(config='broken.json')
        exit_ = self.pair()[1]
        self.assertEqual((result.returncode, exit_['phase'], exit_['error_class'], exit_['outcome']),
                         (1, 'configuration', 'ValueError', 'error'))
        self.setUp(); self.enable()
        result = self.run_hook(raw=b' ' * (1024 * 1024 + 1))
        self.assertEqual((result.returncode, self.pair()[1]['phase']), (1, 'input'))
        self.setUp(); self.enable()
        (self.root / 'inbox_recall.py').write_text('# changed bridge\n')
        result = self.run_hook()
        exit_ = self.pair()[1]
        self.assertEqual((result.returncode, exit_['error_class'], exit_['outcome']), (1, 'ValueError', 'error'))
        self.assertIn(exit_['phase'], ('activation', 'handle'))
        self.setUp(); self.enable()
        result = self.run_hook(dict(self.event, session_id='not-a-session'))
        exit_ = self.pair()[1]
        self.assertEqual((result.returncode, exit_['error_class']), (1, 'HookError'))
        self.assertNotIn('session', exit_)

    # T6 required-context refusal is unchanged and labelled
    def test_required_refusal(self):
        self.enable()
        self.case.fixture['search']['selected'] = [dict(mandatory=True, record=dict(body='Keep the fixture instruction.'))]
        self.case.freeze()
        admitted = self.case.invoke(main=True)
        self.assertEqual(admitted['code'], 0, admitted)
        directory = self.root / 'memory'; directory.mkdir(mode=0o700, exist_ok=True)
        before = records(self.trace)[0]
        with (directory / (self.case.event['session_id'] + '.lock')).open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_hook(self.case.event)
        self.assertEqual((result.returncode, result.stdout), (2, b''))
        exits = [r for r in records(self.trace)[0] if r['kind'] == 'exit' and r not in before]
        self.assertEqual([(r['outcome'], r['exit_code']) for r in exits], [('required_refused', 2)])

    # T7 single hard cap including repair newline; torn tails are repaired
    def test_cap_and_torn_tail(self):
        self.enable()
        with self.trace.open('wb') as out:
            out.truncate(memory.TRACE_CAP - 10)
        self.trace.chmod(0o600)
        self.run_hook()
        self.assertEqual(self.trace.stat().st_size, memory.TRACE_CAP - 10)
        self.setUp(); self.enable()
        self.trace.write_bytes(b'{"partial'); self.trace.chmod(0o600)
        result = self.run_hook()
        self.assertEqual(result.returncode, 0)
        good, torn = records(self.trace)
        self.assertEqual((torn, sorted(r['kind'] for r in good)), (1, ['entry', 'exit']))

    def test_parallel_writers_never_exceed_cap(self):
        self.enable()
        with self.trace.open('wb') as out:
            out.truncate(memory.TRACE_CAP - 2000)
        self.trace.chmod(0o600)
        procs = [subprocess.Popen([sys.executable, str(self.root / 'memory.py'), '--config', str(self.root / 'memory.json')],
                                  stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                 for _ in range(16)]
        for proc in procs:
            proc.communicate(json.dumps(dict(self.event, session_id=str(uuid.uuid4()))).encode(), timeout=30)
        self.assertLessEqual(self.trace.stat().st_size, memory.TRACE_CAP)

    # T8 trace-lock contention drops without waiting or touching the session lock
    def test_trace_lock_contention_drops(self):
        self.enable()
        self.trace.write_bytes(b''); self.trace.chmod(0o600)
        with self.trace.open('rb') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            started = time.monotonic()
            result = self.run_hook()
            self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.trace.read_bytes(), b'')

    # T9 directory pinned by a nofollow fd; never written through a symlink
    def test_directory_replaced_after_entry_never_redirects(self):
        self.enable()
        original = memory._InvocationTrace.write
        moved = self.root / 'moved-trace'
        decoy = self.root / 'decoy'; decoy.mkdir(mode=0o700)
        def swap(trace, record):
            ok = original(trace, record)
            if record.get('kind') == 'entry':
                self.trace_dir.rename(moved)
                self.trace_dir.symlink_to(decoy)
            return ok
        with patch.object(memory._InvocationTrace, 'write', swap):
            self.case.memory_main(self.event)
        self.assertFalse(any(decoy.iterdir()))
        kinds = sorted(r['kind'] for r in records(moved / 'trace.jsonl')[0])
        self.assertEqual(kinds, ['entry', 'exit'])

    # T10 per-invocation state never leaks across main() calls
    def test_invocation_state_is_reset(self):
        self.enable()
        self.case.memory_main(dict(self.event, hook_event_name='Notification'))
        self.assertIsNone(memory._TRACE.get())
        self.case.memory_main(self.event)
        self.assertIsNone(memory._TRACE.get())
        good, _ = records(self.trace)
        exits = [r for r in good if r['kind'] == 'exit']
        self.assertEqual([r['outcome'] for r in exits], ['early_return:unsupported_event', 'completed'])
        self.assertEqual(len({r['inv'] for r in good}), 2)

    # T11 short write is dropped, later records repair the tail
    def test_short_write_is_dropped_and_repaired(self):
        self.enable()
        real_write = os.write
        def short(fd, data):
            return real_write(fd, data[:-5])
        trace = memory._InvocationTrace(self.root / 'memory.json')
        try:
            with patch.object(memory.os, 'write', short):
                self.assertFalse(trace.write(dict(schema='cairn.hook-invocation/1', kind='entry', inv='a' * 32,
                                                  at=1, pid=1, ppid=1)))
            self.assertTrue(trace.write(dict(schema='cairn.hook-invocation/1', kind='entry', inv='b' * 32,
                                             at=2, pid=2, ppid=2)))
        finally:
            trace.close()
        good, torn = records(self.trace)
        self.assertEqual((torn, [r['inv'] for r in good]), (1, ['b' * 32]))

    # T12 privacy and schema bounds
    def test_no_private_text_and_bounded_schema(self):
        self.enable()
        canary = 'CANARY-' + uuid.uuid4().hex
        self.case.fixture['pull']['selection']['record']['body'] = canary + ' body'
        self.case.freeze()
        event = dict(self.event, prompt=canary + ' prompt', workstream=canary[:20])
        self.run_hook(event)
        with patch.object(memory, 'effective_inbox_config', side_effect=OSError('/private/' + canary)):
            self.case.memory_main(self.event)
        raw = self.trace.read_bytes()
        for private in (canary.encode(), str(self.root).encode(), b'/private/', b'token'):
            self.assertNotIn(private, raw)
        good, torn = records(self.trace)
        self.assertEqual(torn, 0)
        for record in good:
            if record['kind'] == 'exit':
                self.assertIn(record['outcome'], memory.TRACE_OUTCOMES)
                self.assertTrue(0 <= record['duration_ms'] <= 3600000)

    # T13 descriptors are never inherited by the CLI subprocess
    def test_trace_descriptors_not_inherited(self):
        self.enable()
        real = self.root / 'real-cairn'
        self.case.cli.rename(real)
        fds = self.root / 'fds.txt'
        self.case.cli.write_text('#!/bin/sh\nls -l /proc/$$/fd/ >> ' + str(fds) + '\nexec ' + str(real) + ' "$@"\n')
        self.case.cli.chmod(0o700)
        self.run_hook()
        listing = fds.read_text()
        self.assertNotIn('invocation-trace', listing)

    # T14 kill: missing exit is unknown; fixture-owned cleanup is separate
    def test_killed_invocation_leaves_entry_only(self):
        self.enable()
        real = self.root / 'real-cairn'
        self.case.cli.rename(real)
        self.case.cli.write_text('#!/bin/sh\nsleep 30\n')
        self.case.cli.chmod(0o700)
        proc = subprocess.Popen([sys.executable, str(self.root / 'memory.py'), '--config', str(self.root / 'memory.json')],
                                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        proc.stdin.write(json.dumps(self.event).encode()); proc.stdin.close()
        deadline = time.monotonic() + 10
        while not records(self.trace)[0] and time.monotonic() < deadline:
            time.sleep(0.05)
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)
        # Fixture hygiene: the owned group is gone (verified separately from trace evidence).
        with self.assertRaises(ProcessLookupError):
            os.killpg(proc.pid, 0)
        good, _ = records(self.trace)
        self.assertEqual([r['kind'] for r in good], ['entry'])
        # Reader contract: a missing exit is unknown, never "died".
        verdict = 'unknown' if not any(r['kind'] == 'exit' for r in good) else 'recorded'
        self.assertEqual(verdict, 'unknown')


if __name__ == '__main__':
    unittest.main()
