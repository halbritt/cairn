"""Exercise the shipped stdio facade with an independent JSON-RPC client."""
from contextlib import contextmanager
import json
import select
import subprocess
import uuid

from check_note_transport import check_harness


@contextmanager
def session(binary, root, environment, extra_args=(), generated=False):
    env = dict(environment, CAIRN_DATABASE_URL='host=/absent-mcp-client dbname=denied')
    for key in ('HOME', 'CAIRN_HOME'):
        env.pop(key, None)
    command = [binary, 'mcp', '--socket', str(root / 'api.sock'), '--token-file',
               str(root / 'hosted-agent.token'), '--repo', 'fixture:socket',
               '--task', 'mcp-integration', '--run', 'stdio', '--tokens', '64000', *extra_args]
    if generated:
        generator_args = ['claude-config', *command[2:]] if generated == 'claude' else ['opencode-config', *command[2:], '--memory-only']
        rendered = subprocess.run([binary, *generator_args],
                                  capture_output=True, text=True, check=True, env=env, timeout=5)
        config = json.loads(rendered.stdout)
        if generated == 'claude':
            server = config['mcpServers']['cairn']
            assert server['type'] == 'stdio'
            command = [server['command'], *server['args']]
        else:
            assert config['permission'] == {'*': 'deny', 'cairn_cairn_search': 'allow', 'cairn_cairn_pull': 'allow'}
            command = config['mcp']['cairn']['command']
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, env=env)
    request_number = 0

    def send(message):
        process.stdin.write(json.dumps(dict(jsonrpc='2.0', **message)) + '\n')
        process.stdin.flush()

    def request(method, params):
        nonlocal request_number
        request_number += 1
        send(dict(id=request_number, method=method, params=params))
        assert select.select([process.stdout], [], [], 15)[0], 'MCP response timed out'
        line = process.stdout.readline()
        assert line, 'MCP exited without a response'
        response = json.loads(line)
        assert response['jsonrpc'] == '2.0' and response['id'] == request_number, response
        assert 'error' not in response, response
        return response['result']

    def tool(name, arguments, error=False):
        result = request('tools/call', dict(name=name, arguments=arguments))
        assert result.get('isError', False) == error, result
        text = result['content'][0]['text']
        return text if error else json.loads(text)

    try:
        initialized = request('initialize', dict(protocolVersion='2025-06-18', capabilities={},
                              clientInfo=dict(name='cairn-independent-stdio-check', version='1')))
        assert initialized['serverInfo']['name'] == 'cairn'
        send(dict(method='notifications/initialized', params={}))
        names = {t['name'] for t in request('tools/list', {})['tools']}
        assert names == {'cairn_search', 'cairn_pull', 'cairn_pull_evidence', 'cairn_remember', 'cairn_edit'}, names
        yield tool
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    trailing = process.stdout.read()
    errors = process.stderr.read()
    assert process.returncode == 0 and trailing == '', (process.returncode, trailing, errors)


def check(binary, root, environment, claim, support):
    with session(binary, root, environment) as tool:
        invalid = tool('cairn_remember', dict(request_id=str(uuid.uuid4()), body='Invalid direct MCP boolean', shareable='false'), error=True)
        assert 'validating' in invalid and 'boolean' in invalid, invalid
        query = 'mcpstdio' + uuid.uuid4().hex
        args = dict(request_id=str(uuid.uuid4()), body='Earlier setup context. ' * 30 + query + ': selected stdio lesson', shareable=True)
        saved = tool('cairn_remember', args)
        assert saved == tool('cairn_remember', args) and 'body' not in saved
        assert 'IDEMPOTENCY_CONFLICT' in tool('cairn_remember', dict(args, body='changed'), error=True)
        local = tool('cairn_remember', dict(request_id=str(uuid.uuid4()), body=query + ' private note'))
        view = tool('cairn_search', dict(query=query))
        assert view['schema'] == 'cairn.mcp-search/1'
        assert 'context' not in view
        assert view['scope'] == dict(repo='fixture:socket', task_id='mcp-integration', run_id='stdio')
        assert [i['record_id'] for i in view['index']] == [saved['record_id']]
        assert local['record_id'] not in json.dumps(view)
        pulled = tool('cairn_pull', view['index'][0]['pull_arguments'])
        assert pulled == tool('cairn_pull', view['index'][0]['pull_arguments'])
        assert pulled['selection']['record']['body'] == args['body']
        assert pulled['selection']['record']['observed_writer'] == 'agent:hosted-capture'
        assert pulled['credits_remaining'] == view['credits_remaining'] - 1
        location = view['index'][0]['summary_span']
        assert location['offset'] > 160
        partial_args = dict(view['index'][0]['pull_arguments'], request_id=str(uuid.uuid4()), span=location)
        partial = tool('cairn_pull', partial_args)
        assert partial['span']['body'] == args['body'][location['offset']:location['offset']+location['length']]
        assert query in partial['span']['body'] and partial['selection']['record']['body'] == ''
        assert partial['span']['total_bytes'] == len(args['body'].encode()) and partial['credits_remaining'] == 2
        assert partial == tool('cairn_pull', partial_args)
        assert 'IDEMPOTENCY_CONFLICT' in tool('cairn_pull', dict(partial_args, span=dict(offset=1, length=8)), error=True)
        missing = tool('cairn_pull', dict(view['index'][0]['pull_arguments'],
                       receipt_id=str(uuid.uuid4())), error=True)
        assert missing.startswith('NOT_FOUND:'), missing
        record = pulled['selection']['record']
        draft = {key: record[key] for key in ('kind', 'body', 'scope', 'claim_type',
                 'sensitivity', 'pins', 'relations', 'attributed_producer', 'attempt_id', 'result_ref')
                 if key in record}
        draft['body'] = query + ': corrected stdio lesson with verification context'
        edit = dict(request_id=str(uuid.uuid4()), record_id=record['record_id'],
                    expected_version=record['version'], draft=draft)
        revised = tool('cairn_edit', edit)
        assert revised == dict(record_id=record['record_id'], version=2, request_id=edit['request_id'])
        assert revised == tool('cairn_edit', edit) and 'body' not in revised
        assert 'VERSION_CONFLICT' in tool('cairn_edit', dict(edit, request_id=str(uuid.uuid4())), error=True)
        assert 'STALE_HANDLE' in tool('cairn_pull', view['index'][0]['pull_arguments'], error=True)
        assert 'STALE_HANDLE' in tool('cairn_pull', partial_args, error=True)
        evidence_view = tool('cairn_search', dict(query='socket'))
        entry = next(e for e in evidence_view['index'] if e['record_id'] == claim['record_id'])
        tool('cairn_pull', entry['pull_arguments'])
        evidence_args = dict(entry['pull_arguments'], request_id=str(uuid.uuid4()),
                             evidence_id=support['evidence_id'], expected_sha256=support['sha256'],
                             span=dict(offset=9, length=10))
        span = tool('cairn_pull_evidence', evidence_args)
        assert span['span']['body'] == 'supporting' and span['evidence']['body'] == ''
        assert span['evidence']['sha256'] == support['sha256'] and span['credits_remaining'] == 2
        assert tool('cairn_pull_evidence', evidence_args) == span
        assert 'IDEMPOTENCY_CONFLICT' in tool('cairn_pull_evidence',
                dict(evidence_args, span=dict(offset=0, length=10)), error=True)
        check_harness(tool, binary, environment)
    with session(binary, root, environment, generated=True) as tool:
        view = tool('cairn_search', dict(query=query))
        assert 'context' not in view
        assert [entry['record_id'] for entry in view['index']] == [saved['record_id']]
        corrected = tool('cairn_pull', view['index'][0]['pull_arguments'])['selection']['record']
        assert corrected['version'] == 2 and corrected['body'] == draft['body']
    with session(binary, root, environment, generated='claude') as tool:
        view = tool('cairn_search', dict(query=query))
        entry = next(e for e in view['index'] if e['record_id'] == saved['record_id'])
        assert tool('cairn_pull', entry['pull_arguments'])['selection']['record']['body'] == draft['body']
    if environment.get('CAIRN_CLAUDE_BINARY'):
        from check_claude_config import check as check_claude
        check_claude(environment['CAIRN_CLAUDE_BINARY'], binary, root)
        from check_claude_tools import check as check_claude_tools
        check_claude_tools(environment['CAIRN_CLAUDE_BINARY'], binary, root, environment, claim, support)
    # Startup failures must not put a Cairn response envelope on the MCP stream.
    failed = subprocess.run([binary, 'mcp'], capture_output=True, text=True, env=environment, timeout=5)
    assert failed.returncode != 0 and failed.stdout == '' and failed.stderr
    print('MCP stdio capture/search/pull/edit, exact retries, stale-version refusal, fresh-session correction and hosted filtering pass without HOME or client DB access')
