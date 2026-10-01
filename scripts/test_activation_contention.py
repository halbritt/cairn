"""Real file-lock overlap at the activation fsync boundary; no native/API service."""
import fcntl
import json
from pathlib import Path
import select
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

import test_claude_inbox_recall as claude
import test_inbox_recall_bridge as fixture

CHILD = r'''
import importlib.util,json,os,sys
from pathlib import Path
root=Path(sys.argv[1]); first=sys.argv[2]
source=Path(sys.argv[3])/'integrations/lifecycle'/('memory.py' if first=='memory' else 'coordination.py')
spec=importlib.util.spec_from_file_location('first_hook',source)
hook=importlib.util.module_from_spec(spec);spec.loader.exec_module(hook)
config=json.loads((root/(first+'.json')).read_text())
event=json.loads((root/'event.json').read_text())
original=os.fsync
paused=False
def fsync(fd):
 global paused
 if not paused:
  paused=True
  print('locked',flush=True)
  if sys.stdin.buffer.read(1)!=b'x':raise RuntimeError('fixture release missing')
 return original(fd)
os.fsync=fsync
result=hook.effective_inbox_config(config,event)
print(json.dumps({'enabled':bool(result.get('inbox_recall_binding'))}),flush=True)
'''


class ActivationContentionTests(unittest.TestCase):
    def test_same_wake_hooks_share_activation_despite_overlapping_fsync(self):
        for first in ('memory', 'coordination'):
            with self.subTest(first=first):
                case = claude.ClaudeInboxRecallTests()
                case.setUp()
                self.addCleanup(case.doCleanups)
                (case.root / 'event.json').write_text(json.dumps(case.event))
                child = subprocess.Popen([sys.executable, '-c', CHILD, str(case.root), first, str(fixture.ROOT)],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                def cleanup(child=child):
                    if child.poll() is None:
                        child.kill()
                    child.communicate()
                self.addCleanup(cleanup)
                self.assertTrue(select.select([child.stdout], [], [], 5)[0], 'first activation did not reach fsync')
                self.assertEqual(child.stdout.readline(), b'locked\n')
                original = fcntl.flock
                timer = None
                blocked = 0
                def release():
                    child.stdin.write(b'x')
                    child.stdin.flush()
                def flock(*args):
                    nonlocal timer, blocked
                    try:
                        return original(*args)
                    except BlockingIOError:
                        blocked += 1
                        if timer is None:
                            timer = threading.Timer(0.03, release)
                            timer.start()
                        raise
                with patch.object(fcntl, 'flock', side_effect=flock):
                    out = case.invoke(main=True) if first == 'memory' else case.memory_main(case.event)
                if timer is not None:
                    timer.join()
                else:
                    release()
                stdout, stderr = child.communicate(timeout=5)
                self.assertEqual(child.returncode, 0, stderr)
                self.assertEqual(json.loads(stdout), {'enabled': True})
                self.assertGreater(blocked, 0, 'fixture must exercise actual lock contention')
                self.assertEqual(out['code'], 0, out)
                if first == 'coordination':
                    self.assertEqual(case.invoke(main=True)['code'], 0)
                ledger = case.ledger()
                self.assertEqual(ledger['inbox_activation'], {case.native: True})
                self.assertEqual(len(ledger['grants']), 1)
                grant = ledger['grants'][ledger['active']]
                self.assertLessEqual(grant['output_bytes'] + grant['native_allowance_bytes'], 9500)
                calls = case.calls()
                self.assertEqual(case.invoke(main=True)['code'], 2)
                self.assertEqual(case.calls(), calls)
                self.assertEqual(case.ledger(), ledger)

    def test_persistent_contention_refuses_without_reads_or_accounting_changes(self):
        case = claude.ClaudeInboxRecallTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        bridge = fixture.coord.load_inbox_recall(case.cc)
        self.assertTrue(bridge.activation_decision(case.cc, case.event, True))
        directory = case.root / 'memory'
        before = {p.name: p.read_bytes() for p in directory.iterdir()}
        with (directory / (fixture.SESSION + '.lock')).open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            started = time.monotonic()
            out = case.invoke(main=True)
            elapsed = time.monotonic() - started
            self.assertEqual(out['code'], 2, out)
            self.assertIn('inbox activation decision unavailable', out['stderr'])
            self.assertGreaterEqual(elapsed, 0.24)
            self.assertLess(elapsed, 1.5)
            self.assertEqual(case.calls(), [])
            self.assertEqual({p.name: p.read_bytes() for p in directory.iterdir()}, before)
            # Privacy exclusion never waits for or changes an activation ledger.
            (case.root / '.cairn-no-memory').touch()
            out = case.memory_main(case.event)
            self.assertEqual(out['code'], 0, out)
            self.assertEqual(case.calls(), [])
            self.assertEqual({p.name: p.read_bytes() for p in directory.iterdir()}, before)

    def test_non_contention_error_is_not_retried(self):
        case = claude.ClaudeInboxRecallTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        bridge = fixture.coord.load_inbox_recall(case.cc)
        with patch.object(fcntl, 'flock', side_effect=PermissionError('fixture')) as lock, \
             patch.object(bridge.time, 'sleep') as sleep:
            with self.assertRaises(PermissionError):
                bridge.activation_decision(case.cc, case.event, True)
        self.assertEqual(lock.call_count, 1)
        sleep.assert_not_called()
        self.assertFalse((case.root/'memory'/f'{fixture.SESSION}.inbox-recall.json').exists())

    def test_codex_contention_keeps_existing_decision_and_turn_accounting(self):
        case = fixture.InboxRecallBridgeTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        bridge = fixture.coord.load_inbox_recall(case.cc)
        self.assertTrue(bridge.activation_decision(case.cc, case.event, True))
        path = case.root/'memory'/f'{fixture.SESSION}.memory-budget.json'
        before = path.read_bytes()
        with (case.root/'memory'/f'{fixture.SESSION}.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            timer = threading.Timer(0.03, lambda: fcntl.flock(lock, fcntl.LOCK_UN))
            timer.start()
            try:
                self.assertTrue(bridge.activation_decision(case.cc, case.event, False))
            finally:
                timer.join()
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(case.calls(), [])

    def test_contention_does_not_hide_corrupt_ledger_after_acquisition(self):
        case = claude.ClaudeInboxRecallTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        bridge = fixture.coord.load_inbox_recall(case.cc)
        self.assertTrue(bridge.activation_decision(case.cc, case.event, True))
        path = case.root/'memory'/f'{fixture.SESSION}.inbox-recall.json'
        path.write_text('{invalid')
        with (case.root/'memory'/f'{fixture.SESSION}.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            timer = threading.Timer(0.03, lambda: fcntl.flock(lock, fcntl.LOCK_UN))
            timer.start()
            try:
                out = case.invoke(main=True)
            finally:
                timer.join()
        self.assertEqual(out['code'], 2, out)
        self.assertEqual(path.read_text(), '{invalid')
        self.assertEqual(case.calls(), [])
