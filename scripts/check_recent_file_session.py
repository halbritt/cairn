"""Native read-to-memory retrieval, permissions and retry checks; no model inference."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def check(binary, root, environment, opencode):
    output = root / 'recent-file-session'
    output.mkdir(mode=0o700)
    client = [binary, 'agent', '--socket', str(root / 'api.sock'),
              '--token-file', str(root / 'hosted-agent.token')]
    marker = 'recentfiles' + uuid.uuid4().hex
    name = marker + '.go'
    refs = [dict(kind='file', name=name)]
    result = subprocess.run([*client, 'remember', '--repo', 'fixture:socket', '--shareable',
                             '--entity-file', name, 'Check every known applicability mismatch first.'],
                            env=environment, capture_output=True, text=True, check=True, timeout=15)
    saved = json.loads(result.stdout)['data']
    reports = []
    for case in ('baseline', 'enabled', 'search-denied', 'scope-denied', 'read-denied'):
        case_root = output / case
        work = case_root / 'work'
        work.mkdir(parents=True)
        subprocess.run(['git', 'init', '-q', str(work)], check=True)
        (work / name).write_text('package fixture\n')
        (work / 'second.go').write_text('package fixture\n')
        install = [binary, 'opencode-install', '--project', str(work), '--socket', str(root / 'api.sock'),
                   '--token-file', str(root / 'hosted-agent.token'), '--repo', 'fixture:socket']
        if case != 'baseline':
            install.append('--recent-files')
        subprocess.run(install, env=environment, capture_output=True, text=True, check=True, timeout=15)
        requests = []
        query = 'unmatched' + uuid.uuid4().hex
        capture_id = str(uuid.uuid4())

        def step(messages):
            if len(messages) == 0:
                return 'read', dict(filePath=str(work / name))
            if len(messages) == 1:
                return 'cairn_search', dict(query=query, offset=0)
            if case != 'enabled':
                return None
            first = json.loads(messages[1]['content'])
            if len(messages) == 2:
                return 'cairn_pull', first['index'][0]['pull_arguments']
            if len(messages) == 3:
                return 'read', dict(filePath=str(work / 'second.go'))
            if len(messages) == 4:
                return 'cairn_search', dict(query=query, offset=0, entities=first['query_entities'],
                                           request_id=first['request_id'])
            if len(messages) == 5:
                return 'cairn_search', dict(query=query, entities=[])
            if len(messages) == 6:
                return 'cairn_remember', dict(request_id=capture_id, body='Selected session note ' + marker,
                                             shareable=True)
            return None

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.connection.settimeout(10)
                size = int(self.headers.get('Content-Length', '0'))
                if self.path != '/v1/chat/completions' or not 0 < size <= 1024 * 1024:
                    self.send_error(400)
                    return
                request = json.loads(self.rfile.read(size))
                requests.append(request)
                (case_root / f'request-{len(requests)}.json').write_text(json.dumps(request, indent=2) + '\n')
                if len(requests) > 12:
                    self.send_error(429)
                    return
                messages = [m for m in request['messages'] if m['role'] == 'tool']
                action = step(messages) if request.get('tools') else None
                if action is None:
                    delta, finish = dict(role='assistant', content='Fixture complete.'), 'stop'
                else:
                    name_, args = action
                    delta = dict(role='assistant', tool_calls=[dict(index=0, id='step-' + str(len(messages)),
                                 type='function', function=dict(name=name_, arguments=json.dumps(args)))])
                    finish = 'tool_calls'
                chunks = [dict(id='probe', object='chat.completion.chunk', created=0, model='probe',
                               choices=[dict(index=0, delta=delta, finish_reason=None)]),
                          dict(id='probe', object='chat.completion.chunk', created=0, model='probe',
                               choices=[dict(index=0, delta={}, finish_reason=finish)])]
                data = (''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n').encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        config = dict(model='fixture/probe', provider={'fixture': dict(npm='@ai-sdk/openai-compatible',
                      name='No-inference fixture', options=dict(baseURL=f'http://127.0.0.1:{server.server_port}/v1',
                      apiKey='unused'), models={'probe': dict(name='Probe')})}, permission={
                      '*': 'deny', 'read': 'deny' if case == 'read-denied' else 'allow',
                      'cairn_search': 'deny' if case == 'search-denied' else 'allow',
                      'cairn_pull': 'allow', 'cairn_remember': 'allow'})
        if case == 'scope-denied':
            config['permission']['cairn_search'] = {'*': 'allow', 'fixture:socket': 'deny'}
        cfg = case_root / 'opencode.json'
        cfg.write_text(json.dumps(config))
        env = {key: environment[key] for key in ('PATH', 'LANG') if key in environment}
        for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME'):
            directory = case_root / key.lower()
            directory.mkdir()
            env[key] = str(directory)
        env.update(OPENCODE_CONFIG=str(cfg), OPENCODE_DISABLE_AUTOUPDATE='true',
                   OPENCODE_DISABLE_MODELS_FETCH='true', OPENCODE_DISABLE_DEFAULT_PLUGINS='true',
                   CAIRN_DATABASE_URL='host=/absent-native-recent-file-db dbname=denied')
        thread.start()
        try:
            with (case_root / 'stdout.jsonl').open('w') as stdout, (case_root / 'stderr.log').open('w') as stderr:
                run = subprocess.run([opencode, 'run', '--format', 'json', '--model', 'fixture/probe',
                                      'Exercise the scripted read and search transport.'], cwd=work, env=env,
                                     stdout=stdout, stderr=stderr, timeout=60)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        assert run.returncode == 0, (case, run.returncode)
        (case_root / 'requests.json').write_text(json.dumps(requests, indent=2) + '\n')
        main = [r for r in requests if r.get('tools')]
        assert len(main) == (8 if case == 'enabled' else 3), (case, len(main))
        messages = [m for m in main[-1]['messages'] if m['role'] == 'tool']
        if case == 'search-denied':
            assert 'cairn_search' not in [t['function']['name'] for t in main[0]['tools']]
            assert 'unavailable tool' in messages[1]['content'], messages[1]
            assert saved['record_id'] not in messages[1]['content']
        elif case == 'scope-denied':
            assert 'cairn_search' in [t['function']['name'] for t in main[0]['tools']]
            assert 'prevents you from using this specific tool call' in messages[1]['content'], messages[1]
            assert '"pattern":"fixture:socket","action":"deny"' in messages[1]['content'], messages[1]
            assert saved['record_id'] not in messages[1]['content']
        else:
            first = json.loads(messages[1]['content'])
            assert first['query_entities'] == (refs if case == 'enabled' else []), (case, first)
            assert [e['record_id'] for e in first['index']] == ([saved['record_id']] if case == 'enabled' else []), (case, first)
        if case == 'enabled':
            assert json.loads(messages[2]['content'])['selection']['record']['record_id'] == saved['record_id']
            retry = json.loads(messages[4]['content'])
            assert retry['receipt_id'] == first['receipt_id'] and retry['query_entities'] == refs
            assert not json.loads(messages[5]['content'])['index']
            captured = json.loads(messages[6]['content'])
            history = subprocess.run([*client, 'history'], env=environment,
                input=json.dumps(dict(record_id=captured['record_id'], version=1)),
                capture_output=True, text=True, check=True, timeout=15)
            assert not json.loads(history.stdout)['data']['versions'][0].get('entities')
        reports.append(dict(case=case, main_requests=len(main), inference_calls=0, passed=True))
    report = dict(schema='cairn.recent-file-session-check/1',
                  opencode_sha256=hashlib.sha256(Path(opencode).read_bytes()).hexdigest(), cases=reports)
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))
