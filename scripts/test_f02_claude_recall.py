"""Actual Source and checked Go Claude recall with a disposable Unix API."""
import copy
import base64
import json
from pathlib import Path
import shutil
import unittest
import uuid

import test_f02_checked_ordinary as ordinary
import test_f02_bound_memory as bound


class ClaudeUnboundRecallTests(unittest.TestCase):
    setUpClass = classmethod(ordinary.CheckedOrdinaryTests.setUpClass.__func__)
    tearDownClass = classmethod(ordinary.CheckedOrdinaryTests.tearDownClass.__func__)
    setUp = ordinary.CheckedOrdinaryTests.setUp
    close = ordinary.CheckedOrdinaryTests.close
    source = ordinary.CheckedOrdinaryTests.source
    go = ordinary.CheckedOrdinaryTests.go
    go_vector = ordinary.CheckedOrdinaryTests.go_vector
    selected_vector = ordinary.CheckedOrdinaryTests.selected_vector
    go_launcher = ordinary.CheckedOrdinaryTests.go_launcher
    state = ordinary.CheckedOrdinaryTests.state
    overwrite = ordinary.CheckedOrdinaryTests.overwrite
    repin = ordinary.CheckedOrdinaryTests.repin

    def claude(self, name, prompt):
        for config, path in ((self.f.memory, self.f.memory_path), (self.f.coord, self.f.coord_path)):
            config['harness'] = 'claude'
            self.overwrite(path, ordinary.encoded(config))
        self.repin()
        self.f.event.update(hook_event_name=name, session_id='11111111-1111-4111-8111-111111111111',
                            prompt=prompt, prompt_id='native-fixture-prompt')
        self.f.raw = ordinary.encoded(self.f.event)

    def pair(self):
        rows = []
        for launch in (self.source, self.go):
            shutil.rmtree(self.f.memory['state_dir'], ignore_errors=True)
            self.calls.clear()
            result = launch()
            calls = copy.deepcopy(self.calls)
            for operation, body, _ in calls:
                body.pop('request_id', None)
                for key in ('elapsed_ms', 'search_ms', 'pulls_ms', 'selector_ms'):
                    if operation == 'recall-observation':
                        self.assertGreaterEqual(body.pop(key), 0)
            rows.append(dict(exit=result.returncode, stdout=result.stdout.decode(),
                             stderr=result.stderr.decode(), state=self.state(),
                             requests=[(op, body) for op, body, _ in calls]))
        print('CLAUDE_UNBOUND_PAIR='+json.dumps(dict(event=self.f.event['hook_event_name'],
              source=rows[0], go=rows[1]), sort_keys=True), flush=True)
        self.assertEqual(rows[1], rows[0])
        return rows[1]

    def test_start_and_prompt_reach_real_checked_recall_body(self):
        for name in ('SessionStart', 'UserPromptSubmit'):
            with self.subTest(event=name):
                self.claude(name, 'Inspect the current retention policy.')
                row = self.pair()
                self.assertEqual((row['exit'], row['stderr']), (0, ''))
                self.assertEqual([op for op, _ in row['requests']], ['version', 'index'])

    def test_start_and_prompt_query_refusal_keeps_exact_public_diagnostics(self):
        for name in ('SessionStart', 'UserPromptSubmit'):
            with self.subTest(event=name):
                self.claude(name, 'Inspect "alpha\x00beta" policy.')
                row = self.pair()
                self.assertEqual((row['exit'], row['stdout']), (1, ''))
                self.assertEqual(row['requests'], [])
                self.assertEqual(row['stderr'], 'Cairn lifecycle diagnostic: phase=handle error_class=ValueError\n'
                    'Cairn lifecycle: invalid lifecycle input or unavailable local file; use native tools or an explicit handoff.\n')


class ClaudeBoundRecallTests(bound.BoundMemoryCase):
    def setUp(self):
        super().setUp()
        for config, path in ((self.f.memory, self.f.memory_path), (self.f.coord, self.f.coord_path)):
            config['harness'] = 'claude'
            self.overwrite(path, bound.encoded(config))
        self.repin_profiles()
        self.event('UserPromptSubmit', prompt_id='native-fixture-prompt', prompt='Inspect current retention policy.')

    def pair(self):
        directory = Path(self.f.memory['state_dir'])
        seed = {p.name:p.read_bytes() for p in directory.iterdir()} if directory.exists() else {}
        rows = []
        for label, launch in (('source', self.source), ('go', self.go)):
            self.reset_state(seed)
            self.calls.clear();self.report_locks.clear()
            self.before_run(label)
            result = launch()
            self.after_run()
            requests = []
            for op, body, headers in copy.deepcopy(self.calls):
                if op != 'version':
                    identity = body.pop('request_id')
                    self.assertEqual(str(uuid.UUID(identity)), identity)
                if op in ('recall-observation', 'capture-observation'):
                    for key in ('duration_ms', 'elapsed_ms', 'search_ms', 'pulls_ms', 'selector_ms'):
                        if key in body:self.assertGreaterEqual(body.pop(key), 0)
                self.assertEqual(headers['Authorization'], 'Bearer bound-memory-synthetic-token')
                token = headers['Cairn-Client-Diagnostics']
                origin = json.loads(base64.urlsafe_b64decode(token+'='*(-len(token)%4)))['origin']
                self.assertEqual(origin['component'], 'lifecycle-memory')
                self.assertEqual(origin['basis'], 'reported')
                if label == 'go':self.assertEqual(origin['implementation_id'], self.f.receipt['binary']['sha256'])
                requests.append((op, body))
            rows.append(dict(exit=result.returncode, stdout=result.stdout.decode(), stderr=result.stderr.decode(),
                             state=self.state(), requests=requests, locks=list(self.report_locks)))
        print('CLAUDE_BOUND_PAIR='+json.dumps(dict(event=self.f.event['hook_event_name'],
              source=rows[0], go=rows[1]), sort_keys=True), flush=True)
        self.assertEqual(rows[1], rows[0])
        self.assertTrue(all(all(lock.values()) for lock in rows[1]['locks']))
        return result

    def required(self, body='Required current guard. 日本語 🛡.'):
        self.selected = [dict(record=dict(record_id='55555555-5555-4555-8555-555555555555',
                              version=1, body=body), mandatory=True, reason='required', evidence=[], authority=[])]

    def test_bound_start_then_prompt_allocates_and_repeated_owner_does_not_refund(self):
        self.required()
        self.event('SessionStart', source='startup', prompt='')
        self.assertEqual(self.pair().returncode, 0)
        self.event('UserPromptSubmit', prompt_id='first-prompt', prompt='Inspect current retention policy.')
        self.assertEqual(self.pair().returncode, 0)
        before=self.state()[bound.SESSION+'.inbox-recall.json']
        grant=before['grants'][before['active']]
        self.assertGreater(grant['output_bytes'], 0)
        self.assertGreater(grant['native_allowance_bytes'], 0)
        refreshed=self.pair()
        self.assertNotIn(b'candidate_bodies', refreshed.stdout)
        after=self.state()[bound.SESSION+'.inbox-recall.json']
        self.assertEqual(len(after['grants']), 1)
        self.assertEqual(after['grants'][after['active']]['output_bytes'], grant['output_bytes'])
        self.assertEqual(after['grants'][after['active']]['native_allowance_bytes'], grant['native_allowance_bytes'])

    def test_claude_required_start_refuses_exit_one_and_prompt_exit_two(self):
        self.required('Required guard '+('context '*2000))
        for name, code in (('SessionStart', 1), ('UserPromptSubmit', 2)):
            with self.subTest(event=name):
                self.reset_state({})
                self.event(name, prompt_id='first-prompt', prompt='Inspect current retention policy.')
                result=self.pair()
                self.assertEqual((result.returncode, result.stdout), (code, b''))
                self.assertIn(b'RequiredContextRefused', result.stderr)
                state=self.state()[bound.SESSION+'.inbox-recall.json']
                grant=state['grants'][state['active']] if state.get('active') else state
                self.assertTrue(grant['required']['pending'])
