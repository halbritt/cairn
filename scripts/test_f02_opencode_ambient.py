"""Actual H13 installed Source and receipt-checked OpenCode ambient processes."""
import base64
import copy
import json
import math
from pathlib import Path
import shutil
import sys
import unittest
import uuid

import test_f02_checked_ordinary as ordinary
import test_f02_hermes_recall as hermes
import test_f02_ordinary_host as host


class OpenCodeAmbientTests(unittest.TestCase):
    setUpClass = classmethod(ordinary.CheckedOrdinaryTests.setUpClass.__func__)
    tearDownClass = classmethod(ordinary.CheckedOrdinaryTests.tearDownClass.__func__)
    close_server = hermes.HermesRecallTests.close_server
    go = ordinary.CheckedOrdinaryTests.go
    go_vector = ordinary.CheckedOrdinaryTests.go_vector
    selected_vector = ordinary.CheckedOrdinaryTests.selected_vector
    go_launcher = ordinary.CheckedOrdinaryTests.go_launcher
    overwrite = ordinary.CheckedOrdinaryTests.overwrite
    repin = ordinary.CheckedOrdinaryTests.repin
    configure = hermes.HermesRecallTests.configure
    state_files = hermes.HermesRecallTests.state_files
    optional_note = host.OrdinaryHostTests.optional_note
    normalized_stream = host.OrdinaryHostTests.normalized_stream
    check_pull_intents = host.OrdinaryHostTests.check_pull_intents

    def setUp(self):
        hermes.HermesRecallTests.setUp(self)
        for config, path in ((self.f.memory, self.f.memory_path), (self.f.coord, self.f.coord_path)):
            config['harness'] = 'opencode'
            self.overwrite(path, ordinary.encoded(config))
        self.selection()
        self.trace = self.f.memory_path.parent/'invocation-trace'/'trace.jsonl'
        self.trace.parent.mkdir(mode=0o700)

    def state_key(self):
        return self.f.event['session_id']

    def event(self, name, **changes):
        self.f.event = dict(hook_event_name=name, session_id='ses_AmbientFixture1',
                            cwd=str(self.f.root), prompt='Repair lifecycle recall implementation.')
        self.f.event.update(changes)
        self.f.raw = ordinary.encoded(self.f.event)

    def selection(self, *, mode=True, fallback=True):
        if mode: self.f.memory['recall_mode'] = 'ambient'
        else: self.f.memory.pop('recall_mode', None)
        if fallback: self.f.memory['semantic_fallback'] = False
        else: self.f.memory.pop('semantic_fallback', None)
        self.overwrite(self.f.memory_path, ordinary.encoded(self.f.memory))
        self.repin()

    def source(self):
        self.assertTrue(self.source_repin)
        vector = ordinary.compat.launch_vector(sys.executable, self.f.receipt_path,
                    self.f.receipt_sha, 'memory', 'memory-hook.py', self.f.memory_path, 13, self.f.launch_root)
        self.assertEqual(vector[vector.index('--hook-timeout')+1], '13')
        result = ordinary.CheckedOrdinaryTests.source(self)
        raw = ordinary.compat.launcher_bytes(vector, '/bin/sh')
        launcher = self.f.launch_root/ordinary.sha(raw)/'inbox'
        self.assertEqual(launcher.read_bytes(), raw)
        self.assertEqual(launcher.stat().st_mode & 0o777, 0o500)
        print('AMBIENT_SOURCE_FRAME='+json.dumps(dict(vector=vector, launcher=str(launcher),
              launcher_sha256=ordinary.sha(raw), receipt_sha256=self.f.receipt_sha,
              source_members=self.f.receipt['members'], input_sha256=ordinary.sha(self.f.raw))), flush=True)
        return result

    def files(self):
        directory = Path(self.f.memory['state_dir'])
        return {p.name:dict(mode=p.stat().st_mode & 0o777, raw=p.read_bytes().decode())
                for p in sorted(directory.iterdir())} if directory.exists() else {}

    def state(self):
        return {name:json.loads(item['raw']) for name, item in self.files().items() if name.endswith('.json')}

    def same_keys(self, left, right, path=()):
        self.assertEqual(type(left), type(right), path)
        if isinstance(left, dict):
            self.assertEqual(set(left), set(right), path)
            for key in left: self.same_keys(left[key], right[key], path+(key,))
        elif isinstance(left, (list, tuple)):
            self.assertEqual(len(left), len(right), path)
            for index, (a, b) in enumerate(zip(left, right)): self.same_keys(a, b, path+(index,))

    def measurement(self, value):
        self.assertIn(type(value), (int, float))
        self.assertTrue(math.isfinite(value))
        self.assertGreaterEqual(value, 0)

    def normalize(self, row, side):
        stdout, arguments = self.normalized_stream(row['stdout'])
        calls = copy.deepcopy(row['calls'])
        if arguments: self.check_pull_intents(calls, arguments)
        expansions = set()
        for item in calls:
            self.assertEqual(len(item), 3)
            op, body, headers = item
            self.assertIsInstance(op, str); self.assertIsInstance(body, dict); self.assertIsInstance(headers, dict)
            if op == 'version': self.assertEqual(body, {})
            else:
                ident = body['request_id']; self.assertEqual(str(uuid.UUID(ident)), ident)
                if op == 'expand':
                    self.assertEqual(uuid.UUID(ident).version, 4 if body.get('span') else 5)
                    self.assertNotIn(ident, expansions); expansions.add(ident)
                body['request_id'] = 'validated-independent-uuid'
            if op == 'recall-observation':
                for key in ('elapsed_ms', 'search_ms', 'pulls_ms', 'selector_ms'):
                    self.measurement(body[key]); body[key] = 0
            self.assertEqual(headers['Authorization'], 'Bearer f02-synthetic-token')
            data = headers['Cairn-Client-Diagnostics']
            origin = json.loads(base64.urlsafe_b64decode(data+'='*(-len(data)%4)))['origin']
            image = self.f.receipt['binary']['sha256'] if side == 'go' else ordinary.sha((Path(self.f.receipt['snapshot_root'])/'memory-hook.py').read_bytes())
            self.assertEqual(origin, dict(component='lifecycle-memory', basis='reported', implementation_id=image))
        files = copy.deepcopy(row['files'])
        for name, item in files.items():
            if not name.endswith('.json'): continue
            body = json.loads(item['raw'])
            self.assertEqual(item['raw'].encode(), ordinary.encoded(body))
            status = body.get('last_recall', {})
            for key in ('at', 'elapsed_seconds', 'duration_ms'):
                if key in status: self.measurement(status[key]); status[key] = 0
            item['raw'] = ordinary.encoded(body).decode()
        trace = copy.deepcopy(row['trace'])
        if trace:
            self.assertEqual([x['kind'] for x in trace], ['entry', 'parsed', 'exit'])
            self.assertEqual(len({x['inv'] for x in trace}), 1)
            for item in trace:
                self.assertRegex(item['inv'], '^[0-9a-f]{32}$'); item['inv'] = '0'*32
                for key in ('at', 'duration_ms', 'pid', 'ppid'):
                    if key in item: self.measurement(item[key]); item[key] = 0
        return dict(exit=row['exit'], stdout=stdout.decode() if isinstance(stdout, bytes) else stdout,
                    stderr=row['stderr'].decode(), files=files,
                    requests=[(op, body) for op, body, _ in calls], trace=trace, locks=row['locks'])

    def pair(self):
        directory = Path(self.f.memory['state_dir'])
        seed = self.files(); existed = directory.exists()
        rows = []
        for side, launch in (('source', self.source), ('go', self.go)):
            shutil.rmtree(directory, ignore_errors=True)
            if existed:
                directory.mkdir(mode=0o700)
                for name, item in seed.items(): ordinary.write(directory/name, item['raw'].encode(), item['mode'])
            self.calls.clear(); self.report_locks.clear(); self.trace.unlink(missing_ok=True)
            result = launch()
            trace = [json.loads(x) for x in self.trace.read_text().splitlines()] if self.trace.exists() else []
            if side == 'source':
                self.assertEqual(len(trace), 3, 'Source preflight is not handler parity')
                self.assertEqual(trace[-1]['phase'], 'handle')
            rows.append(dict(exit=result.returncode, stdout=result.stdout, stderr=result.stderr,
                             files=self.files(), calls=copy.deepcopy(self.calls), trace=trace, locks=list(self.report_locks)))
        print('AMBIENT_RAW_PAIR='+json.dumps(dict(event=self.f.event['hook_event_name'],
              mode=self.f.memory.get('recall_mode', '<absent>'),
              **{side:dict(exit=row['exit'], stdout=row['stdout'].decode(), stderr=row['stderr'].decode(),
                          files=row['files'], requests=[(op, body) for op, body, _ in row['calls']],
                          trace=row['trace'], locks=row['locks'])
                 for side, row in zip(('source', 'go'), rows)})), flush=True)
        # Compare original keysets and request tuple structure before changing any
        # UUID or timing value. Missing fields cannot be normalized into success.
        for key in ('files', 'trace', 'locks'): self.same_keys(rows[0][key], rows[1][key], (key,))
        for left, right in zip(rows[0]['files'].values(), rows[1]['files'].values()):
            if left['raw']: self.same_keys(json.loads(left['raw']), json.loads(right['raw']), ('state',))
        self.same_keys([(op, body) for op, body, _ in rows[0]['calls']],
                       [(op, body) for op, body, _ in rows[1]['calls']], ('requests',))
        normalized = [self.normalize(row, side) for row, side in zip(rows, ('source', 'go'))]
        print('AMBIENT_PAIR='+json.dumps(dict(event=self.f.event['hook_event_name'],
              mode=self.f.memory.get('recall_mode', '<absent>'), source=normalized[0], go=normalized[1]), sort_keys=True), flush=True)
        self.assertEqual(normalized[1], normalized[0])
        self.assertTrue(all(normalized[1]['locks']))
        return result

    def test_explicit_and_default_start_prompt_reach_source_handler(self):
        for mode in (True, False):
            for name in ('SessionStart', 'UserPromptSubmit'):
                with self.subTest(mode=mode, event=name):
                    self.selection(mode=mode); self.event(name)
                    result = self.pair()
                    self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'{}\n', b''))
                    self.assertEqual([op for op, _, _ in self.calls], ['version', 'index', 'recall-observation'])


if __name__ == '__main__':
    unittest.main()
