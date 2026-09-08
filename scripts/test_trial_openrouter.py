"""No hosted calls: exercise the relay boundary against a local HTTP peer."""
from contextlib import contextmanager
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest

from trial_openrouter import MAX_BODY, MAX_OUTPUT, MAX_REQUESTS, MODEL, PROVIDER, relay


@contextmanager
def fixture(status=200):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            received.append((self.path, self.headers.get('Authorization'),
                             json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            self.send_response(status)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            # Include private response content, fragmented across wire writes.
            raw = b'data: {"choices":[{"delta":{"content":"private-output"},"finish_reason":"stop"}],"usage":{"prompt_tokens":9,"completion_tokens":2,"cost":0.0001}}\n\ndata: [DONE]\n\n'
            self.wfile.write(raw[:19]); self.wfile.flush()
            self.wfile.write(raw[19:]); self.wfile.flush()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield received, lambda: http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
    finally:
        server.shutdown(); server.server_close(); thread.join()


def send(route, body=None, token=None, path='/v1/chat/completions'):
    port = int(route['endpoint'].split(':')[2].split('/')[0])
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    payload = json.dumps(body or {'model': MODEL, 'messages': [{'role': 'user', 'content': 'private-input'}], 'stream': True})
    try:
        connection.request('POST', path, payload, {'Authorization': 'Bearer ' + (token or route['api_key'])})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


class RelayTest(unittest.TestCase):
    def test_stream_controls_and_metadata_without_payload_or_credential(self):
        with tempfile.TemporaryDirectory() as directory, fixture() as (received, connect):
            path = Path(directory) / 'report.json'
            with relay('operator-secret', path, connect) as route:
                self.assertNotIn('operator-secret', json.dumps(route))
                status, raw = send(route)
                self.assertEqual(status, 200)
                self.assertIn(b'private-output', raw)
            target, auth, body = received[0]
            self.assertEqual(target, '/api/v1/chat/completions')
            self.assertEqual(auth, 'Bearer operator-secret')
            self.assertEqual(body['provider'], PROVIDER)
            self.assertEqual(body['max_tokens'], MAX_OUTPUT)
            self.assertEqual(body['stream_options'], {'include_usage': True})
            report = json.loads(path.read_text())
            self.assertEqual(report['requests'][0]['usage'][0]['cost'], 0.0001)
            self.assertEqual(report['requests'][0]['status'], 'completed')
            for private in ('private-input', 'private-output', 'operator-secret', route['api_key']):
                self.assertNotIn(private, path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_invalid_requests_and_exhaustion_never_reach_upstream(self):
        with tempfile.TemporaryDirectory() as directory, fixture() as (received, connect):
            with relay('secret', Path(directory) / 'report.json', connect) as route:
                self.assertEqual(send(route, token='wrong')[0], 403)
                self.assertEqual(send(route, path='/v1/models')[0], 403)
                for body in ({'model': 'different'}, {'model': MODEL, 'provider': {}},
                             {'model': MODEL, 'max_tokens': MAX_OUTPUT + 1},
                             {'model': MODEL, 'max_tokens': True}):
                    self.assertEqual(send(route, body)[0], 400)
                self.assertEqual(send(route, {'model': MODEL, 'messages': ['x' * MAX_BODY]})[0], 413)
                self.assertEqual(len(received), 0)
                for _ in range(MAX_REQUESTS):
                    self.assertEqual(send(route)[0], 200)
                self.assertEqual(send(route)[0], 429)
                self.assertEqual(len(received), MAX_REQUESTS)

    def test_http_failure_is_retained_without_retry(self):
        with tempfile.TemporaryDirectory() as directory, fixture(503) as (received, connect):
            path = Path(directory) / 'report.json'
            with relay('secret', path, connect) as route:
                self.assertEqual(send(route)[0], 503)
            self.assertEqual(len(received), 1)
            self.assertEqual(json.loads(path.read_text())['requests'][0]['http_status'], 503)

    def test_large_tool_history_fits_and_transport_failure_remains_visible(self):
        with tempfile.TemporaryDirectory() as directory, fixture() as (received, connect):
            path = Path(directory) / 'report.json'
            with relay('secret', path, connect) as route:
                status, _ = send(route, {'model': MODEL, 'stream': True,
                                        'messages': [{'role': 'user', 'content': 'x' * 600000}]})
                self.assertEqual(status, 200)
            self.assertEqual(len(received), 1)
        # This closed fixture port has no listener. It cannot reach a provider.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'report.json'
            with relay('secret', path, connect) as route:
                self.assertEqual(send(route)[0], 502)
            request = json.loads(path.read_text())['requests'][0]
            self.assertEqual(request['status'], 'failed')
            self.assertEqual(request['failure_type'], 'ConnectionRefusedError')


if __name__ == '__main__':
    unittest.main()
