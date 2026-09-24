"""Unit tests for OpenCode native queue client adapter against real Unix domain sockets."""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import threading
import unittest

from integrations.lifecycle import opencode_queue

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

    def test_busy_ordering_without_preemption(self):
        """2. Busy session queues message serially without preemption."""
        self.serve(behavior='busy', busy=True)
        queued_id, started = opencode_queue.enqueue(
            self.path, self.process, 'ses_native123', 'cairn wakeup text', 'delivery_one'
        )
        self.join_workers()
        self.assertEqual(queued_id, 'delivery_one')
        self.assertFalse(started)  # Enqueued but not started; waits for active turn to finish
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

    def start_fixture(self, mode='normal'):
        import signal
        import subprocess
        env = os.environ.copy()
        env['OPENCODE_FIXTURE_MODE'] = mode
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

    def test_fixture_busy_refusal_without_preemption(self):
        """Fixture: Busy session refuses submission (-32600 BUSY) without merging into active generation."""
        proc, sock_path, process = self.start_fixture(mode='busy')
        queued_id, started = opencode_queue.enqueue(
            sock_path, process, 'ses_test', 'wake text', 'req_busy', expected_session_id='ses_test'
        )
        self.assertEqual(queued_id, 'req_busy')
        self.assertFalse(started)

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

    def test_fixture_exact_request_cancellation(self):
        proc, sock_path, process = self.start_fixture(mode='normal')
        self.assertFalse(opencode_queue.abort(sock_path, process, 'ses_test', expected_turn_id='stale'))
        self.assertTrue(opencode_queue.abort(sock_path, process, 'ses_test', expected_turn_id='req_owned'))

    def test_fixture_old_runtime_refuses_wake_and_cancel(self):
        proc, sock_path, process = self.start_fixture(mode='missing_api')
        with self.assertRaises(opencode_queue.QueueUnavailable):
            opencode_queue.enqueue(sock_path, process, 'ses_test', 'wake', 'request')
        with self.assertRaises(opencode_queue.QueueUnavailable):
            opencode_queue.abort(sock_path, process, 'ses_test', expected_turn_id='request')

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


if __name__ == '__main__':
    unittest.main()

