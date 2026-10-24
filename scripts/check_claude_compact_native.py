"""Exercise Claude native compact notice read and settlement via loopback only."""
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
    spec = importlib.util.spec_from_file_location('claude_compact_installer',
        Path(__file__).with_name('install-agent-coordination.py'))
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    root.mkdir(mode=0o700)
    home = root / 'home'
    home.mkdir(mode=0o700)
    settings = home / 'settings.json'
    executable_dir = str(Path(binary).resolve(strict=True).parent)
    config = dict(config, harness='claude', binding='claude-compact-native',
        process_names=['claude'], process_exe_dirs=[executable_dir],
        compact_inbox_version=1, config_home=str(home), native_delivery=True,
        state_dir=str(root / 'engine/state/claude-compact-native'),
        cairn=str(Path(config['cairn']).resolve()))
    installed = installer.install(root / 'engine', settings, config)
    native_id = str(uuid.uuid4())
    body = 'Native compact notice body. ' + ('selected exact source text. ' * 240)
    source = api_call('alice', 'create', raw=True, body=json.dumps(dict(request_id=str(uuid.uuid4()), draft=dict(
        kind='note', body=body, sensitivity='shareable', claim_type='self',
        scope=dict(repo=config['repo'], task_id='*', run_id='*')))))
    message = None
    requests, failures, tool_results = [], [], {}
    source_visibility = dict(before_read=None, after_read=False)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            nonlocal message
            self.connection.settimeout(10)
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 2 * 1024 * 1024 or len(requests) >= 6:
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
                                tool_results[block['tool_use_id']] = block
                if message is None:
                    entries = api_call('bob', 'agents', 'list', '--harness', 'claude')['agents']
                    target = next(a for a in entries if a['native_session_id'] == native_id)
                    assert target['metadata']['state'] == 'busy'
                    message = api_call('alice', 'publish', '--request-id', str(uuid.uuid4()), '--to', target['inbox'],
                        '--kind', 'notice', '--version', str(source['version']), source['record_id'])
                    block = dict(type='text', text='Initial local fixture turn finished.')
                else:
                    contexts = list((root / 'engine/state/claude-compact-native/inbox').glob('*.json'))
                    context = next(json.loads(p.read_text()) for p in contexts
                                   if json.loads(p.read_text()).get('event_id') == message['event_id'])
                    handle = context.get('compact_handle')
                    assert isinstance(handle, str) and len(handle) == 16
                    serialized = json.dumps(request['messages'])
                    assert handle in serialized, 'compact handle was absent from the native provider request'
                    assert 'Bash' in [tool['name'] for tool in request['tools']]
                    if 'compact_read' not in tool_results:
                        source_visibility['before_read'] = body in serialized
                        assert not source_visibility['before_read'], 'full source was present before explicit compact read'
                        command = shlex.join([str(Path(installed).parents[1] / 'coordination.py'),
                            'inbox', 'compact', 'read', handle])
                        block = dict(type='tool_use', id='compact_read', name='Bash',
                                     input=dict(command=command, description='Read exact compact notice source'))
                    elif 'compact_settle' not in tool_results:
                        result = tool_results['compact_read']
                        rendered = json.dumps(result.get('content'))
                        assert body in rendered and source['record_id'] in rendered and 'notice' in rendered
                        source_visibility['after_read'] = body in serialized
                        command = shlex.join([str(Path(installed).parents[1] / 'coordination.py'),
                            'inbox', 'compact', 'settle', handle, 'handled'])
                        block = dict(type='tool_use', id='compact_settle', name='Bash',
                                     input=dict(command=command, description='Explicitly settle compact notice'))
                    else:
                        settled = json.dumps(tool_results['compact_settle'].get('content'))
                        assert 'handled' in settled and 'attempts' in settled
                        block = dict(type='text', text='Native compact notice handled.')
                encoded = response_events('native_compact_' + str(len(requests)), request['model'], block)
            except (AssertionError, ValueError, KeyError, StopIteration) as exc:
                failures.append(type(exc).__name__ + ': ' + str(exc))
                self.send_error(500)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    env = {k: os.environ[k] for k in ('PATH', 'LANG') if k in os.environ}
    env.update(HOME=str(home), CLAUDE_CONFIG_DIR=str(home), ANTHROPIC_API_KEY='fixture-only',
        ANTHROPIC_BASE_URL=f'http://127.0.0.1:{server.server_port}',
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1', ENABLE_TOOL_SEARCH='false',
        CAIRN_INBOX_ROOT=str(root / 'engine'), CLAUDE_SESSION_ID=native_id)
    thread.start()
    process = None
    try:
        with (root / 'stdout.jsonl').open('w') as out, (root / 'stderr.log').open('w') as err:
            process = subprocess.Popen([binary, '--print', '--verbose', '--output-format', 'stream-json',
                '--input-format', 'stream-json', '--setting-sources', '', '--settings', str(settings), '--strict-mcp-config',
                '--mcp-config', '{"mcpServers":{}}', '--tools', 'Bash', '--allowedTools', 'Bash', '--permission-mode', 'dontAsk',
                '--session-id', native_id, '--max-turns', '6', '--model', 'sonnet'], cwd=root, env=env,
                stdin=subprocess.PIPE, stdout=out, stderr=err, text=True, start_new_session=True)
            process.communicate(json.dumps(dict(type='user', message=dict(role='user', content='Reply INITIAL_DONE.'))) + '\n', timeout=80)
        assert process.returncode == 0 and not failures, (process.returncode, failures)
        assert message is not None and 'compact_read' in tool_results and 'compact_settle' in tool_results, \
            'native Claude compact notice did not complete read and settlement'
        status = api_call('alice', 'event-status', message['event_id'])
        assert status['deliveries'][0]['state'] == 'handled'
        print(json.dumps(dict(result='PASS', native_session_registered=True, exact_compact_read=True,
            explicit_handled_settlement=True, source_text_before_compact_read=source_visibility['before_read'],
            source_text_after_compact_read=source_visibility['after_read'],
            source_bytes=len(body.encode()), subscription_provider_called=False), sort_keys=True))
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
