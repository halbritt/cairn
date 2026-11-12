"""Actual H13 processes and physical observer probes, separate from copy effects."""
import copy
import fcntl
import json
import os
from pathlib import Path
import sys
import traceback
import types
import unittest
from unittest import mock

import test_f02_checked_ordinary as ordinary
import test_f02_hermes_recall as hermes
import test_f02_opencode_ambient as ambient


class ObserverLockProbeTests(unittest.TestCase):
    setUpClass = classmethod(ordinary.CheckedOrdinaryTests.setUpClass.__func__)
    tearDownClass = classmethod(ordinary.CheckedOrdinaryTests.tearDownClass.__func__)
    same_keys = ambient.OpenCodeAmbientTests.same_keys
    pairs = 0

    def fixture(self, kind):
        cls = hermes.HermesRecallTests if kind == 'hermes' else ambient.OpenCodeAmbientTests
        case = cls('test_start_and_prompt_reach_actual_checked_recall' if kind == 'hermes'
                   else 'test_explicit_and_default_start_prompt_reach_source_handler')
        case.binary, case.stager, case.cli = self.binary, self.stager, self.cli
        case.setUp()
        self.addCleanup(case.doCleanups)
        if kind == 'hermes':
            case.configure(recall_observations=True)
        case.trace = case.f.memory_path.parent/'invocation-trace'/'trace.jsonl'
        case.trace.parent.mkdir(mode=0o700, exist_ok=True)
        case.measurement = types.MethodType(ambient.OpenCodeAmbientTests.measurement, case)
        return case

    def files(self, case):
        directory = Path(case.f.memory['state_dir'])
        return {p.name:dict(mode=p.stat().st_mode & 0o777, raw=p.read_text())
                for p in sorted(directory.iterdir())} if directory.exists() else {}

    def exercise(self, kind, *, contention=False, separate=False):
        case = self.fixture(kind)
        real_flock = fcntl.flock
        phase = 'setup'
        physical, recorded, rows, captured = [], [], [], []
        captured_ids = set()

        class Results(list):
            def append(self, result):
                recorded.append(dict(phase=phase, value=result))
                super().append(result)

        case.report_locks = Results()
        target = None if separate else Path(case.f.memory['state_dir'])/(case.state_key()+'.lock')

        def probe(fd, flags):
            number = fd.fileno() if hasattr(fd, 'fileno') else fd
            path = Path(os.readlink('/proc/self/fd/'+str(number)))
            if flags != fcntl.LOCK_EX | fcntl.LOCK_NB or path != target:
                return real_flock(fd, flags)
            item = dict(phase=phase, flags=flags, path=str(path), contention=contention)
            physical.append(item)
            holder = None
            try:
                if contention:
                    holder = path.open('rb')
                    real_flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    item['contender_acquired'] = True
                try:
                    result = real_flock(fd, flags)
                except BlockingIOError:
                    item['result'] = False
                    raise
                else:
                    item['result'] = True
                    return result
            finally:
                if holder is not None:
                    real_flock(holder, fcntl.LOCK_UN)
                    holder.close()

        original_go = case.go
        def launch(side, invoke):
            nonlocal phase
            phase = side+'-frontend'
            case.trace.unlink(missing_ok=True)
            result = invoke()
            calls = copy.deepcopy(list(case.calls))
            trace = [json.loads(line) for line in case.trace.read_bytes().splitlines()]
            rows.append(dict(side=side, exit=result.returncode, stdout=result.stdout,
                             stderr=result.stderr, files=self.files(case), calls=calls,
                             trace=trace, locks=list(case.report_locks)))
            phase = side+'-collector'
            return result
        case.source = lambda: launch('source', lambda: ambient.OpenCodeAmbientTests.source(case))
        case.go = lambda: launch('go', original_go)

        method = (case.test_malformed_surrogate_identity_is_a_stable_class_b_refusal if separate else case.pair)
        code = method.__func__.__code__
        def observe(frame, event, argument):
            if frame.f_code is code:
                local = frame.f_locals
                values = [local[name] for name in ('calls', 'source_calls') if name in local]
                if 'rows' in local:
                    values.extend(row['calls'] for row in local['rows'] if 'calls' in row)
                for value in values:
                    if id(value) not in captured_ids:
                        captured_ids.add(id(value)); captured.append(value)
            return observe

        error = None
        previous_trace = sys.gettrace()
        with mock.patch('fcntl.flock', side_effect=probe):
            try:
                sys.settrace(observe)
                try:
                    method()
                except AssertionError as exc:
                    error = dict(message=str(exc), frames=[dict(file=f.filename, line=f.lineno, text=f.line)
                                                           for f in traceback.extract_tb(exc.__traceback__)])
            finally:
                sys.settrace(previous_trace)
            phase = 'future-copy'
            before = len(physical), len(recorded)
            for value in captured:
                # Copy the actual object captured by the original collector,
                # not a replacement assembled by this leaf.
                again = copy.deepcopy(value)
                nested = copy.deepcopy(again)
                self.assertEqual(again, value)
                self.assertEqual(nested, value)
                if again:
                    again[0][1]['observer_leaf_mutation'] = True
                    self.assertNotIn('observer_leaf_mutation', value[0][1])
                    self.assertNotIn('observer_leaf_mutation', nested[0][1])
                else:
                    again.append('independent empty snapshot')
                    self.assertEqual(value, [])
            after = len(physical), len(recorded)
        type(self).pairs += 1
        print('OBSERVER_RAW_PAIR='+json.dumps(dict(kind=kind, contention=contention, separate=separate,
              receipt_sha256=case.f.receipt_sha, image_sha256=case.f.receipt['binary']['sha256'],
              source_members=case.f.receipt['members'], physical=physical, recorded=recorded,
              captured_types=[type(value).__name__ for value in captured], future_before=before,
              future_after=after, original_assertion=error,
              rows=[dict(row, stdout=row['stdout'].decode(), stderr=row['stderr'].decode()) for row in rows]),
              ensure_ascii=True), flush=True)
        self.assertEqual(len(rows), 2)
        self.assertTrue(captured, 'the actual collector snapshot must be observed')
        for row in rows:
            self.assertEqual([item['kind'] for item in row['trace']], ['entry', 'parsed', 'exit'])
            self.assertEqual(row['trace'][-1]['phase'], 'handle')
            self.assertEqual(len({item['inv'] for item in row['trace']}), 1)
            reports = sum(op == 'recall-observation' for op, _, _ in row['calls'])
            attempts = [item for item in physical if item['phase'] == row['side']+'-frontend']
            self.assertEqual(len(attempts), reports)
            self.assertEqual(len([item for item in recorded if item['phase'] == row['side']+'-frontend']), reports)
            self.assertEqual(reports, 0 if separate else 1)
        self.assertEqual([item for item in physical if not item['phase'].endswith('-frontend')], [])
        self.assertEqual(before, after, 'future copies cannot perform physical or recorded probes')
        self.assertTrue(all(type(value) is list for value in captured))
        if separate:
            self.assertIsNone(error)
            self.assertEqual(physical, [])
            self.assertEqual(recorded, [])
            self.assertEqual([row['exit'] for row in rows], [1, 1])
            return
        self.assertEqual([row['exit'] for row in rows], [0, 0])
        self.same_keys(rows[0]['trace'], rows[1]['trace'])
        self.same_keys([(op, body) for op, body, _ in rows[0]['calls']],
                       [(op, body) for op, body, _ in rows[1]['calls']])
        self.same_keys(self.decoded(rows[0]), self.decoded(rows[1]))
        normalized = []
        for row in rows:
            row = copy.deepcopy(row)
            body = json.loads(row['stdout'])
            status = body.get('cairn_status', {}).get('last_recall')
            if status is not None:
                for key in ('at', 'elapsed_seconds', 'duration_ms'):
                    case.measurement(status[key])
                    status[key] = 0
            row['stdout'] = json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode()+b'\n'
            normalized.append(ambient.OpenCodeAmbientTests.normalize(case, row, row['side']))
        self.assertEqual(normalized[0], normalized[1])
        self.assertEqual(case.calls, rows[1]['calls'], 'copy/mutation must preserve original API values')
        self.assertEqual([item['result'] for item in physical], [not contention]*2)
        self.assertEqual([item['value'] for item in recorded], [not contention]*2)
        if contention:
            self.assertTrue(all(item['contender_acquired'] for item in physical))
            self.assertIsNotNone(error, 'the original post-lock assertion must reject real False probes')
            self.assertEqual(error['message'], 'False is not true')
            self.assertTrue(any(frame['file'] == code.co_filename and
                                'assertTrue(all(' in (frame['text'] or '')
                                for frame in error['frames']))
        else:
            self.assertIsNone(error)

    def decoded(self, row):
        return dict(stdout=json.loads(row['stdout']),
                    files={name:dict(mode=item['mode'], raw=json.loads(item['raw']) if name.endswith('.json') else item['raw'])
                           for name, item in row['files'].items()})

    def test_collectors_copy_without_replaying_real_probe(self):
        for kind in ('hermes', 'ambient'):
            with self.subTest(kind=kind):
                self.exercise(kind)

    def test_real_false_probe_survives_and_original_assertion_rejects(self):
        for kind in ('hermes', 'ambient'):
            with self.subTest(kind=kind):
                self.exercise(kind, contention=True)

    def test_separate_source_snapshot_is_plain_and_independent(self):
        self.exercise('hermes', separate=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
