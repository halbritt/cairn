"""Real Unix transport failures must not become cleanup confirmation or replay."""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integrations/lifecycle'))
import coordination
import hermes_cancel


class ControlTransportTests(unittest.TestCase):
    def exchange(self, respond, *, wrong_peer=False, timeout=4):
        with tempfile.TemporaryDirectory(prefix='cairn-hermes-control-') as directory:
            endpoint = str(Path(directory) / 'bridge.sock')
            listener = socket.socket(socket.AF_UNIX)
            listener.bind(endpoint)
            listener.listen(1)
            listener.settimeout(3)
            observed = []
            failures = []

            def serve():
                try:
                    with listener.accept()[0] as connection:
                        connection.settimeout(3)
                        received = bytearray()
                        while b'\n' not in received:
                            chunk = connection.recv(4096)
                            if not chunk:
                                break
                            received.extend(chunk)
                        if received:
                            request = json.loads(received)
                            observed.append(request)
                            payload = respond(request)
                            if payload is not None:
                                connection.sendall(payload)
                except Exception as exc:
                    failures.append(exc)

            thread = threading.Thread(target=serve)
            thread.start()
            try:
                process = coordination.process_reference(os.getpid())
                if wrong_peer:
                    process['pid'] += 1
                client = hermes_cancel.BridgeClient(endpoint, process, timeout=timeout)
                return client.rpc('session/control', {'ownership_token': 'owned-one'})
            finally:
                thread.join(4)
                listener.close()
                self.assertFalse(thread.is_alive(), 'fixture thread did not finish')
                self.assertEqual(failures, [])
                self.assertEqual(len(observed), 0 if wrong_peer else 1)

    def test_wrong_native_process_is_rejected_before_send(self):
        with self.assertRaises(hermes_cancel.ControlUnavailable):
            self.exchange(lambda _: None, wrong_peer=True)

    def test_unanswered_request_times_out_without_replay(self):
        def delayed(_):
            time.sleep(0.2)
        with self.assertRaises(hermes_cancel.ControlUncertain):
            self.exchange(delayed, timeout=0.05)

    def test_success_requires_matching_request_and_object(self):
        result = self.exchange(lambda r: (json.dumps(dict(id=r['id'], result={'turn_ended': False}))+'\n').encode())
        self.assertEqual(result, {'turn_ended': False})

    def test_explicit_refusal_preserves_native_error(self):
        with self.assertRaises(hermes_cancel.ControlRefused) as error:
            self.exchange(lambda r: (json.dumps(dict(id=r['id'], error={'code': -32004, 'message': 'OWNERSHIP_REVOKED'}))+'\n').encode())
        self.assertEqual(error.exception.code, -32004)

    def test_lost_or_invalid_reply_is_uncertain_without_replay(self):
        responses = [
            lambda r: None,
            lambda r: b'not json\n',
            lambda r: b'[]\n',
            lambda r: (json.dumps(dict(id='another-request', result={}))+'\n').encode(),
            lambda r: (json.dumps(dict(id=r['id'], result={}, error={}))+'\n').encode(),
            lambda r: (json.dumps(dict(id=r['id'], result=[]))+'\n').encode(),
            lambda r: (json.dumps(dict(id=r['id'], error={'code': True, 'message': 'bad'}))+'\n').encode(),
            lambda r: b'x' * (hermes_cancel.MAX_RESPONSE_BYTES + 1),
            lambda r: ('{"id":'+json.dumps(r['id'])+',"result":'+'['*1500+'0'+']'*1500+'}\n').encode(),
        ]
        for respond in responses:
            with self.subTest(response=responses.index(respond)):
                with self.assertRaises(hermes_cancel.ControlUncertain):
                    self.exchange(respond)


if __name__ == '__main__':
    unittest.main()
