"""Actual retained Source/main and receipt-checked Go Hermes recall processes.

Run from the repository root: python3 -B scripts/test_f02_hermes_recall.py.
The existing fixture engines supply only constructed local API data.
"""
import base64
import copy
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import unittest
import uuid

import test_f02_checked_ordinary as ordinary
import test_f02_claude_recall as claude
import test_f02_ordinary_host as host
import test_f02_start as fixture


class HermesRecallTests(unittest.TestCase):
    setUpClass = classmethod(ordinary.CheckedOrdinaryTests.setUpClass.__func__)
    tearDownClass = classmethod(ordinary.CheckedOrdinaryTests.tearDownClass.__func__)
    close_server = fixture.F02StartTests.close_server
    source = ordinary.CheckedOrdinaryTests.source
    go = ordinary.CheckedOrdinaryTests.go
    go_vector = ordinary.CheckedOrdinaryTests.go_vector
    selected_vector = ordinary.CheckedOrdinaryTests.selected_vector
    go_launcher = ordinary.CheckedOrdinaryTests.go_launcher
    overwrite = ordinary.CheckedOrdinaryTests.overwrite
    repin = ordinary.CheckedOrdinaryTests.repin
    state = claude.ClaudeBoundCase.state
    optional_note = host.OrdinaryHostTests.optional_note
    normalized_stream = host.OrdinaryHostTests.normalized_stream
    check_pull_intents = host.OrdinaryHostTests.check_pull_intents

    def setUp(self):
        fixture.F02StartTests.setUp(self)
        # Keep the existing Unix API engine; issue the checked ordinary fixture.
        self.f = ordinary.UnboundFixture(self.root/'checked', self.binary,
                                        stager=self.stager, harness='opencode', enabled=False)
        for config in (self.f.memory, self.f.coord):
            config.update(harness='hermes', cairn=str(self.cli), socket=str(self.root/'api.sock'),
                          token_file=str(self.root/'token'))
        self.f.memory['recall_observations'] = True
        self.f.publish_configuration(enabled=False)
        self.event('SessionStart')
        self.report_locks = []
        owner = self

        class Calls(list):
            def append(self, item):
                if item[0] == 'recall-observation':
                    path = Path(owner.f.memory['state_dir'])/(owner.state_key()+'.lock')
                    with path.open('rb') as lock:
                        try:
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            owner.report_locks.append(False)
                        else:
                            owner.report_locks.append(True)
                super().append(item)

        self.calls = Calls()
        print('HERMES_IMAGES='+json.dumps(dict(receipt_sha256=self.f.receipt_sha,
              source_members=self.f.receipt['members'],
              go_image_sha256=ordinary.sha(self.f.binary.read_bytes())), sort_keys=True), flush=True)

    def state_key(self):
        return hashlib.sha256(self.f.event['session_id'].encode()).hexdigest()

    def configure(self, **changes):
        self.f.memory.update(changes)
        self.overwrite(self.f.memory_path, ordinary.encoded(self.f.memory))
        self.repin()

    def seed(self, body):
        directory = Path(self.f.memory['state_dir'])
        directory.mkdir(mode=0o700, exist_ok=True)
        ordinary.write(directory/(self.state_key()+'.json'), ordinary.encoded(body))

    def state_files(self):
        directory = Path(self.f.memory['state_dir'])
        if not directory.exists(): return None
        return dict(mode=directory.stat().st_mode & 0o777,
                    files={p.name:(p.stat().st_mode & 0o777, ordinary.sha(p.read_bytes()))
                           for p in sorted(directory.iterdir())})

    def context(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        text = json.loads(result.stdout).get('hookSpecificOutput', {}).get('additionalContext', '')
        return json.loads(text[text.index('{"selected"'):]) if text else {}

    def event(self, name, **changes):
        self.f.event = dict(hook_event_name=name, session_id='Hermes session/run:日本語-01',
                            cwd=str(self.f.root), prompt='Inspect current retention policy.')
        self.f.event.update(changes)
        self.f.raw = ordinary.encoded(self.f.event)

    def output(self, raw):
        if not raw:
            return raw, []
        body = json.loads(raw)
        self.assertEqual(raw, json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode()+b'\n')
        status = body.get('cairn_status', {}).get('last_recall', {})
        for key in ('at', 'duration_ms', 'elapsed_seconds'):
            if key in status:
                self.assertGreaterEqual(status.pop(key), 0)
        return self.normalized_stream(json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode()+b'\n')

    def pair(self):
        directory = Path(self.f.memory['state_dir'])
        files_before = self.state_files()
        seed = {p.name:(p.read_bytes(), p.stat().st_mode & 0o777) for p in directory.iterdir()} if directory.exists() else {}
        rows = []
        for side, launch in (('source', self.source), ('go', self.go)):
            shutil.rmtree(directory, ignore_errors=True)
            if files_before is not None:
                directory.mkdir(mode=files_before['mode'])
                for name, (raw, mode) in seed.items():
                    ordinary.write(directory/name, raw, mode)
            self.calls.clear()
            self.report_locks.clear()
            result = launch()
            if result.stderr == b'Cairn lifecycle diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n':
                self.assertEqual(self.calls, [])
                self.assertEqual(self.state_files(), files_before)
            stdout, arguments = self.output(result.stdout)
            calls = copy.deepcopy(self.calls)
            self.check_pull_intents(calls, arguments)
            for op, body, headers in calls:
                if op != 'version':
                    ident = body.pop('request_id')
                    self.assertEqual(str(uuid.UUID(ident)), ident)
                if op == 'recall-observation':
                    for key in ('elapsed_ms', 'search_ms', 'pulls_ms', 'selector_ms'):
                        self.assertGreaterEqual(body.pop(key), 0)
                self.assertEqual(headers['Authorization'], 'Bearer f02-synthetic-token')
                token = headers['Cairn-Client-Diagnostics']
                origin = json.loads(base64.urlsafe_b64decode(token+'='*(-len(token)%4)))['origin']
                self.assertEqual(origin['component'], 'lifecycle-memory')
                self.assertEqual(origin['basis'], 'reported')
                if side == 'go':
                    self.assertEqual(origin['implementation_id'], self.f.receipt['binary']['sha256'])
                else:
                    expected = dict(component='lifecycle-memory', basis='reported')
                    if getattr(self, 'source_repin', False):
                        member = Path(self.f.receipt['snapshot_root'])/'memory-hook.py'
                        expected['implementation_id'] = ordinary.sha(member.read_bytes())
                    self.assertEqual(origin, expected)
            rows.append(dict(exit=result.returncode, stdout=stdout.decode() if isinstance(stdout, bytes) else stdout,
                             stderr=result.stderr.decode(), state=self.state(),
                             requests=[(op, body) for op, body, _ in calls], locks=list(self.report_locks)))
        print('HERMES_PAIR='+json.dumps(dict(event=self.f.event['hook_event_name'],
              receipt_sha256=self.f.receipt_sha, source=rows[0], go=rows[1]), sort_keys=True), flush=True)
        self.assertEqual(rows[1], rows[0])
        self.assertEqual(ordinary.scalar_tree(rows[1]['state']), ordinary.scalar_tree(rows[0]['state']))
        self.assertTrue(all(rows[1]['locks']))
        return result

    def test_start_and_prompt_reach_actual_checked_recall(self):
        for name in ('SessionStart', 'UserPromptSubmit'):
            with self.subTest(event=name):
                self.event(name)
                result = self.pair()
                self.assertEqual((result.returncode, result.stderr), (0, b''))
                request = next(body for op, body, _ in self.calls if op == 'index')
                self.assertEqual(request['scope']['task_id'], 'hermes/'+self.f.event['session_id'])
                self.assertEqual(request['scope']['run_id'], self.f.event['session_id'])
                key = hashlib.sha256(self.f.event['session_id'].encode()).hexdigest()
                self.assertEqual(list(self.state()), [key+'.json'])
                self.assertIn('cairn_status', json.loads(result.stdout))

    def test_selected_and_optional_context_through_actual_sdk(self):
        self.selected = [dict(record=dict(record_id='66666666-6666-4666-8666-666666666666',
                              version=2, body='Required retention policy. 日本語 🛡.'),
                              mandatory=True, reason='required', evidence=[], authority=[])]
        self.optional_note('Current retention policy: 日本語 😀\n')
        for name in ('SessionStart', 'UserPromptSubmit'):
            with self.subTest(event=name):
                self.event(name)
                result = self.pair()
                context = self.context(result)
                record = context['selected'][0]['record']
                self.assertEqual((record['record_id'], record['version'], record['body']),
                                 ('66666666-6666-4666-8666-666666666666', 2, self.selected[0]['record']['body']))
                self.assertTrue(context['selected'][0]['mandatory'])
                self.assertEqual(context['candidate_bodies'][0]['response']['selection']['record']['body'], self.optional[0]['body'])
                self.assertEqual(context['candidate_inspection']['pull_calls'], 1)
                self.assertEqual([op for op, _, _ in self.calls], ['version', 'index', 'expand', 'recall-observation'])

    def test_taskless_start_and_notification_defer_optional(self):
        self.optional_note('Unrequested retention policy must stay deferred.')
        notification = self.f.wake()
        for name, prompt, reason in (('SessionStart', '', 'taskless_startup'),
                                     ('UserPromptSubmit', notification, 'taskless_notification')):
            with self.subTest(event=name):
                self.event(name, prompt=prompt)
                result = self.pair()
                self.assertEqual(result.returncode, 0)
                state = self.state()[self.state_key()+'.json']['last_recall']
                self.assertEqual(state['optional_deferred'], reason)
                self.assertEqual(state['discovery'], 'deferred')
                request = next(body for op, body, _ in self.calls if op == 'index')
                self.assertEqual(request['kinds'], ['decision', 'preference'])
                self.assertNotIn('expand', [op for op, _, _ in self.calls])
                self.assertNotIn(self.optional[0]['body'], result.stdout.decode())

    def test_semantic_discovery_inside_agent_tools_remains_sdk_only(self):
        self.configure(semantic_fallback=True)
        self.discovery = dict(mode='semantic', state='ready')
        self.event('UserPromptSubmit')
        self.assertEqual(self.pair().returncode, 0)
        request = next(body for op, body, _ in self.calls if op == 'index')
        self.assertTrue(request['semantic'])
        self.assertNotIn('expand', [op for op, _, _ in self.calls])

    def test_seen_retention_start_reset_and_binding_reset(self):
        self.seed(dict(binding=[None, ''], seen=dict(old=1, retained=2),
                       opaque=['日本語', 900719925474099312345],
                       last_capture=dict(at=123.25, outcome='retained', bytes=91)))
        self.event('UserPromptSubmit', retained_record_ids=['retained'])
        result = self.pair()
        state = self.state()[self.state_key()+'.json']
        self.assertEqual(state['seen'], dict(retained=2))
        self.assertEqual(state['opaque'], ['日本語', 900719925474099312345])
        self.assertEqual(json.loads(result.stdout)['cairn_status']['last_capture'],
                         dict(at=123.25, outcome='retained', bytes=91))
        self.event('SessionStart')
        self.pair()
        self.assertEqual(self.state()[self.state_key()+'.json']['seen'], {})
        self.event('UserPromptSubmit', project_path=str(self.f.root), workstream='retention')
        result = self.pair()
        state = self.state()[self.state_key()+'.json']
        self.assertEqual(state['binding'], [str(self.f.root), 'retention'])
        self.assertNotIn('opaque', state)
        self.assertTrue(state['workstream'].endswith('retention'))
        self.assertEqual(json.loads(result.stdout)['cairn_status']['workstream'], state['workstream'])

    def test_legal_labels_keep_raw_api_scope_and_hash_only_state_paths(self):
        for label in ('simple-label', '../label/with:separators', '11111111-1111-4111-8111-111111111111', 'h'*128, 'legal\x7flabel'):
            with self.subTest(label=label):
                shutil.rmtree(self.f.memory['state_dir'], ignore_errors=True)
                self.event('UserPromptSubmit', session_id=label)
                self.pair()
                self.assertEqual(list(self.state()), [self.state_key()+'.json'])
                request = next(body for op, body, _ in self.calls if op == 'index')
                self.assertEqual(request['scope']['run_id'], label)
                self.assertEqual(request['scope']['task_id'], 'hermes/'+label)

    def test_invalid_missing_and_control_identity_refuse_without_effects(self):
        for label in (None, '', 17, 'h'*129, 'control\nlabel', 'control\x00label', 'control\x1flabel'):
            with self.subTest(label=label):
                self.event('UserPromptSubmit', session_id=label)
                result = self.pair()
                self.assertEqual((result.returncode, result.stdout), (1, b''))
                self.assertIn(b'error_class=HookError', result.stderr)
                self.assertEqual(self.calls, [])
                self.assertFalse(Path(self.f.memory['state_dir']).exists())
        self.f.event.pop('session_id'); self.f.raw = ordinary.encoded(self.f.event)
        self.assertEqual(self.pair().returncode, 1)
        self.assertEqual(self.calls, [])
        self.assertFalse(Path(self.f.memory['state_dir']).exists())

    def test_project_privacy_and_disabled_environment_precede_api_and_state(self):
        marker = self.f.root/'.cairn-no-memory'
        marker.touch()
        self.assertEqual(self.pair().stdout, b'{}\n')
        self.assertEqual(self.calls, [])
        self.assertFalse(Path(self.f.memory['state_dir']).exists())
        marker.unlink()
        original = self.f.env
        for variable in ('CAIRN_LIFECYCLE_DISABLED', 'CAIRN_LIFECYCLE_CHILD'):
            with self.subTest(variable=variable):
                self.f.env = lambda: dict(original(), **{variable:'1'})
                self.assertEqual(self.pair().stdout, b'{}\n')
                self.assertEqual(self.calls, [])
                self.assertFalse(Path(self.f.memory['state_dir']).exists())

    def test_other_harness_account_variables_do_not_create_hermes_account_policy(self):
        original = self.f.env
        self.f.env = lambda: dict(original(), CLAUDE_CONFIG_DIR=str(self.root/'foreign-claude'),
                                  CODEX_HOME=str(self.root/'foreign-codex'), HERMES_HOME=str(self.root/'foreign-hermes'))
        self.assertEqual(self.pair().returncode, 0)
        self.assertIn('index', [op for op, _, _ in self.calls])

    def test_handled_api_failures_keep_status_diagnostics_and_meter(self):
        for name in ('SessionStart', 'UserPromptSubmit'):
            for refusal in ('API_UNAVAILABLE', 'OPEN_CONFLICT', 'POLICY_UNENFORCEABLE'):
                with self.subTest(event=name, refusal=refusal):
                    self.event(name)
                    self.refusal = refusal
                    result = self.pair()
                    self.assertEqual((result.returncode, result.stdout), (1, b''))
                    self.assertEqual(len(result.stderr.splitlines()), 2)
                    self.assertEqual(self.state()[self.state_key()+'.json']['last_recall']['outcome'], 'failed')
                    report = next(body for op, body, _ in self.calls if op == 'recall-observation')
                    self.assertEqual((report['status'], report['injected_bytes']), ('error', 0))

    def test_context_budget_and_meter_charge_only_injected_context(self):
        self.configure(context_bytes=3000)
        result = self.pair()
        text = json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
        report = next(body for op, body, _ in self.calls if op == 'recall-observation')
        self.assertEqual((report['harness'], report['search_calls'], report['pull_calls'], report['selector_calls']),
                         ('hermes', 1, 0, []))
        self.assertEqual(report['injected_bytes'], len(text.encode()))
        self.assertLess(report['injected_bytes'], len(result.stdout))
        self.configure(recall_observations=False)
        self.assertEqual(self.pair().returncode, 0)
        self.assertEqual([op for op, _, _ in self.calls], ['version', 'index'])
        self.configure(context_bytes=1000, recall_observations=True)
        self.selected = [dict(record=dict(record_id='66666666-6666-4666-8666-666666666666',
                              version=2, body='Required policy. '*300),
                              mandatory=True, reason='required', evidence=[], authority=[])]
        result = self.pair()
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.assertIn(b'no partial instructions injected', result.stderr)

    def test_invocation_trace_matches_success_failure_and_early_admission(self):
        directory = self.f.memory_path.parent/'invocation-trace'
        directory.mkdir(mode=0o700)
        path = directory/'trace.jsonl'
        for kind in ('success', 'failure', 'invalid', 'uuid'):
            with self.subTest(kind=kind):
                path.unlink(missing_ok=True)
                self.refusal = 'API_UNAVAILABLE' if kind == 'failure' else None
                label = 'bad\nlabel' if kind == 'invalid' else '11111111-1111-4111-8111-111111111111' if kind == 'uuid' else 'Hermes trace label'
                self.event('UserPromptSubmit', session_id=label)
                self.pair()
                rows = [json.loads(line) for line in path.read_text().splitlines()]
                self.assertEqual([row['kind'] for row in rows], ['entry', 'parsed', 'exit']*2)
                for part in (rows[:3], rows[3:]):
                    self.assertEqual(len({row['inv'] for row in part}), 1)
                for row in rows:
                    identity = row.pop('inv'); self.assertRegex(identity, '^[0-9a-f]{32}$')
                    for key in ('at', 'duration_ms', 'pid', 'ppid'):
                        if key in row: self.assertGreaterEqual(row.pop(key), 0)
                self.assertEqual(rows[3:], rows[:3])
                if kind == 'uuid': self.assertEqual(rows[-1]['session'], label)
                else: self.assertNotIn('session', rows[-1])

    def refused(self, launch=None):
        self.calls.clear()
        before = self.state()
        files_before = self.state_files()
        directory = Path(self.f.memory['state_dir'])
        existed = directory.exists()
        result = (launch or self.go)()
        print('HERMES_PREFLIGHT_REFUSAL='+json.dumps(dict(event=self.f.event['hook_event_name'],
              exit=result.returncode, stdout=result.stdout.decode(), stderr=result.stderr.decode())), flush=True)
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.assertEqual(result.stderr, b'Cairn lifecycle diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.state(), before)
        self.assertEqual(self.state_files(), files_before)
        self.assertEqual(directory.exists(), existed)

    def test_tools_capture_and_unknown_events_remain_outside_checked_recall(self):
        for name in ('PreToolUse', 'PostToolUse', 'PostToolUseFailure', 'PreCompact', 'SessionEnd', 'Stop', 'Unknown'):
            with self.subTest(event=name):
                self.event(name)
                self.refused()

    def test_ambient_and_mixed_bound_profiles_remain_refused(self):
        self.configure(recall_mode='ambient')
        self.refused()
        self.configure(recall_mode='agent_tools')
        for path in (self.f.memory_path, self.f.coord_path):
            with self.subTest(profile=path.name):
                old = path.read_bytes()
                body = json.loads(old); body['inbox_recall_binding'] = str(self.f.binding)
                self.overwrite(path, ordinary.encoded(body)); self.repin()
                try: self.refused()
                finally: self.overwrite(path, old); self.repin()
        self.seed(dict(opaque=['preserved exactly', 900719925474099312345]))
        self.selection_overrides = {'hook-timeout':'150'}
        self.refused()

    def test_bare_hermes_recall_remains_unavailable(self):
        for name in ('SessionStart', 'UserPromptSubmit'):
            self.event(name)
            result = subprocess.run([str(self.f.binary), 'memory', '--config', str(self.f.memory_path)],
                                    input=self.f.raw, cwd=self.f.root, env=self.f.env(), capture_output=True, timeout=18)
            self.assertEqual((result.returncode, result.stdout), (1, b''))
            self.assertIn(b'ordinary memory harness remains unavailable', result.stderr)
            self.assertEqual(self.calls, [])
            self.assertFalse(Path(self.f.memory['state_dir']).exists())

    test_changed_selected_artifacts_refuse_before_api_or_state = ordinary.CheckedOrdinaryTests.test_changed_selected_artifacts_refuse_before_api_or_state
    test_changed_receipt_refuses_before_api_or_state = ordinary.CheckedOrdinaryTests.test_changed_receipt_refuses_before_api_or_state
    test_mutated_actual_go_launcher_refuses_before_configuration = ordinary.CheckedOrdinaryTests.test_mutated_actual_go_launcher_refuses_before_configuration
    test_forged_argv_cannot_substitute_same_bytes_executing_image = ordinary.CheckedOrdinaryTests.test_forged_argv_cannot_substitute_same_bytes_executing_image
    test_direct_entry_deadline_covers_waiting_for_original_stdin = ordinary.CheckedOrdinaryTests.test_direct_entry_deadline_covers_waiting_for_original_stdin
    test_search_timeout_preserves_both_fixed_diagnostics = fixture.F02StartTests.test_search_timeout_preserves_both_fixed_diagnostics


if __name__ == '__main__':
    unittest.main()
