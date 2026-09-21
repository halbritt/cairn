"""Unit tests for OpenCode native queue client adapter against real Unix domain sockets."""
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from integrations.lifecycle import coordination, opencode_queue

ROOT = Path(__file__).resolve().parent.parent


class OpenCodeQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / 'opencode-bridge.sock')
        self.process = dict(
            pid=os.getpid(),
            start=int(Path(f'/proc/{os.getpid()}/stat').read_text().rsplit(')', 1)[1].split()[19]),
            boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        )
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(self.path)
        self.listener.listen()
        self.listener.settimeout(3)
        self.addCleanup(self.listener.close)
        self.requests = []
        self.errors = []
        self.threads = []

    def serve(self, behavior='confirm', busy=False, turn_match=True):
        """Serve one connection with specified mock behavior:
        behavior: 'confirm', 'busy', 'not_found', 'drop', 'mismatch', 'abort_confirm', 'abort_refuse'
        """
        def run():
            try:
                conn, _ = self.listener.accept()
                with conn:
                    conn.settimeout(3)
                    file_obj = conn.makefile('r', encoding='utf-8')
                    line = file_obj.readline()
                    if not line:
                        return
                    req = json.loads(line)
                    self.requests.append(req)
                    req_id = req.get('id')
                    method = req.get('method')
                    params = req.get('params', {})

                    if behavior == 'drop':
                        return  # Connection terminates abruptly after receiving request
                    if isinstance(behavior, dict):
                        conn.sendall((json.dumps(behavior) + '\n').encode('utf-8'))
                        return

                    if method == 'session/prompt_async':
                        if behavior == 'not_found':
                            resp = {'id': req_id, 'error': {'code': -32002, 'message': 'SESSION_NOT_FOUND: session does not exist'}}
                        elif behavior == 'mismatch':
                            resp = {'id': req_id, 'error': {'code': -32001, 'message': 'SESSION_MISMATCH: session changed'}}
                        elif behavior == 'busy' or busy:
                            resp = {'id': req_id, 'error': {'code': -32600, 'message': 'BUSY: target session is currently busy; refuse to merge input into active generation'}}
                        else:
                            resp = {
                                'id': req_id,
                                'result': {
                                    'queued': True,
                                    'queued_id': params.get('client_id'),
                                    'session_id': params.get('session_id'),
                                    'started': True
                                }
                            }
                        conn.sendall((json.dumps(resp) + '\n').encode('utf-8'))
                    elif method == 'session/abort':
                        resp = {'id': req_id, 'error': {'code': -32004, 'message': 'UNSUPPORTED_CONTROL: session abort disabled; atomic turn-fencing not yet supported upstream'}}
                        conn.sendall((json.dumps(resp) + '\n').encode('utf-8'))
            except Exception as exc:
                self.errors.append(exc)

        worker = threading.Thread(target=run)
        self.threads.append(worker)
        worker.start()

    def join_workers(self):
        for worker in self.threads:
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(self.errors, [])

    def test_unchanged_composer_direct_submission(self):
        """1. Submission calls promptAsync without touching TUI composer."""
        self.serve(behavior='confirm', busy=False)
        queued_id, started = opencode_queue.enqueue(
            self.path, self.process, 'ses_native123', 'cairn wakeup text', 'delivery_one'
        )
        self.join_workers()
        self.assertEqual(queued_id, 'delivery_one')
        self.assertTrue(started)
        self.assertEqual(len(self.requests), 1)
        req = self.requests[0]
        self.assertEqual(req['method'], 'session/prompt_async')
        self.assertEqual(req['params']['session_id'], 'ses_native123')
        self.assertEqual(req['params']['text'], 'cairn wakeup text')
        # Composer is untouched because promptAsync goes directly to backend session API

    def test_invalid_correlation_rejected_without_opening_socket(self):
        for client in (None, '', '  ', 12, [], {}):
            with self.subTest(client=client), patch.object(opencode_queue, '_open') as connect:
                with self.assertRaises(opencode_queue.QueueUnavailable):
                    opencode_queue.enqueue(self.path, self.process, 'ses_test', 'wake', client)
                connect.assert_not_called()

    def test_busy_ordering_without_preemption(self):
        """2. Busy session refuses submission (-32600 BUSY) as QueueUnavailable without preemption."""
        self.serve(behavior='busy', busy=True)
        with self.assertRaises(opencode_queue.QueueUnavailable) as ctx:
            opencode_queue.enqueue(
                self.path, self.process, 'ses_native123', 'cairn wakeup text', 'delivery_one'
            )
        self.join_workers()
        self.assertIn('BUSY', str(ctx.exception))
        self.assertEqual(self.requests[0]['method'], 'session/prompt_async')
        # No abort was sent; active turn remains unmolested

    def test_stale_native_session_refusal(self):
        """3. Refuses when native session is not found or has mismatched expectation."""
        self.serve(behavior='not_found')
        with self.assertRaises(opencode_queue.QueueUnavailable) as ctx:
            opencode_queue.enqueue(
                self.path, self.process, 'ses_stale', 'wake text', 'delivery_one'
            )
        self.join_workers()
        self.assertIn('SESSION_NOT_FOUND', str(ctx.exception))

        self.serve(behavior='mismatch')
        with self.assertRaises(opencode_queue.QueueUnavailable) as ctx:
            opencode_queue.enqueue(
                self.path, self.process, 'ses_stale', 'wake text', 'delivery_one', expected_session_id='ses_other'
            )
        self.join_workers()
        self.assertIn('SESSION_MISMATCH', str(ctx.exception))

    def test_uncertain_delivery_no_resend(self):
        """4. Failure after request transmission raises QueueError (uncertain outcome; never resend)."""
        self.serve(behavior='drop')
        with self.assertRaises(opencode_queue.QueueError) as ctx:
            opencode_queue.enqueue(
                self.path, self.process, 'ses_native123', 'wake text', 'delivery_one'
            )
        self.join_workers()
        self.assertNotIsInstance(ctx.exception, opencode_queue.QueueUnavailable)
        self.assertIn('uncertain', str(ctx.exception))

    def test_coordinator_retains_single_submission_after_success_or_uncertainty(self):
        state_path = Path(self.temp.name) / 'session.json'
        config = dict(harness='opencode', binding='fixture', native_delivery=True, idle_wakeup=True)
        ready = dict(delivery_id='00000000-0000-4000-8000-000000000001', request_id='fixture-request')
        initial = dict(workspace=self.temp.name, process=self.process, agent=dict(
            agent_id='fixture-agent', execution_id='fixture-execution', native_session_id='ses_native123',
            metadata=dict(workspace=self.temp.name, state='idle', delivery_mode='existing-session')))

        def prepare():
            with coordination.session_lock(state_path):
                return coordination.prepare_idle_wake(config, json.loads(state_path.read_text()), state_path)

        def ready_call(_config, operation, request):
            self.assertEqual(operation, 'session-inbox-ready')
            self.assertEqual(request, dict(agent_id='fixture-agent', execution_id='fixture-execution'))
            return ready

        with patch.object(coordination, 'call', side_effect=ready_call), \
                patch.object(coordination, 'opencode_queue_endpoint', return_value=self.path), \
                patch.dict('sys.modules', {'opencode_queue': opencode_queue}):
            for outcome in ('confirm', 'drop', {'id': 99, 'result': {}},
                            {'id': 1, 'error': {'code': -32000, 'message': 'PROMPT_REFUSED: SDK failed'}}):
                with self.subTest(outcome=outcome):
                    coordination.write_state(state_path, initial)
                    prepared = prepare()
                    prior = len(self.requests)
                    self.serve(behavior=outcome)
                    if outcome == 'confirm':
                        coordination.submit_idle_wake(config, state_path, prepared)
                    else:
                        with self.assertRaises(coordination.CoordinationError) as failure:
                            coordination.submit_idle_wake(config, state_path, prepared)
                        self.assertEqual(failure.exception.code, 'WAKE_UNCERTAIN')
                    self.join_workers()
                    retained = state_path.read_bytes()
                    wake = json.loads(retained)['idle_wake']
                    self.assertEqual(wake['delivery_id'], ready['delivery_id'])
                    self.assertEqual(wake['status'], 'submitted' if outcome == 'confirm' else 'uncertain')
                    if outcome == 'confirm':
                        self.assertEqual(wake['queued_submission_id'], ready['delivery_id'])
                    for _ in range(3):
                        self.assertIsNone(prepare(), 'retained submission must not be replayed')
                    self.assertEqual(state_path.read_bytes(), retained)
                    self.assertEqual(len(self.requests), prior + 1)
                    self.assertEqual(self.requests[-1]['params']['client_id'], ready['delivery_id'])

    def test_invalid_acknowledgment_is_uncertain(self):
        valid = {
            'queued': True,
            'queued_id': 'delivery_one',
            'session_id': 'ses_native123',
            'started': True,
        }
        replies = [
            {},
            {'id': 1, 'result': {}},
            {'id': 2, 'result': valid},
            {'id': True, 'result': valid},
            {'id': 1, 'result': valid, 'error': {'code': -32002}},
            *({'id': 1, 'result': {**valid, field: value}}
              for field, value in (
                  ('queued', False),
                  ('queued_id', 'another_delivery'),
                  ('session_id', 'another_session'),
                  ('started', 'true'),
              )),
        ]
        for reply in replies:
            with self.subTest(reply=reply):
                before = len(self.requests)
                self.serve(behavior=reply)
                with self.assertRaises(opencode_queue.QueueError) as ctx:
                    opencode_queue.enqueue(
                        self.path, self.process, 'ses_native123', 'wake text', 'delivery_one'
                    )
                self.join_workers()
                self.assertNotIsInstance(ctx.exception, opencode_queue.QueueUnavailable)
                self.assertIn('uncertain', str(ctx.exception))
                self.assertEqual(len(self.requests), before + 1)

    def test_unsafe_cancellation_refused_to_protect_newer_work(self):
        """5. Unsafe cancellation refused to protect newer owner turns/work."""
        self.serve(behavior='abort_refuse', turn_match=False)
        with self.assertRaises(opencode_queue.QueueUnavailable) as ctx:
            opencode_queue.abort(
                self.path, self.process, 'ses_native123', expected_turn_id='turn_old'
            )
        self.join_workers()
        self.assertIn('UNSUPPORTED_CONTROL', str(ctx.exception))


class OpenCodeBridgeFixtureTests(unittest.TestCase):
    """Integration tests running the actual coordination.ts plugin via Node bridge fixture."""

    def start_fixture(self, mode='normal', extra_env=None):
        import signal
        import subprocess
        env = os.environ.copy()
        env['OPENCODE_FIXTURE_MODE'] = mode
        env.update(extra_env or {})
        proc = subprocess.Popen(
            ['node', str(Path(__file__).parent / 'run_opencode_bridge_fixture.mjs')],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        line = proc.stdout.readline()
        if not line.startswith('READY:'):
            proc.kill()
            raise RuntimeError(f'Fixture failed to start: {line} stderr: {proc.stderr.read()}')
        pid = int(line.split(':')[1].strip())
        process = dict(
            pid=pid,
            start=int(Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]),
            boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        )
        sock_path = f'/tmp/cairn-opencode-{pid}.sock'
        def cleanup():
            try:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=2)
            except Exception:
                proc.kill()
            try:
                proc.stdout.close()
                proc.stderr.close()
            except Exception:
                pass
            try:
                if os.path.exists(sock_path):
                    os.unlink(sock_path)
            except OSError:
                pass
        self.addCleanup(cleanup)
        return proc, sock_path, process

    def test_fixture_normal_idle_delivery(self):
        """Fixture: Idle session delivers promptAsync directly to SDK without touching TUI composer."""
        proc, sock_path, process = self.start_fixture(mode='normal')
        queued_id, started = opencode_queue.enqueue(
            sock_path, process, 'ses_test', 'wake text', 'req_normal', expected_session_id='ses_test'
        )
        self.assertEqual(queued_id, 'req_normal')
        self.assertTrue(started)

    def test_fixture_correlation_preserved_with_native_owned_message_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / 'requests.jsonl'
            _, endpoint, process = self.start_fixture(extra_env={'OPENCODE_FIXTURE_CAPTURE': str(capture)})
            first = '00000000-0000-4000-8000-000000000001'
            second = '00000000-0000-4000-8000-000000000002'
            for session, client in [('ses_test', first), ('ses_test', second), ('ses_other', first)]:
                queued_id, started = opencode_queue.enqueue(endpoint, process, session, 'wake text', client)
                self.assertEqual(queued_id, client)
                self.assertTrue(started)
            requests = [json.loads(line) for line in capture.read_text().splitlines()]
            self.assertEqual(len(requests), 3, 'one SDK submission per queue request')
            self.assertTrue(all('messageID' not in request['body'] for request in requests))
            self.assertEqual([r['session_id'] for r in requests], ['ses_test', 'ses_test', 'ses_other'])

    def test_fixture_invalid_correlation_rejected_before_sdk(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / 'requests.jsonl'
            _, endpoint, _ = self.start_fixture(extra_env={'OPENCODE_FIXTURE_CAPTURE': str(capture)})
            for client in (None, '', '  ', 12, [], {}):
                with self.subTest(client=client), socket.socket(socket.AF_UNIX) as conn:
                    conn.settimeout(2)
                    conn.connect(endpoint)
                    conn.sendall((json.dumps(dict(id=1, method='session/prompt_async', params=dict(
                        session_id='ses_test', expected_session_id='ses_test', text='wake', client_id=client))) + '\n').encode())
                    with conn.makefile('r') as response:
                        reply = json.loads(response.readline())
                    self.assertEqual(reply.get('error', {}).get('code'), -32602)
            self.assertFalse(capture.exists(), 'invalid IDs must not reach promptAsync')

    def test_fixture_busy_refusal_without_preemption(self):
        """Fixture: Busy session refuses submission (-32600 BUSY) as QueueUnavailable without merging into active generation."""
        proc, sock_path, process = self.start_fixture(mode='busy')
        with self.assertRaises(opencode_queue.QueueUnavailable) as ctx:
            opencode_queue.enqueue(
                sock_path, process, 'ses_test', 'wake text', 'req_busy', expected_session_id='ses_test'
            )
        self.assertIn('BUSY', str(ctx.exception))

    def test_fixture_session_not_found(self):
        """Fixture: Session not found returns -32002 SESSION_NOT_FOUND (QueueUnavailable)."""
        proc, sock_path, process = self.start_fixture(mode='not_found')
        with self.assertRaises(opencode_queue.QueueUnavailable) as ctx:
            opencode_queue.enqueue(
                sock_path, process, 'ses_test', 'wake text', 'req_not_found', expected_session_id='ses_test'
            )
        self.assertIn('SESSION_NOT_FOUND', str(ctx.exception))

    def test_fixture_session_mismatch(self):
        """Fixture: Session mismatch returns -32001 SESSION_MISMATCH (QueueUnavailable)."""
        proc, sock_path, process = self.start_fixture(mode='normal')
        with self.assertRaises(opencode_queue.QueueUnavailable) as ctx:
            opencode_queue.enqueue(
                sock_path, process, 'ses_test', 'wake text', 'req_mismatch', expected_session_id='ses_different'
            )
        self.assertIn('SESSION_MISMATCH', str(ctx.exception))

    def test_fixture_sdk_error_propagation(self):
        """Fixture: SDK errors during promptAsync are surfaced as QueueError (uncertain outcome)."""
        proc, sock_path, process = self.start_fixture(mode='sdk_error')
        with self.assertRaises(opencode_queue.QueueError) as ctx:
            opencode_queue.enqueue(
                sock_path, process, 'ses_test', 'wake text', 'req_err', expected_session_id='ses_test'
            )
        self.assertNotIsInstance(ctx.exception, opencode_queue.QueueUnavailable)
        self.assertIn('HTTP 500', str(ctx.exception))

    def test_fixture_abort_disabled(self):
        """Fixture: session/abort returns -32004 UNSUPPORTED_CONTROL to protect newer owner work."""
        proc, sock_path, process = self.start_fixture(mode='normal')
        with self.assertRaises(opencode_queue.QueueUnavailable) as ctx:
            opencode_queue.abort(sock_path, process, 'ses_test', expected_turn_id='turn_1')
        self.assertIn('UNSUPPORTED_CONTROL', str(ctx.exception))

    def test_fixture_payload_limit_enforced(self):
        """Fixture: Payloads exceeding 64KB are rejected/disconnected."""
        proc, sock_path, process = self.start_fixture(mode='normal')
        oversized = 'X' * (66 * 1024)
        with self.assertRaises(opencode_queue.QueueError):
            opencode_queue.enqueue(
                sock_path, process, 'ses_test', oversized, 'req_large', expected_session_id='ses_test'
            )

    def test_fixture_null_and_malformed_envelope_rejection(self):
        """Fixture: null and non-object envelopes are rejected with -32600 without crashing."""
        proc, sock_path, process = self.start_fixture(mode='normal')
        # Send raw 'null\n'
        s = socket.socket(socket.AF_UNIX)
        s.connect(sock_path)
        s.sendall(b"null\n")
        resp = json.loads(s.makefile('r').readline())
        s.close()
        self.assertEqual(resp.get('error', {}).get('code'), -32600)
        self.assertIn("envelope must be a JSON object", resp['error']['message'])

        # Send raw '[]\n'
        s = socket.socket(socket.AF_UNIX)
        s.connect(sock_path)
        s.sendall(b"[]\n")
        resp = json.loads(s.makefile('r').readline())
        s.close()
        self.assertEqual(resp.get('error', {}).get('code'), -32600)

        # Process must still be alive and responsive
        self.assertIsNone(proc.poll(), "Fixture process must not crash on malformed envelope")
        res = opencode_queue.status(sock_path, process, 'ses_test')
        self.assertEqual(res.get('status'), 'idle')

    def test_fixture_idempotent_double_disposal_preserves_peer_instance(self):
        """Fixture: Disposing an instance twice is idempotent; socket stays active while peer lives."""
        script = """
import plugin from "./integrations/opencode/coordination.ts";
import fs from "node:fs";
import net from "node:net";

const configPath = `/tmp/cairn-test-disp-config-${process.pid}.json`;
fs.writeFileSync(configPath, JSON.stringify({ python: process.execPath, script: "/bin/true", config: "/dev/null" }));
process.env.CAIRN_COORDINATION_CONFIG = configPath;

const clientA = {
  session: {
    get: async () => ({ data: { id: "ses_a" } }),
    status: async () => ({ data: { "ses_a": { type: "idle" } } }),
    promptAsync: async () => ({ data: { id: "msg_a" } })
  }
};
const clientB = {
  session: {
    get: async () => ({ data: { id: "ses_b" } }),
    status: async () => ({ data: { "ses_b": { type: "idle" } } }),
    promptAsync: async () => ({ data: { id: "msg_b" } })
  }
};

const a = await plugin({ directory: "/tmp", client: clientA, project: {}, worktree: "/tmp", serverUrl: new URL("http://localhost"), $: null });
const b = await plugin({ directory: "/tmp", client: clientB, project: {}, worktree: "/tmp", serverUrl: new URL("http://localhost"), $: null });

const sock = `/tmp/cairn-opencode-${process.pid}.sock`;
const before = fs.existsSync(sock);

await a.dispose();
const afterFirstDispose = fs.existsSync(sock);

await a.dispose(); // Double dispose on A must be a no-op
const afterRepeatedDispose = fs.existsSync(sock);

// Verify B is still reachable and functioning
const s = net.connect(sock);
let resp = "";
s.on("data", d => resp += d);
await new Promise(resolve => {
  s.on("end", resolve);
  s.end(JSON.stringify({
    id: 1,
    method: "session/prompt_async",
    params: { session_id: "ses_b", text: "hi", client_id: "c1", expected_session_id: "ses_b" }
  }) + "\\n");
});

await b.dispose();
const afterBDispose = fs.existsSync(sock);

process.stdout.write(JSON.stringify({
  before,
  afterFirstDispose,
  afterRepeatedDispose,
  bOperated: resp.includes('"queued":true'),
  afterBDispose,
}) + "\\n");
"""
        proc = subprocess.run(['node', '--input-type=module', '-e', script],
                              cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Disposal test crashed: {proc.stderr}")
        data = json.loads(proc.stdout.strip())
        self.assertTrue(data['before'])
        self.assertTrue(data['afterFirstDispose'])
        self.assertTrue(data['afterRepeatedDispose'])
        self.assertTrue(data['bOperated'])
        self.assertFalse(data['afterBDispose'])


class OpenCodeNativeSchemaTests(unittest.TestCase):
    """Check actual installed server validation, without a session or provider turn."""

    start_fixture = OpenCodeBridgeFixtureTests.start_fixture

    def test_native_validator_accepts_payload_without_message_id(self):
        binary = os.environ.get('OPENCODE_TEST_BINARY') or shutil.which('opencode')
        if not binary:
            self.skipTest('installed OpenCode required for native request schema validation')
        temp = tempfile.TemporaryDirectory(prefix='cairn-opencode-native-schema-')
        self.addCleanup(temp.cleanup)
        home = Path(temp.name)
        models = home / 'models.json'
        models.write_text('{}')
        # No operational configuration, credentials, plugins, database or provider.
        env = dict(HOME=str(home), PATH=os.environ.get('PATH', ''),
                   XDG_CONFIG_HOME=str(home / 'config'), XDG_DATA_HOME=str(home / 'data'),
                   XDG_STATE_HOME=str(home / 'state'), XDG_CACHE_HOME=str(home / 'cache'),
                   OPENCODE_DISABLE_AUTOUPDATE='true', OPENCODE_DISABLE_DEFAULT_PLUGINS='true',
                   OPENCODE_DISABLE_MODELS_FETCH='true', OPENCODE_MODELS_PATH=str(models))
        with socket.socket() as reserve:
            reserve.bind(('127.0.0.1', 0))
            port = reserve.getsockname()[1]
        server = subprocess.Popen([binary, 'serve', '--hostname', '127.0.0.1', '--port', str(port)],
                                  cwd=home, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def stop_server():
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        self.addCleanup(stop_server)
        url = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 10
        while True:
            try:
                with urllib.request.urlopen(url + '/global/health', timeout=.5) as response:
                    self.assertTrue(json.load(response)['healthy'])
                break
            except (urllib.error.URLError, TimeoutError):
                if server.poll() is not None or time.monotonic() >= deadline:
                    self.fail('isolated native OpenCode server did not become ready')
                time.sleep(.05)

        delivery_id = '00000000-0000-4000-8000-000000000001'
        body = dict(messageID=delivery_id, parts=[dict(type='text', text='schema fixture')])
        req = urllib.request.Request(url + '/session/ses_fixture_missing/prompt_async',
                                     data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as invalid:
            urllib.request.urlopen(req, timeout=3)
        self.assertEqual(invalid.exception.code, 400)
        with invalid.exception as response:
            self.assertIn('messageID', response.read().decode())

        capture = home / 'requests.jsonl'
        _, endpoint, process = self.start_fixture('native_schema', {
            'OPENCODE_FIXTURE_URL': url, 'OPENCODE_FIXTURE_CAPTURE': str(capture),
        })
        with self.assertRaises(opencode_queue.QueueError) as outcome:
            opencode_queue.enqueue(endpoint, process, 'ses_fixture_missing', 'schema fixture', delivery_id)
        # No session exists: native lookup rejects only AFTER request validation.
        self.assertNotIsInstance(outcome.exception, opencode_queue.QueueUnavailable)
        self.assertIn('HTTP 404', str(outcome.exception))
        submitted = [json.loads(line) for line in capture.read_text().splitlines()]
        self.assertEqual(len(submitted), 1)
        self.assertNotIn('messageID', submitted[0]['body'])
        self.assertEqual(submitted[0]['body']['parts'], body['parts'])


if __name__ == '__main__':
    unittest.main()
