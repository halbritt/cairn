"""Capture/recall concurrency through public hooks, isolated fake CLI only."""
import concurrent.futures
import contextlib
import fcntl
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import test_inbox_recall_bridge as fixture

memory = fixture.memory


class CaptureConcurrencyTests(unittest.TestCase):
    def test_completed_capture_waits_for_recall_commit_without_reselecting(self):
        written, recall_entered = threading.Event(), threading.Event()
        release_recall, commit_busy = threading.Event(), threading.Event()
        original_call, flock, writes = memory.Memory.call, fcntl.flock, []

        def call(instance, operation, *args, **kwargs):
            if operation == 'create':
                writes.append(kwargs['payload'])
                written.set()
                if not recall_entered.wait(5):
                    raise AssertionError('recall did not hold its session lock')
                return dict(record_id='saved-fixture-record')
            if operation == 'search' and threading.current_thread().name.startswith('recall-fixture'):
                recall_entered.set()
                if not release_recall.wait(5):
                    raise AssertionError('test did not release recall')
            return original_call(instance, operation, *args, **kwargs)

        def observe_flock(fd, flags):
            try:
                return flock(fd, flags)
            except BlockingIOError:
                if threading.current_thread().name.startswith('capture-fixture'):
                    commit_busy.set()
                raise

        with patch.object(memory.Memory, 'call', new=call), \
             patch.object(memory.fcntl, 'flock', new=observe_flock), \
             patch.object(memory, 'select_json', return_value=dict(structured_output=self.notes(1))) as selector, \
             concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='capture-fixture') as captures, \
             concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='recall-fixture') as recalls:
            capture_future = captures.submit(memory.handle, self.f.mc, self.stop)
            try:
                self.assertTrue(written.wait(5), 'capture did not save its note')
                recall_future = recalls.submit(memory.handle, self.f.mc, self.prompt)
                self.assertTrue(commit_busy.wait(5), 'test missed real commit contention')
            finally:
                release_recall.set()
            self.assertIn('additionalContext', recall_future.result(timeout=5)['hookSpecificOutput'])
            grant = self.path.with_name(fixture.SESSION + '.memory-budget.json').read_bytes()
            capture_future.result(timeout=5)
            state = json.loads(self.path.read_text())
            self.assertEqual(state['codex_capture_marker']['offset'], self.transcript.stat().st_size)
            self.assertIn('last_recall', state)
            self.assertEqual(self.path.with_name(fixture.SESSION + '.memory-budget.json').read_bytes(), grant)
            memory.handle(self.f.mc, self.stop)
            self.assertEqual(selector.call_count, 1)
            self.assertEqual(len(writes), 1, 'same dialogue caused another write')

    def setUp(self):
        self.f = fixture.InboxRecallBridgeTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.transcript = self.f.root / 'synthetic-capture.jsonl'
        self.transcript.write_text(''.join(json.dumps(dict(type='response_item', payload=dict(
            type='message', role=role, content=[dict(type='input_text' if role == 'user' else 'output_text',
                                                   text=f'Fixture migration decision {n}.')]
        ))) + '\n' for n in range(3) for role in ('user', 'assistant')))
        self.stop = dict(self.f.event, hook_event_name='Stop', transcript_path=str(self.transcript))
        self.prompt = dict(self.f.event, prompt='Inspect fixture migration guidance.')
        self.path = Path(self.f.mc['state_dir']) / (fixture.SESSION + '.json')

    @contextlib.contextmanager
    def selecting(self, selected=None):
        entered, release = threading.Event(), threading.Event()
        self.selector_calls = 0

        def select(*args, **kwargs):
            self.selector_calls += 1
            entered.set()
            if not release.wait(5):
                raise AssertionError('fixture did not release capture')
            return dict(structured_output=selected or dict(checkpoint=None, workstream=None, memories=[]))

        with patch.object(memory, 'select_json', side_effect=select), \
             concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(memory.handle, self.f.mc, self.stop)
            try:
                self.assertTrue(entered.wait(5), 'capture did not reach selector')
                yield future
            finally:
                release.set()
        # Caller checks the future after the worker is joined, including errors.

    def update_as_legacy_writer(self, *, drop=(), **fields):
        """A prior engine owns only the real session lock; it cannot know the new capture lock."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state = json.loads(self.path.read_text()) if self.path.exists() else {}
            state.update(fields)
            for key in drop:
                state.pop(key, None)
            memory.save_state(self.path, state)
        return self.path.read_bytes()

    def test_second_stop_and_compaction_do_not_duplicate_capture(self):
        with self.selecting() as future:
            before = self.f.calls()
            for event_name in ('Stop', 'PreCompact', 'SessionEnd'):
                with self.subTest(event=event_name), self.assertRaisesRegex(memory.HookError, 'capture.*already running'):
                    memory.handle(self.f.mc, dict(self.stop, hook_event_name=event_name))
            self.assertEqual(self.f.calls(), before, 'duplicate capture dispatched an API call')
            self.assertEqual(self.selector_calls, 1)
        self.assertEqual(future.result(), {})

    def test_transcript_append_during_selection_remains_for_next_stop(self):
        original_size = self.transcript.stat().st_size
        with self.selecting() as future:
            appended = ''.join(json.dumps(dict(type='response_item', payload=dict(
                type='message', role=role, content=[dict(
                    type='input_text' if role == 'user' else 'output_text',
                    text=f'Appended migration follow-up {role}.')]
            ))) + '\n' for _ in range(3) for role in ('user', 'assistant'))
            with self.transcript.open('a') as transcript:
                transcript.write(appended)
        self.assertEqual(future.result(), {})

        state = json.loads(self.path.read_text())
        self.assertEqual(state['codex_capture_marker']['offset'], original_size)
        self.assertEqual(state['captured_messages'], 6)

        with patch.object(memory, 'select_json', return_value=dict(structured_output=dict(
                checkpoint=None, workstream=None, memories=[]))) as selector:
            memory.handle(self.f.mc, self.stop)
        self.assertEqual(selector.call_count, 1, 'the appended turn was skipped after capture')
        state = json.loads(self.path.read_text())
        self.assertEqual(state['codex_capture_marker']['offset'], self.transcript.stat().st_size)
        self.assertEqual(state['captured_messages'], 12)

    def test_new_binding_refuses_stale_capture_commit(self):
        with self.selecting() as future:
            result = memory.handle(self.f.mc, dict(self.prompt, workstream='New task'))
            self.assertIn('remaining_memory_bytes', result['hookSpecificOutput']['additionalContext'])
            current = self.path.read_bytes()
            ledger = self.path.with_name(fixture.SESSION + '.memory-budget.json')
            grant = ledger.read_bytes()
        with self.assertRaisesRegex(memory.HookError, 'state changed before commit'):
            future.result()
        self.assertEqual(self.path.read_bytes(), current)
        self.assertEqual(ledger.read_bytes(), grant)
        self.assertNotIn('codex_capture_marker', json.loads(current))

    def test_changed_capture_field_from_legacy_writer_refuses_commit(self):
        with self.selecting() as future:
            newer = self.update_as_legacy_writer(last_capture=dict(outcome='newer capture', at=1))
        with self.assertRaisesRegex(memory.HookError, 'state changed before commit'):
            future.result()
        self.assertEqual(self.path.read_bytes(), newer)

    def test_capture_compare_distinguishes_absent_fields_from_explicit_null(self):
        for make_present in (True, False):
            with self.subTest(make_present=make_present):
                with self.selecting() as future:
                    newer = (self.update_as_legacy_writer(last_capture=None) if make_present else
                             self.update_as_legacy_writer(drop=('last_capture',)))
                with self.assertRaisesRegex(memory.HookError, 'state changed before commit'):
                    future.result()
                self.assertEqual(self.path.read_bytes(), newer)

    def test_new_workstream_and_hints_survive_capture(self):
        self.update_as_legacy_writer(workstream='Old workstream')
        with self.selecting() as future:
            newer = dict(workstream='New workstream', hints=dict(files={'fixture.py': 1}))
            self.update_as_legacy_writer(**newer)
        self.assertEqual(future.result(), {})
        state = json.loads(self.path.read_text())
        self.assertEqual({k: state[k] for k in newer}, newer)
        self.assertEqual(state['codex_capture_marker']['offset'], self.transcript.stat().st_size)

    def test_capture_can_commit_its_own_new_project_binding_and_workstream(self):
        self.update_as_legacy_writer(workstream='Prior project workstream')
        project = self.f.root / 'next-project'
        project.mkdir()
        self.stop['project_path'] = str(project)
        original_call = memory.Memory.call

        def call(instance, operation, *args, **kwargs):
            if operation == 'create':
                return dict(record_id='saved-handoff')
            return original_call(instance, operation, *args, **kwargs)

        selected = dict(checkpoint='Continue the fixture migration review.', workstream='Migration', memories=[])
        with patch.object(memory.Memory, 'call', new=call), self.selecting(selected) as future:
            pass
        future.result()
        state = json.loads(self.path.read_text())
        self.assertEqual(state['binding'], [str(project), ''])
        self.assertEqual(state['workstream'], memory.workstream_prefix(self.stop) + 'Migration')

    @staticmethod
    def notes(count):
        return dict(checkpoint=None, workstream=None, memories=[
            dict(record_id=None, kind='lesson', title=f'Fixture lesson {n}', body=f'Retain fixture rule {n}.')
            for n in range(count)])

    @contextlib.contextmanager
    def contended_after_write(self, *, on_write=lambda: None):
        original_call, held, writes = memory.Memory.call, [], []

        def call(instance, operation, *args, **kwargs):
            if operation == 'create':
                writes.append(kwargs['payload'])
                lock = self.path.with_suffix('.lock').open('a')
                held.append(lock)
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                on_write()
                return dict(record_id='saved-fixture-record')
            return original_call(instance, operation, *args, **kwargs)

        try:
            with patch.object(memory.Memory, 'call', new=call), \
                 patch.object(memory, 'select_json', return_value=dict(structured_output=self.notes(1))) as selector:
                yield writes, selector
        finally:
            for lock in held:
                lock.close()

    def test_exhausted_commit_wait_preserves_recall_grant_and_partial_write(self):
        memory.handle(self.f.mc, self.prompt)
        before = self.path.read_bytes()
        ledger = self.path.with_name(fixture.SESSION + '.memory-budget.json')
        grant = ledger.read_bytes()
        clock, sleeps = [100.0], []
        committing, real_sleep = threading.Event(), memory.time.sleep

        def sleep(seconds):
            if not committing.is_set():
                return real_sleep(seconds)
            sleeps.append(seconds)
            clock[0] += seconds

        with self.contended_after_write(on_write=committing.set) as (writes, selector), \
             patch.object(memory.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(memory.time, 'sleep', side_effect=sleep):
            with self.assertRaisesRegex(memory.HookError, 'selected notes may already be saved'):
                memory.handle(self.f.mc, self.stop)
        self.assertAlmostEqual(sum(sleeps), 5.0)
        self.assertTrue(all(0 < delay <= 0.05 for delay in sleeps))
        self.assertEqual(selector.call_count, 1)
        self.assertEqual(len(writes), 1)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(ledger.read_bytes(), grant)
        self.assertNotIn('codex_capture_marker', json.loads(before))

    def test_commit_wait_respects_original_capture_deadline_and_short_events(self):
        cases = [('codex', 'Stop', 144.8, 0.2), ('codex', 'PreCompact', 145.1, 0.0),
                 ('codex', 'SessionEnd', 0.0, 0.0), ('unknown', 'PreCompact', 0.0, 0.0),
                 ('claude', 'PreCompact', 144.8, 0.2), ('claude', 'SessionEnd', 144.8, 0.2),
                 ('hermes', 'SessionEnd', 144.8, 0.2), ('opencode', 'PreCompact', 144.8, 0.2)]
        for harness, event_name, elapsed, expected in cases:
            with self.subTest(harness=harness, event=event_name, elapsed=elapsed):
                clock, sleeps = [100.0], []
                committing, real_sleep = threading.Event(), memory.time.sleep

                def sleep(seconds):
                    if not committing.is_set():
                        return real_sleep(seconds)
                    sleeps.append(seconds)
                    clock[0] += seconds

                def after_write():
                    clock[0] = 100.0 + elapsed
                    committing.set()

                event = dict(self.stop, hook_event_name=event_name)
                if harness == 'opencode':
                    event['session_id'] = 'ses_fixture'
                # Use explicit synthetic dialogue for non-Codex adapters.
                if harness != 'codex':
                    event['messages'] = [dict(role='user', text='Retain the fixture migration rule.'),
                                         dict(role='assistant', text='The fixture rule is retained.')]
                config = dict(self.f.mc, harness=harness)
                original_path = self.path
                self.path = Path(config['state_dir']) / (event['session_id'] + '.json')
                if harness == 'hermes':
                    self.path = self.path.with_name(memory.hashlib.sha256(event['session_id'].encode()).hexdigest() + '.json')
                try:
                    with self.contended_after_write(on_write=after_write) as (writes, selector), \
                         patch.object(memory.time, 'monotonic', side_effect=lambda: clock[0]), \
                         patch.object(memory.time, 'sleep', side_effect=sleep):
                        with self.assertRaisesRegex(memory.HookError, 'could not commit state'):
                            memory.handle(config, event)
                    self.assertAlmostEqual(sum(sleeps), expected)
                    self.assertEqual((len(writes), selector.call_count), (1, 1))
                finally:
                    self.path = original_path

    def test_interrupted_commit_wait_releases_capture_lock_without_advancing_marker(self):
        memory.handle(self.f.mc, self.prompt)
        before = self.path.read_bytes()
        committing, real_sleep = threading.Event(), memory.time.sleep

        def interrupt_commit(seconds):
            if committing.is_set():
                raise KeyboardInterrupt('fixture interrupt')
            return real_sleep(seconds)

        with self.contended_after_write(on_write=committing.set) as (writes, selector), \
             patch.object(memory.time, 'sleep', side_effect=interrupt_commit):
            with self.assertRaises(KeyboardInterrupt):
                memory.handle(self.f.mc, self.stop)
            # The competing writer still holds .lock; only capture ownership must be released here.
            with self.path.with_suffix('.capture.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.path.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.assertEqual((len(writes), selector.call_count), (1, 1))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertNotIn('codex_capture_marker', json.loads(before))

    def test_partial_note_write_does_not_advance_capture_or_rollback_recall(self):
        original_call, writes = memory.Memory.call, []

        def call(instance, operation, *args, **kwargs):
            if operation == 'create':
                writes.append(kwargs['payload'])
                if len(writes) == 2:
                    raise memory.HookError('fixture second write refused')
                return dict(record_id='saved-fixture-record')
            return original_call(instance, operation, *args, **kwargs)

        with patch.object(memory.Memory, 'call', new=call):
            with self.selecting(self.notes(2)) as future:
                memory.handle(self.f.mc, self.prompt)
                state = self.path.read_bytes()
                ledger = self.path.with_name(fixture.SESSION + '.memory-budget.json')
                grant = ledger.read_bytes()
        with self.assertRaisesRegex(memory.HookError, 'second write refused'):
            future.result()
        self.assertEqual(len(writes), 2, 'capture internally retried a partially completed write set')
        self.assertNotEqual(writes[0]['request_id'], writes[1]['request_id'])
        self.assertEqual(self.path.read_bytes(), state)
        self.assertEqual(ledger.read_bytes(), grant)
        self.assertNotIn('codex_capture_marker', json.loads(state))

    def test_commit_replace_failure_preserves_recall_after_confirmed_note(self):
        original_call, replace, writes = memory.Memory.call, Path.replace, []

        def call(instance, operation, *args, **kwargs):
            if operation == 'create':
                writes.append(kwargs['payload'])
                return dict(record_id='saved-fixture-record')
            return original_call(instance, operation, *args, **kwargs)

        def fail_capture_replace(source, destination):
            if destination == self.path and threading.current_thread() is not threading.main_thread():
                raise OSError('fixture state replace failed')
            return replace(source, destination)

        with patch.object(memory.Memory, 'call', new=call), patch.object(Path, 'replace', new=fail_capture_replace):
            with self.selecting(self.notes(1)) as future:
                memory.handle(self.f.mc, self.prompt)
                state = self.path.read_bytes()
                ledger = self.path.with_name(fixture.SESSION + '.memory-budget.json')
                grant = ledger.read_bytes()
        with self.assertRaisesRegex(OSError, 'state replace failed'):
            future.result()
        self.assertEqual(len(writes), 1, 'confirmed external write was internally retried')
        self.assertEqual(self.path.read_bytes(), state)
        self.assertEqual(ledger.read_bytes(), grant)
        self.assertNotIn('codex_capture_marker', json.loads(state))


if __name__ == '__main__':
    unittest.main()
