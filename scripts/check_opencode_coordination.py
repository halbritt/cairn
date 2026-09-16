"""Installed OpenCode presence plugin through a local scripted provider."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading


def check(binary, root, config):
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
            chunks = [dict(id='probe', object='chat.completion.chunk', created=0, model='probe',
                choices=[dict(index=0, delta=dict(role='assistant', content='COORDINATION_OK'), finish_reason=None)]),
                dict(id='probe', object='chat.completion.chunk', created=0, model='probe',
                choices=[dict(index=0, delta={}, finish_reason='stop')])]
            body = (''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n').encode()
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
        models={'probe': dict(name='Probe')})}, permission={'*': 'deny'})))
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
        print('Installed OpenCode native conversation identity reaches provider and ends presence after exit')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
