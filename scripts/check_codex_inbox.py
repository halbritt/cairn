"""Installed Codex Stop continuation with a local Responses protocol fixture."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import shlex
import threading
import time
import uuid


def response_stream(ident, item):
    response = dict(id=ident, object='response', created_at=0, status='in_progress', output=[], model='gpt-6-astra')
    events = [dict(type='response.created', response=response),
              dict(type='response.output_item.added', output_index=0, item=dict(item, status='in_progress'))]
    if item['type'] == 'message':
        part = item['content'][0]
        events += [dict(type='response.content_part.added', item_id=item['id'], output_index=0, content_index=0, part=dict(part, text='')),
            dict(type='response.output_text.delta', item_id=item['id'], output_index=0, content_index=0, delta=part['text']),
            dict(type='response.output_text.done', item_id=item['id'], output_index=0, content_index=0, text=part['text']),
            dict(type='response.content_part.done', item_id=item['id'], output_index=0, content_index=0, part=part)]
    elif item['type'] == 'function_call':
        events += [dict(type='response.function_call_arguments.done', item_id=item['id'], output_index=0, arguments=item['arguments'])]
    events += [dict(type='response.output_item.done', output_index=0, item=item),
               dict(type='response.completed', response=dict(response, status='completed', output=[item],
                    usage=dict(input_tokens=1, output_tokens=1, total_tokens=2)))]
    return ''.join('event: ' + event['type'] + '\ndata: ' + json.dumps(dict(event, sequence_number=i)) + '\n\n'
                   for i, event in enumerate(events)).encode()


def check(binary, root, config, api_call):
    spec = importlib.util.spec_from_file_location('codex_inbox_installer', Path(__file__).with_name('install-agent-coordination.py'))
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    root.mkdir(mode=0o700)
    home = root / 'home'
    home.mkdir()
    settings = home / 'hooks.json'
    config = dict(config, harness='codex', binding='codex-inbox', process_names=['codex'], config_home=str(home), native_delivery=True)
    installed = installer.install(root / 'engine', settings, config)
    native_id = None
    source = api_call('alice', 'create', raw=True, body=json.dumps(dict(request_id=str(uuid.uuid4()), draft=dict(
        kind='note', body='Native Codex selected request source', sensitivity='shareable', claim_type='self',
        scope=dict(repo=config['repo'], task_id='*', run_id='*')))))
    message = None
    requests, failures = [], []
    completed = threading.Event()

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
            if self.path != '/v1/responses':
                self.send_error(404)
                return
            requests.append(request)
            try:
                item = None
                finished = False
                results = {item['call_id']: item for item in request['input'] if item.get('type') == 'custom_tool_call_output'}
                if message is None:
                    entries = api_call('bob', 'agents', 'list', '--harness', 'codex')['agents']
                    target = next(a for a in entries if a['native_session_id'] == native_id)
                    assert target['metadata']['state'] == 'busy'
                    message = api_call('alice', 'publish', '--request-id', str(uuid.uuid4()), '--to', target['inbox'],
                        '--kind', 'request', '--version', '1', source['record_id'])
                    text = 'Initial user task finished.'
                else:
                    paths = list((root / 'engine/state/codex-inbox/inbox').glob('*.json'))
                    path = next(p for p in paths if json.loads(p.read_text())['event_id'] == message['event_id'])
                    context = json.loads(path.read_text())
                    assert str(path) in json.dumps(request['input']), 'Stop context did not reach native provider'
                    if 'source' not in results:
                        command = 'printf %s ' + shlex.quote(json.dumps(context['read_input'])) + ' | ' + shlex.join(context['read'])
                        call_id = 'source'
                    elif 'completion' not in results:
                        assert 'Native Codex selected request source' in json.dumps(results['source'])
                        command = "printf %s 'Native Codex handled selected request' | " + shlex.join(context['completion'])
                        call_id = 'completion'
                    else:
                        assert 'handled' in json.dumps(results['completion'])
                        command = None
                        text = 'Native inbox request handled.'
                        finished = True
                    if command is not None:
                        tools = next(item['tools'] for item in request['input'] if item.get('type') == 'additional_tools')
                        namespace = next(tool for tool in tools if tool['name'] == 'functions')
                        assert any(tool['name'] == 'exec' and 'tools.exec_command' in tool['description'] for tool in namespace['tools'])
                        code = 'text(await tools.exec_command(' + json.dumps(dict(cmd=command, max_output_tokens=2000)) + '));'
                        item = dict(type='custom_tool_call', id='ctc_'+call_id, name='exec', namespace='functions',
                                    call_id=call_id, input=code)
                    else:
                        item = None
                if item is None:
                    item = dict(type='message', id='msg_'+str(len(requests)), role='assistant', status='completed',
                                content=[dict(type='output_text', text=text, annotations=[])])
                body = response_stream('resp_'+str(len(requests)), item)
            except (AssertionError, ValueError, KeyError, StopIteration) as exc:
                failures.append(repr(exc))
                self.send_error(500)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            if finished:
                completed.set()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    (home / 'config.toml').write_text('model_provider = "fixture"\n[model_providers.fixture]\nname = "Fixture"\n'
        f'base_url = "http://127.0.0.1:{server.server_port}/v1"\nwire_api = "responses"\nrequires_openai_auth = false\nrequest_max_retries = 0\nstream_max_retries = 0\n')
    command = shlex.join([os.sys.executable, str(root / 'engine/coordination.py'), 'hook', '--config', str(installed)])
    def verify(rpc):
        nonlocal native_id
        native_id = rpc('thread/start', dict(cwd=str(root), model='gpt-6-astra', approvalPolicy='never', sandbox='danger-full-access', ephemeral=True))['thread']['id']
        rpc('turn/start', dict(threadId=native_id, input=[dict(type='text', text='Reply INITIAL_DONE.', text_elements=[])]))
        deadline = time.monotonic() + 45
        while not completed.wait(.1):
            assert not failures, failures
            assert time.monotonic() < deadline, 'Native Codex Stop continuation did not complete'
        assert api_call('alice', 'event-status', message['event_id'])['deliveries'][0]['state'] == 'handled'
        context = next(json.loads(p.read_text()) for p in (root / 'engine/state/codex-inbox/inbox').glob('*.json'))
        while True:
            attempt = api_call('bob', 'session-inbox-claim', raw=True, body=json.dumps(dict(request_id=context['attempt_id'],
                session={k: context[k] for k in ('agent_id', 'execution_id')})))['attempt']
            if attempt.get('finished_at'):
                break
            assert time.monotonic() < deadline, 'Native Codex final Stop did not reconcile completed handling'
            time.sleep(.1)
    thread.start()
    try:
        installer.trust_codex_hooks(home, settings, command, verify=verify, codex_binary=binary)
        assert not failures, failures
        print('Installed Codex receives busy-session mail at Stop, reads source and completes through native exec_command')
    finally:
        installer.engine.watch_once(installer.engine.load_config(installed))
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        (root / 'failures.json').write_text(json.dumps(failures))
        (root / 'requests.json').write_text(json.dumps(requests))
