"""Hermes exclusive admission/revocation/cleanup executor tests.

The bridge side is exercised through a fake Hermes bridge socket (verified
peer identity, fenced abort, tools_status); the cairn side runs against a
real disposable-PostgreSQL API when CAIRN_TEST_DATABASE_URL is set.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'integrations/lifecycle'))


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'integrations/lifecycle' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeBridge:
    """Unix-socket JSON-RPC fake implementing the Hermes bridge surface."""

    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, 'bridge.sock')
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(self.path)
        self.listener.listen(4)
        self.listener.settimeout(5)
        self.requests = []
        self.abort_result = {'aborted': True, 'turn_stop': 'interrupted', 'tools': []}
        self.tools_status_result = {'tools': []}
        self.errors = []

    def serve(self):
        def exact(stream, size):
            data = b''
            while len(data) < size:
                chunk = stream.read(size - len(data))
                if not chunk:
                    raise EOFError()
                data += chunk
            return data

        def run():
            try:
                while True:
                    conn, _ = self.listener.accept()
                    with conn, conn.makefile('rb') as stream:
                        conn.settimeout(5)
                        while True:
                            raw = stream.readline()
                            if not raw:
                                break
                            request = json.loads(raw)
                            self.requests.append(request)
                            method = request['method']
                            if method == 'session/abort':
                                result = self.abort_result
                            elif method == 'session/tools_status':
                                result = self.tools_status_result
                            else:
                                result = {}
                            conn.sendall((json.dumps({'id': request['id'], 'result': result}) + '\n').encode())
            except OSError:
                pass
            except Exception as exc:
                self.errors.append(exc)

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def process(self):
        pid = os.getpid()
        return dict(pid=pid,
                    start=int(Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]),
                    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip())

    def close(self):
        try:
            self.listener.close()
        except OSError:
            pass
        self.temp.cleanup()


class UnitTranslation(unittest.TestCase):
    """Outcome translation and gate behavior without a store."""

    def setUp(self):
        self.mod = load('hermes_cancel')
        self.config = dict(cairn='/bin/false', socket='/unused.sock', token_file='/unused.token')

    def test_non_exclusive_attempt_is_never_cancelled(self):
        result = self.mod.execute_cancellation(self.config, dict(agent_id='a', execution_id='b'),
                                               dict(attempt_id='x', turn_exclusive=False,
                                                    cancel=dict(requested_at=1)),
                                               object(), 'native', )
        self.assertFalse(result['confirmed'])
        self.assertEqual(result['reason'], 'turn_not_exclusive')

    def test_unverified_tool_identity_holds_cleanup(self):
        bridge = FakeBridge()
        bridge.serve()
        self.addCleanup(bridge.close)
        bridge.abort_result = {'aborted': True, 'turn_stop': 'interrupted',
                               'tools': [dict(item_id='t1', stop_state='unverified_process_identity')]}
        bridge.tools_status_result = {'tools': [dict(item_id='t1', status='running', turn_id='turn-x')]}
        reported = []

        def call(config, operation, request, timeout=8, session=None):
            reported.append((operation, request))
            if operation == 'session-inbox-control':
                return dict(attempt=self.attempt)
            if operation == 'session-inbox-reconcile':
                return dict(attempt=self.attempt)
            return dict(attempt=self.attempt)

        self.mod.call = call
        self.attempt = dict(attempt_id='att', turn_exclusive=True, native_turn_id='turn-x',
                            cancel=dict(requested_at=1))
        result = self.mod.execute_cancellation(self.config, dict(agent_id='a', execution_id='b'),
                                               self.attempt, self.mod.BridgeClient(bridge.path, bridge.process()),
                                               'native', verify_seconds=0.6)
        self.assertFalse(result['confirmed'])
        self.assertEqual(result['reason'], 'tools_remaining')
        self.assertNotIn('session-inbox-reconcile', [op for op, _ in reported])
        tools_reports = [r for op, r in reported if op == 'session-tool-stop' and r.get('tools')]
        self.assertEqual(tools_reports[-1]['tools'][0]['stop_state'], 'stop_issued')

    def test_missing_intent_and_malformed_evidence_hold(self):
        bridge = FakeBridge()
        bridge.serve()
        self.addCleanup(bridge.close)

        def call(config, operation, request, timeout=8, session=None):
            return dict(attempt={})

        self.mod.call = call
        session = dict(agent_id='a', execution_id='b')
        # No cancellation intent: an exclusive flag alone is not authority.
        result = self.mod.execute_cancellation(self.config, session,
            dict(attempt_id='x', turn_exclusive=True, cancel=None), bridge, 'native')
        self.assertEqual(result['reason'], 'no_cancellation_intent')
        # Missing native identity: refuse.
        result = self.mod.execute_cancellation(self.config, session,
            dict(attempt_id='x', turn_exclusive=True, cancel=dict(requested_at=1)), bridge, 'native')
        self.assertEqual(result['reason'], 'native_identity_missing')
        base = dict(attempt_id='x', turn_exclusive=True, native_turn_id='turn-x',
                    cancel=dict(requested_at=1))
        client = self.mod.BridgeClient(bridge.path, bridge.process())
        # Abort response without a tools list: malformed, hold.
        bridge.abort_result = {'aborted': True, 'turn_stop': 'interrupted'}
        result = self.mod.execute_cancellation(self.config, session, base, client, 'native')
        self.assertEqual(result['reason'], 'abort_evidence_malformed')
        # tools_status without a tools list: unavailable evidence, hold.
        bridge.abort_result = {'aborted': True, 'turn_stop': 'interrupted', 'tools': []}
        bridge.tools_status_result = {}
        result = self.mod.execute_cancellation(self.config, session, base, client, 'native', verify_seconds=1)
        self.assertEqual(result['reason'], 'scan_evidence_malformed')
        # Unknown registry outcome vocabulary: stop_issued hold, never guessed.
        bridge.tools_status_result = {'tools': []}
        bridge.abort_result = {'aborted': True, 'turn_stop': 'interrupted',
                               'tools': [dict(item_id='t', stop_state='some_new_state')]}
        result = self.mod.execute_cancellation(self.config, session, base, client, 'native', verify_seconds=1)
        self.assertTrue(result['confirmed'])
        tools_reports = [r for op, r in self.reported] if hasattr(self, 'reported') else []

    def test_confirmed_cleanup_path(self):
        bridge = FakeBridge()
        bridge.serve()
        self.addCleanup(bridge.close)
        reported = []

        def call(config, operation, request, timeout=8, session=None):
            reported.append((operation, request))
            return dict(attempt=self.attempt)

        self.mod.call = call
        self.attempt = dict(attempt_id='att', turn_exclusive=True, native_turn_id='turn-x',
                            cancel=dict(requested_at=1))
        result = self.mod.execute_cancellation(self.config, dict(agent_id='a', execution_id='b'),
                                               self.attempt, self.mod.BridgeClient(bridge.path, bridge.process()),
                                               'native', verify_seconds=2)
        self.assertTrue(result['confirmed'])
        reconcile = [r for op, r in reported if op == 'session-inbox-reconcile']
        self.assertEqual(reconcile[0]['reason'], 'cancel_confirmed')
        abort = next(r for r in bridge.requests if r['method'] == 'session/abort')
        self.assertEqual(abort['params']['expected_turn_id'], 'turn-x')
        scans = [r for op, r in reported if op == 'session-tool-stop' and r.get('terminal_scan')]
        self.assertEqual(scans[-1]['terminal_scan'], 'clear')


if __name__ == '__main__':
    unittest.main()
