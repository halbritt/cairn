"""Installed OpenCode presence plugin through a local scripted provider."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import threading
import uuid

from check_native_inbox import inbox_tool_step, chat_stream


def check(binary, root, config, api_call):
    spec = importlib.util.spec_from_file_location('opencode_coordination_installer', Path(__file__).with_name('install-agent-coordination.py'))
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    root.mkdir(mode=0o700)
    work = root / 'work'
    work.mkdir()
    config = dict(config, harness='opencode', binding='opencode-native', process_names=[])
    installed = installer.install(root / 'engine', work / '.opencode', config)
    env = {k: os.environ[k] for k in ('PATH', 'LANG') if k in os.environ}
    for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME'):
        path = root / key.lower()
        path.mkdir(mode=0o700)
        env[key] = str(path)
    requests, failures = [], []
    active_event = None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.connection.settimeout(10)
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 2 * 1024 * 1024:
                self.send_error(413)
                return
            request = json.loads(self.rfile.read(size))
            requests.append(request)
            if self.path != '/v1/chat/completions':
                failures.append('unexpected provider route')
            # OpenCode also makes auxiliary title/summary requests without tools.
            if request.get('tools') and 'Cairn session agent-' not in json.dumps(request['messages']):
                failures.append('main request lacks native session identity')
            try:
                delta, finish = dict(role='assistant', content='COORDINATION_OK'), 'stop'
                if active_event and request.get('tools'):
                    path = next(p for p in (root / 'engine/state/opencode-native/inbox').glob('*.json')
                                if json.loads(p.read_text())['event_id'] == active_event['event_id'])
                    assert str(path) in json.dumps(request['messages'])
                    delta, finish = inbox_tool_step(request, json.loads(path.read_text()), 'Native OpenCode selected request', 'bash')
                body = chat_stream(delta, finish)
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
    settings = root / 'opencode.json'
    settings.write_text(json.dumps(dict(model='fixture/probe', provider={'fixture': dict(npm='@ai-sdk/openai-compatible',
        options=dict(baseURL=f'http://127.0.0.1:{server.server_port}/v1', apiKey='fixture-only'),
        models={'probe': dict(name='Probe')})}, permission={'*': 'deny', 'bash': 'allow'})))
    env.update(OPENCODE_CONFIG=str(settings), OPENCODE_DISABLE_AUTOUPDATE='true', OPENCODE_DISABLE_MODELS_FETCH='true',
               OPENCODE_DISABLE_DEFAULT_PLUGINS='true', CAIRN_DATABASE_URL='host=/absent-native-coordination-db dbname=denied')
    thread.start()
    try:
        result = subprocess.run([binary, 'run', '--format', 'json', '--model', 'fixture/probe', 'Reply COORDINATION_OK'],
            cwd=work, env=env, capture_output=True, text=True, timeout=90)
        (root / 'stdout.jsonl').write_text(result.stdout)
        (root / 'stderr.log').write_text(result.stderr)
        assert result.returncode == 0 and not failures, (result.returncode, failures, result.stderr[-2000:])
        assert any('Cairn session agent-' in json.dumps(r.get('messages')) for r in requests), 'no identity reached the provider'
        installer.engine.watch_once(installer.engine.load_config(installed))
        entries = installer.engine.call(config, 'agent-directory', dict(repo=config['repo'], harness='opencode', include_offline=True))['agents']
        entry = next(a for a in entries if a['metadata']['workspace'] == str(work))
        assert entry['native_session_id'].startswith('ses_') and not entry['online']
        assert entry['metadata']['observed_model'] == 'fixture/probe'
        selected = api_call('alice', 'create', raw=True, body=json.dumps(dict(request_id=str(uuid.uuid4()), draft=dict(
            kind='note', body='Native OpenCode selected request', sensitivity='shareable', claim_type='self',
            scope=dict(repo=config['repo'], task_id='*', run_id='*')))))
        active_event = api_call('alice', 'publish', '--request-id', str(uuid.uuid4()), '--to', entry['inbox'],
            '--kind', 'request', '--version', '1', selected['record_id'])
        resumed = subprocess.run([binary, 'run', '--session', entry['native_session_id'], '--format', 'json', '--model',
            'fixture/probe', 'Handle the queued Cairn inbox request.'], cwd=work, env=env, capture_output=True, text=True, timeout=90)
        (root / 'resumed.stdout.jsonl').write_text(resumed.stdout)
        (root / 'resumed.stderr.log').write_text(resumed.stderr)
        assert resumed.returncode == 0 and not failures, (resumed.returncode, failures)
        installer.engine.watch_once(installer.engine.load_config(installed))
        assert api_call('alice', 'event-status', active_event['event_id'])['deliveries'][0]['state'] == 'handled'
        print('Installed OpenCode resumes its UUID inbox, reads selected source and completes through native Bash')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
