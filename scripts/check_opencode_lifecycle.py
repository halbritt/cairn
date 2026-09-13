"""Native cross-harness handoff continuation; scripted provider, disposable API."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading

from check_claude_tools import response_events
from test_claude_lifecycle import hook, module, ROOT

installer = module('opencode_lifecycle_installer', ROOT / 'scripts/install-opencode-hooks.py')


def check(opencode, root, context):
    output = Path(root) / 'opencode-lifecycle'
    output.mkdir(mode=0o700)
    work, config = context['work'], context['config']
    subprocess.run([config['cairn'], 'opencode-install', '--project', str(work), '--socket', config['socket'],
                    '--token-file', config['token_file'], '--repo', config['repo']], check=True, capture_output=True, timeout=10)
    installer.install(work / '.opencode', output / 'engine', config['claude'], 'sonnet')
    env = {k: os.environ[k] for k in ('PATH', 'LANG') if k in os.environ}
    for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME', 'CLAUDE_CONFIG_DIR'):
        path = output / key.lower()
        path.mkdir(mode=0o700)
        env[key] = str(path)
    requests, selections, failures = [], [], []
    checkpoint = 'Goal: PostgreSQL lifecycle validation. Verification: OpenCode continued the Claude handoff and pulled its current body. Next: review deployment.'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.connection.settimeout(10)
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 2 * 1024 * 1024:
                self.send_error(413)
                return
            request = json.loads(self.rfile.read(size))
            try:
                if self.path.startswith('/v1/messages'):
                    selections.append(request)
                    excerpt = json.loads(request['messages'][0]['content'])
                    assert any(h['topic'] == 'PostgreSQL lifecycle validation' for h in excerpt['existing_handoffs']), excerpt
                    selection = dict(checkpoint=checkpoint, workstream='PostgreSQL lifecycle validation', memories=[])
                    schema_tool = next(t['name'] for t in request['tools'] if t['name'].lower() == 'structuredoutput')
                    body = response_events('selection', request['model'], dict(type='tool_use', id='selected', name=schema_tool, input=selection))
                else:
                    assert self.path == '/v1/chat/completions'
                    requests.append(request)
                    assert len(requests) <= 8
                    if request.get('tools'):
                        serialized = json.dumps(request['messages'])
                        assert 'Cairn lifecycle memory:' in serialized, 'Main request lacks ambient memory'
                        assert context['record']['record_id'] in serialized, 'Claude workstream was not discovered'
                        results = [m for m in request['messages'] if m['role'] == 'tool']
                        if not results:
                            parts = [m['content'] for m in request['messages'] if isinstance(m['content'], str)]
                            parts += [b['text'] for m in request['messages'] if isinstance(m['content'], list)
                                      for b in m['content'] if b.get('type') == 'text']
                            text = next(t for t in parts if 'Cairn lifecycle memory:' in t)
                            view = json.JSONDecoder().raw_decode(text[text.index('{"selected":'):])[0]
                            entry = next(e for e in view['index'] if e['record_id'] == context['record']['record_id'])
                            delta = dict(role='assistant', tool_calls=[dict(index=0, id='pull', type='function',
                                         function=dict(name='cairn_pull', arguments=json.dumps(entry['pull_arguments'])))])
                            finish = 'tool_calls'
                        else:
                            assert context['record']['body'] in json.loads(results[-1]['content'])['selection']['record']['body']
                            delta, finish = dict(role='assistant', content=checkpoint), 'stop'
                    else:
                        assert 'Cairn lifecycle memory:' not in json.dumps(request), 'Memory leaked into auxiliary request'
                        delta, finish = dict(role='assistant', content='PostgreSQL lifecycle validation'), 'stop'
                    chunks = [dict(id='probe', object='chat.completion.chunk', created=0, model='probe',
                                   choices=[dict(index=0, delta=delta, finish_reason=None)]),
                              dict(id='probe', object='chat.completion.chunk', created=0, model='probe',
                                   choices=[dict(index=0, delta={}, finish_reason=finish)])]
                    body = (''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n').encode()
            except (AssertionError, ValueError, TypeError, KeyError, StopIteration) as exc:
                failures.append(repr(exc))
                self.send_error(400)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    endpoint = f'http://127.0.0.1:{server.server_port}'
    settings = output / 'opencode.json'
    settings.write_text(json.dumps(dict(model='fixture/probe', provider={'fixture': dict(npm='@ai-sdk/openai-compatible',
        name='Scripted lifecycle fixture', options=dict(baseURL=endpoint + '/v1', apiKey='unused'),
        models={'probe': dict(name='Probe')})}, permission={'*': 'deny', 'cairn_pull': 'allow'})))
    env.update(OPENCODE_CONFIG=str(settings), OPENCODE_DISABLE_AUTOUPDATE='true', OPENCODE_DISABLE_MODELS_FETCH='true',
               OPENCODE_DISABLE_DEFAULT_PLUGINS='true', ANTHROPIC_BASE_URL=endpoint, ANTHROPIC_API_KEY='fixture-only',
               CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1', CAIRN_DATABASE_URL='host=/absent-native-lifecycle-db dbname=denied')
    thread.start()
    try:
        completed = subprocess.run([opencode, 'run', '--format', 'json', '--model', 'fixture/probe',
                                    'Continue PostgreSQL lifecycle validation from the saved handoff.'], cwd=work,
                                   env=env, capture_output=True, text=True, timeout=120)
        (output / 'stdout.jsonl').write_text(completed.stdout)
        (output / 'stderr.log').write_text(completed.stderr)
        assert completed.returncode == 0 and not failures, (completed.returncode, failures, completed.stderr[-2000:])
        assert 'Cairn lifecycle: operation failed' not in completed.stderr, completed.stderr
        assert len([r for r in requests if r.get('tools')]) == 2, requests
        assert selections, 'Native idle/dispose did not complete capture'
        record = hook.Memory(config, 'fixture-inspect').checkpoint(context['title'])
        assert record['record_id'] == context['record']['record_id'] and record['version'] == context['record']['version'] + 1, record
        assert record['body'].endswith(checkpoint)
        print('OpenCode native task-only injection, authenticated pull, idle capture and Claude workstream continuation pass')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        (output / 'requests.json').write_text(json.dumps(requests))
        (output / 'selections.json').write_text(json.dumps(selections))
        (output / 'failures.json').write_text(json.dumps(failures))
