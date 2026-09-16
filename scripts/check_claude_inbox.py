"""Actual Claude Stop continuation, source read and completion via a fixture provider."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import threading
import uuid

from check_claude_tools import response_events


def check(binary, root, config, api_call):
    spec = importlib.util.spec_from_file_location('claude_inbox_installer', Path(__file__).with_name('install-agent-coordination.py'))
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    root.mkdir(mode=0o700)
    home = root / 'home'
    home.mkdir()
    settings = home / 'settings.json'
    config = dict(config, harness='claude', binding='claude-inbox', process_names=['claude'], config_home=str(home), native_delivery=True)
    installed = installer.install(root / 'engine', settings, config)
    native_id = str(uuid.uuid4())
    source = api_call('alice', 'create', raw=True, body=json.dumps(dict(request_id=str(uuid.uuid4()), draft=dict(
        kind='note', body='Native Claude selected request source', sensitivity='shareable', claim_type='self',
        scope=dict(repo=config['repo'], task_id='*', run_id='*')))))
    message = None
    requests, failures = [], []
    results = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            nonlocal message
            self.connection.settimeout(10)
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 2 * 1024 * 1024 or len(requests) >= 8:
                self.send_error(413)
                return
            request = json.loads(self.rfile.read(size))
            if self.path.split('?')[0] != '/v1/messages':
                self.send_error(404)
                return
            requests.append(request)
            try:
                for item in request['messages']:
                    if isinstance(item.get('content'), list):
                        for block in item['content']:
                            if block.get('type') == 'tool_result':
                                results[block['tool_use_id']] = block
                if message is None:
                    entries = api_call('bob', 'agents', 'list', '--harness', 'claude')['agents']
                    target = next(a for a in entries if a['native_session_id'] == native_id)
                    assert target['metadata']['state'] == 'busy'
                    message = api_call('alice', 'publish', '--request-id', str(uuid.uuid4()), '--to', target['inbox'],
                        '--kind', 'request', '--version', '1', source['record_id'])
                    block = dict(type='text', text='Initial user task finished.')
                else:
                    paths = list((root / 'engine/state/claude-inbox/inbox').glob('*.json'))
                    path = next(p for p in paths if json.loads(p.read_text())['event_id'] == message['event_id'])
                    context = json.loads(path.read_text())
                    assert str(path) in json.dumps(request['messages']), 'Stop context did not reach native provider'
                    assert 'Bash' in [tool['name'] for tool in request['tools']]
                    if 'source' not in results:
                        command = 'printf %s ' + shlex.quote(json.dumps(context['read_input'])) + ' | ' + shlex.join(context['read'])
                        block = dict(type='tool_use', id='source', name='Bash', input=dict(command=command, description='Read exact fixture source'))
                    elif 'completion' not in results:
                        assert 'Native Claude selected request source' in json.dumps(results['source'])
                        command = "printf %s 'Native Claude handled selected request' | " + shlex.join(context['completion'])
                        block = dict(type='tool_use', id='completion', name='Bash', input=dict(command=command, description='Complete fixture inbox handling'))
                    else:
                        assert 'handled' in json.dumps(results['completion'])
                        block = dict(type='text', text='Native inbox request handled.')
                body = response_events('native_inbox_' + str(len(requests)), request['model'], block)
            except (AssertionError, ValueError, KeyError, StopIteration) as exc:
                failures.append(repr(exc))
                self.send_error(500)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    env = {k: os.environ[k] for k in ('PATH', 'LANG') if k in os.environ}
    env.update(HOME=str(home), CLAUDE_CONFIG_DIR=str(home), ANTHROPIC_API_KEY='fixture-only',
        ANTHROPIC_BASE_URL=f'http://127.0.0.1:{server.server_port}', CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1', ENABLE_TOOL_SEARCH='false')
    thread.start()
    process = None
    try:
        with (root / 'stdout.jsonl').open('w') as out, (root / 'stderr.log').open('w') as err:
            process = subprocess.Popen([binary, '--print', '--verbose', '--output-format', 'stream-json',
                '--input-format', 'stream-json', '--setting-sources', '', '--settings', str(settings), '--strict-mcp-config',
                '--mcp-config', '{"mcpServers":{}}', '--tools', 'Bash', '--allowedTools', 'Bash', '--permission-mode', 'dontAsk',
                '--session-id', native_id, '--max-turns', '6', '--model', 'sonnet'], cwd=root, env=env,
                stdin=subprocess.PIPE, stdout=out, stderr=err, text=True, start_new_session=True)
            process.communicate(json.dumps(dict(type='user', message=dict(role='user', content='Reply INITIAL_DONE.'))) + '\n', timeout=60)
        assert process.returncode == 0 and not failures, (process.returncode, failures)
        assert message is not None and 'completion' in results, 'native Stop continuation did not execute completion'
        status = api_call('alice', 'event-status', message['event_id'])
        assert status['deliveries'][0]['state'] == 'handled'
        print('Installed Claude receives busy-session mail at Stop, reads source and completes through native Bash')
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        installer.engine.watch_once(installer.engine.load_config(installed))
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
