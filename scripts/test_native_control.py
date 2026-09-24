"""Native request cancellation: exact turn interrupt, owned-tool termination,
durable capture from notifications, and confirmed-cleanup release."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import threading
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'integrations/lifecycle' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NativeCancel(unittest.TestCase):
    def setUp(self):
        self.control = load('native_control')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / 'native.sock')
        self.state_path = str(Path(self.temp.name) / 'state.json')
        self.process = dict(pid=os.getpid(),
            start=int(Path(f'/proc/{os.getpid()}/stat').read_text().rsplit(')', 1)[1].split()[19]),
            boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
        self.config = dict(cairn='/bin/false', socket='/unused/cairn.sock', token_file='/unused/token',
                           binding='test-binding', state_dir=self.temp.name)
        self.session = dict(agent_id='11111111-1111-4111-8111-111111111111',
                            execution_id='22222222-2222-4222-8222-222222222222')
        self.attempt = dict(attempt_id='33333333-3333-4333-8333-333333333333', session=self.session,
                            native_turn_id='turn-one', turn_stop_state='', turn_exclusive=True,
                            delivery=dict(delivery_id='44444444-4444-4444-8444-444444444444'),
                            cancel=dict(requested_at=1, by='operator', reason='test'),
                            tools=[dict(item_id='exec-owned', process_id='101', stop_state='captured'),
                                   dict(item_id='exec-later', process_id='202', stop_state='captured')])
        self.state = dict(agent=dict(agent_id=self.session['agent_id'],
                                     execution_id=self.session['execution_id'],
                                     native_session_id='thread-one'),
                          process=self.process)
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(self.path)
        self.listener.listen()
        self.listener.settimeout(4)
        self.addCleanup(self.listener.close)
        self.requests = []
        self.errors = []
        self.api = []
        self.responses = {'turn/interrupt': dict(result={}),
                          'thread/backgroundTerminals/terminate': dict(result=dict(terminated=True)),
                          'thread/turns/list': dict(result=dict(data=[dict(id='turn-one', status='interrupted')])),
                          'thread/backgroundTerminals/list': dict(result=dict(data=[], nextCursor=None)),
                          'thread/loaded/list': dict(result=dict(data=['thread-one'])),
                          'thread/resume': dict(result=dict(thread=dict(id='thread-one'))),
                          'thread/unsubscribe': dict(result=dict(status='unsubscribed'))}
        self.terminals = []
        self.notifications = []

    def serve(self):
        def exact(stream, size):
            result = b''
            while len(result) < size:
                chunk = stream.read(size - len(result))
                if not chunk:
                    raise EOFError()
                result += result[:0] + chunk
            return result

        def send(connection, payload):
            body = json.dumps(payload).encode()
            header = bytes([129, len(body)]) if len(body) < 126 else bytes([129, 126]) + struct.pack('!H', len(body))
            connection.sendall(header + body)

        def run():
            try:
                connection, _ = self.listener.accept()
                with connection, connection.makefile('rb') as stream:
                    connection.settimeout(4)
                    headers = {}
                    while (line := stream.readline()) != b'\r\n':
                        if not line:
                            return
                        if b':' in line:
                            key, value = line.split(b':', 1)
                            headers[key.lower()] = value.strip()
                    accept = base64.b64encode(hashlib.sha1(headers[b'sec-websocket-key'] +
                        b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest())
                    connection.sendall(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n'
                        b'Connection: Upgrade\r\nSec-WebSocket-Accept: ' + accept + b'\r\n\r\n')
                    for notification in list(self.notifications):
                        send(connection, notification)
                        self.notifications.remove(notification)
                    while True:
                        opcode, length = exact(stream, 2)
                        if opcode & 15 == 8:
                            return
                        masked, length = length & 128, length & 127
                        if length == 126:
                            length = struct.unpack('!H', exact(stream, 2))[0]
                        elif length == 127:
                            length = struct.unpack('!Q', exact(stream, 8))[0]
                        mask = exact(stream, 4) if masked else b''
                        raw = exact(stream, length)
                        if masked:
                            raw = bytes(c ^ mask[i % 4] for i, c in enumerate(raw))
                        request = json.loads(raw)
                        self.requests.append(request)
                        method = request['method']
                        if method == 'initialized':
                            continue
                        if method == 'initialize':
                            send(connection, {'id': request['id'], 'result': {'userAgent': 'fixture'}})
                            continue
                        response = dict(id=request['id'], **self.responses.get(method, dict(error={
                            'code': -32601, 'message': f'unsupported {method}'})))
                        send(connection, response)
                        for notification in list(self.notifications):
                            send(connection, notification)
                            self.notifications.remove(notification)
            except EOFError:
                pass
            except Exception as exc:
                self.errors.append(exc)

        self.worker = threading.Thread(target=run)
        self.worker.start()
        self.addCleanup(self.join)

    def join(self):
        self.worker.join(timeout=6)
        self.assertFalse(self.worker.is_alive())
        self.assertEqual(self.errors, [])

    def api_call(self, operation, request):
        self.api.append((operation, request))
        if operation == 'session-inbox-control':
            return dict(attempt=self.attempt)
        if operation == 'session-tool-capture':
            return dict(attempt=self.attempt)
        if operation == 'session-tool-stop':
            for tool in request.get('tools') or []:
                for known in self.attempt['tools']:
                    if known['item_id'] == tool['item_id']:
                        known['stop_state'] = tool['stop_state']
            if request.get('turn_stop') and self.attempt['turn_stop_state'] in ('', 'ambiguous'):
                self.attempt['turn_stop_state'] = request['turn_stop']
            if request.get('terminal_scan'):
                self.attempt['terminal_scan'] = request['terminal_scan']
            if request.get('capture_state') == 'complete' and self.attempt.get('capture_state') == 'attached':
                self.attempt['capture_state'] = 'complete'
            return dict(attempt=self.attempt)
        if operation == 'session-inbox-reconcile':
            # Mirror the server's confirmed-cleanup contract: the final clear
            # terminal scan is mandatory; capture completeness never
            # substitutes for it.
            if request['reason'] == 'cancel_confirmed':
                if (self.attempt['turn_stop_state'] not in ('interrupted', 'ended')
                        or any(t['stop_state'] not in ('terminated', 'unavailable') for t in self.attempt['tools'])
                        or self.attempt.get('terminal_scan') != 'clear'):
                    raise self.control.ControlError('CLEANUP_UNCONFIRMED', 'evidence incomplete')
            return dict(attempt=self.attempt)
        raise AssertionError(operation)

    def run_stop(self, budget=8.0):
        self.control.call = lambda config, operation, request, timeout=8: self.api_call(operation, request)
        return self.control.stop_sequence(self.config, self.state, self.state_path, self.attempt, self.path,
                                          budget_seconds=budget)

    def methods(self):
        return [r['method'] for r in self.requests]

    def test_interrupts_exact_turn_and_terminates_only_owned_tools(self):
        self.serve()
        result = self.run_stop()
        self.assertTrue(result['confirmed'])
        interrupt = next(r for r in self.requests if r['method'] == 'turn/interrupt')
        self.assertEqual(interrupt['params'], dict(threadId='thread-one', turnId='turn-one'))
        terminated = [r['params']['processId'] for r in self.requests
                      if r['method'] == 'thread/backgroundTerminals/terminate']
        self.assertEqual(sorted(terminated), ['101', '202'])  # captured tools only
        stops = [op for op, _ in self.api if op == 'session-tool-stop']
        self.assertTrue(stops)
        reconcile = next(request for op, request in self.api if op == 'session-inbox-reconcile')
        self.assertEqual(reconcile['reason'], 'cancel_confirmed')
        self.assertEqual(reconcile['session'], self.session)
        self.assertNotIn('native_cancel', json.loads(Path(self.state_path).read_text()))
        self.assertEqual(self.attempt['turn_stop_state'], 'interrupted')

    def test_wrong_active_turn_reports_ended_and_never_interrupts_it(self):
        self.responses['turn/interrupt'] = dict(error=dict(code=-32600,
            message='expected active turn id turn-later but found turn-one'))
        self.serve()
        result = self.run_stop()
        self.assertTrue(result['confirmed'])
        self.assertEqual(self.attempt['turn_stop_state'], 'ended')
        terminated = [r['params']['processId'] for r in self.requests
                      if r['method'] == 'thread/backgroundTerminals/terminate']
        self.assertTrue(terminated)  # owned tools still cleaned

    def test_mutation_identities_are_uuids(self):
        self.serve()
        result = self.run_stop()
        self.assertTrue(result['confirmed'])
        for operation, request in self.api:
            if operation == 'session-inbox-control':
                continue
            uuid.UUID(request['request_id'])  # raises on non-UUID identities

    def test_uncaptured_terminal_keeps_the_hold_for_coverage(self):
        self.responses['thread/backgroundTerminals/list'] = dict(result=dict(
            data=[dict(itemId='exec-foreign', processId='909')], nextCursor=None))
        self.serve()
        result = self.run_stop(budget=2)
        self.assertFalse(result['confirmed'])
        self.assertEqual(result['reason'], 'capture_coverage_missing')
        scans = [request for op, request in self.api if op == 'session-tool-stop' and request.get('terminal_scan')]
        self.assertEqual(scans[-1]['terminal_scan'], 'unknown_remaining')
        self.assertNotIn('session-inbox-reconcile', [op for op, _ in self.api])

    def test_interrupt_ack_does_not_confirm_running_generation(self):
        self.responses['thread/turns/list'] = dict(result=dict(data=[dict(id='turn-one', status='inProgress')]))
        self.serve()
        result = self.run_stop(budget=.3)
        self.assertFalse(result['confirmed'])
        self.assertNotIn('session-inbox-reconcile', [op for op, _ in self.api])

    def test_malformed_terminal_list_cannot_confirm(self):
        self.responses['thread/backgroundTerminals/list'] = dict(result=dict(data=None))
        self.serve()
        result = self.run_stop(budget=2)
        self.assertFalse(result['confirmed'])
        self.assertEqual(result['reason'], 'verification_unavailable')
        self.assertNotIn('session-inbox-reconcile', [op for op, _ in self.api])

    def test_interrupt_refusal_stays_ambiguous_and_holds(self):
        self.responses['turn/interrupt'] = dict(error=dict(code=-32000, message='socket saturated'))
        self.serve()
        result = self.run_stop(budget=2)
        self.assertFalse(result['confirmed'])
        self.assertEqual(result['reason'], 'cleanup_unconfirmed')
        self.assertEqual(self.attempt['turn_stop_state'], 'ambiguous')
        reconciles = [request for op, request in self.api if op == 'session-inbox-reconcile']
        self.assertTrue(reconciles)  # attempted, refused by the evidence contract
        self.assertFalse(result['confirmed'])

    def test_unterminatable_tool_keeps_the_hold(self):
        self.responses['thread/backgroundTerminals/terminate'] = dict(error=dict(code=-32000, message='unknown'))
        self.responses['thread/backgroundTerminals/list'] = dict(result=dict(
            data=[dict(itemId='exec-owned', processId='101')], nextCursor=None))
        self.attempt['tools'] = [self.attempt['tools'][0]]
        self.serve()
        result = self.run_stop(budget=1.5)
        self.assertFalse(result['confirmed'])
        self.assertEqual(result['reason'], 'tools_remaining')
        self.assertNotIn('session-inbox-reconcile', [op for op, _ in self.api])
        stage = json.loads(Path(self.state_path).read_text())['native_cancel']
        self.assertEqual(stage['attempt_id'], self.attempt['attempt_id'])

    def test_recovery_resumes_from_persisted_intent(self):
        self.state['native_cancel'] = dict(attempt_id=self.attempt['attempt_id'], stage='terminate',
                                           delivery_id=self.attempt['delivery']['delivery_id'])
        self.attempt['turn_stop_state'] = 'interrupted'  # A prior pass already stopped the turn.
        self.serve()
        result = self.run_stop()
        self.assertTrue(result['confirmed'])
        self.assertNotIn('turn/interrupt', self.methods())  # never re-issued after success
        self.assertIn('thread/backgroundTerminals/terminate', self.methods())

    def test_unconfirmed_cleanup_is_reported_not_released(self):
        def refuse(operation, request):
            self.api.append((operation, request))
            if operation == 'session-inbox-control':
                return dict(attempt=self.attempt)
            if operation == 'session-inbox-reconcile':
                raise self.control.ControlError('CLEANUP_UNCONFIRMED', 'stop report missing')
            return dict(attempt=self.attempt)
        self.control.call = lambda config, operation, request, timeout=8: refuse(operation, request)
        self.serve()
        result = self.control.stop_sequence(self.config, self.state, self.state_path, self.attempt, self.path,
                                            budget_seconds=5)
        self.assertFalse(result['confirmed'])
        self.assertEqual(result['reason'], 'cleanup_unconfirmed')

    def test_capture_listener_records_only_pinned_turn_tools(self):
        captured = []

        def call(config, operation, request, timeout=8):
            self.api.append((operation, request))
            if operation == 'session-tool-capture':
                captured.append(request)
                return dict(attempt=self.attempt)
            raise AssertionError(operation)

        self.control.call = call
        self.notifications = [
            dict(method='item/started', params=dict(threadId='thread-one', turnId='turn-one', item=dict(
                type='commandExecution', id='exec-a', processId='55', command='/bin/sleep 30'))),
            dict(method='item/started', params=dict(threadId='thread-one', turnId='turn-later', item=dict(
                type='commandExecution', id='exec-b', processId='66', command='/bin/sleep 30'))),
            dict(method='item/started', params=dict(threadId='thread-one', turnId='turn-one', item=dict(
                type='reasoning', id='think-a'))),
            dict(method='turn/completed', params=dict(threadId='thread-one', turn=dict(id='turn-one', status='interrupted'))),
        ]
        self.serve()
        with self.control.NativeControl(self.path, self.process) as control:
            tools = self.control.subscribe_capture(control, 'thread-one', 'turn-one', time.monotonic() + 4)
        self.assertEqual([t['item_id'] for t in tools], ['exec-a'])
        self.assertEqual(tools[0]['process_id'], '55')
        self.assertEqual(tools[0]['native_turn_id'], 'turn-one')
        self.assertEqual(tools[0]['command'], '/bin/sleep 30')

    def test_capture_listener_persists_durable_ownership(self):
        captured = []

        def call(config, operation, request, timeout=8):
            self.api.append((operation, request))
            if operation in ('session-tool-capture', 'session-tool-stop'):
                if operation == 'session-tool-capture':
                    captured.append(request)
                return dict(attempt=self.attempt)
            raise AssertionError(operation)

        self.control.call = call
        self.attempt['capture_state'] = 'attached'
        self.notifications = [
            dict(method='item/started', params=dict(threadId='thread-one', turnId='turn-one', item=dict(
                type='commandExecution', id='exec-c', processId='77', command='/bin/sleep 5'))),
            dict(method='turn/completed', params=dict(threadId='thread-one', turn=dict(id='turn-one', status='interrupted'))),
        ]
        self.serve()
        listener = self.control.CaptureListener(self.config, self.state, self.state_path, self.path, self.attempt)
        listener.start()
        listener.join(timeout=6)
        self.assertFalse(listener.is_alive())
        self.assertEqual(len(captured), 1)
        request = captured[0]
        self.assertEqual(request['attempt_id'], self.attempt['attempt_id'])
        self.assertEqual(request['items'][0]['item_id'], 'exec-c')
        uuid.UUID(request['request_id'])
        coverage = [r for op, r in self.api if op == 'session-tool-stop' and r.get('capture_state')]
        self.assertEqual(coverage[-1]['capture_state'], 'complete')

    def test_subscription_requires_loaded_thread_and_attaches_stream(self):
        self.responses['thread/loaded/list'] = dict(result=dict(data=['thread-one']))
        self.responses['thread/resume'] = dict(result=dict(thread=dict(id='thread-one')))
        self.serve()
        with self.control.NativeControl(self.path, self.process) as control:
            self.control.subscribe_loaded(control, 'thread-one')
        self.assertIn('thread/resume', self.methods())
        self.assertNotIn('thread/read', self.methods())

    def test_subscription_does_not_reload_unloaded_thread(self):
        self.responses['thread/loaded/list'] = dict(result=dict(data=[]))
        self.serve()
        with self.control.NativeControl(self.path, self.process) as control:
            with self.assertRaises(self.control.ControlError):
                self.control.subscribe_loaded(control, 'thread-one')
        self.assertNotIn('thread/resume', self.methods())

    def test_pending_cancellation_gates(self):
        self.control.call = lambda config, operation, request, timeout=8: self.api_call(operation, request)
        self.assertEqual(self.control.pending_cancellation(self.config, self.state, self.state_path, None),
                         dict(confirmed=False, reason='no_native_control_endpoint',
                              attempt_id=self.attempt['attempt_id']))
        self.attempt['native_turn_id'] = ''
        self.assertEqual(self.control.pending_cancellation(self.config, self.state, self.state_path, self.path),
                         dict(confirmed=False, reason='turn_unpinned',
                              attempt_id=self.attempt['attempt_id']))
        self.attempt['native_turn_id'] = 'turn-one'
        self.attempt['cancel'] = None
        self.assertIsNone(self.control.pending_cancellation(self.config, self.state, self.state_path, self.path))
        self.state['agent'] = None
        self.assertIsNone(self.control.pending_cancellation(self.config, self.state, self.state_path, self.path))

    def test_non_exclusive_attempt_never_stops(self):
        self.control.call = lambda config, operation, request, timeout=8: self.api_call(operation, request)
        self.attempt['turn_exclusive'] = False
        self.assertEqual(self.control.pending_cancellation(self.config, self.state, self.state_path, self.path),
                         dict(confirmed=False, reason='turn_not_exclusive',
                              attempt_id=self.attempt['attempt_id']))

    def test_listener_stop_reports_durable_gap(self):
        reports = []

        def call(config, operation, request, timeout=8):
            reports.append((operation, request))
            return dict(attempt=self.attempt)

        self.control.call = call
        self.serve()
        listener = self.control.CaptureListener(self.config, self.state, self.state_path, self.path, self.attempt)
        listener.start()
        time.sleep(0.5)
        listener.stop()
        listener.join(timeout=6)
        self.assertFalse(listener.is_alive())
        lost = [r for op, r in reports if op == 'session-tool-stop' and r.get('capture_state') == 'lost']
        self.assertTrue(lost, 'explicit stop must leave a durable coverage gap')

    def test_peer_identity_is_enforced(self):
        self.serve()
        stranger = dict(self.process, pid=self.process['pid'] + 1)
        with self.assertRaises(self.control.ControlError) as raised:
            self.control.NativeControl(self.path, stranger)
        self.assertEqual(raised.exception.code, 'WRONG_PROCESS')


if __name__ == '__main__':
    unittest.main()
