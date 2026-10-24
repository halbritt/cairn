"""Run native Claude SessionEnd capture through a local Messages stub."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading
import uuid


CAPTURE_CUE = 'Select a concise Cairn handoff'
CAPTURE_DIALOGUE = 'CAIRN_NATIVE_CAPTURE_DIALOGUE_SENTINEL: retain the verified handoff.'


def sse(message_id, model, block, usage):
    tool = block['type'] == 'tool_use'
    start = dict(block, input={}) if tool else dict(type='text', text='')
    delta = (dict(type='input_json_delta', partial_json=json.dumps(block['input'])) if tool
             else dict(type='text_delta', text=block['text']))
    events = [
        dict(type='message_start', message=dict(id=message_id, type='message', role='assistant',
            model=model, content=[], stop_reason=None, stop_sequence=None,
            usage=dict(input_tokens=usage['input_tokens'], output_tokens=1,
                       cache_creation_input_tokens=usage['cache_creation_input_tokens'],
                       cache_read_input_tokens=usage['cache_read_input_tokens']))),
        dict(type='content_block_start', index=0, content_block=start),
        dict(type='content_block_delta', index=0, delta=delta),
        dict(type='content_block_stop', index=0),
        dict(type='message_delta', delta=dict(stop_reason='tool_use' if tool else 'end_turn', stop_sequence=None),
             usage=dict(output_tokens=usage['output_tokens'])),
        dict(type='message_stop'),
    ]
    return ''.join('event: ' + event['type'] + '\ndata: ' + json.dumps(event) + '\n\n'
                   for event in events).encode()


def check(claude, binary, root, repo, agent):
    api_root = Path(root)
    root = api_root / 'native-capture'
    root.mkdir(mode=0o700)
    home, config_home, work, destination = (root / name for name in ('home', 'config', 'work', 'hooks'))
    for directory in (home, config_home, work):
        directory.mkdir(mode=0o700)
    (work / '.git').mkdir()
    settings = config_home / 'settings.json'
    settings.write_text('{}', encoding='utf-8')
    config = dict(cairn=str(Path(binary).resolve()), claude=str(Path(claude).resolve(strict=True)),
        socket=str(api_root / 'api.sock'), token_file=str(api_root / 'hook.token'),
        repo=repo, harness='claude', model='sonnet', capture_observations=True)
    installer_spec = importlib.util.spec_from_file_location('claude_capture_installer',
        Path(__file__).with_name('install-claude-hooks.py'))
    installer = importlib.util.module_from_spec(installer_spec)
    installer_spec.loader.exec_module(installer)
    installer.install(settings, destination, config)

    session_id = str(uuid.uuid4())
    usage = dict(input_tokens=137, output_tokens=23,
                 cache_creation_input_tokens=9, cache_read_input_tokens=41)
    counts = dict(actor=0, capture=0)
    failures = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.connection.settimeout(10)
            try:
                size = int(self.headers.get('Content-Length', '0'))
                assert self.path.split('?', 1)[0] == '/v1/messages' and 0 < size <= 2 * 1024 * 1024
                request = json.loads(self.rfile.read(size))
                is_capture = CAPTURE_CUE in json.dumps(request.get('system'))
                role = 'capture' if is_capture else 'actor'
                counts[role] += 1
                assert sum(counts.values()) <= 4, 'native fixture request budget exceeded'
                if is_capture:
                    names = [tool.get('name', '') for tool in request.get('tools', [])]
                    tool_name = next((name for name in names if name.lower() == 'structuredoutput'), None)
                    assert tool_name, 'Claude request omitted the declared JSON schema tool'
                    output = dict(checkpoint=None, workstream=None, memories=[dict(
                        record_id=None, kind='lesson', title='Native capture fixture',
                        body='Selected by the local fixture; no dialogue or model output retained.')])
                    block = dict(type='tool_use', id='capture_result', name=tool_name, input=output)
                else:
                    block = dict(type='text', text='Local actor fixture completed.')
                encoded = sse('native_capture_' + str(sum(counts.values())), request['model'], block, usage)
            except (AssertionError, ValueError, TypeError, KeyError):
                failures.append('invalid_or_unexpected_local_messages_request')
                self.send_error(400)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    env = {key: os.environ[key] for key in ('PATH', 'LANG') if key in os.environ}
    env.update(HOME=str(home), CLAUDE_CONFIG_DIR=str(config_home), ANTHROPIC_API_KEY='fixture-only',
        ANTHROPIC_BASE_URL=f'http://127.0.0.1:{server.server_port}',
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1', ENABLE_TOOL_SEARCH='false')
    process = None
    thread.start()
    try:
        command = [str(Path(claude).resolve(strict=True)), '--print', '--verbose', '--output-format', 'stream-json',
            '--input-format', 'stream-json', '--setting-sources', '', '--settings', str(settings),
            '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--tools', '',
            '--permission-mode', 'dontAsk', '--max-turns', '1', '--model', 'sonnet', '--session-id', session_id]
        process = subprocess.Popen(command, cwd=work, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            stdout, _stderr = process.communicate(json.dumps(dict(type='user', message=dict(
                role='user', content=CAPTURE_DIALOGUE))) + '\n', timeout=60)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            raise AssertionError('native Claude SessionEnd fixture exceeded 60-second bound')
        assert process.returncode == 0 and not failures, 'native Claude/local Messages exchange failed'
        try:
            events = [json.loads(line) for line in stdout.splitlines() if line]
        except json.JSONDecodeError as exc:
            raise AssertionError('native Claude stream output was malformed') from exc
        assert counts['capture'] == 1 and counts['actor'] >= 1, \
            'fixture did not observe one capture selector and at least one native actor request'

        session_hash = hashlib.sha256(session_id.encode()).hexdigest()
        rows = agent('report', 'capture-observations', dict(repo=repo, limit=10, offset=0,
                    session_sha256=session_hash))['rows']
        assert len(rows) == 1, 'native SessionEnd did not record exactly one capture observation'
        row = rows[0]
        assert row['hook_event'] == 'SessionEnd', 'capture observation was not attributed to native SessionEnd'
        state_path = destination / 'state' / (session_id + '.json')
        state = json.loads(state_path.read_text(encoding='utf-8'))
        capture_state = state['last_capture']
        assert capture_state['outcome'] == 'saved' and capture_state['selector_calls'] == 1
        assert row['outcome'] == 'saved' and row['selector_outcome'] == 'completed'
        assert row['session_sha256'] == session_hash and row['snapshot_sha256'] == state['captured_digest']
        assert row['requested_model'] == 'sonnet'
        for name, expected in usage.items():
            assert row[name] == expected, (name, row.get(name))
        assert row['duration_ms'] is not None and row['selector_duration_ms'] is not None
        record_id = capture_state['records'][0]
        record = agent('hook', 'history', dict(record_id=record_id, version=1))['versions'][0]
        assert 'Selected by the local fixture' in record['body']
        assert CAPTURE_DIALOGUE not in json.dumps(row) + json.dumps(state) + json.dumps(record)
        dsn = os.environ.get('CAIRN_TEST_DATABASE_URL')
        assert dsn and os.environ.get('CAIRN_DATABASE_URL') == dsn, 'disposable database required'
        for table_sql in (
            "SELECT row_to_json(c)::text FROM cairn.capture_observation c WHERE repo='" + repo + "'",
            "SELECT body FROM cairn.record_version WHERE repo='" + repo + "'",
        ):
            stored = subprocess.run(['psql', dsn, '-X', '-A', '-t', '-c', table_sql],
                capture_output=True, text=True, timeout=15, check=True).stdout
            assert CAPTURE_DIALOGUE not in stored
        print(json.dumps(dict(result='PASS', native_event=row['hook_event'], capture_outcome=row['outcome'],
            selector_outcome=row['selector_outcome'], usage={key: row[key] for key in usage},
            requested_model=row['requested_model'], reported_model=row.get('reported_model'),
            duration_ms=row['duration_ms'], selector_duration_ms=row.get('selector_duration_ms'),
            cost_usd=row.get('cost_usd'),
            session_joined=True, native_actor_requests=counts['actor'], raw_dialogue_retained=False,
            provider='loopback_fixture'), sort_keys=True))
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        shutil.rmtree(root, ignore_errors=True)
