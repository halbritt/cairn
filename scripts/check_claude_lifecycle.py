"""Native Claude lifecycle delivery and selected capture against a disposable API."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading
import uuid

from check_claude_tools import response_events
from check_note_transport import operator
from test_claude_lifecycle import hook, installer


def check(claude, binary, root, environment):
    output = Path(environment.get('CAIRN_CLAUDE_LIFECYCLE_REPORT', root / 'claude-lifecycle'))
    output.mkdir(mode=0o700)
    work = output / ('lifecycle' + uuid.uuid4().hex)
    work.mkdir()
    subprocess.run(['git', 'init', '--quiet', str(work)], check=True, capture_output=True, timeout=5)
    env = {key: os.environ[key] for key in ('PATH', 'LANG') if key in os.environ}
    for key, name in [('HOME', 'home'), ('CLAUDE_CONFIG_DIR', 'config')]:
        (output / name).mkdir(mode=0o700)
        env[key] = str(output / name)
    env.update(CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1', ENABLE_TOOL_SEARCH='false')
    marker = work.name
    seed = operator(binary, environment, 'create', dict(request_id=str(uuid.uuid4()), draft=dict(
        kind='decision', body=marker + ': use a disposable PostgreSQL cluster for validation.',
        claim_type='self', sensitivity='shareable', scope=dict(repo='fixture:socket', task_id='*', run_id='*'))))
    operator(binary, environment, 'create', dict(request_id=str(uuid.uuid4()), draft=dict(
        kind='note', body=marker + ': PRIVATE-NATIVE-CANARY', claim_type='self', sensitivity='local',
        scope=dict(repo='fixture:socket', task_id='*', run_id='*'))))
    settings = output / 'settings.json'
    settings.write_text('{}')
    config = dict(cairn=binary, claude=claude, socket=str(root / 'api.sock'),
                  token_file=str(root / 'hosted-agent.token'), repo='fixture:socket', model='sonnet')
    installer.install(settings, output / 'hooks', config)
    mcp = output / 'mcp.json'
    mcp.write_text(subprocess.run([binary, 'claude-config', '--socket', config['socket'], '--token-file',
        config['token_file'], '--repo', config['repo'], '--task', 'native-lifecycle', '--run', 'tools'],
        capture_output=True, text=True, check=True, timeout=5).stdout)
    session_id = str(uuid.uuid4())
    event = dict(session_id=session_id, cwd=str(work))
    requests, captures, failures, results = [], [], [], []
    checkpoint = 'Goal: verify lifecycle memory. Decision: disposable PostgreSQL. Verification: pending. Next: continue the native check.'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.connection.settimeout(10)
            size = int(self.headers.get('Content-Length', '0'))
            if size <= 0 or size > 2 * 1024 * 1024 or len(requests) >= 35:
                self.send_error(413)
                return
            request = json.loads(self.rfile.read(size))
            if self.path.split('?')[0] != '/v1/messages':
                self.send_error(404)
                return
            requests.append(request)
            try:
                serialized = json.dumps(request)
                assert 'PRIVATE-NATIVE-CANARY' not in serialized
                names = {tool['name'] for tool in request.get('tools', [])}
                if 'Select a concise Cairn handoff' in json.dumps(request.get('system')):
                    captures.append(request)
                    schema_tool = next((name for name in names if name.lower() == 'structuredoutput'), None)
                    if schema_tool:
                        block = dict(type='tool_use', id='capture_' + str(len(captures)), name=schema_tool,
                                     input=dict(checkpoint=checkpoint))
                    else:
                        block = dict(type='text', text=json.dumps(dict(checkpoint=checkpoint)))
                elif 'mcp__cairn__cairn_pull' in names:
                    assert 'Cairn lifecycle memory:' in serialized, 'Native request missing hook context'
                    result_blocks = [b for m in request['messages'] if isinstance(m.get('content'), list)
                                     for b in m['content'] if b.get('type') == 'tool_result']
                    results.extend(result_blocks)
                    if not result_blocks:
                        # Parse the actual hook-produced index delivered in a user block.
                        candidates = [b['text'] for m in request['messages'] if isinstance(m.get('content'), list)
                                      for b in m['content'] if b.get('type') == 'text' and 'Cairn lifecycle memory:' in b['text']]
                        assert candidates, 'No native context block'
                        text = candidates[-1]
                        start = text.index('{"selected":')
                        view, _ = json.JSONDecoder().raw_decode(text[start:])
                        entry = next(e for e in view['index'] if e['record_id'] == seed['record_id'])
                        block = dict(type='tool_use', id='pull_' + str(len(requests)), name='mcp__cairn__cairn_pull', input=entry['pull_arguments'])
                    else:
                        block = dict(type='text', text=checkpoint)
                else:
                    # Native compaction request. Its summary remains host-owned.
                    block = dict(type='text', text='The task is to verify lifecycle memory. Continue the native check. Tests pending.')
                encoded = response_events('msg_lifecycle_' + str(len(requests)), request['model'], block)
            except (AssertionError, KeyError, ValueError, StopIteration) as exc:
                failures.append(repr(exc))
                (output / 'failures.json').write_text(json.dumps(failures))
                self.send_error(400)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = HTTPServer(('127.0.0.1', 0), Handler)
    env.update(ANTHROPIC_BASE_URL=f'http://127.0.0.1:{server.server_port}', ANTHROPIC_API_KEY='fixture-only')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = [claude, '--print', '--verbose', '--output-format', 'stream-json', '--setting-sources', '',
            '--settings', str(settings), '--strict-mcp-config', '--mcp-config', str(mcp), '--tools', '',
            '--allowedTools', 'mcp__cairn__cairn_pull', '--permission-mode', 'dontAsk', '--max-turns', '5', '--model', 'sonnet']
    try:
        def run(label, arguments):
            assert arguments[-2] == '--'
            incoming = dict(type='user', message=dict(role='user', content=arguments[-1]))
            completed = subprocess.run(base + ['--input-format', 'stream-json'] + arguments[:-2],
                input=json.dumps(incoming) + '\n', cwd=work, env=env, capture_output=True, text=True, timeout=100)
            (output / (label + '.jsonl')).write_text(completed.stdout)
            (output / (label + '.stderr')).write_text(completed.stderr)
            assert completed.returncode == 0, (label, completed.returncode, completed.stderr[-1000:], completed.stdout[-1200:])
            assert not failures, failures
            events = [json.loads(line) for line in completed.stdout.splitlines()]
            hook_results = [event for event in events if event.get('subtype') == 'hook_response']
            assert hook_results and all(event.get('exit_code') == 0 for event in hook_results), hook_results
            assert 'hook [' not in completed.stderr or 'failed:' not in completed.stderr, completed.stderr
            return completed

        run('startup', ['--session-id', session_id, '--', 'Continue the ' + marker + ' PostgreSQL lifecycle task.'])
        assert json.dumps(requests[0]).count('Cairn lifecycle memory:') >= 2, 'Startup and submitted-task hooks must both inject'
        assert results and any(not b.get('is_error') and seed['body'] in json.dumps(b) for b in results), results
        assert captures, 'SessionEnd did not trigger selected checkpoint extraction'
        memory = hook.Memory(config, session_id)
        previous = memory.checkpoint(hook.title_for(event))
        assert previous and previous['body'].endswith(checkpoint), (previous, captures)
        assert previous['observed_writer'] == 'agent:hosted-capture'
        assert previous['scope']['task_id'] == '*' and previous['sensitivity'] == 'shareable'
        before = len(requests)
        run('resume', ['--resume', session_id, '--', 'Continue the lifecycle task.'])
        assert any(hook.title_for(event) in json.dumps(r) for r in requests[before:]), 'Resume omitted checkpoint index'
        assert memory.checkpoint(hook.title_for(event))['version'] == previous['version'], 'Identical selection created a redundant revision'
        checkpoint = 'Goal: verify lifecycle memory. Decision: disposable PostgreSQL. Verification: startup and resume passed. Next: inspect compaction.'
        run('compact', ['--resume', session_id, '--', '/compact'])
        after = memory.checkpoint(hook.title_for(event))
        assert after and after['record_id'] == previous['record_id'] and after['version'] > previous['version'], after
        assert after['body'].endswith(checkpoint)
        assert any('PreCompact' in json.dumps(r['messages']) for r in captures), 'Native PreCompact did not run'
        print('Claude native startup, task context, MCP pull, resume, PreCompact and SessionEnd selected capture pass')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        (output / 'requests.json').write_text(json.dumps(requests, indent=2))
        (output / 'failures.json').write_text(json.dumps(failures))
