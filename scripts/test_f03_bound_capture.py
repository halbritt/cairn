"""Bound capture through the actual checked Source and Go process boundaries."""
import fcntl
import copy
import json
from pathlib import Path
import unittest
import uuid

from test_f02_bound_memory import BoundMemoryCase
from test_f04_activation import ROOT, SESSION, encoded, write
import test_f03_capture_boundary as capture_fixture


class BoundCaptureTests(BoundMemoryCase):
    def setUp(self):
        super().setUp()
        self.f.memory['capture_observations'] = True
        self.f.publish_configuration()
        self.event('PreCompact')

    def session_key(self):
        return self.f.event['session_id']

    def before_run(self, side):
        super().before_run(side)
        capture_fixture.CaptureBoundaryTests.start_scheduler(self, side)

    def after_run(self):
        capture_fixture.CaptureBoundaryTests.finish_scheduler(self)

    def scheduled(self):
        for side in ('source', 'go'):
            setattr(self, side + '_prefix', ('/usr/bin/strace', '-f', '-qq', '-o', str(self.root / (side + '-schedule.log')),
                    '-e', 'trace=flock', '-e', 'inject=flock:delay_enter=200ms'))

    def rollout(self, count=0):
        path = self.root / 'rollout.jsonl'
        rows = []
        for i in range(count):
            role = 'user' if i % 2 == 0 else 'assistant'
            rows.append(dict(type='response_item', payload=dict(type='message', role=role,
                        content=[dict(type='input_text' if role == 'user' else 'output_text',
                                      text='thanks' if role == 'user' else 'no problem')])))
        write(path, b''.join(encoded(row) + b'\n' for row in rows))
        return path

    def test_missing_transcript_commits_pending_without_native_activation(self):
        result = self.pair()
        self.assertEqual(result.returncode, 0)
        self.assertIn(b'Cairn capture is pending', result.stdout)
        state = self.state()
        self.assertEqual(set(state), {SESSION + '.json'})
        self.assertEqual(state[SESSION + '.json']['last_capture']['outcome'], 'transcript_unavailable')
        self.assertEqual([row[0] for row in self.calls], ['capture-observation'])

    def test_empty_courtesy_and_duplicate_reuse_existing_capture_body(self):
        for count, outcome in ((0, 'empty'), (6, 'courtesy')):
            with self.subTest(messages=count):
                self.reset_state({})
                self.event('PreCompact', transcript_path=str(self.rollout(count)))
                self.assertEqual(self.pair().returncode, 0)
                self.assertEqual(self.calls[-1][1]['outcome'], outcome)
                if count:
                    self.assertEqual(self.pair().returncode, 0)
                    self.assertEqual(self.calls[-1][1]['outcome'], 'unchanged')

    def test_short_and_continuation_stop_preserve_source_early_returns(self):
        path = self.rollout(2)
        self.event('Stop', transcript_path=str(path))
        self.assertEqual(self.pair().stdout, b'{}\n')
        self.assertEqual(self.calls[-1][1]['outcome'], 'too_little_dialogue')
        self.reset_state({})
        self.event('Stop', transcript_path=str(path), stop_hook_active=True)
        self.assertEqual(self.pair().stdout, b'{}\n')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.state(), {})

    def test_capture_exclusion_does_not_read_missing_manifest(self):
        # F04 admission excludes capture before binding reads. This is a Source
        # boundary, rather than permission to reinterpret an enabled generation.
        parked = self.f.binding.with_name('unavailable-binding.json')
        self.f.binding.rename(parked)
        try:
            result = self.pair()
            self.assertEqual(result.returncode, 0)
            self.assertEqual([row[0] for row in self.calls], ['capture-observation'])
            self.assertNotIn(SESSION + '.memory-budget.json', self.state())
        finally:
            parked.rename(self.f.binding)

    def test_capture_lock_refuses_without_committing_a_status(self):
        directory = Path(self.f.memory['state_dir'])
        directory.mkdir(mode=0o700)
        lock = directory / (SESSION + '.capture.lock')
        for side in (self.source, self.go):
            with lock.open('a') as stream:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = side()
                self.assertEqual((result.returncode, result.stdout), (1, b''))
                self.assertIn(b'a capture is already running for this session', result.stderr)
                self.assertEqual(self.state(), {})
        self.assertEqual(self.report_locks, [{'.lock': True, '.capture.lock': False}] * 2)

    def test_claude_capture_rows_keep_source_eligibility_and_no_activation(self):
        for config in (self.f.memory, self.f.coord):
            config['harness'] = 'claude'
        self.f.publish_configuration()
        for name in ('PreCompact', 'SessionEnd'):
            with self.subTest(event=name):
                self.reset_state({})
                self.event(name, messages=[])
                self.assertEqual(self.pair().stdout, b'{}\n')
                self.assertEqual(set(self.state()), {SESSION + '.json'})
                self.assertEqual(self.calls[-1][1]['outcome'], 'empty')

    def test_changed_capture_fields_and_binding_refuse_cas_before_marker(self):
        self.scheduled()
        directory = Path(self.f.memory['state_dir'])
        directory.mkdir(mode=0o700)
        for changes in (dict(capture_pending=None), dict(binding=['/different', 'task'])):
            with self.subTest(changes=changes):
                state_path = directory / (SESSION + '.json')
                write(state_path, encoded({}))
                def change(path):
                    with path.with_suffix('.lock').open('a') as lock:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        state = json.loads(path.read_bytes())
                        state.update(changes)
                        write(path, encoded(state))
                self.before_commit = change
                result = self.pair()
                self.assertEqual((result.returncode, result.stdout), (1, b''))
                self.assertIn(b'capture state changed before commit', result.stderr)
                self.assertEqual(self.state()[state_path.name], changes)
                self.assertEqual(self.calls[-1][1]['outcome'], 'state_commit_error')

    def test_concurrent_recall_and_hints_survive_bound_capture_commit(self):
        self.scheduled()
        directory = Path(self.f.memory['state_dir'])
        directory.mkdir(mode=0o700)
        write(directory / (SESSION + '.json'), encoded({}))
        def change(path):
            with path.with_suffix('.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                state = json.loads(path.read_bytes())
                state.update(hints=dict(files={'current.go': 123}), last_recall=dict(outcome='current'))
                write(path, encoded(state))
        self.before_commit = change
        self.assertEqual(self.pair().returncode, 0)
        state = self.state()[SESSION + '.json']
        self.assertEqual(state['hints'], dict(files={'current.go': 123}))
        self.assertEqual(state['last_recall'], dict(outcome='current'))

    def test_failed_replace_cannot_advance_marker_and_next_capture_can_retry(self):
        self.event('PreCompact', transcript_path=str(self.rollout(6)))
        for side in ('source', 'go'):
            setattr(self, side + '_prefix', ('/usr/bin/strace', '-f', '-qq', '-o', str(self.root / (side + '-replace.log')),
                    '-e', 'trace=rename,renameat,renameat2', '-e', 'inject=rename,renameat,renameat2:error=EACCES:when=1'))
        result = self.pair()
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.assertEqual(self.calls[-1][1]['outcome'], 'state_commit_error')
        self.assertEqual(self.state(), {})
        for side in ('source', 'go'):
            self.assertIn('EACCES', (self.root / (side + '-replace.log')).read_text())
            setattr(self, side + '_prefix', ())
        self.assertEqual(self.pair().returncode, 0)
        self.assertEqual(self.calls[-1][1]['outcome'], 'courtesy')
        self.assertIn('codex_capture_marker', self.state()[SESSION + '.json'])

    def test_incorrect_capture_budget_refuses_before_body_or_reports(self):
        for side in (self.source, self.go):
            result = side(timeout=13)
            self.assertEqual((result.returncode, result.stdout), (1, b''))
            self.assertIn(b'CompatibilityPreflightError', result.stderr)
            self.assertEqual(self.calls, [])
            self.assertEqual(self.state(), {})


class BoundProviderCaptureTests(BoundMemoryCase):
    source_pairs = 0

    @classmethod
    def setUpClass(cls):
        if not (ROOT / 'internal/lifecycle/memoryhook/capture_provider.go').exists():
            raise unittest.SkipTest('selected provider construction input is not present; fixtures prepared only')
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        try:
            print('BOUND_PROVIDER_PAIRS=' + str(cls.source_pairs), flush=True)
        finally:
            super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.selection = dict(checkpoint=None, workstream=None, memories=[])
        self.fail_write = None
        self.after_write = None
        self.spec = {}
        self.provider_sides = {}
        script = b'''#!/usr/bin/python3
import json,os,pathlib,sys,time
root=pathlib.Path(__file__).parent
body=sys.stdin.buffer.read()
spec=json.loads((root/'provider.json').read_text())
with (root/'provider.calls').open('a') as log:
 log.write(json.dumps(dict(argv=sys.argv[1:],body=json.loads(body),cwd=os.getcwd(),entries=os.listdir(),pid=os.getpid(),child=os.environ.get('CAIRN_LIFECYCLE_CHILD'),claudecode=os.environ.get('CLAUDECODE')))+'\\n')
if spec.get('delay'):time.sleep(spec['delay'])
if spec.get('stdout') is not None:sys.stdout.write(spec['stdout'])
else:sys.stdout.write(json.dumps(spec['result'],ensure_ascii=False,separators=(',',':')))
sys.exit(spec.get('exit',0))
'''
        write(self.root / 'selector', script, 0o700)
        self.f.memory.update(claude=str(self.root / 'selector'), model='bound-synthetic-model', capture_observations=True)
        self.f.publish_configuration()
        original_env = self.f.env
        self.f.env = lambda: dict(original_env(), CLAUDECODE='synthetic-parent')
        transcript = self.root / 'substantive-rollout.jsonl'
        messages = [('user', 'Preserve the postgres retention decision for this project.'),
                    ('assistant', 'Keep the accepted retention policy and update the operator guide.')]
        write(transcript, b''.join(encoded(dict(type='response_item', payload=dict(type='message', role=role,
              content=[dict(type='input_text' if role == 'user' else 'output_text', text=text)]))) + b'\n'
              for role, text in messages))
        self.event('PreCompact', transcript_path=str(transcript))

    def create_reply(self, body):
        self.write_count += 1
        failure = 'CONFLICT' if self.write_count == self.fail_write else None
        identity = str(uuid.uuid5(uuid.NAMESPACE_URL, body['request_id']))
        if not failure:
            self.writes.append(dict(record_id=identity, draft=copy.deepcopy(body['draft'])))
            if self.after_write:
                self.after_write()
        return dict(record_id=identity), failure

    def before_run(self, side):
        super().before_run(side)
        self.side = side
        self.write_count = 0
        self.writes = []
        (self.root / 'provider.calls').unlink(missing_ok=True)
        spec = dict(result=dict(structured_output=self.selection))
        spec.update(self.spec)
        write(self.root / 'provider.json', encoded(spec))

    def after_run(self):
        path = self.root / 'provider.calls'
        calls = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        for call in calls:
            self.assertFalse(Path('/proc', str(call.pop('pid'))).exists())
            self.assertEqual(call.pop('entries'), [])
            directory = Path(call.pop('cwd'))
            self.assertTrue(directory.name.startswith('cairn-selection-'))
            self.assertFalse(directory.exists())
            self.assertEqual(call['child'], '1')
            self.assertIsNone(call['claudecode'])
        self.provider_sides[self.side] = (calls, copy.deepcopy(self.writes))

    def pair(self):
        result = super().pair()
        self.assertEqual(self.provider_sides['go'], self.provider_sides['source'])
        self.assertEqual(len(self.provider_sides['go'][0]), 1)
        type(self).source_pairs += 1
        return result

    def note(self, title='Retention policy'):
        return dict(record_id=None, kind='decision', title=title, body='Use thirty days for postgres retention.')

    def test_checked_null_selection_commits_digest_after_real_selector(self):
        self.assertEqual(self.pair().stdout, b'{}\n')
        state = self.state()[SESSION + '.json']
        self.assertEqual(state['last_capture'], dict(outcome='nothing_selected', records=[], selector_calls=1))
        self.assertIn('captured_digest', state)
        self.assertEqual([row[0] for row in self.calls if row[0] != 'version'], ['index'] * 3 + ['capture-observation'])

    def test_checked_ordered_note_then_checkpoint_save(self):
        self.selection.update(checkpoint='Retain the postgres policy; next update the operator docs.',
                              workstream='postgres retention', memories=[self.note()])
        self.assertEqual(self.pair().returncode, 0)
        self.assertEqual([row['draft']['kind'] for row in self.writes], ['decision', 'note'])
        state = self.state()[SESSION + '.json']
        self.assertEqual(state['last_capture']['outcome'], 'saved')
        self.assertEqual(state['last_capture']['records'], [row['record_id'] for row in self.writes])
        self.assertIn('codex_capture_marker', state)

    def test_checked_invalid_late_selection_prevents_all_writes(self):
        self.selection['memories'] = [self.note(), dict(record_id=None, kind='unsupported', title='bad', body='bad')]
        self.assertEqual(self.pair().returncode, 1)
        self.assertEqual(self.writes, [])
        self.assertEqual(self.state(), {})

    def test_checked_partial_write_retains_confirmed_note_without_state_commit(self):
        self.selection['memories'] = [self.note(), self.note('Cleanup policy')]
        self.fail_write = 2
        self.assertEqual(self.pair().returncode, 1)
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.state(), {})
        self.assertEqual(self.calls[-1][1]['outcome'], 'write_error')

    def test_checked_cas_failure_keeps_confirmed_note_and_concurrent_state(self):
        self.selection['memories'] = [self.note()]
        def change():
            path = Path(self.f.memory['state_dir']) / (SESSION + '.json')
            with path.with_suffix('.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                write(path, encoded(dict(binding=['/changed', 'owner'])))
        self.after_write = change
        self.assertEqual(self.pair().returncode, 1)
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.state()[SESSION + '.json'], dict(binding=['/changed', 'owner']))
        self.assertEqual(self.calls[-1][1]['outcome'], 'state_commit_error')

    def test_checked_selector_invalid_json_is_a_refusal_without_commit(self):
        self.spec['stdout'] = 'invalid json'
        self.assertEqual(self.pair().returncode, 1)
        self.assertEqual(self.writes, [])
        self.assertEqual(self.state(), {})
        self.assertEqual(self.calls[-1][1]['selector_outcome'], 'error')

    def deadline_process(self, source):
        import subprocess
        import threading
        import time
        command = [str(self.f.launcher('memory', 150) if source else self.go_launcher())]
        started = time.monotonic()
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, cwd=self.f.root, env=self.f.env())
        timer = threading.Timer(130, child.stdin.close)
        try:
            child.stdin.write(self.f.raw)
            child.stdin.flush()
            timer.start()
            child.wait(timeout=165)
            elapsed = time.monotonic() - started
            self.assertGreaterEqual(elapsed, 148)
            self.assertLess(elapsed, 162)
            return subprocess.CompletedProcess(command, child.returncode, child.stdout.read(), child.stderr.read())
        finally:
            timer.cancel()
            if child.poll() is None:
                child.kill()
                child.wait(timeout=3)
            for stream in (child.stdin, child.stdout, child.stderr):
                stream.close()

    def source(self, *, timeout=None):
        if getattr(self, 'deadline_fixture', False):
            return self.deadline_process(True)
        return super().source(timeout=timeout)

    def go(self, *, timeout=None, prefix=()):
        if getattr(self, 'deadline_fixture', False):
            return self.deadline_process(False)
        return super().go(timeout=timeout, prefix=prefix)

    def test_checked_original_150s_deadline_includes_stdin_retrieval_and_selector(self):
        self.deadline_fixture = True
        self.spec['delay'] = 35
        self.selection['memories'] = [self.note()]
        result = self.pair()
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.assertEqual(self.writes, [])
        self.assertEqual(self.state(), {})
        self.assertFalse(any(row[0] in ('create', 'revise') for row in self.calls))


if __name__ == '__main__':
    unittest.main()
