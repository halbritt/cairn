"""Real typed-facade/API rolling-upgrade check, using only a wrapper-owned PG.

Run with scripts/trial-task-eval.sh -- python3 scripts/check_preview_compat.py
CURRENT OLD_D6 OUTPUT [--opencode BINARY]. No models or production profiles.
"""
from contextlib import contextmanager
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import select
import subprocess
import time
import uuid


def uid():
    return str(uuid.uuid4())


def run(argv, **kwargs):
    return subprocess.run(argv, check=True, capture_output=True, text=True, timeout=30, **kwargs)


@contextmanager
def facade(binary, root, env):
    process = subprocess.Popen([binary, 'mcp', '--socket', str(root / 'api.sock'),
        '--token-file', str(root / 'hosted-agent.token'), '--repo', 'fixture:socket',
        '--task', 'compat', '--run', 'stdio', '--tokens', '32000'], env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    number = 0

    def request(method, params):
        nonlocal number
        number += 1
        process.stdin.write(json.dumps(dict(jsonrpc='2.0', id=number, method=method, params=params)) + '\n')
        process.stdin.flush()
        assert select.select([process.stdout], [], [], 15)[0], 'MCP timeout'
        response = json.loads(process.stdout.readline())
        assert response.get('id') == number and 'error' not in response, response
        return response['result']

    def tool(name, args, error=False):
        result = request('tools/call', dict(name=name, arguments=args))
        assert bool(result.get('isError')) == error, result
        text = result['content'][0]['text']
        return text if error else json.loads(text)

    try:
        request('initialize', dict(protocolVersion='2025-06-18', capabilities={},
                                  clientInfo=dict(name='preview-compat', version='1')))
        process.stdin.write(json.dumps(dict(jsonrpc='2.0', method='notifications/initialized', params={})) + '\n')
        process.stdin.flush()
        yield tool
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        assert process.returncode == 0, process.stderr.read()


def check(current, old, root, opencode=None):
    socket = Path(os.environ['CAIRN_TASK_EVAL_PG'])
    assert socket.is_dir() and socket.parent.name.startswith('cairn-task-eval-pg.')
    root.mkdir(mode=0o700)
    database = 'preview_' + uuid.uuid4().hex
    pg = Path(os.environ['CAIRN_TASK_EVAL_PG_BIN'])
    run([str(pg / 'createdb'), '-h', str(socket), database])
    env = {key: value for key, value in os.environ.items() if not key.startswith(('CAIRN_', 'PG'))}
    env.update(CAIRN_HOME=str(root), CAIRN_DATABASE_URL=f'host={socket} dbname={database} sslmode=disable')
    report = dict(checks=[], binaries={})
    for label, binary in [('old', old), ('current', current)]:
        build = json.loads(run([binary, 'version'], env=env).stdout)['data']
        if label == 'old':
            assert build['vcs_revision'].startswith('d6f886f') and build['vcs_modified'] is False
        report['binaries'][label] = dict(build=build, sha256=hashlib.sha256(Path(binary).read_bytes()).hexdigest())
    run([current, 'migrate'], env=env)
    token = secrets.token_urlsafe(32)
    token_path, identities = root / 'hosted-agent.token', root / 'identities.json'
    token_path.write_text(token)
    token_path.chmod(0o600)
    identities.write_text(json.dumps([dict(token_sha256=hashlib.sha256(token.encode()).hexdigest(),
        principal='agent:preview-compat', repo='fixture:socket', role='agent', destination='hosted')]))
    identities.chmod(0o600)
    api = None

    def stop():
        nonlocal api
        if api is not None:
            api.terminate()
            api.communicate(timeout=10)
            api = None

    def start(binary):
        nonlocal api
        stop()
        api = subprocess.Popen([binary, 'serve'], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            assert api.poll() is None, api.stderr.read()
            result = subprocess.run([binary, 'agent', '--socket', str(root / 'api.sock'), '--token-file', str(token_path), 'version'],
                                    env=env, capture_output=True, text=True, timeout=2)
            if result.returncode == 0:
                return
            time.sleep(.03)
        raise AssertionError('API readiness timeout')

    def cli(binary, operation, args=(), body=None, error=False):
        result = subprocess.run([binary, 'agent', '--socket', str(root / 'api.sock'), '--token-file', str(token_path), operation, *args],
            input=None if body is None else json.dumps(body), env=env, capture_output=True, text=True, timeout=15)
        value = json.loads(result.stdout)
        assert bool(value['ok']) != error, value
        return value if error else value['data']

    def passed(label):
        report['checks'].append(label)
        print('PASS', label, flush=True)

    try:
        entities = [dict(kind='file', name='core/indexed_retrieval.go'), dict(kind='file', name='semantic/index.go')]
        body = ('Preview compatibility exact source. ' + 'Synthetic unrelated context. ' * 90)[:1200]
        note = json.loads(run([current, 'create'], env=env, input=json.dumps(dict(request_id=uid(), draft=dict(
            kind='note', body=body, entities=entities, claim_type='self', sensitivity='shareable',
            scope=dict(repo='fixture:socket', task_id='*', run_id='*'))))).stdout)['data']
        query = '"Preview compatibility exact source"'
        args = dict(query=query, available_tokens=6500, request_id=uid())
        start(old)
        with facade(old, root, env) as old_tool, facade(current, root, env) as legacy_choice:
            old_result = old_tool('cairn_search', args)
            assert not old_result['index']  # full associations exceed optional room
            new_on_old = legacy_choice('cairn_search', dict(args, request_id=uid()))
            assert not new_on_old['index']
            assert not legacy_choice('cairn_prepare_note', dict(args, request_id=uid()))['index']
            # Updated CLI against old API omits the unknown request flag.
            cli_request = uid()
            cli_args = ['--repo', 'fixture:socket', '--task', 'compat', '--run', 'stdio', '--tokens', '6500', '--request-id', cli_request, query]
            assert not cli(current, 'search', cli_args)['index']
            passed('new CLI and typed facade work with old API without opt-in')
            start(current)
            changed = cli(current, 'search', cli_args, error=True)
            assert changed['status'] == 'IDEMPOTENCY_CONFLICT', changed
            passed('changed capability across CLI invocations requires explicit new request identity')
            retry = old_tool('cairn_search', args)
            assert retry['receipt_id'] == old_result['receipt_id'] and retry['source_seal'] == old_result['source_seal']
            assert not legacy_choice('cairn_search', dict(args, request_id=uid()))['index']
            roomy = old_tool('cairn_search', dict(args, available_tokens=9500, request_id=uid()))
            assert roomy['index'][0]['entities'] == entities and 'entities_omitted' not in roomy['index'][0]
            assert old_tool('cairn_pull', roomy['index'][0]['pull_arguments'])['selection']['record']['body'] == body
            passed('held old facade/new API retains full entities, receipt retry and checked pull')
            with facade(current, root, env) as new_tool:
                for name in ['cairn_search', 'cairn_prepare_note']:
                    view = new_tool(name, dict(args, request_id=uid()))
                    entry = view['index'][0]
                    assert entry['record_id'] == note['record_id'] and entry['entities_omitted'] == 2 and not entry.get('entities')
                    assert new_tool('cairn_pull', entry['pull_arguments'])['selection']['record']['entities'] == entities
                view = cli(current, 'search', ['--repo', 'fixture:socket', '--task', 'compat', '--run', 'stdio', '--tokens', '6500', query])
                entry = view['index'][0]
                assert entry['entities_omitted'] == 2 and entry['record_id'] == note['record_id']
                passed('updated CLI, MCP search and preparation retain marker and complete pull identity')
                startup = run([current, 'agent', '--socket', str(root / 'api.sock'), '--token-file', str(token_path), 'start', '--carrier', 'stdin', '--repo', 'fixture:socket', '--task', 'compat', '--run', 'stdio', '--tokens', '6500', '--query', query, '--prompt', 'Inspect the source.', '--pull-tool', 'cairn_pull', '--search-tool', 'cairn_search', '--', '/bin/cat'], env=env)
                assert '"entities_omitted":2' in startup.stdout and note['record_id'] in startup.stdout
                passed('CLI start preserves marker in actual child input')
                if opencode:
                    check_native(current, opencode, root, env, query, note['record_id'])
                    passed('native OpenCode search and preparation retain actual CLI/API compaction marker')
                start(old)
                error = new_tool('cairn_search', dict(args, request_id=uid()), error=True)
                assert error.startswith('INVALID_REQUEST:'), error
                refusal = cli(current, 'expand', body=dict(entry['pull_arguments'], request_id=uid()), error=True)
                assert refusal['status'] == 'INTEGRITY_FAILURE', refusal
                passed('cached positive facade refuses rollback; old API refuses new sealed receipt')
        report['status'] = 'PASS'
    finally:
        stop()
        token_path.unlink(missing_ok=True)
        identities.unlink(missing_ok=True)
        report['api_stopped_credentials_removed'] = True
        (root / 'result.json').write_text(json.dumps(report, indent=2) + '\n')


def check_native(binary, native, root, env, query, record_id):
    work = root / 'native'
    work.mkdir()
    run([binary, 'opencode-install', '--project', str(work), '--socket', str(root / 'api.sock'),
         '--token-file', str(root / 'hosted-agent.token'), '--repo', 'fixture:socket', '--tokens', '6500'], env=env)
    config = work / 'opencode.json'
    config.write_text(json.dumps(dict(model='fixture/probe', provider={'fixture': {
        'npm': '@ai-sdk/openai-compatible', 'name': 'No inference',
        'options': {'baseURL': 'http://127.0.0.1:1/v1', 'apiKey': 'unused'},
        'models': {'probe': {'name': 'Probe'}}}}, permission={'*': 'deny', 'cairn_search': 'allow', 'cairn_prepare_note': 'allow'})))
    native_env = {key: env[key] for key in ('PATH', 'LANG') if key in env}
    for key in ['HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME']:
        directory = work / key.lower()
        directory.mkdir()
        native_env[key] = str(directory)
    native_env.update(OPENCODE_CONFIG=str(config), OPENCODE_DISABLE_AUTOUPDATE='true', OPENCODE_DISABLE_MODELS_FETCH='true',
                      OPENCODE_DISABLE_DEFAULT_PLUGINS='true', CAIRN_DATABASE_URL='host=/absent-native-client dbname=denied')
    for name in ['search', 'prepare_note']:
        output = run([native, 'debug', 'agent', 'build', '--pure', '--tool', 'cairn_' + name,
                      '--params', json.dumps(dict(query=query))], cwd=work, env=native_env)
        result = json.loads(json.loads(output.stdout)['result']['output'])
        entry = result['index'][0]
        assert entry['record_id'] == record_id and entry['entities_omitted'] == 2 and entry['pull_arguments']['handle']
        assert 'pull_command' not in entry and len(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode()) <= 6500


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('current', type=lambda value: str(Path(value).resolve(strict=True)))
    parser.add_argument('old', type=lambda value: str(Path(value).resolve(strict=True)))
    parser.add_argument('output', type=Path)
    parser.add_argument('--opencode', type=lambda value: str(Path(value).resolve(strict=True)))
    args = parser.parse_args()
    check(args.current, args.old, args.output.resolve(), args.opencode)
