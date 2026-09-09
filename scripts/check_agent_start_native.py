"""Native compact startup and permission-checked pulls; scripted provider, no inference."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading
import uuid

from check_agent_start import memory_input


def check(binary, root, environment, opencode, fixture):
    output = Path(os.environ.get('CAIRN_OPENCODE_START_REPORT', str(root / 'opencode-start')))
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    cases = []
    for case in ('allowed', 'denied', 'stale'):
        work = output / case
        work.mkdir()
        subprocess.run([binary, 'opencode-install', '--project', str(work), '--socket', str(root / 'api.sock'),
                        '--token-file', str(root / 'hosted-agent.token'), '--repo', 'fixture:socket'],
                       env=environment, capture_output=True, text=True, check=True, timeout=15)
        env = {key: environment[key] for key in ('PATH', 'LANG') if key in environment}
        for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME'):
            directory = work / key.lower()
            directory.mkdir()
            env[key] = str(directory)
        requests = []
        failures = []
        request_lock = threading.Lock()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                try:
                    self.respond()
                except Exception as error:
                    failures.append(repr(error))
                    (work / 'fixture-errors.json').write_text(json.dumps(failures, indent=2))
                    self.send_error(400)

            def respond(self):
                self.connection.settimeout(10)
                size = int(self.headers.get('Content-Length', '0'))
                assert self.path == '/v1/chat/completions' and 0 < size < 1048576
                request = json.loads(self.rfile.read(size))
                with request_lock:
                    requests.append(request)
                    (work / f'request-{len(requests)}.json').write_text(json.dumps(request, indent=2))
                    assert len(requests) <= 5, 'scripted request budget exceeded'
                tool_results = [m for m in request['messages'] if m['role'] == 'tool']
                if request.get('tools') and not tool_results and case != 'denied':
                    text = next(m['content'] for m in request['messages'] if m['role'] == 'user')
                    if isinstance(text, list):
                        text = '\n'.join(p['text'] for p in text if p['type'] == 'text')
                    view, _ = memory_input(text)
                    entry = next(e for e in view['index'] if e['record_id'] == fixture['record_id'])
                    if case == 'stale':
                        subprocess.run([binary, 'agent', '--socket', str(root / 'api.sock'), '--token-file',
                                        str(root / 'hosted-agent.token'), 'revise'],
                                       input=json.dumps(dict(request_id=str(uuid.uuid4()), record_id=entry['record_id'],
                                            expected_version=entry['version'], repo='fixture:socket', body=fixture['body']+' Revised after startup.')),
                                       env=environment, capture_output=True, text=True, check=True, timeout=15)
                    delta = dict(role='assistant', tool_calls=[dict(index=0, id='startup-pull', type='function',
                                 function=dict(name='cairn_pull', arguments=json.dumps(entry['pull_arguments'])))])
                    finish = 'tool_calls'
                else:
                    delta, finish = dict(role='assistant', content='Fixture complete.'), 'stop'
                chunks = [dict(id='startup', object='chat.completion.chunk', created=0, model='probe',
                               choices=[dict(index=0, delta=delta, finish_reason=None)]),
                          dict(id='startup', object='chat.completion.chunk', created=0, model='probe',
                               choices=[dict(index=0, delta={}, finish_reason=finish)])]
                body = (''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n').encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        config = dict(model='fixture/probe', provider={'fixture': {
            'npm': '@ai-sdk/openai-compatible', 'name': 'Scripted startup, no inference',
            'options': {'baseURL': f'http://127.0.0.1:{server.server_port}/v1', 'apiKey': 'unused'},
            'models': {'probe': {'name': 'Probe'}}}},
            permission={'*': 'deny', 'cairn_search': 'allow', 'cairn_pull': 'deny' if case == 'denied' else 'allow'})
        config_path = work / 'opencode.json'
        config_path.write_text(json.dumps(config))
        env.update(OPENCODE_CONFIG=str(config_path), OPENCODE_DISABLE_AUTOUPDATE='true',
                   OPENCODE_DISABLE_MODELS_FETCH='true', CAIRN_DATABASE_URL='host=/absent-start-native dbname=denied')
        thread.start()
        try:
            with (work / 'stdout.jsonl').open('w') as stdout, (work / 'stderr.log').open('w') as stderr:
                result = subprocess.run([*fixture['start'], '--', opencode, 'run', '--format', 'json',
                                         '--model', 'fixture/probe'], cwd=work, env=env,
                                        stdout=stdout, stderr=stderr, timeout=60)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        assert not failures, failures
        assert result.returncode == 0, f'{case}: native exit {result.returncode}, see {work}/stderr.log'
        main = [r for r in requests if r.get('tools')]
        assert len(main) == (1 if case == 'denied' else 2), (case, len(main))
        names = {tool['function']['name'] for tool in main[0]['tools']}
        assert ('cairn_pull' in names) == (case != 'denied')
        text = next(m['content'] for m in main[0]['messages'] if m['role'] == 'user')
        if isinstance(text, list):
            text = '\n'.join(p['text'] for p in text if p['type'] == 'text')
        view, prompt = memory_input(text)
        assert prompt == fixture['prompt']
        assert view['scope'] == fixture['scope'] and len(text.encode()) <= 16000
        assert any(s['record']['record_id'] == fixture['required_record_id'] and s['mandatory'] for s in view['selected'])
        assert fixture['body'] not in text and fixture['local_record_id'] not in text
        results = [m for m in main[-1]['messages'] if m['role'] == 'tool']
        assert all(m['tool_call_id'] == 'startup-pull' for m in results)
        if case == 'allowed':
            pulled = json.loads(results[0]['content'])
            assert pulled['selection']['record']['body'] == fixture['body'] and pulled['credits_remaining'] == 3
        elif case == 'stale':
            assert len(results) == 1 and 'STALE_HANDLE' in results[0]['content']
        else:
            assert results == []
        cases.append(dict(case=case, native_exit_code=result.returncode, main_requests=len(main),
                          tool_results=len(results), startup_bytes=len(text.encode()),
                          receipt_id=view['receipt_id'], scoped_mandatory_context_preserved=True))
    report = dict(opencode_version=subprocess.check_output([opencode, '--version'], text=True).strip(),
                  model_inference_calls=0, cases=cases)
    (output / 'report.json').write_text(json.dumps(report, indent=2)+'\n')
    print('Native OpenCode startup delivers the initial index, supports a direct body pull, preserves tool denial and refuses a stale source; no inference')
    return report
