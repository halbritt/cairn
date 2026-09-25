"""Exercise Claude's native MCP execution with scripted local responses, no inference."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading
import uuid

from check_note_transport import operator


def response_events(message_id, model, block):
    tool = block['type'] == 'tool_use'
    start = dict(block, input={}) if tool else dict(type='text', text='')
    delta = dict(type='input_json_delta', partial_json=json.dumps(block['input'])) if tool else dict(type='text_delta', text=block['text'])
    events = [dict(type='message_start', message=dict(id=message_id, type='message', role='assistant',
                   model=model, content=[], stop_reason=None, stop_sequence=None,
                   usage=dict(input_tokens=1, output_tokens=1))),
              dict(type='content_block_start', index=0, content_block=start),
              dict(type='content_block_delta', index=0, delta=delta),
              dict(type='content_block_stop', index=0),
              dict(type='message_delta', delta=dict(stop_reason='tool_use' if tool else 'end_turn', stop_sequence=None),
                   usage=dict(output_tokens=1)), dict(type='message_stop')]
    return ''.join('event: ' + event['type'] + '\ndata: ' + json.dumps(event) + '\n\n' for event in events).encode()


def session(claude, binary, root, output, run_id, cases, allowed, repo='fixture:socket'):
    output.mkdir(mode=0o700)
    work = output / 'workspace'
    work.mkdir()
    env = {k: os.environ[k] for k in ('PATH', 'LANG') if k in os.environ}
    for key, name in [('HOME', 'home'), ('CLAUDE_CONFIG_DIR', 'config')]:
        path = output / name
        path.mkdir(mode=0o700)
        env[key] = str(path)
    generated = subprocess.run([binary, 'claude-config', '--socket', str(root / 'api.sock'),
                               '--token-file', str(root / 'hosted-agent.token'), '--repo', repo,
                               '--task', 'claude-native-tools', '--run', run_id],
                              env=env, capture_output=True, text=True, check=True, timeout=5).stdout
    config = output / 'cairn.json'
    config.write_text(generated)
    results, requests, failures, arguments_by_case = {}, [], [], {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.connection.settimeout(10)
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if size <= 0 or size > 1024 * 1024 or len(requests) >= 2 * len(cases) + 4:
                    failures.append('invalid request size or scripted request budget exceeded')
                    self.send_error(413)
                    return
                request = json.loads(self.rfile.read(size))
                if not isinstance(request, dict):
                    raise ValueError('request body must be a JSON object')
                if self.path.split('?')[0] != '/v1/messages':
                    failures.append(f'unexpected request path: {self.path.split("?")[0]}')
                    self.send_error(404)
                    return
                requests.append(request)
                for message in request['messages']:
                    if not isinstance(message, dict):
                        raise ValueError('message must be a JSON object')
                    content = message.get('content', [])
                    if isinstance(content, list):
                        for block in content:
                            if not isinstance(block, dict):
                                raise ValueError('message block must be a JSON object')
                            if block.get('type') == 'tool_result':
                                results[block['tool_use_id']] = block
                pending = next((case for case in cases if case[0] not in results), None)
                if pending is not None:
                    call_id, tool, arguments = pending
                    if call_id not in arguments_by_case:
                        arguments_by_case[call_id] = arguments(results) if callable(arguments) else arguments
                    block = dict(type='tool_use', id=call_id, name='mcp__cairn__' + tool, input=arguments_by_case[call_id])
                else:
                    block = dict(type='text', text='Scripted fixture complete.')
                encoded = response_events('msg_fixture_' + str(len(results)), request['model'], block)
            except (KeyError, ValueError, TypeError, OSError, AssertionError, StopIteration) as exc:
                failures.append(repr(exc))
                self.send_error(500)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    env.update(ANTHROPIC_BASE_URL=f'http://127.0.0.1:{server.server_port}', ANTHROPIC_API_KEY='fixture-only',
               CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1', ENABLE_TOOL_SEARCH='false')
    command = [claude, '--bare', '--print', '--verbose', '--output-format', 'stream-json',
               '--no-session-persistence', '--strict-mcp-config', '--mcp-config', str(config),
               '--tools', '', '--allowedTools', ','.join('mcp__cairn__' + name for name in allowed),
               '--permission-mode', 'dontAsk', '--max-turns', str(len(cases) + 2),
               '--model', 'sonnet', '--', 'Execute the scripted local MCP check.']
    thread.start()
    try:
        with (output / 'stdout.jsonl').open('w') as stdout, (output / 'stderr.log').open('w') as stderr:
            process = subprocess.run(command, cwd=work, env=env, stdout=stdout, stderr=stderr, timeout=90)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        (output / 'requests.json').write_text(json.dumps(requests, indent=2) + '\n')
        (output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    assert process.returncode == 0, f'Claude exited {process.returncode}: {(output / "stderr.log").read_text()[-3000:]}; output {output}'
    assert not failures, failures
    assert set(results) == {case[0] for case in cases}, (set(results), output)
    names = {t['name'] for t in requests[0]['tools']}
    assert {'mcp__cairn__' + t for t in allowed} <= names, names
    return results


def value(results, call_id):
    result = results[call_id]
    assert not result.get('is_error'), result
    content = result['content']
    text = content if isinstance(content, str) else ''.join(b['text'] for b in content if b['type'] == 'text')
    return json.loads(text)


def check(claude, binary, root, environment, claim, support):
    output = Path(environment.get('CAIRN_CLAUDE_REPORT', str(root / 'claude-native')))
    output.mkdir(mode=0o700)
    query = 'claudenative' + uuid.uuid4().hex
    body = query + ': selected ordinary setup guidance'
    revised_body = query + ': corrected ordinary setup guidance'
    remember = dict(request_id=str(uuid.uuid4()), body=body, shareable=True)
    def entry(results, search):
        return next(e for e in value(results, search)['index'] if e['record_id'] == value(results, 'capture')['record_id'])
    def edit(results):
        return dict(request_id=edit_id, record_id=value(results, 'capture')['record_id'], expected_version=1, body=revised_body)
    edit_id = str(uuid.uuid4())
    cases = [
        ('capture', 'cairn_remember', remember),
        ('private', 'cairn_remember', dict(request_id=str(uuid.uuid4()), body=query + ' private')),
        ('text-false', 'cairn_remember', dict(request_id=str(uuid.uuid4()), body=query + ' textual false', shareable='false')),
        ('malformed', 'cairn_remember', dict(request_id=str(uuid.uuid4()), body=query + ' invalid flag', shareable='not-a-boolean')),
        ('search', 'cairn_search', dict(query=query)),
        ('pull', 'cairn_pull', lambda r: entry(r, 'search')['pull_arguments']),
        ('edit', 'cairn_edit', edit),
        ('stale-pull', 'cairn_pull', lambda r: dict(entry(r, 'search')['pull_arguments'], request_id=str(uuid.uuid4()))),
        ('stale-edit', 'cairn_edit', lambda r: dict(edit(r), request_id=str(uuid.uuid4()))),
        ('fresh-search', 'cairn_search', dict(query=query)),
        ('fresh-pull', 'cairn_pull', lambda r: entry(r, 'fresh-search')['pull_arguments']),
        ('capture-retry', 'cairn_remember', remember),
        ('edit-retry', 'cairn_edit', edit),
        ('evidence-search', 'cairn_search', dict(query='socket')),
        ('evidence', 'cairn_pull_evidence', lambda r: dict(next(e for e in value(r, 'evidence-search')['index'] if e['record_id'] == claim['record_id'])['pull_arguments'],
             request_id=str(uuid.uuid4()), evidence_id=support['evidence_id'], expected_sha256=support['sha256'], span=dict(offset=9, length=10))),
    ]
    tools = ['cairn_search', 'cairn_pull', 'cairn_pull_evidence', 'cairn_remember', 'cairn_edit']
    results = session(claude, binary, root, output / 'first', 'first', cases, tools)
    note_id = value(results, 'capture')['record_id']
    assert value(results, 'pull')['selection']['record']['body'] == body
    assert value(results, 'fresh-pull')['selection']['record']['body'] == revised_body
    assert value(results, 'capture-retry') == value(results, 'capture')
    assert value(results, 'edit-retry') == value(results, 'edit')
    assert value(results, 'evidence')['span']['body'] == 'supporting'
    assert results['malformed'].get('is_error'), results['malformed']
    for call_id, code in [('stale-pull', 'STALE_HANDLE'), ('stale-edit', 'VERSION_CONFLICT')]:
        assert results[call_id].get('is_error') and code in json.dumps(results[call_id]), results[call_id]
    assert [e['record_id'] for e in value(results, 'search')['index']] == [note_id]
    second = [('search', 'cairn_search', dict(query=query)),
              ('pull', 'cairn_pull', lambda r: next(e for e in value(r, 'search')['index'] if e['record_id'] == note_id)['pull_arguments']),
              ('denied', 'cairn_edit', dict(request_id=str(uuid.uuid4()), record_id=note_id, expected_version=2, body='must not write'))]
    fresh = session(claude, binary, root, output / 'second', 'second', second, ['cairn_search', 'cairn_pull'])
    assert value(fresh, 'pull')['selection']['record']['body'] == revised_body
    assert value(results, 'search')['scope']['run_id'] == 'first'
    assert value(fresh, 'search')['scope']['run_id'] == 'second'
    assert fresh['denied'].get('is_error'), fresh['denied']
    normalized = operator(binary, environment, 'get', record_id=value(results, 'text-false')['record_id'])
    assert normalized['sensitivity'] == 'local' and normalized['body'] == query + ' textual false'
    private = operator(binary, environment, 'get', record_id=value(results, 'private')['record_id'])
    assert private['sensitivity'] == 'local' and private['body'] == query + ' private'
    stored = operator(binary, environment, 'get', record_id=note_id)
    assert stored['body'] == revised_body and stored['version'] == 2
    assert stored['sensitivity'] == 'shareable' and stored['scope']['repo'] == 'fixture:socket'
    report = dict(schema='cairn.claude-native-tools/1', claude_version=subprocess.check_output([claude, '--version'], text=True, timeout=10).strip(),
                  completed_scripted_cases=len(results) + len(fresh), exact_capture_edit_pull=True, exact_evidence_span=True,
                  retries=True, textual_false_stays_local=True, malformed_capture_refused=True, stale_refusals=True, hosted_filtering=True, fresh_session=True,
                  denied_edit_has_no_effect=True, model_inference=False, operational_store_access=False)
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))
