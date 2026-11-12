"""Real Source/main and Go processes for Hermes capture/tool identity boundaries.

Use make test-isolated; the existing fixtures provide only disposable local APIs.
The handlers, Source closure, selection verifier and admission remain real.
"""
import base64
import copy
import fcntl
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import time
import unittest
import uuid

import test_f02_bound_memory as bound
import test_f03_checked_unbound_capture as checked
from test_f04_activation import ROOT, encoded, sha, write


CELLS = (('checked-capture', 'PreCompact'), ('checked-capture', 'SessionEnd'),
         ('bare', 'PreCompact'), ('bare', 'SessionEnd'), ('bare', 'PreToolUse'),
         ('bare', 'PostToolUse'), ('bare', 'PostToolUseFailure'))
CAPTURE = ('PreCompact', 'SessionEnd')
PREFLIGHT = b'Cairn lifecycle diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n'
GO_IDENTITY = (b'Cairn lifecycle diagnostic: phase=handle error_class=HookError\n'
               b'Cairn lifecycle: invalid Hermes session identity; use native tools or an explicit handoff.\n')
SOURCE_IDENTITY = (b'Cairn lifecycle diagnostic: phase=handle error_class=ValueError\n'
                   b'Cairn lifecycle: invalid lifecycle input or unavailable local file; use native tools or an explicit handoff.\n')


class HermesIdentityTests(checked.UnboundSelection, bound.BoundMemoryCase):
    capture_entrypoint = 'memory.py'
    pairs = 0

    def setUp(self):
        super().setUp()
        self.f.memory['harness'] = self.f.coord['harness'] = 'hermes'
        self.f.publish_configuration(enabled=False)
        self.directory = Path(self.f.memory['state_dir'])
        self.trace_path = self.f.memory_path.parent/'invocation-trace'/'trace.jsonl'
        self.trace_path.parent.mkdir(mode=0o700, exist_ok=True)
        (self.f.root/'alpha.py').write_text('disposable fixture\n')
        print('HERMES_IDENTITY_IMAGES='+json.dumps(dict(
            test=self.id(), go_sha256=sha(self.f.binary.read_bytes()),
            source_sha256=sha((ROOT/'integrations/lifecycle/memory.py').read_bytes()),
            receipt_sha256=self.f.receipt_sha, members=self.f.receipt['members']),
            sort_keys=True), flush=True)

    @classmethod
    def tearDownClass(cls):
        print('HERMES_IDENTITY_COUNT='+json.dumps(dict(pairs=cls.pairs)), flush=True)
        super().tearDownClass()

    def observe_locks(self, operation):
        key = sha(self.f.event['session_id'].encode())
        locks = {}
        for suffix in ('.lock', '.capture.lock'):
            with (self.directory/(key+suffix)).open('rb') as stream:
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    locks[suffix] = False
                else:
                    locks[suffix] = True
        self.report_locks.append(locks)

    def set_event(self, name, label, *, ascii=True):
        self.f.event = dict(hook_event_name=name, session_id=label, cwd=str(self.f.root),
                            messages=[], tool_name='Read', tool_input=dict(file_path='alpha.py'),
                            error='EACCES: disposable error')
        self.encode_event(ascii=ascii)

    def encode_event(self, *, ascii=True):
        self.f.raw = json.dumps(self.f.event, ensure_ascii=ascii, separators=(',', ':')).encode()

    def snapshot(self):
        if not self.directory.exists():
            return None
        return dict(mode=self.directory.stat().st_mode & 0o777,
                    files={p.name:dict(mode=p.stat().st_mode & 0o777,
                                       bytes=base64.b64encode(p.read_bytes()).decode())
                           for p in sorted(self.directory.iterdir())})

    def restore(self, seed):
        if self.directory.exists():
            shutil.rmtree(self.directory)
        if seed is None:
            return
        self.directory.mkdir(mode=seed['mode'])
        self.directory.chmod(seed['mode'])
        for name, item in seed['files'].items():
            write(self.directory/name, base64.b64decode(item['bytes']), item['mode'])

    def seeded(self, label):
        replacement = ''.join('\ufffd' if 0xd800 <= ord(c) <= 0xdfff else c for c in label)
        key = sha(replacement.encode())
        self.directory.mkdir(mode=0o750)
        self.directory.chmod(0o750)
        write(self.directory/(key+'.json'), b'{"binding":[null,""],"opaque":["keep",900719925474099312345]}\n', 0o640)
        write(self.directory/(key+'.lock'), b'retained lock bytes\n', 0o640)
        write(self.directory/'unrelated.keep', b'untouched\x00fixture\n', 0o400)

    def launch(self, route):
        if route == 'checked-capture':
            return self.go()
        return subprocess.run([str(self.f.binary), 'memory', '--config', str(self.f.memory_path)],
                              input=self.f.raw, cwd=self.f.root, env=self.f.env(),
                              capture_output=True, timeout=20)

    def observation(self, call):
        self.calls.clear()
        self.report_locks.clear()
        self.trace_path.unlink(missing_ok=True)
        started = time.time()
        result = call()
        ended = time.time()
        trace = [json.loads(line) for line in self.trace_path.read_bytes().splitlines()] if self.trace_path.exists() else []
        return dict(exit=result.returncode, stdout=result.stdout.decode(), stderr=result.stderr.decode(),
                    files=self.snapshot(), requests=copy.deepcopy(self.calls),
                    locks=list(self.report_locks), trace=trace, started=started, ended=ended)

    def pair(self, route, *, class_b=False, unchanged=False):
        seed = self.snapshot()
        rows = []
        for side, call in (('source', self.source), ('go', lambda: self.launch(route))):
            self.restore(seed)
            self.assertEqual(self.snapshot(), seed, side+' independent seed')
            rows.append(self.observation(call))
        type(self).pairs += 1
        print('HERMES_IDENTITY_PAIR='+json.dumps(dict(test=self.id(), route=route,
              event=self.f.event, raw_hex=self.f.raw.hex(), seed=seed, source=rows[0], go=rows[1]),
              ensure_ascii=True, sort_keys=True), flush=True)
        if class_b:
            for side, row, diagnostic in (('source', rows[0], SOURCE_IDENTITY), ('go', rows[1], GO_IDENTITY)):
                self.assertEqual((row['exit'], row['stdout']), (1, ''), side)
                self.assertEqual(row['stderr'].encode(), diagnostic, side)
                self.assertEqual(row['files'], seed, side+' no state effects')
                self.assertEqual(row['requests'], [], side+' no API effects')
                self.assertEqual(row['locks'], [], side+' no report/lock effects')
            # Class B allows these fixed diagnostic/error_class differences only.
            left, right = copy.deepcopy(rows)
            left['stderr'] = right['stderr'] = 'class-B diagnostic asserted separately'
            for row in (left, right):
                self.assertEqual(row['trace'][-1]['error_class'], 'ValueError' if row is left else 'HookError')
                row['trace'][-1]['error_class'] = 'class-B error asserted separately'
        else:
            left, right = copy.deepcopy(rows)
        if unchanged:
            for row in rows:
                self.assertEqual(row['files'], seed)
                self.assertEqual(row['requests'], [])
                self.assertEqual(row['locks'], [])
        # Check the complete key structure before removing ANY timing values.
        self.assertEqual(self.keysets(left), self.keysets(right))
        self.assertEqual(self.normalized(left, 'source'), self.normalized(right, 'go'))
        return rows

    def keysets(self, obj, path=()):
        if isinstance(obj, dict):
            result = {path:tuple(sorted(obj))}
            for key, item in obj.items():
                result.update(self.keysets(item, path+(key,)))
            return result
        if isinstance(obj, list):
            result = {}
            for n, item in enumerate(obj):
                result.update(self.keysets(item, path+(n,)))
            return result
        return {}

    def normalized(self, row, side):
        started, ended = row.pop('started'), row.pop('ended')
        def measurement(obj, key):
            n = obj.pop(key)  # required, never pop(key, None)
            self.assertIn(type(n), (int, float))
            self.assertTrue(math.isfinite(n))
            self.assertGreaterEqual(n, 0)
        def status(body):
            if 'last_capture' in body:
                self.assertEqual(set(body['last_capture']), {'at', 'outcome', 'records', 'selector_calls'})
                measurement(body['last_capture'], 'at')
            if 'hints' in body:
                for group in ('files', 'searches'):
                    for name in body['hints'].get(group, {}):
                        stamp = body['hints'][group][name]
                        self.assertTrue(started <= stamp <= ended)
                        body['hints'][group][name] = 'wallclock'
                if 'error_at' in body['hints']:
                    stamp = body['hints']['error_at']
                    self.assertTrue(started <= stamp <= ended)
                    measurement(body['hints'], 'error_at')
            return body
        if row['stdout']:
            body = json.loads(row['stdout'])
            self.assertEqual(row['stdout'], json.dumps(body, ensure_ascii=False, separators=(',', ':'))+'\n')
            if 'cairn_status' in body:
                status(body['cairn_status'])
            row['stdout'] = body
        if row['files'] is not None:
            for name, item in row['files']['files'].items():
                if name.endswith('.json'):
                    item['bytes'] = status(json.loads(base64.b64decode(item['bytes'])))
        requests = []
        for operation, body, headers in row['requests']:
            identifier = body.pop('request_id')
            self.assertEqual(str(uuid.UUID(identifier)), identifier)
            self.assertNotEqual(uuid.UUID(identifier).int, 0)
            self.assertEqual(operation, 'capture-observation')
            measurement(body, 'duration_ms')
            self.assertEqual(body['session_sha256'], sha(self.f.event['session_id'].encode()))
            self.assertEqual((body['harness'], body['hook_event']), ('hermes', self.f.event['hook_event_name']))
            self.assertEqual(headers['Authorization'], 'Bearer bound-memory-synthetic-token')
            declaration = headers['Cairn-Client-Diagnostics']
            origin = json.loads(base64.urlsafe_b64decode(declaration+'='*(-len(declaration)%4)))['origin']
            image = self.f.receipt['binary']['sha256'] if side == 'go' else self.f.receipt['members']['memory.py']['sha256']
            self.assertEqual(origin, dict(component='lifecycle-memory', basis='reported', implementation_id=image))
            requests.append((operation, body))
        row['requests'] = requests
        self.assertEqual([t['kind'] for t in row['trace']], ['entry', 'parsed', 'exit'])
        for item in row['trace']:
            measurement(item, 'at')
            self.assertRegex(item.pop('inv'), '^[0-9a-f]{32}$')
            if item['kind'] == 'entry':
                self.assertGreater(item.pop('pid'), 1)
                self.assertGreater(item.pop('ppid'), 1)
            if item['kind'] == 'exit':
                measurement(item, 'duration_ms')
        self.assertTrue(all(all(lock.values()) for lock in row['locks']))
        return row

    def test_lone_surrogates_refuse_without_effects(self):
        for route, name in CELLS:
            for label in ('bad\ud800label', 'bad\udffflabel'):
                for seeded in (False, True):
                    with self.subTest(route=route, event=name, label=repr(label), seeded=seeded):
                        self.restore(None)
                        if seeded:
                            self.seeded(label)
                        self.set_event(name, label)
                        self.pair(route, class_b=True)

    def test_valid_labels_keep_capture_status_and_tool_state(self):
        labels = (('not-a-uuid/session:label', True), ('日本語 / supplementary \U0001f600', False),
                  ('escaped \U0001f600 pair', True), ('DEL\x7flabel', True), ('h'*128, True))
        for route, name in CELLS:
            for label, ascii in labels:
                with self.subTest(route=route, event=name, label=label, ascii=ascii):
                    self.restore(None)
                    self.set_event(name, label, ascii=ascii)
                    rows = self.pair(route)
                    self.assertEqual((rows[1]['exit'], rows[1]['stderr']), (0, ''))
                    key = sha(label.encode())
                    self.assertIn(key+'.json', rows[1]['files']['files'])
                    self.assertIn('cairn_status', json.loads(rows[1]['stdout']))
                    self.assertEqual([op for op, _, _ in rows[1]['requests']], ['capture-observation'] if name in CAPTURE else [])
                    if ascii and '\U0001f600' in label:
                        self.assertIn(b'\\ud83d\\ude00', self.f.raw)

    def test_missing_c0_and_oversize_admission_is_unchanged(self):
        labels = (None, '', 17, 'h'*129, 'control\x00label', 'control\nlabel', 'control\x1flabel', 'MISSING')
        for route, name in CELLS:
            for label in labels:
                with self.subTest(route=route, event=name, label=repr(label)):
                    self.restore(None)
                    self.set_event(name, label)
                    if label == 'MISSING':
                        del self.f.event['session_id']
                        self.encode_event()
                    rows = self.pair(route, unchanged=True)
                    self.assertEqual((rows[1]['exit'], rows[1]['stdout']), (1, ''))
                    self.assertIn('error_class=HookError', rows[1]['stderr'])

    def test_privacy_and_disabled_guards_precede_malformed_identity(self):
        original = self.f.env
        marker = self.f.root/'.cairn-no-memory'
        for route, name in CELLS:
            for guard in ('privacy', 'CAIRN_LIFECYCLE_DISABLED', 'CAIRN_LIFECYCLE_CHILD'):
                with self.subTest(route=route, event=name, guard=guard):
                    self.restore(None)
                    self.set_event(name, 'bad\ud800label')
                    if guard == 'privacy':
                        marker.touch()
                    else:
                        self.f.env = lambda guard=guard: dict(original(), **{guard:'1'})
                    try:
                        rows = self.pair(route, unchanged=True)
                        self.assertEqual((rows[1]['exit'], rows[1]['stdout'], rows[1]['stderr']), (0, '{}\n', ''))
                    finally:
                        marker.unlink(missing_ok=True)
                        self.f.env = original

    def test_foreign_account_variables_do_not_change_hermes_policy(self):
        original = self.f.env
        self.f.env = lambda: dict(original(), CODEX_HOME=str(self.root/'other-codex'),
                                 CLAUDE_CONFIG_DIR=str(self.root/'other-claude'),
                                 HERMES_HOME=str(self.root/'other-hermes'))
        for route, name in CELLS:
            with self.subTest(route=route, event=name):
                self.restore(None)
                self.set_event(name, 'foreign-account-label')
                self.assertEqual(self.pair(route)[1]['exit'], 0)

    def test_checked_capture_selection_and_deadline_refusals_are_unchanged(self):
        self.set_event('PreCompact', 'bad\ud800label')
        def refused(call):
            self.restore(None)
            self.calls.clear()
            result = call()
            print('HERMES_SELECTION_REFUSAL='+json.dumps(dict(exit=result.returncode,
                  stdout=result.stdout.decode(), stderr=result.stderr.decode())), flush=True)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (1, b'', PREFLIGHT))
            self.assertIsNone(self.snapshot())
            self.assertEqual(self.calls, [])
        for timeout in (13, 149, 151):
            with self.subTest(timeout=timeout):
                refused(lambda: self.go(timeout=timeout))
        for path in (self.f.receipt_path, self.f.memory_path, self.f.coord_path,
                     Path(self.f.receipt['snapshot_root'])/'memory.py', self.f.binding):
            with self.subTest(changed=path.name):
                original = path.read_bytes()
                mode = path.stat().st_mode & 0o777
                path.chmod(0o600)
                path.write_bytes(original+b'\n')
                path.chmod(mode)
                try:
                    refused(self.go)
                finally:
                    path.chmod(0o600)
                    path.write_bytes(original)
                    path.chmod(mode)

    def test_checked_recall_still_refuses_capture_tools_and_bound_hermes(self):
        for _, name in CELLS:
            with self.subTest(event=name):
                self.restore(None)
                self.set_event(name, 'legal identity')
                vector = bound.BoundMemoryCase.go_vector(self, timeout=13)
                raw = ('#!/bin/sh\nexec '+__import__('shlex').join(vector)+' -- "$@"\n').encode()
                launcher = self.f.launch_root/sha(raw)/'inbox'
                launcher.parent.mkdir(mode=0o700, exist_ok=True)
                if not launcher.exists():
                    write(launcher, raw, 0o500)
                result = subprocess.run([str(launcher)], input=self.f.raw, cwd=self.f.root,
                                        env=self.f.env(), capture_output=True, timeout=20)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (1, b'', PREFLIGHT))
                self.assertIsNone(self.snapshot())
                self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
