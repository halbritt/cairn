"""Credential-holding, bounded OpenRouter relay for the opt-in repair trial.

The child receives a loopback endpoint and a disposable relay token. Only this
parent process reads the existing operator credential. This is not a general
proxy or a production model service. No prompt or completion bytes are retained.
"""
from contextlib import contextmanager
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import time

MODEL = 'deepseek/deepseek-v4-flash-0731'
MAX_REQUESTS = 24
MAX_BODY = 1024 * 1024
MAX_RESPONSE = 8 * 1024 * 1024
MAX_OUTPUT = 8192
PROVIDER = {'max_price': {'prompt': 0.2, 'completion': 0.5, 'request': 0},
            'data_collection': 'deny', 'require_parameters': True}


def configured_key():
    config = json.loads((Path.home() / '.config/opencode/opencode.json').read_text())
    provider = config['provider']['openrouter']
    if provider['options']['baseURL'] != 'https://openrouter.ai/api/v1' or MODEL not in provider['models']:
        raise RuntimeError('expected existing OpenRouter route is not configured')
    reference = provider['options']['apiKey']
    if not reference.startswith('{env:') or not reference.endswith('}'):
        raise RuntimeError('trial requires an existing environment-backed credential')
    key = os.environ.get(reference[5:-1])
    if not key:
        raise RuntimeError('configured OpenRouter credential is unavailable')
    return key


@contextmanager
def relay(key, report_path, connection_factory=None):
    # The optional transport is solely for the local fixture test. CLI callers
    # cannot choose a different upstream or forward arbitrary request headers.
    connect = connection_factory or (lambda: http.client.HTTPSConnection('openrouter.ai', timeout=45))
    token = secrets.token_urlsafe(32)
    lock = threading.Lock()
    report = {'schema': 'cairn.hosted-relay/1', 'model': MODEL,
              'limits': {'requests': MAX_REQUESTS, 'request_bytes': MAX_BODY,
                         'response_bytes': MAX_RESPONSE, 'output_tokens': MAX_OUTPUT,
                         'provider': PROVIDER}, 'requests': [], 'rejections': 0, 'rejection_codes': {}}

    def save():
        # Called under lock; publish whole metadata snapshots atomically.
        temporary = report_path.with_suffix('.pending')
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as stream:
            json.dump(report, stream, indent=2)
        os.replace(temporary, report_path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reject(self, code):
            with lock:
                report['rejections'] += 1
                code_key = str(code)
                report['rejection_codes'][code_key] = report['rejection_codes'].get(code_key, 0) + 1
                save()
            self.send_response(code)
            self.send_header('Content-Length', '0')
            self.end_headers()

        def do_POST(self):
            self.connection.settimeout(10)
            if self.path != '/v1/chat/completions' or self.headers.get('Authorization') != 'Bearer ' + token:
                return self.reject(403)
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if self.headers.get('Transfer-Encoding') or not 0 < size <= MAX_BODY:
                    return self.reject(413)
                raw = self.rfile.read(size)
                body = json.loads(raw)
                if len(raw) != size or not isinstance(body, dict) or body.get('model') != MODEL:
                    return self.reject(400)
                # Permit only fields this trial needs. Routing, fallbacks,
                # plugins and model-controlled provider options cannot escape.
                allowed = {'model', 'messages', 'tools', 'tool_choice', 'stream', 'stream_options',
                           'temperature', 'top_p', 'stop', 'max_tokens', 'max_completion_tokens',
                           'frequency_penalty', 'presence_penalty', 'parallel_tool_calls', 'seed'}
                if set(body) - allowed:
                    return self.reject(400)
                requested = body.pop('max_completion_tokens', body.get('max_tokens', MAX_OUTPUT))
                if type(requested) is not int or not 0 < requested <= MAX_OUTPUT:
                    return self.reject(400)
                body['max_tokens'] = requested
                body['provider'] = PROVIDER
                if body.get('stream'):
                    body['stream_options'] = {'include_usage': True}
                outgoing = json.dumps(body).encode()
            except (ValueError, UnicodeError, TimeoutError, OSError):
                return self.reject(400)
            with lock:
                if len(report['requests']) >= MAX_REQUESTS:
                    exhausted = True
                else:
                    exhausted = False
                    observation = {'request_bytes': len(raw), 'request_sha256': hashlib.sha256(raw).hexdigest(),
                                   'status': 'started', 'usage': [], 'finish_reasons': []}
                    index = len(report['requests'])
                    # Publish snapshots only. Other request threads may save the
                    # report while this handler accumulates streaming metadata.
                    report['requests'].append(json.loads(json.dumps(observation)))
                    save()
            if exhausted:
                return self.reject(429)
            upstream = connect()
            sent = False
            started = time.monotonic()
            digest = hashlib.sha256()
            total = 0
            pending = b''

            def metadata(payload):
                if not isinstance(payload, dict):
                    return
                # Never preserve arbitrary provider error text or model prose.
                for name in ('id', 'model', 'provider'):
                    value = payload.get(name)
                    if isinstance(value, str) and len(value) <= 200:
                        observation[name] = value
                usage = payload.get('usage')
                if isinstance(usage, dict):
                    numeric = {k: v for k, v in usage.items()
                               if k in ('prompt_tokens', 'completion_tokens', 'total_tokens', 'cost')
                               and type(v) in (int, float) and v >= 0}
                    if numeric and len(observation['usage']) < 32:
                        observation['usage'].append(numeric)
                for choice in payload.get('choices', []):
                    reason = choice.get('finish_reason')
                    if reason in ('stop', 'length', 'tool_calls', 'content_filter', 'error') and len(observation['finish_reasons']) < 32:
                        observation['finish_reasons'].append(reason)

            try:
                upstream.request('POST', '/api/v1/chat/completions', body=outgoing,
                                 headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
                response = upstream.getresponse()
                observation['http_status'] = response.status
                self.send_response(response.status)
                content_type = response.getheader('Content-Type', 'application/json')
                self.send_header('Content-Type', content_type)
                self.send_header('Connection', 'close')
                self.end_headers()
                sent = True
                while True:
                    chunk = response.read1(16384)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_RESPONSE or time.monotonic() - started > 120:
                        raise TimeoutError('relay response bound exceeded')
                    digest.update(chunk)
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    pending += chunk
                    if 'text/event-stream' in content_type:
                        while b'\n' in pending:
                            line, pending = pending.split(b'\n', 1)
                            if line.startswith(b'data:'):
                                try:
                                    metadata(json.loads(line[5:]))
                                except (ValueError, UnicodeError):
                                    pass  # SSE keepalives and [DONE] are not JSON.
                    if len(pending) > MAX_BODY:
                        raise ValueError('relay metadata frame bound exceeded')
                if 'text/event-stream' not in content_type:
                    try:
                        metadata(json.loads(pending))
                    except (ValueError, UnicodeError):
                        pass  # HTTP status remains the outcome for non-JSON errors.
                observation['status'] = 'completed'
            except (OSError, http.client.HTTPException, ValueError, TypeError, AttributeError) as error:
                observation['status'] = 'failed'
                observation['failure_type'] = type(error).__name__
                if not sent:
                    self.send_response(502)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
            finally:
                upstream.close()
                self.close_connection = True
                observation.update(response_bytes=total, response_sha256=digest.hexdigest(),
                                   duration_seconds=round(time.monotonic() - started, 3))
                with lock:
                    report['requests'][index] = observation
                    save()

    # Non-daemon request threads are joined on close; each has bounded I/O.
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with lock:
            save()
        yield {'provider': 'trial-openrouter', 'model': MODEL, 'binding': 'opencode-openrouter',
               'endpoint': f'http://127.0.0.1:{server.server_port}/v1', 'api_key': token, 'lease_id': None}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
