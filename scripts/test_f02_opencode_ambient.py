"""Actual H13 installed Source and receipt-checked OpenCode ambient processes."""
import base64
import copy
import json
import math
from pathlib import Path
import shutil
import sys
import subprocess
import time
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
    context = hermes.HermesRecallTests.context
    seed = hermes.HermesRecallTests.seed

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

    def validate_keysets(self, rows):
        for row in rows:
            for item in row['calls']:
                self.assertIsInstance(item, tuple)
                self.assertEqual(len(item), 3)
                self.assertIsInstance(item[0], str)
                self.assertIsInstance(item[1], dict)
                self.assertIsInstance(item[2], dict)
        for key in ('files', 'trace', 'locks'): self.same_keys(rows[0][key], rows[1][key], (key,))
        for left, right in zip(rows[0]['files'].values(), rows[1]['files'].values()):
            if left['raw']: self.same_keys(json.loads(left['raw']), json.loads(right['raw']), ('state',))
        self.same_keys([(op, body) for op, body, _ in rows[0]['calls']],
                       [(op, body) for op, body, _ in rows[1]['calls']], ('requests',))

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
            self.assertEqual(item['raw'].encode(), json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode())
            status = body.get('last_recall', {})
            for key in ('at', 'elapsed_seconds', 'duration_ms'):
                if key in status: self.measurement(status[key]); status[key] = 0
            if self.f.event['hook_event_name'] in ('PreToolUse', 'PostToolUse', 'PostToolUseFailure'):
                hints = body.get('hints', {})
                for bucket in ('files', 'searches'):
                    for key, value in hints.get(bucket, {}).items():
                        self.measurement(value); hints[bucket][key] = 0
                if 'error_at' in hints: self.measurement(hints['error_at']); hints['error_at'] = 0
            item['raw'] = json.dumps(body, ensure_ascii=False, separators=(',', ':'))
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
                             files=self.files(), calls=copy.deepcopy(list(self.calls)), trace=trace, locks=list(self.report_locks)))
        print('AMBIENT_RAW_PAIR='+json.dumps(dict(event=self.f.event['hook_event_name'],
              mode=self.f.memory.get('recall_mode', '<absent>'),
              **{side:dict(exit=row['exit'], stdout=row['stdout'].decode(), stderr=row['stderr'].decode(),
                          files=row['files'], requests=[(op, body) for op, body, _ in row['calls']],
                          trace=row['trace'], locks=row['locks'])
                 for side, row in zip(('source', 'go'), rows)})), flush=True)
        # Compare original keysets and request tuple structure before changing any
        # UUID or timing value. Missing fields cannot be normalized into success.
        self.last_raw_pair = copy.deepcopy(rows)
        self.validate_keysets(rows)
        normalized = [self.normalize(row, side) for row, side in zip(rows, ('source', 'go'))]
        print('AMBIENT_PAIR='+json.dumps(dict(event=self.f.event['hook_event_name'],
              mode=self.f.memory.get('recall_mode', '<absent>'), source=normalized[0], go=normalized[1]), sort_keys=True), flush=True)
        self.assertEqual(normalized[1], normalized[0])
        self.assertTrue(all(normalized[1]['locks']))
        return result

    def test_explicit_and_default_start_prompt_reach_source_handler(self):
        for mode in (True, False):
            for fallback in (True, False):
                for name in ('SessionStart', 'UserPromptSubmit'):
                    with self.subTest(mode=mode, fallback=fallback, event=name):
                        self.selection(mode=mode, fallback=fallback); self.event(name)
                        result = self.pair()
                        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'{}\n', b''))
                        self.assertEqual([op for op, _, _ in self.calls], ['version', 'index', 'recall-observation'])

    def required(self, body='Required lifecycle recall policy. 日本語 🛡.'):
        self.selected = [dict(record=dict(record_id='66666666-6666-4666-8666-666666666666',
                              version=2, body=body), mandatory=True, reason='required',
                              evidence=[], authority=[], conflicts=[])]

    def test_required_and_whole_competing_sources_and_fitting_failure(self):
        self.required()
        second = copy.deepcopy(self.selected[0])
        second['record'].update(record_id='77777777-7777-4777-8777-777777777777', body='Competing complete lifecycle instruction.\nKeep every byte.')
        self.selected.append(second)
        conflict = dict(id='88888888-8888-4888-8888-888888888888', version=1,
                        members=[dict(record_id=x['record']['record_id'], version=2) for x in self.selected])
        for item in self.selected: item['conflicts'] = [conflict]
        for name in ('SessionStart', 'UserPromptSubmit'):
            self.event(name); result = self.pair(); context = self.context(result)
            self.assertEqual([x['record']['body'] for x in context['selected']], [x['record']['body'] for x in self.selected])
            self.assertTrue(all(x['mandatory'] for x in context['selected']))
        self.configure(context_bytes=1000); self.required('Required whole source. '*400)
        result = self.pair()
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.assertIn(b'no partial instructions injected', result.stderr)

    def test_optional_whole_body_seen_and_repeated_prompt(self):
        self.optional_note('Current lifecycle recall implementation. 日本語 😀\nKeep the real API and state.')
        self.event('UserPromptSubmit')
        first = self.pair(); context = self.context(first)
        self.assertEqual(context['expanded']['selection']['record']['body'], self.optional[0]['body'])
        self.assertEqual(self.state()[self.state_key()+'.json']['seen'], {self.optional[0]['entry']['record_id']:3})
        self.assertEqual([op for op, _, _ in self.calls], ['version', 'index', 'expand', 'recall-observation'])
        self.assertEqual(self.pair().stdout, b'{}\n')
        self.assertNotIn('expand', [op for op, _, _ in self.calls])
        self.event('SessionStart'); self.assertEqual(self.pair().returncode, 0)
        self.assertIn('expand', [op for op, _, _ in self.calls])

    def test_paid_whole_excerpt_and_budget_refused_span(self):
        self.configure(context_bytes=3000)
        self.optional_note('Current lifecycle recall implementation.\n'+'filler '*1800, offset=0, length=36)
        first = self.context(self.pair())
        self.assertEqual(first['expanded']['source_extent'], 'partial_span')
        self.assertEqual(first['expanded']['span_origin'], 'whole_pull')
        self.assertEqual(sum(op == 'expand' for op, _, _ in self.calls), 1)
        shutil.rmtree(self.f.memory['state_dir'])
        self.expand_refusal = 'BUDGET_REFUSED'
        second = self.context(self.pair())
        self.assertEqual(second['expanded']['source_extent'], 'partial_span')
        self.assertEqual(sum(op == 'expand' for op, _, _ in self.calls), 2)

    def test_unavailable_body_warning_depends_on_remaining_relevant_preview(self):
        self.required()
        for relevant in (False, True):
            with self.subTest(relevant=relevant):
                shutil.rmtree(self.f.memory['state_dir'], ignore_errors=True)
                entry = self.optional_note('Unrelated gardening context.\nPlant in another season.')
                entry['summary'] = 'lifecycle recall implementation' if relevant else 'Unrelated gardening context'
                self.expand_transform = lambda request, response: response['selection']['record'].update(version=99)
                result = self.pair(); context = self.context(result)
                text = json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
                self.assertEqual('Optional body unavailable; search again before relying on its preview.\n' in text, relevant)
                self.assertEqual(bool(context['index']), relevant)
                self.assertEqual(self.state()[self.state_key()+'.json']['last_recall']['rejected'], {'unavailable':1})
                self.assertEqual(self.confirmed_expands, 1)

    def test_deferred_start_notification_and_required_only_context(self):
        self.optional_note('Current lifecycle recall implementation must stay deferred.')
        for required in (False, True):
            if required: self.required()
            for name, prompt, reason in (('SessionStart', '', 'taskless_startup'),
                                         ('UserPromptSubmit', self.f.wake(), 'taskless_notification')):
                with self.subTest(required=required, event=name):
                    self.event(name, prompt=prompt); result = self.pair()
                    self.assertEqual(result.returncode, 0)
                    self.assertNotIn('expand', [op for op, _, _ in self.calls])
                    request = next(body for op, body, _ in self.calls if op == 'index')
                    self.assertEqual(request['kinds'], ['decision', 'preference'])
                    self.assertEqual(self.state()[self.state_key()+'.json']['last_recall']['optional_deferred'], reason)
                    self.assertEqual(bool(self.context(result)), required)

    def test_seen_retention_binding_reset_and_opaque_order(self):
        self.seed(dict(binding=[None, ''], seen=dict(old=1, retained=2), opaque=['日本語', 900719925474099312345]))
        self.event('UserPromptSubmit', retained_record_ids=['retained']); self.pair()
        state = self.state()[self.state_key()+'.json']
        self.assertEqual(state['seen'], {'retained':2})
        self.assertEqual(state['opaque'], ['日本語', 900719925474099312345])
        self.event('SessionStart'); self.pair()
        self.assertEqual(self.state()[self.state_key()+'.json']['seen'], {})
        self.event('UserPromptSubmit', project_path=str(self.f.root), workstream='ambient')
        self.pair(); state = self.state()[self.state_key()+'.json']
        self.assertEqual(state['binding'], [str(self.f.root), 'ambient'])
        self.assertNotIn('opaque', state)
        self.assertEqual(state['workstream'], 'Handoff: '+self.f.root.name+' / ambient')

    def test_query_file_entities_and_context_pins(self):
        (self.f.root/'src').mkdir(); (self.f.root/'src/current.go').write_text('fixture\n')
        self.configure(context=dict(revision='fixture-revision', workspace_sha256='b'*64, task_class='repair', task_phase='test'))
        self.event('UserPromptSubmit', prompt='Repair ./src/current.go and "exact source failure".')
        self.pair(); request = next(body for op, body, _ in self.calls if op == 'index')
        self.assertIn('src/current.go', request['query'])
        self.assertIn(dict(kind='file', name='src/current.go'), request['entities'])
        self.assertEqual(request['context']['revision'], 'fixture-revision')
        self.assertFalse(request.get('semantic', False))

    def test_handled_api_failures_and_byte_meter(self):
        for failure in ('API_UNAVAILABLE', 'OPEN_CONFLICT', 'POLICY_UNENFORCEABLE'):
            with self.subTest(failure=failure):
                self.refusal = failure; result = self.pair()
                self.assertEqual((result.returncode, result.stdout), (1, b''))
                report = next(body for op, body, _ in self.calls if op == 'recall-observation')
                self.assertEqual((report['status'], report['search_calls'], report['pull_calls'], report['injected_bytes']), ('error', 1, 0, 0))
        self.refusal = None; self.required(); result = self.pair()
        text = json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
        report = next(body for op, body, _ in self.calls if op == 'recall-observation')
        self.assertEqual(report['injected_bytes'], len(text.encode()))
        self.configure(recall_observations=False); self.pair()
        self.assertNotIn('recall-observation', [op for op, _, _ in self.calls])

    def test_admission_privacy_disabled_account_and_invalid_ses_identity(self):
        for label in ('invalid', 'ses_bad-label', '', None, 'ses_bad\nlabel'):
            with self.subTest(label=label):
                self.event('UserPromptSubmit', session_id=label)
                self.assertEqual(self.pair().returncode, 1)
                self.assertEqual(self.calls, []); self.assertEqual(self.files(), {})
        self.event('UserPromptSubmit')
        marker = self.f.root/'.cairn-no-memory'; marker.touch()
        self.assertEqual(self.pair().stdout, b'{}\n'); self.assertEqual(self.calls, [])
        marker.unlink(); original = self.f.env
        for variable in ('CAIRN_LIFECYCLE_DISABLED', 'CAIRN_LIFECYCLE_CHILD'):
            self.f.env = lambda: dict(original(), **{variable:'1'})
            self.assertEqual(self.pair().stdout, b'{}\n'); self.assertEqual(self.calls, [])
        self.f.env = lambda: dict(original(), CODEX_HOME=str(self.root/'foreign-codex'), CLAUDE_CONFIG_DIR=str(self.root/'foreign-claude'))
        self.assertEqual(self.pair().returncode, 0)
        self.assertIn('index', [op for op, _, _ in self.calls])

    def test_keyset_checks_reject_missing_measurements_and_request_tuples(self):
        self.pair()
        for field in ('state', 'request', 'tuple'):
            changed = copy.deepcopy(self.last_raw_pair)
            if field == 'state':
                item = changed[1]['files'][self.state_key()+'.json']; body = json.loads(item['raw'])
                del body['last_recall']['at']; item['raw'] = ordinary.encoded(body).decode()
            elif field == 'request':
                request = next(body for op, body, _ in changed[1]['calls'] if op == 'recall-observation')
                del request['elapsed_ms']
            else: changed[1]['calls'][0] = changed[1]['calls'][0][:2]
            with self.subTest(field=field), self.assertRaises(AssertionError): self.validate_keysets(changed)

    def refused(self, launch=None):
        self.calls.clear(); before = self.state_files()
        result = (launch or self.go)()
        print('AMBIENT_PREFLIGHT_REFUSAL='+json.dumps(dict(event=self.f.event['hook_event_name'],
              exit=result.returncode, stdout=result.stdout.decode(), stderr=result.stderr.decode())), flush=True)
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.assertEqual(result.stderr, b'Cairn lifecycle diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n')
        self.assertEqual(self.calls, []); self.assertEqual(self.state_files(), before)

    def test_provider_other_harness_modes_tools_capture_and_bound_refuse(self):
        for fallback in (True, 'provider-selected', 1):
            self.configure(semantic_fallback=fallback); self.refused()
        self.selection()
        for harness in ('claude', 'hermes', 'agy', 'codex'):
            self.configure(harness=harness); self.refused()
        self.configure(harness='opencode')
        for mode in ('unsupported', None, False):
            self.configure(recall_mode=mode); self.refused()
        self.selection()
        for name in ('PreToolUse', 'PostToolUse', 'PostToolUseFailure', 'PreCompact', 'SessionEnd', 'Stop'):
            self.event(name); self.refused()
        self.event('SessionStart')
        for path in (self.f.memory_path, self.f.coord_path):
            old = path.read_bytes(); config = json.loads(old); config['inbox_recall_binding'] = str(self.f.binding)
            self.overwrite(path, ordinary.encoded(config)); self.repin()
            try: self.refused()
            finally: self.overwrite(path, old); self.repin()
        self.seed(dict(opaque=['unchanged', 900719925474099312345]))
        self.selection_overrides = {'hook-timeout':'150'}; self.refused()

    def test_tampered_receipt_config_image_closure_launcher_refuse(self):
        for path in (self.f.receipt_path, self.f.memory_path, self.f.coord_path,
                     self.f.binding, self.f.binary, Path(self.f.receipt['snapshot_root'])/'memory.py'):
            old = path.read_bytes(); self.overwrite(path, old+b' ')
            try: self.refused()
            finally: self.overwrite(path, old)
        launcher = self.go_launcher(); old = launcher.read_bytes(); self.overwrite(launcher, old+b'# changed\n')
        try:
            self.refused(lambda: subprocess.run(self.go_vector()+['--'], input=self.f.raw,
                         cwd=self.f.root, env=self.f.env(), capture_output=True, timeout=18))
        finally: self.overwrite(launcher, old)

    def test_checked_agent_tools_tool_cell_stays_reached(self):
        self.configure(recall_mode='agent_tools')
        (self.f.root/'alpha.py').write_text('fixture\n')
        self.event('PostToolUse', tool_name='Read', tool_input=dict(file_path='alpha.py'), tool_response={})
        result = self.pair()
        self.assertEqual((result.returncode, result.stdout), (0, b'{}\n'))
        self.assertEqual(self.calls, [])
        self.assertIn('alpha.py', self.state()[self.state_key()+'.json']['hints']['files'])

    def test_search_timeout_and_original_thirteen_second_stdin_deadline(self):
        self.index_delay = 6; result = self.pair()
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.assertIn(b'command timed out', result.stderr)
        self.index_delay = 0
        shutil.rmtree(self.f.memory['state_dir']); self.calls.clear()
        ordinary.CheckedOrdinaryTests.test_direct_entry_deadline_covers_waiting_for_original_stdin(self)

    def test_ambient_eleven_second_cap_bounds_multiple_optional_reads(self):
        self.index_delay = 4
        self.optional_note('Current lifecycle recall implementation. Delayed source.')
        template = self.optional.pop()
        for index in range(6):
            item = copy.deepcopy(template)
            item['entry']['record_id'] = str(uuid.UUID(int=index+20, version=4))
            self.optional.append(item)
        self.expand_transform = lambda request, response: time.sleep(4.5)
        started = time.monotonic(); result = self.pair(); elapsed = time.monotonic()-started
        self.assertEqual(result.returncode, 0)
        self.assertGreaterEqual(elapsed, 20)
        self.assertLess(elapsed, 27)
        self.assertEqual(self.state()[self.state_key()+'.json']['last_recall']['rejected'], {'unavailable':6})
        self.assertEqual(sum(op == 'expand' for op, _, _ in self.calls), 4)


if __name__ == '__main__':
    unittest.main()
