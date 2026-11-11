"""Whole Source/Go capture processes with a local selector and Unix API.

Only the external provider and service are fixtures. Both frontends perform
their own discovery, subprocess exchange, selection validation and state CAS.
No provider credentials or real provider route are used by this fixture.
"""
import base64
import copy
import fcntl
import http.server
import json
import os
from pathlib import Path
import shutil
import socketserver
import subprocess
import tempfile
import threading
import unittest
import uuid

import test_f02_start as start_fixture
import test_f03_capture_boundary as boundary_fixture
from test_f04_activation import Fixture, ROOT, SESSION, encoded, scalar_tree, sha, write, module


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class CaptureSelectionTests(unittest.TestCase):
    setUpClass = classmethod(start_fixture.F02StartTests.setUpClass.__func__)
    tearDownClass = classmethod(start_fixture.F02StartTests.tearDownClass.__func__)
    configure = boundary_fixture.CaptureBoundaryTests.configure
    state = boundary_fixture.CaptureBoundaryTests.state
    session_key = boundary_fixture.CaptureBoundaryTests.session_key
    close_server = boundary_fixture.CaptureBoundaryTests.close_server
    start_scheduler = boundary_fixture.CaptureBoundaryTests.start_scheduler
    finish_scheduler = boundary_fixture.CaptureBoundaryTests.finish_scheduler
    rollout = boundary_fixture.CaptureBoundaryTests.rollout

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.f = Fixture(self.root/'project', self.binary)
        self.f.memory.update(cairn=str(self.cli), socket=str(self.root/'api.sock'),
                             token_file=str(self.root/'token'), capture_observations=True,
                             claude=str(self.root/'selector'), model='fixture-model')
        self.f.memory.pop('inbox_recall_binding', None)
        write(self.root/'token', b'f03-synthetic-token', 0o600)
        self.configure('opencode', 'PreCompact', messages=[
            dict(role='user', text='Keep the postgres retention policy for this project.'),
            dict(role='assistant', text='Use the agreed retention period and preserve the checkpoint.')])
        self.selection = dict(checkpoint=None, workstream=None, memories=[])
        self.provider = {}
        self.records = []
        self.calls = []
        self.report_locks = []
        self.fail_write = None
        self.index_failure = None
        self.expand_failure = None
        self.expand_transform = None
        self.after_write = None
        self.write_count = 0
        self.index_count = 0
        script = '''#!/usr/bin/python3
import json,os,pathlib,sys,time
root=pathlib.Path(__file__).parent
spec=json.loads((root/'provider.json').read_text())
body=sys.stdin.buffer.read()
with (root/'provider.calls').open('a') as log:
 log.write(json.dumps(dict(argv=sys.argv[1:],body=body.decode(),cwd=os.getcwd(),entries=os.listdir(),pid=os.getpid(),child=os.environ.get('CAIRN_LIFECYCLE_CHILD'),claudecode=os.environ.get('CLAUDECODE')))+'\\n')
if spec.get('delay'):time.sleep(spec['delay'])
if spec.get('stderr'):sys.stderr.buffer.write(bytes.fromhex(spec['stderr']));sys.stderr.flush()
if spec.get('stdout_hex') is not None:sys.stdout.buffer.write(bytes.fromhex(spec['stdout_hex']))
else:sys.stdout.write(json.dumps(spec['result'],ensure_ascii=False,separators=(',',':')))
sys.exit(spec.get('exit',0))
'''
        write(self.root/'selector', script.encode(), 0o700)
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                operation = self.path.removeprefix('/v1/')
                owner.calls.append((operation, body, dict(self.headers)))
                failure = None
                if operation == 'version':
                    data = dict(preview_capabilities=dict(schema='cairn.preview-capabilities/1', entities_omitted=True))
                elif operation == 'index':
                    owner.index_count += 1
                    failure = owner.index_failure
                    query = body['query']
                    if query.startswith('"') and query.endswith('"'):
                        records = [r for r in owner.records if r['body'].startswith(query[1:-1]+'\n')]
                    else:
                        records = [r for r in owner.records if r['kind'] in body.get('kinds', [])]
                    receipt = str(uuid.uuid5(uuid.NAMESPACE_URL, 'fixture-index-'+str(owner.index_count)))
                    entries = [dict(record_id=r['record_id'], version=r['version'], kind=r['kind'],
                                    **{'class': r['class']}, summary=r['body'], body_sha256=sha(r['body'].encode())) for r in records]
                    data = dict(package=dict(receipt_id=receipt, nonce='', seal='blake3:'+'a'*64,
                                             semantic=dict(schema='cairn.semantic/17', mode='index', status='READY',
                                                           scope=body['scope'], purpose='context', destination=dict(name='hosted', allow_local=False),
                                                           query='sha256:fixture-query', available_tokens=32000, selected=[], omitted={}, index=entries)),
                                handles=[dict(record_id=r['record_id'], version=r['version'], handle=r['record_id']) for r in records],
                                expires_at='2030-01-01T00:00:00Z', credits_remaining=4, bytes_remaining=24000)
                elif operation == 'expand':
                    failure = owner.expand_failure
                    record = next(r for r in owner.records if r['record_id'] == body['handle'])
                    data = dict(selection=dict(record=copy.deepcopy(record)), credits_remaining=3, bytes_remaining=20000)
                    if owner.expand_transform:
                        owner.expand_transform(data['selection']['record'])
                elif operation in ('create', 'revise'):
                    owner.write_count += 1
                    if owner.write_count == owner.fail_write:
                        failure = 'CONFLICT'
                    identity = body.get('record_id') or str(uuid.uuid5(uuid.NAMESPACE_URL, body['request_id']))
                    data = dict(record_id=identity)
                    if not failure:
                        if operation == 'create':
                            owner.records.append(dict(record_id=identity, version=1, kind=body['draft']['kind'],
                                                      **{'class': 'A'}, body=body['draft']['body']))
                        else:
                            record = next(r for r in owner.records if r['record_id'] == identity)
                            record.update(body=body['body'], version=body['expected_version']+1)
                        if owner.after_write:
                            owner.after_write(owner)
                elif operation == 'capture-observation':
                    directory = Path(owner.f.memory['state_dir'])
                    locks = {}
                    for suffix in ('.capture.lock', '.lock'):
                        with (directory/(owner.session_key()+suffix)).open('a') as lock:
                            try:
                                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                                locks[suffix] = True
                            except BlockingIOError:
                                locks[suffix] = False
                    owner.report_locks.append(locks)
                    data = {}
                else:
                    self.send_error(400)
                    return
                self.send_response(422 if failure else 200)
                self.end_headers()
                response = dict(schema='cairn.response/1', ok=not failure, status=failure or 'OK')
                response.update(dict(message='fixture refusal') if failure else dict(data=data))
                self.wfile.write(encoded(response))

        self.server = Server(str(self.root/'api.sock'), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def source(self):
        root = self.root/'retained-source'
        loader = module('f03_selection_bundle', ROOT/'integrations/lifecycle/lifecycle_bundle.py')
        loader.stage_runtime(ROOT/'integrations/lifecycle', root)
        descriptor = loader.bundle_descriptor(root)
        manifest = dict(schema='cairn.inbox-recall-binding/2', enabled=False,
                        bridge=dict(path=str(root/'inbox_recall.py'), sha256=sha((root/'inbox_recall.py').read_bytes()), bundle=descriptor),
                        engine=dict(path=str(root/'memory.py'), sha256=sha((root/'memory.py').read_bytes()), bundle=descriptor))
        saved_config = self.f.memory_path.read_bytes()
        saved_binding = self.f.binding.read_bytes()
        write(self.f.memory_path, encoded(dict(self.f.memory, inbox_recall_binding=str(self.f.binding))))
        write(self.f.binding, encoded(manifest))
        try:
            return subprocess.run([os.sys.executable, '-B', str(root/'memory.py'), '--config', str(self.f.memory_path)],
                                  input=self.f.raw, cwd=self.f.root, env=dict(self.f.env(), CLAUDECODE='fixture-parent'), capture_output=True, timeout=45)
        finally:
            write(self.f.memory_path, saved_config)
            write(self.f.binding, saved_binding)

    def pair(self):
        directory = Path(self.f.memory['state_dir'])
        seed = {p.name: p.read_bytes() for p in directory.iterdir() if p.suffix != '.lock'} if directory.exists() else {}
        records = copy.deepcopy(self.records)
        spec = dict(result=dict(structured_output=self.selection))
        spec.update(self.provider)
        write(self.root/'provider.json', encoded(spec))
        observations = []
        for side in ('source', 'go'):
            self.records = copy.deepcopy(records)
            self.calls.clear()
            self.report_locks.clear()
            self.index_count = self.write_count = 0
            (self.root/'provider.calls').unlink(missing_ok=True)
            if side == 'go':
                if directory.exists():
                    for p in directory.iterdir():
                        if p.suffix != '.lock': p.unlink()
                if seed:
                    directory.mkdir(mode=0o700, exist_ok=True)
                    for name, raw in seed.items(): write(directory/name, raw)
            self.start_scheduler(side)
            if side == 'source':
                result = self.source()
            else:
                result = subprocess.run([str(self.binary), 'memory', '--config', str(self.f.memory_path)],
                                        input=self.f.raw, cwd=self.f.root, env=dict(self.f.env(), CLAUDECODE='fixture-parent'), capture_output=True, timeout=45)
            self.finish_scheduler()
            provider_calls = [json.loads(line) for line in (self.root/'provider.calls').read_text().splitlines()] if (self.root/'provider.calls').exists() else []
            for call in provider_calls:
                # Both frontends reap their direct selector before returning.
                self.assertFalse(Path('/proc', str(call.pop('pid'))).exists())
                self.assertEqual(call.pop('entries'), [])
                cwd = Path(call.pop('cwd'))
                self.assertTrue(cwd.name.startswith('cairn-selection-'))
                self.assertFalse(cwd.exists())
                self.assertEqual(call['child'], '1')
                self.assertIsNone(call['claudecode'])
                call['body'] = json.loads(call['body'])
            observations.append((result, self.state(), copy.deepcopy(self.calls), list(self.report_locks), copy.deepcopy(self.records), provider_calls))
        source, candidate = observations
        def output(raw):
            if self.f.memory['harness'] != 'hermes' or not raw:
                return raw
            body = json.loads(raw)
            last = body.get('cairn_status', {}).get('last_capture')
            if last is not None: self.assertGreaterEqual(last.pop('at'), 0)
            return scalar_tree(body)
        self.assertEqual((candidate[0].returncode, output(candidate[0].stdout), candidate[0].stderr),
                         (source[0].returncode, output(source[0].stdout), source[0].stderr))
        self.assertEqual(scalar_tree(candidate[1]), scalar_tree(source[1]))
        self.assertEqual(candidate[3:], source[3:])
        self.assertTrue(all(all(locks.values()) for locks in self.report_locks))
        self.assertEqual([c[0] for c in candidate[2]], [c[0] for c in source[2]])
        for (operation, actual, headers), (_, expected, source_headers) in zip(candidate[2], source[2]):
            actual, expected = copy.deepcopy(actual), copy.deepcopy(expected)
            for request in (actual, expected):
                if operation not in ('create', 'revise', 'version'):
                    identity = request.pop('request_id')
                    self.assertEqual(str(uuid.UUID(identity)), identity)
                if operation == 'capture-observation':
                    self.assertGreaterEqual(request.pop('duration_ms'), 0)
                    if 'selector_duration_ms' in request: self.assertGreaterEqual(request.pop('selector_duration_ms'), 0)
            self.assertEqual(actual, expected, operation)
            for declared, image in ((headers, sha(self.binary.read_bytes())), (source_headers, sha((ROOT/'integrations/lifecycle/memory.py').read_bytes()))):
                self.assertEqual(declared['Authorization'], 'Bearer f03-synthetic-token')
                raw = declared['Cairn-Client-Diagnostics']
                diagnostic = json.loads(base64.urlsafe_b64decode(raw+'='*(-len(raw)%4)))
                self.assertEqual(diagnostic['origin']['component'], 'lifecycle-memory')
                self.assertEqual(diagnostic['origin']['implementation_id'], image)
        self.provider_calls = candidate[5]
        return candidate[0]

    def note(self, title='Retention policy', body='Use thirty days for postgres retention.', record_id=None):
        return dict(record_id=record_id, kind='decision', title=title, body=body)

    def record(self, body, kind='decision'):
        record = dict(record_id=str(uuid.uuid4()), version=3, kind=kind, **{'class': 'A'}, body=body)
        self.records.append(record)
        return record

    def test_null_selection_commits_digest(self):
        self.assertEqual(self.pair().returncode, 0)
        state = self.state()[self.session_key()+'.json']
        self.assertEqual(state['last_capture'], dict(outcome='nothing_selected', records=[], selector_calls=1))
        self.assertEqual(len(state['captured_digest']), 64)
        self.assertEqual([c[0] for c in self.calls if c[0] != 'version'], ['index', 'index', 'index', 'capture-observation'])
        argv = self.provider_calls[0]['argv']
        self.assertEqual(argv[argv.index('--settings')+1], '{"disableAllHooks":true}')
        self.assertEqual(argv[argv.index('--mcp-config')+1], '{"mcpServers":{}}')

    def test_new_durable_then_checkpoint(self):
        self.selection.update(checkpoint='Retain the current postgres policy; next update the operator docs.',
                              workstream='postgres retention', memories=[self.note()])
        self.assertEqual(self.pair().returncode, 0)
        writes = [c for c in self.calls if c[0] in ('create', 'revise')]
        self.assertEqual([c[1]['draft']['kind'] for c in writes], ['decision', 'note'])
        state = self.state()[self.session_key()+'.json']
        self.assertEqual(state['workstream'], 'Handoff: project / postgres retention')
        self.assertEqual(state['last_capture']['records'], [r['record_id'] for r in self.records])

    def test_revise_selected_candidate(self):
        old = self.record('project: postgres retention policy\nUse fourteen days.')
        self.selection['memories'] = [self.note(body='project: postgres retention policy\nUse thirty days.', record_id=old['record_id'])]
        self.assertEqual(self.pair().returncode, 0)
        self.assertEqual([c[0] for c in self.calls].count('revise'), 1)
        excerpt = self.provider_calls[0]['body']
        self.assertEqual(excerpt['existing_memories'][0]['record_id'], old['record_id'])

    def test_equal_selected_body_is_noop(self):
        old = self.record('project: postgres retention policy\nUse thirty days.')
        self.selection['memories'] = [self.note(body=old['body'], record_id=old['record_id'])]
        self.assertEqual(self.pair().returncode, 0)
        self.assertFalse(any(c[0] in ('create', 'revise') for c in self.calls))
        self.assertEqual(self.calls[-1][1]['outcome'], 'nothing_selected')

    def test_invalid_late_note_prevents_all_writes(self):
        self.selection['memories'] = [self.note(), dict(record_id=None, kind='unknown', title='bad', body='bad')]
        self.assertNotEqual(self.pair().returncode, 0)
        self.assertFalse(any(c[0] in ('create', 'revise') for c in self.calls))
        self.assertEqual(self.state(), {})

    def test_invalid_selector_json(self):
        self.provider['stdout_hex'] = b'not json'.hex()
        self.assertNotEqual(self.pair().returncode, 0)
        self.assertEqual(self.calls[-1][1]['selector_outcome'], 'error')
        self.assertEqual(self.state(), {})

    def test_selector_reported_error(self):
        self.provider['result'] = dict(is_error=True, structured_output=self.selection)
        self.assertNotEqual(self.pair().returncode, 0)
        self.assertEqual(self.calls[-1][1]['selector_outcome'], 'error')

    def test_selector_nonzero(self):
        self.provider.update(exit=7, stdout_hex=b'{"status":"FIXTURE_REFUSED"}'.hex())
        self.assertNotEqual(self.pair().returncode, 0)
        self.assertEqual(self.state(), {})

    def test_selector_metrics_are_bounded_and_post_lock(self):
        self.provider['result'] = dict(structured_output=self.selection, modelUsage={'fixture-reported': {}},
                                      duration_ms=900, total_cost_usd=0.125,
                                      usage=dict(input_tokens=17, output_tokens=9, cache_creation_input_tokens=True,
                                                 cache_read_input_tokens=1 << 41))
        self.assertEqual(self.pair().returncode, 0)
        body = self.calls[-1][1]
        self.assertEqual((body['requested_model'], body['reported_model'], body['input_tokens'], body['output_tokens'], body['cost_usd']),
                         ('fixture-model', 'fixture-reported', 17, 9, 0.125))
        self.assertNotIn('cache_creation_input_tokens', body)
        self.assertNotIn('cache_read_input_tokens', body)

    def test_late_write_failure_keeps_confirmed_note_without_digest(self):
        self.selection['memories'] = [self.note(), self.note(title='Cleanup procedure', body='Keep the postgres cleanup procedure explicit.')]
        self.fail_write = 2
        self.assertNotEqual(self.pair().returncode, 0)
        self.assertEqual(len(self.records), 1)
        self.assertEqual(self.state(), {})
        self.assertEqual(self.calls[-1][1]['outcome'], 'write_error')

    def test_protocol_and_output_limits_preserve_retry_eligibility(self):
        for label, options in (
                ('array', dict(stdout_hex=b'[]'.hex())),
                ('trailing-value', dict(stdout_hex=b'{} {}'.hex())),
                ('invalid-utf8', dict(stdout_hex=b'\xff'.hex())),
                ('stdout-limit', dict(stdout_hex=(b'x' * ((1 << 20) + 1)).hex())),
                ('stderr-limit', dict(stderr=(b'x' * ((1 << 20) + 1)).hex()))):
            with self.subTest(label=label):
                self.provider = options
                self.assertNotEqual(self.pair().returncode, 0)
                self.assertEqual(self.state(), {})
                self.assertEqual(self.calls[-1][1]['selector_outcome'], 'error')
                self.assertFalse(any(c[0] in ('create', 'revise') for c in self.calls))

    def test_missing_selected_executable_is_not_capture_success(self):
        self.f.memory['claude'] = str(self.root/'absent-selector')
        self.f.publish_configuration()
        self.assertNotEqual(self.pair().returncode, 0)
        self.assertEqual(self.state(), {})
        self.assertEqual(self.provider_calls, [])
        self.assertEqual(self.calls[-1][1]['selector_outcome'], 'error')

    def test_selector_timeout_leaves_no_digest_or_running_child(self):
        self.provider['delay'] = 60
        self.assertNotEqual(self.pair().returncode, 0)
        self.assertEqual(self.state(), {})
        self.assertEqual(self.calls[-1][1]['selector_outcome'], 'timeout')

    def test_confirmed_partial_write_is_reconciled_on_retry(self):
        self.selection['memories'] = [self.note(), self.note(title='Cleanup procedure', body='Keep the postgres cleanup procedure explicit.')]
        self.fail_write = 2
        self.assertNotEqual(self.pair().returncode, 0)
        first = copy.deepcopy(self.records)
        self.assertEqual(len(first), 1)
        self.assertEqual(self.state(), {})
        self.fail_write = None
        self.assertEqual(self.pair().returncode, 0)
        self.assertEqual(len(self.records), 2)
        self.assertEqual(self.records[0], first[0])
        self.assertEqual([c[0] for c in self.calls].count('create'), 1)
        self.assertEqual(self.state()[self.session_key()+'.json']['last_capture']['outcome'], 'saved')

    def test_selected_write_remains_when_concurrent_capture_state_refuses_cas(self):
        self.selection['memories'] = [self.note()]
        def change(owner):
            path = Path(owner.f.memory['state_dir'])/(owner.session_key()+'.json')
            with path.with_suffix('.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                write(path, encoded(dict(capture_pending=dict(reason='concurrent'))))
        self.after_write = change
        result = self.pair()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'capture state changed before commit', result.stderr)
        self.assertEqual(len(self.records), 1)
        self.assertEqual(self.state()[self.session_key()+'.json'], dict(capture_pending=dict(reason='concurrent')))
        self.assertEqual(self.calls[-1][1]['outcome'], 'state_commit_error')

    def test_selected_write_merges_independent_recall_and_workstream(self):
        self.selection['memories'] = [self.note()]
        def change(owner):
            path = Path(owner.f.memory['state_dir'])/(owner.session_key()+'.json')
            with path.with_suffix('.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                write(path, encoded(dict(workstream='concurrent recall workstream', seen={'record': 7})))
        self.after_write = change
        self.assertEqual(self.pair().returncode, 0)
        state = self.state()[self.session_key()+'.json']
        self.assertEqual(state['workstream'], 'concurrent recall workstream')
        self.assertEqual(state['seen'], {'record': 7})
        self.assertEqual(state['last_capture']['outcome'], 'saved')

    def test_codex_substantive_capture_commits_original_snapshot_then_duplicate(self):
        messages = [dict(role='user' if n % 2 == 0 else 'assistant', text='Postgres retention checkpoint '+str(n)) for n in range(6)]
        path = self.rollout(messages)
        self.configure('codex', 'PreCompact', transcript_path=str(path))
        self.selection.update(checkpoint='Continue postgres retention work.', workstream='postgres retention')
        self.assertEqual(self.pair().returncode, 0)
        state = self.state()[SESSION+'.json']
        self.assertEqual(state['codex_capture_marker'], dict(path=str(path), offset=path.stat().st_size))
        self.assertEqual(state['captured_messages'], 6)
        self.assertEqual(self.pair().returncode, 0)
        self.assertEqual(self.state()[SESSION+'.json']['last_capture']['outcome'], 'unchanged')
        self.assertEqual(self.provider_calls, [])

    def test_exact_prior_handoff_and_durable_candidates_reach_selector(self):
        prior = self.record('Handoff: project / Claude session '+self.session_key()+'\nPostgres retention policy in progress.', 'note')
        handoff = self.record('Handoff: project / postgres retention\nUse the current policy.', 'note')
        durable = self.record('project: postgres retention policy\nUse thirty days.')
        self.assertEqual(self.pair().returncode, 0)
        excerpt = self.provider_calls[0]['body']
        self.assertEqual(excerpt['previous_checkpoint'], prior['body'])
        self.assertEqual(excerpt['existing_handoffs'], [dict(topic='postgres retention', body=handoff['body'])])
        self.assertEqual(excerpt['existing_memories'], [dict(record_id=durable['record_id'], kind='decision', body=durable['body'])])

    def test_optional_candidate_budget_refusal_still_allows_null_selection(self):
        self.record('project: postgres retention policy\nUse thirty days.')
        self.expand_failure = 'BUDGET_REFUSED'
        self.assertEqual(self.pair().returncode, 0)
        self.assertEqual(self.provider_calls[0]['body']['existing_memories'], [])
        self.assertEqual(self.state()[self.session_key()+'.json']['last_capture']['outcome'], 'nothing_selected')

    def test_search_failure_never_calls_selector_or_commits_state(self):
        self.index_failure = 'POLICY_UNENFORCEABLE'
        self.assertNotEqual(self.pair().returncode, 0)
        self.assertEqual(self.provider_calls, [])
        self.assertEqual(self.state(), {})
        self.assertNotIn('selector_outcome', self.calls[-1][1])

    def test_invalid_lifecycle_model_prevents_provider_spawn(self):
        for key in ('preview_model', 'recall_model'):
            with self.subTest(key=key):
                self.f.memory[key] = 'has whitespace'
                self.f.publish_configuration()
                self.assertNotEqual(self.pair().returncode, 0)
                self.assertEqual(self.provider_calls, [])
                self.assertEqual(self.state(), {})
                self.assertNotIn('selector_outcome', self.calls[-1][1])
                self.f.memory.pop(key)

    def test_non_ascii_capture_writes_for_claude_and_hermes(self):
        for harness in ('claude', 'hermes'):
            with self.subTest(harness=harness):
                self.records = []
                self.configure(harness, 'PreCompact', messages=[dict(role='user', text='Keep the 日本語 retention decision 😀.'),
                                                               dict(role='assistant', text='Record the agreed policy.')])
                self.selection.update(checkpoint='Next: 更新 the retention docs.', workstream='retention 日本語',
                                      memories=[self.note(title='日本語 retention', body='Preserve the decision 😀 and U+2028 separator\u2028exactly.')])
                self.assertEqual(self.pair().returncode, 0)
                self.assertEqual([r['kind'] for r in self.records], ['decision', 'note'])
                self.assertEqual(self.state()[self.session_key()+'.json']['last_capture']['outcome'], 'saved')

    def test_malformed_pulled_versions_refuse_before_provider_or_mutation(self):
        record = self.record('project: postgres retention policy\nUse thirty days.')
        self.selection['memories'] = [self.note(body=record['body']+'\nUpdated.', record_id=record['record_id'])]
        # Owner class (b): hostile/malformed service data is a stable Go refusal,
        # rather than silently converting an invalid version to zero. This is
        # not asserted to match Python's exception order or class.
        for version in ('3', True, 1 << 100):
            with self.subTest(version=version):
                self.expand_transform = lambda r: r.update(version=version)
                self.calls.clear()
                self.index_count = self.write_count = 0
                write(self.root/'provider.json', encoded(dict(result=dict(structured_output=self.selection))))
                result = subprocess.run([str(self.binary), 'memory', '--config', str(self.f.memory_path)], input=self.f.raw,
                                        cwd=self.f.root, env=self.f.env(), capture_output=True, timeout=45)
                self.assertEqual(result.returncode, 1)
                self.assertIn(b'outside the supported JSON and Unicode domain', result.stderr)
                self.assertFalse((self.root/'provider.calls').exists())
                self.assertEqual(self.state(), {})
                self.assertFalse(any(c[0] in ('create', 'revise') for c in self.calls))


if __name__ == '__main__':
    unittest.main()
