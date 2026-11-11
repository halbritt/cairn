"""End-to-end /3 Coordination caller, post-lock report scheduling, and paired Go consumer tests."""
import fcntl
import http.server
import json
import os
from pathlib import Path
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_f04_activation import Fixture, ROOT, SESSION, TURN, encoded, module, sha, write

AGENT = '33333333-3333-4333-8333-333333333333'
EXECUTION = '44444444-4444-4444-8444-444444444444'
DELIVERY = '55555555-5555-4555-8555-555555555555'
ATTEMPT = '66666666-6666-4666-8666-666666666666'
RECORD = '77777777-7777-4777-8777-777777777777'


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def consumer_build_root():
    if (ROOT / 'internal/lifecycle/inboxipc/run.go').exists():
        return ROOT
    raise RuntimeError('Go inboxipc consumer is required in this checkout')


class F02InboxCallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.build.name) / 'cairn-lifecycle'
        cls.cli = Path(cls.build.name) / 'cairn'
        build_root = consumer_build_root()
        for target, pkg in ((cls.binary, './cmd/cairn-lifecycle'), (cls.cli, './cmd/cairn')):
            subprocess.run(['go', 'build', '-buildvcs=false', '-o', str(target), pkg], cwd=build_root, check=True)
        cls.coord_mod = module('f02_caller_coord', ROOT / 'integrations/lifecycle/coordination.py')

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.calls = []
        self.lock_checks = []
        self.body = 'Implement the /3 Coordination caller and post-lock report scheduling. 日本語'
        self.selected = []
        self.optional = []
        self.destination = dict(name='hosted', allow_local=False)
        self.index_refusals = []
        self.history_refusal = None
        self.history_change = None
        self.renew_change = None
        self.renew_refusal = None
        self.meter_refusal = None
        self.release_calls = []
        self._start_server()

    def _start_server(self):
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(inner):
                request = json.loads(inner.rfile.read(int(inner.headers['Content-Length'])))
                operation = inner.path.removeprefix('/v1/')
                owner.calls.append((operation, request, dict(inner.headers)))
                refusal = None
                if inner.headers.get('Authorization') != 'Bearer inbox-fixture-token':
                    inner.send_error(401)
                    return
                if operation == 'version':
                    data = dict(preview_capabilities=dict(schema='cairn.preview-capabilities/1', entities_omitted=True))
                elif operation == 'index':
                    if owner.index_refusals:
                        refusal = owner.index_refusals.pop(0)
                    optional = [] if request.get('kinds') else owner.optional
                    data = dict(
                        package=dict(
                            receipt_id=SESSION,
                            nonce='',
                            seal='blake3:' + 'a' * 64,
                            semantic=dict(
                                schema='cairn.semantic/17',
                                mode='index',
                                status='READY',
                                scope=request['scope'],
                                purpose='context',
                                destination=owner.destination,
                                query='sha256:fixture-query',
                                available_tokens=32000,
                                selected=owner.selected,
                                omitted={},
                                index=[n['entry'] for n in optional],
                            ),
                        ),
                        handles=[
                            dict(record_id=n['entry']['record_id'], version=n['entry']['version'], handle='optional-' + str(i))
                            for i, n in enumerate(optional)
                        ],
                        expires_at='2030-01-01T00:00:00Z',
                        credits_remaining=4,
                        bytes_remaining=24000,
                    )
                elif operation == 'history':
                    refusal = owner.history_refusal
                    data = dict(
                        historical=True,
                        record_id=RECORD,
                        versions=[dict(
                            version=1,
                            payload_available=True,
                            body=owner.body,
                            body_bytes=len(owner.body.encode()),
                            body_sha256=sha(owner.body.encode()),
                        )],
                    )
                    if owner.history_change:
                        owner.history_change(data)
                elif operation == 'event-renew':
                    refusal = owner.renew_refusal
                    data = dict(owner.delivery_payload(), state='leased')
                    if owner.renew_change:
                        owner.renew_change(data)
                elif operation == 'recall-observation':
                    owner.lock_checks.append(owner._locks_free())
                    data = {}
                    refusal = owner.meter_refusal
                else:
                    inner.send_error(400)
                    return
                inner.send_response(422 if refusal else 200)
                inner.end_headers()
                inner.wfile.write(encoded(dict(
                    schema='cairn.response/1',
                    ok=not bool(refusal),
                    status=refusal or 'OK',
                    **({'message': 'fixture refusal'} if refusal else {'data': data}),
                )))

        self.server = Server(str(self.root / 'api.sock'), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._close_server)

    def _close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def delivery_payload(self):
        return dict(
            delivery_id=DELIVERY,
            lease_id=ATTEMPT,
            state='leased',
            event=dict(
                event_id=TURN,
                kind=self.kind,
                ref=dict(record_id=RECORD, version=1),
                **{'from': 'agent/' + AGENT},
            ),
        )

    def coord_state_path(self):
        return self.coord_mod.state_path(self.f.coord, SESSION)

    def _locks_free(self):
        coord_lock = self.coord_state_path().with_suffix('.json.lock')
        memory_lock = Path(self.f.memory['state_dir']) / (SESSION + '.lock')
        result = {}
        for name, target in (('coordination', coord_lock), ('memory', memory_lock)):
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with target.open('a') as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(handle, fcntl.LOCK_UN)
                    result[name] = True
                except BlockingIOError:
                    result[name] = False
        return result

    def run_public(self, role='coordination', timeout=None):
        env = dict(self.f.env(), HOME=str(self.root))
        return subprocess.run(
            [str(self.f.launcher(role, timeout))],
            input=self.f.raw, capture_output=True, cwd=self.f.root, env=env, timeout=18,
        )

    def configure(self, harness='codex', kind='request', trace=False, meter=False, compact=False):
        if hasattr(self, 'f') and self.f.root.exists():
            shutil.rmtree(self.f.root)
        self.kind = kind
        self.f = Fixture(self.root / 'project', self.binary, harness=harness, compact=compact)
        for cfg in (self.f.memory, self.f.coord):
            cfg.update(socket=str(self.root / 'api.sock'), token_file=str(self.root / 'token'))
        self.f.memory.update(inbox_recall_trace=trace, recall_observations=meter)
        if compact:
            self.f.memory['context_lifecycle'] = 'compact/1'
        write(self.root / 'token', b'inbox-fixture-token', 0o600)
        self.f.publish_configuration()
        start_event = dict(self.f.event, hook_event_name='SessionStart', prompt='', host_pid=os.getpid())
        self.f.raw = encoded(start_event)
        started = self.run_public('coordination')
        self.assertEqual((started.returncode, started.stderr), (0, b''), started.stdout)
        self.f.replies({
            'event-renew': [{
                'ok': True,
                'status': 'OK',
                'data': self.delivery_payload(),
            }],
            'session-inbox-claim': [{
                'ok': True,
                'status': 'OK',
                'claim_identity': True,
                'data': {
                    'attempt': {
                        'attempt_id': ATTEMPT,
                        'session': {'agent_id': AGENT, 'execution_id': EXECUTION},
                        'delivery': self.delivery_payload(),
                        'native_turn_id': TURN if harness == 'codex' else 'claude-channel:fixture-prompt',
                    },
                },
            }],
            'session-inbox-reconcile': [
                {'ok': False, 'status': 'DELIVERY_ACTIVE'},
                {
                    'ok': True,
                    'status': 'OK',
                    'data': {'state': 'failed', 'delivery_id': DELIVERY},
                },
            ],
            'history': [{
                'ok': True,
                'status': 'OK',
                'data': {
                    'historical': True,
                    'record_id': RECORD,
                    'versions': [{
                        'version': 1,
                        'payload_available': True,
                        'body': self.body,
                        'body_bytes': len(self.body.encode()),
                        'body_sha256': sha(self.body.encode()),
                    }],
                },
            }],
        })
        state_file = self.coord_state_path()
        state = json.loads(state_file.read_text())
        state['idle_wake'] = dict(
            transport='claude-channel' if harness == 'claude' else 'codex-queue',
            status='submitted',
            session={'agent_id': AGENT, 'execution_id': EXECUTION},
            native_id=SESSION,
            delivery_id=DELIVERY,
            event_id=TURN,
            request_id=ATTEMPT,
            attempted_at=time.time(),
        )
        write(state_file, encoded(state))
        self.f.event['host_pid'] = os.getpid()
        if harness == 'claude':
            self.f.event['prompt'] = (
                f'<channel source="cairn-events" native_session_id="{SESSION}" '
                f'agent_id="{AGENT}" execution_id="{EXECUTION}" delivery_id="{DELIVERY}">\n'
                + self.f.wake() + '\n</channel>'
            )
        else:
            self.f.event['prompt'] = self.f.wake()
        self.f.raw = encoded(self.f.event)
        self.calls.clear()
        self.lock_checks.clear()

    def test_public_coordination_wake_consumes_go_recall_and_flushes_reports_post_lock(self):
        for harness in ('codex', 'claude'):
            with self.subTest(harness=harness):
                self.configure(harness=harness, kind='request', trace=True, meter=True)
                trace_log = self.f.root / ('strace-' + harness)
                result = subprocess.run(
                    ['/usr/bin/strace', '-f', '-qq', '-o', str(trace_log), '-s', '1024',
                     '-e', 'trace=execve', str(self.f.launcher('coordination'))],
                    input=self.f.raw, capture_output=True, cwd=self.f.root,
                    env=dict(self.f.env(), HOME=str(self.root)), timeout=20,
                )
                self.assertEqual((result.returncode, result.stderr), (0, b''), result.stdout)
                payload = json.loads(result.stdout)
                context_text = payload['hookSpecificOutput']['additionalContext']
                self.assertIn(self.body, context_text)
                self.assertIn('"schema":"cairn.inbox-recall/1"', context_text)
                self.assertNotIn('search', [c['operation'] for c in self.f.api_calls()])
                self.assertEqual(
                    [op for op, _, _ in self.calls],
                    ['version', 'index', 'history', 'version', 'index', 'event-renew', 'recall-observation'],
                )
                self.assertEqual(self.lock_checks, [{'coordination': True, 'memory': True}])
                traces = list((Path(self.f.memory['state_dir']) / 'inbox-recall-traces').glob('*.json'))
                self.assertEqual(len(traces), 1)
                trace_doc = json.loads(traces[0].read_text())
                self.assertEqual(trace_doc['task_source']['body_sha256'], sha(self.body.encode()))
                rows = [r for r in trace_log.read_text().splitlines() if 'execve("/proc/self/fd/' in r]
                self.assertEqual(len(rows), 3, '\n'.join(rows))
                self.assertIn('"activation"', rows[0])
                self.assertIn('"recall"', rows[1])
                self.assertIn('"report"', rows[2])
                recall_deadline = rows[1].split('"--deadline", "')[1].split('"')[0]
                report_deadline = rows[2].split('"--deadline", "')[1].split('"')[0]
                self.assertEqual(recall_deadline, report_deadline)

    def test_refusal_after_required_search_settles_claude_under_lock_then_flushes_trace_post_lock(self):
        self.configure(harness='claude', kind='request', trace=True, meter=True)
        self.history_change = lambda d: d['versions'][0].update(body_sha256='0' * 64)
        result = self.run_public('coordination')
        self.assertEqual((result.returncode, result.stdout), (2, b''))
        self.assertIn(b'bound inbox recall unavailable; no source or memory grant delivered', result.stderr)
        ops = [c['operation'] for c in self.f.api_calls()]
        self.assertIn('session-inbox-reconcile', ops)
        self.assertNotIn('search', ops)
        state = json.loads(self.coord_state_path().read_text())
        diag = state['wake_refusal_diagnostic']
        self.assertEqual(diag['phase'], 'bridge_after_claim')
        self.assertEqual(diag['settlement'], 'turn_ended')
        self.assertEqual(diag['refusal_stage'], 'task_source')
        self.assertEqual(diag['refusal_reason'], 'source_version')
        traces = list((Path(self.f.memory['state_dir']) / 'inbox-recall-traces').glob('*.json'))
        self.assertEqual(len(traces), 1)
        trace_doc = json.loads(traces[0].read_text())
        self.assertEqual(trace_doc['status'], 'error')
        self.assertEqual(trace_doc['returned_context_bytes'], 0)
        self.assertEqual(self.lock_checks, [{'coordination': True, 'memory': True}])

    def test_required_search_failure_preserves_recall_failure_metadata_and_settles_claude(self):
        self.configure(harness='claude', kind='request', trace=False, meter=False)
        self.index_refusals = ['AUTHORITY_DENIED']
        result = self.run_public('coordination')
        self.assertEqual((result.returncode, result.stdout), (2, b''))
        state = json.loads(self.coord_state_path().read_text())
        diag = state['wake_refusal_diagnostic']
        self.assertEqual(diag['settlement'], 'turn_ended')
        self.assertEqual(diag['error_class'], 'ValueError')
        self.assertEqual(diag['recall_phase'], 'required_search')
        self.assertEqual(diag['recall_operation'], 'search')
        self.assertEqual(diag['recall_attempts'], 1)
        self.assertEqual(diag['recall_max_attempts'], 2)
        self.assertGreaterEqual(diag['deadline_remaining_ms'], 0)

    def test_stale_lease_and_lock_contention_preserve_exception_families(self):
        self.configure(harness='claude', kind='request', trace=False, meter=False)
        self.renew_change = lambda d: d.update(state='expired')
        result = self.run_public('coordination')
        self.assertEqual((result.returncode, result.stdout), (2, b''))
        state = json.loads(self.coord_state_path().read_text())
        diag = state['wake_refusal_diagnostic']
        self.assertEqual(diag['error_class'], 'CoordinationError')
        self.assertEqual(diag['refusal_stage'], 'lease_revalidation')
        self.assertEqual(diag['refusal_reason'], 'unknown')

        self.configure(harness='codex', kind='request', trace=False, meter=False)
        self.renew_change = None
        activated = self.f.activation('coordination')
        self.assertEqual((activated.returncode, activated.stdout, activated.stderr), (0, b'{"enabled":true}\n', b''))
        compat_mod = module('f02_caller_compat', ROOT / 'integrations/lifecycle/lifecycle_compatibility.py')
        import types
        args = types.SimpleNamespace(
            receipt=str(self.f.receipt_path), receipt_sha256=self.f.receipt_sha,
            role='coordination', entrypoint='coordination-hook.py',
            config=str(self.f.coord_path), hook_timeout='15',
            launch_root=str(self.f.launch_root), shell='/bin/sh',
            tail=['hook', '--config', str(self.f.coord_path)],
        )
        vector = compat_mod.launch_vector(
            sys.executable, self.f.receipt_path, self.f.receipt_sha, 'coordination',
            'coordination-hook.py', self.f.coord_path, 15, self.f.launch_root,
        )
        binary_fd = os.open(str(self.f.binary), os.O_RDONLY)
        self.addCleanup(os.close, binary_fd)
        sources = compat_mod.guarded_sources(ROOT / 'integrations/lifecycle')
        ctx = compat_mod.InvocationContext(
            args, self.f.receipt, sources, binary_fd, time.monotonic() + 15, vector,
            lambda p, limit: (Path(p).read_bytes(), os.stat(p)),
        )
        loader = types.ModuleType('cairn_checked_loader')
        loader.__file__ = str(Path(self.f.receipt['snapshot_root']) / 'lifecycle_bundle.py')
        loader._CAIRN_COMPATIBILITY_CONTEXT = ctx
        exec(compile(sources['lifecycle_bundle.py'], loader.__file__, 'exec'), loader.__dict__)
        bundle = loader.Bundle(
            self.f.receipt['snapshot_root'],
            sources={k: v for k, v in sources.items() if k != 'lifecycle_compatibility.py'},
        )
        bundle.compatibility_context = ctx
        ctx.bundle = bundle
        ctx.enabled = True
        ctx.checked_binding()
        frame_state = dict(
            agent=dict(agent_id=AGENT, execution_id=EXECUTION),
            workspace=str(self.f.root),
            inbox_attempt=dict(
                attempt_id=ATTEMPT,
                session=dict(agent_id=AGENT, execution_id=EXECUTION),
                native_turn_id=TURN,
                delivery=self.delivery_payload(),
            ),
        )
        memory_lock = Path(self.f.memory['state_dir']) / (SESSION + '.lock')
        with memory_lock.open('a') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError) as cm:
                ctx.inbox_recall_output(
                    self.f.coord, self.f.event,
                    dict(event='UserPromptSubmit', native_turn_id=TURN),
                    frame_state, 'Cairn fixture control',
                )
        self.assertEqual(
            cm.exception.cairn_inbox_refusal,
            {'refusal_stage': 'inbox_ledger', 'refusal_reason': 'unknown'},
        )
        for bad in (b'', b'not-json\n', b'{"ok":true}\n', b'{"ok":false,"error":{"class":"UnknownError","message":"x","cairn_inbox_refusal":{"refusal_stage":"bridge","refusal_reason":"unknown"}}}\n'):
            with self.assertRaises(subprocess.SubprocessError):
                if bad == b'{"ok":true}\n':
                    ctx.child = lambda *a, **kw: (bad, b'', 0)
                    ctx.inbox_recall_output(
                        self.f.coord, self.f.event,
                        dict(event='UserPromptSubmit', native_turn_id=TURN),
                        frame_state, 'Cairn fixture control',
                    )
                else:
                    ctx.inbox_recall_error(json.loads(bad)['error'] if bad.startswith(b'{') else None)

    def test_budget_refusal_and_report_failure_isolation(self):
        self.selected = [dict(
            record=dict(record_id=RECORD, version=1, body='M' * 2000),
            mandatory=True, reason='fixture', evidence=[], authority=[],
        )]
        self.configure(harness='claude', kind='request', trace=True, meter=True)
        self.f.memory['context_bytes'] = 1000
        self.f.publish_configuration()
        result = self.run_public('coordination')
        self.assertEqual((result.returncode, result.stdout), (2, b''))
        state = json.loads(self.coord_state_path().read_text())
        diag = state['wake_refusal_diagnostic']
        self.assertEqual(diag['refusal_stage'], 'required_render')
        self.assertEqual(diag['refusal_reason'], 'required_context')
        self.assertEqual(diag['configured_budget_bytes'], 1000)
        self.assertEqual(diag['budget_bytes'], 1000)

        self.selected = []
        self.configure(harness='codex', kind='notice', trace=True, meter=True)
        self.meter_refusal = 'SERVICE_UNAVAILABLE'
        ok_result = self.run_public('coordination')
        self.assertEqual((ok_result.returncode, ok_result.stderr), (0, b''), ok_result.stdout)
        self.assertEqual(self.lock_checks, [{'coordination': True, 'memory': True}])


if __name__ == '__main__':
    unittest.main()
