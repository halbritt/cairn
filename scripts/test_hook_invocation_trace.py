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

ORDINARY = 'Repair the fixture validation while retaining the current scope.'
EVENTS = {'SessionStart', 'UserPromptSubmit', 'PreCompact', 'SessionEnd', 'PostToolUse', 'PostToolUseFailure', 'Stop', 'other'}
OUTCOMES = {'engine_completed', 'early_return:disabled_env', 'early_return:no_memory_marker',
            'early_return:wake_coordinated', 'early_return:stop_not_boundary', 'early_return:unsupported_event',
            'early_return:stop_too_little_dialogue', 'lock_busy_skip', 'required_refused', 'error', 'unexpected',
            'unlabelled_return'}


def _uuid(v):
    try:
        return isinstance(v, str) and str(uuid.UUID(v)) == v and v != str(uuid.UUID(int=0))
    except ValueError:
        return False


def _int(lo, hi):
    return lambda v: type(v) is int and lo <= v <= hi


def _enum(values):
    return lambda v: isinstance(v, str) and v in values


IDENTITY = dict(event=_enum(EVENTS), session=_uuid, turn=_uuid)
FIELDS = {
    'entry': dict(inv=lambda v: isinstance(v, str) and len(v) == 32 and all(c in '0123456789abcdef' for c in v),
                  at=_int(0, 2 ** 53 - 1), pid=_int(1, 4194304), ppid=_int(1, 4194304)),
    'parsed': dict(inv=None, at=None, **IDENTITY),
    'exit': dict(inv=None, at=None, duration_ms=_int(0, 3600000), main_return_code=lambda v: type(v) is int and v in (0, 1, 2),
                 phase=_enum({'configuration', 'input', 'activation', 'handle'}),
                 stage=_enum({'activation', 'validated', 'bridge', 'lock_attempt', 'locked', 'recall', 'capture', 'saved'}),
                 outcome=_enum(OUTCOMES), cause=_enum({'lock_busy', 'blocking_io'}),
                 error_class=_enum({'RequiredContextRefused', 'HookError', 'BlockingIOError', 'OSError', 'ValueError',
                                    'KeyError', 'TypeError'}),
                 context_bytes=_int(0, 1048576), bound=lambda v: type(v) is bool,
                 entry_recorded=lambda v: type(v) is bool, **IDENTITY)}
for kind in ('parsed', 'exit'):
    FIELDS[kind].update(inv=FIELDS['entry']['inv'], at=FIELDS['entry']['at'])
REQUIRED = {'entry': {'inv', 'at', 'pid', 'ppid'}, 'parsed': {'inv', 'at'},
            'exit': {'inv', 'at', 'duration_ms', 'outcome', 'entry_recorded'}}


def _no_duplicates(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError('duplicate key')
    return dict(pairs)


def _reject_constant(name):
    raise ValueError('non-finite constant')


def parse_line(line):
    """Test-only strict reader for one newline-free line: a record dict, or None (damage)."""
    if len(line) + 1 > 512:
        return None
    try:
        value = json.loads(line, object_pairs_hook=_no_duplicates, parse_constant=_reject_constant)
    except ValueError:
        return None
    if not isinstance(value, dict) or value.get('schema') != 'cairn.hook-invocation/1':
        return None
    kind = value.get('kind')
    if kind not in FIELDS:
        return None
    fields = {k: v for k, v in value.items() if k not in ('schema', 'kind')}
    if not REQUIRED[kind] <= set(fields) or not set(fields) <= set(FIELDS[kind]):
        return None
    if not all(FIELDS[kind][k](v) for k, v in fields.items()):
        return None
    return value


def records(path):
    """Complete, schema-valid lines are records; incomplete or invalid lines are damage."""
    good, torn = [], 0
    raw = path.read_bytes() if path.exists() else b''
    lines = raw.split(b'\n')
    for n, line in enumerate(lines):
        if n == len(lines) - 1:
            torn += bool(line)  # no terminating newline
            continue
        if not line:
            continue
        value = parse_line(line)
        if value is None:
            torn += 1
        else:
            good.append(value)
    return good, torn


class TraceFixture(unittest.TestCase):
    """Shared helpers only; no tests here, so nothing is collected twice."""
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
        self.assertTrue(all(r['inv'] == entries[0]['inv'] for r in good))
        return entries[0], exits[0]

    def kinds(self):
        return [r['kind'] for r in records(self.trace)[0]]

    def stable_state(self):
        path = self.root / 'memory' / (self.event['session_id'] + '.json')
        state = json.loads(path.read_text()) if path.exists() else {}
        last = state.get('last_recall', {})
        return {k: last.get(k) for k in ('outcome', 'bytes', 'error_type', 'event')}


class InvocationTraceTests(TraceFixture):
    # T1 actual two-record entry/exit through the real file path
    def test_normal_prompt_writes_entry_and_exit(self):
        self.enable()
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        entry, exit_ = self.pair()
        self.assertEqual(entry['pid'] > 0 and entry['ppid'] > 0, True)
        self.assertEqual(self.kinds(), ['entry', 'parsed', 'exit'])
        parsed = [r for r in records(self.trace)[0] if r['kind'] == 'parsed'][0]
        self.assertEqual((parsed['event'], parsed['session'], parsed['turn']),
                         ('UserPromptSubmit', self.event['session_id'], self.event['turn_id']))
        self.assertEqual(exit_['outcome'], 'engine_completed')
        self.assertEqual(exit_['main_return_code'], 0)
        self.assertNotIn('cause', exit_)
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
                    self.assertEqual((result.returncode, exit_['outcome'], exit_['main_return_code']), (1, 'error', 1))
                    self.assertEqual(exit_['error_class'], 'HookError')
                else:
                    self.assertEqual((result.returncode, result.stdout, exit_['outcome']), (0, b'{}\n', 'lock_busy_skip'))
                self.assertEqual((exit_['stage'], exit_['cause']), ('lock_attempt', 'lock_busy'))

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
        self.assertEqual([(r['outcome'], r['main_return_code']) for r in exits], [('required_refused', 2)])

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
        self.assertEqual((torn, sorted(r['kind'] for r in good)), (1, ['entry', 'exit', 'parsed']))

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
        self.assertEqual(kinds, ['entry', 'exit', 'parsed'])

    # T10 per-invocation state never leaks across main() calls
    def test_invocation_state_is_reset(self):
        self.enable()
        self.case.memory_main(dict(self.event, hook_event_name='Notification'))
        self.assertIsNone(memory._TRACE.get())
        self.case.memory_main(self.event)
        self.assertIsNone(memory._TRACE.get())
        good, _ = records(self.trace)
        exits = [r for r in good if r['kind'] == 'exit']
        self.assertEqual([r['outcome'] for r in exits], ['early_return:unsupported_event', 'engine_completed'])
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
        braced = '{' + self.event['session_id'] + '}'  # accepted by uuid.UUID, not canonical
        self.run_hook(dict(self.event, session_id=braced, turn_id=canary, transcript_path='/tmp/' + canary,
                           project_path='/nonexistent/' + canary))
        self.run_hook(dict(self.event, hook_event_name=canary))
        with patch.object(memory, 'effective_inbox_config', side_effect=OSError('/private/' + canary)):
            self.case.memory_main(self.event)
        raw = self.trace.read_bytes()
        for private in (canary.encode(), str(self.root).encode(), b'/private/', b'token'):
            self.assertNotIn(private, raw)
        good, torn = records(self.trace)
        self.assertEqual(torn, 0)
        self.assertNotIn(b'{' + self.event['session_id'].encode(), raw)
        self.assertTrue(any(r.get('event') == 'other' for r in good))
        self.assertEqual(set(memory.TRACE_OUTCOMES), OUTCOMES)

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
        self.assertEqual([r['kind'] for r in good], ['entry', 'parsed'])
        # The parsed record attributes the entry-only invocation to its session.
        self.assertEqual(good[1]['session'], self.event['session_id'])
        # Reader contract: a missing exit is unknown, never "died".
        verdict = 'unknown' if not any(r['kind'] == 'exit' for r in good) else 'recorded'
        self.assertEqual(verdict, 'unknown')


if __name__ == '__main__':
    unittest.main()


class InvocationTraceCorrectionTests(TraceFixture):
    """Root disposition corrections: isolation, cause, positive completion, reader."""
    def baseline(self, event):
        self.setUp()
        return self.case.memory_main(event)

    # 1. every diagnostic step is isolated and never masks the real hook
    def test_trace_faults_never_change_the_hook(self):
        expected = self.baseline(self.event)
        class Exploding(dict):
            def update(self, *a, **k):
                raise RuntimeError('note failure')
            def setdefault(self, *a, **k):
                raise RuntimeError('note failure')
        original_init = memory._InvocationTrace.__init__
        def exploding_fields(trace, path):
            original_init(trace, path)
            trace.fields = Exploding()
        faults = {
            'constructor': patch.object(memory._InvocationTrace, '__init__', side_effect=RuntimeError('ctor')),
            'entry': patch.object(memory._InvocationTrace, 'entry', side_effect=RuntimeError('entry')),
            'parsed': patch.object(memory._InvocationTrace, 'parsed', side_effect=RuntimeError('parsed')),
            'notes': patch.object(memory._InvocationTrace, '__init__', exploding_fields),
            'finish': patch.object(memory._InvocationTrace, 'finish', side_effect=RuntimeError('finish')),
            'close': patch.object(memory._InvocationTrace, 'close', side_effect=RuntimeError('close')),
            'uuid': patch.object(memory.uuid, 'uuid4', side_effect=RuntimeError('uuid')),
        }
        for name, fault in faults.items():
            with self.subTest(name):
                self.setUp(); self.enable()
                with fault:
                    observed = self.case.memory_main(self.event)
                self.assertEqual(observed, expected)
                self.assertIsNone(memory._TRACE.get())

    def test_context_metric_fault_is_isolated(self):
        event = dict(self.event, hook_event_name='Notification')
        expected = self.baseline(event)
        self.setUp(); self.enable()
        with patch.object(memory, 'injected_bytes', side_effect=TypeError('metric')):
            observed = self.case.memory_main(event)
        self.assertEqual(observed, expected)
        self.assertEqual(self.pair()[1]['outcome'], 'early_return:unsupported_event')

    def test_real_hook_exception_is_not_swallowed(self):
        for traced in (False, True):
            with self.subTest(traced=traced):
                self.setUp()
                if traced:
                    self.enable()
                with patch.object(memory, 'handle', side_effect=RuntimeError('real hook failure')):
                    with self.assertRaises(RuntimeError):
                        self.case.memory_main(self.event)
                self.assertIsNone(memory._TRACE.get())
                if traced:
                    exit_ = self.pair()[1]
                    self.assertEqual(exit_['outcome'], 'unexpected')
                    self.assertNotIn('main_return_code', exit_)

    # 2. cause kept separately from the final required refusal
    def test_lock_busy_cause_survives_required_refusal(self):
        for name, event, code, stdout_prefix in (
                ('UserPromptSubmit', dict(self.case.event, prompt=ORDINARY), 2, b''),
                ('SessionStart', dict(self.case.event, hook_event_name='SessionStart', source='resume'), 0,
                 b'{"continue":false')):
            with self.subTest(name):
                self.setUp(); self.enable()
                self.case.fixture['search']['selected'] = [dict(mandatory=True, record=dict(body='Keep the fixture instruction.'))]
                self.case.freeze()
                self.assertEqual(self.case.invoke(main=True)['code'], 0)
                directory = self.root / 'memory'; directory.mkdir(mode=0o700, exist_ok=True)
                before = len(records(self.trace)[0])
                with (directory / (self.case.event['session_id'] + '.lock')).open('a') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    result = self.run_hook(event)
                self.assertEqual(result.returncode, code)
                self.assertTrue(result.stdout.startswith(stdout_prefix), result.stdout)
                exit_ = [r for r in records(self.trace)[0][before:] if r['kind'] == 'exit'][0]
                self.assertEqual((exit_['outcome'], exit_['cause'], exit_['stage'], exit_['main_return_code']),
                                 ('required_refused', 'lock_busy', 'lock_attempt', code))

    # 3. engine_completed is positive-only; unlabelled returns never claim it
    def test_unlabelled_return_is_not_completion(self):
        self.enable()
        with patch.object(memory, 'handle', return_value={}):
            observed = self.case.memory_main(self.event)
        self.assertEqual(observed['code'], 0)
        self.assertEqual(self.pair()[1]['outcome'], 'unlabelled_return')

    def test_broken_stdout_does_not_claim_process_success(self):
        self.enable()
        with open('/dev/full', 'wb') as full:
            result = subprocess.run([sys.executable, str(self.root / 'memory.py'), '--config', str(self.root / 'memory.json')],
                                    input=json.dumps(self.event).encode(), stdout=full, stderr=subprocess.PIPE, timeout=20)
        self.setUp()
        with open('/dev/full', 'wb') as full:
            untraced = subprocess.run([sys.executable, str(self.root / 'memory.py'), '--config', str(self.root / 'memory.json')],
                                      input=json.dumps(self.event).encode(), stdout=full, stderr=subprocess.PIPE, timeout=20)
        self.assertEqual(result.returncode, untraced.returncode)
        self.assertNotEqual(result.returncode, 0)  # flush failed at shutdown
        exit_ = [r for r in records(self.trace)[0] if r['kind'] == 'exit'] if self.trace.exists() else []
        # The trace never has a process-exit field; main_return_code is main()'s value only.
        for record in exit_:
            self.assertNotIn('exit_code', record)


class StrictReaderTests(unittest.TestCase):
    VALID = ('{"at":1,"inv":"' + 'a' * 32 + '","kind":"entry","pid":1,"ppid":1,'
             '"schema":"cairn.hook-invocation/1"}')

    def test_valid_line_is_accepted(self):
        self.assertIsNotNone(parse_line(self.VALID.encode()))

    def test_adversarial_lines_are_damage(self):
        bad = {
            'duplicate key': self.VALID[:-1] + ',"pid":2}',
            'nan': self.VALID.replace('"pid":1', '"pid":NaN'),
            'bool as int': self.VALID.replace('"at":1', '"at":true'),
            'pid bound': self.VALID.replace('"pid":1', '"pid":0'),
            'extra key': self.VALID[:-1] + ',"prompt":"x"}',
            'missing key': self.VALID.replace(',"ppid":1', ''),
            'bad kind': self.VALID.replace('"entry"', '"full"'),
            'bad schema': self.VALID.replace('/1"', '/2"'),
            'bad inv': self.VALID.replace('a' * 32, 'A' * 32),
            'bad enum': ('{"at":1,"duration_ms":1,"entry_recorded":true,"inv":"' + 'a' * 32 +
                         '","kind":"exit","outcome":"completed","schema":"cairn.hook-invocation/1"}'),
            'non-canonical session': ('{"at":1,"inv":"' + 'a' * 32 + '","kind":"parsed","schema":"cairn.hook-invocation/1",'
                                      '"session":"{11111111-1111-4111-8111-111111111111}"}'),
            'oversized': self.VALID[:-1] + ',"pad":"' + 'x' * 600 + '"}',
            'not json': '{"at":',
        }
        for name, line in bad.items():
            with self.subTest(name):
                self.assertIsNone(parse_line(line.encode()))

    def test_incomplete_final_line_is_damage(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'trace.jsonl'
            path.write_bytes(self.VALID.encode() + b'\n' + self.VALID.encode())
            good, torn = records(path)
            self.assertEqual((len(good), torn), (1, 1))
