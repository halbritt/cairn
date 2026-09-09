"""Check explicit compact startup through the actual CLI and disposable API."""
import json
import os
import selectors
import signal
import subprocess
import sys
import uuid


def memory_input(text):
    prefix, body = text.split('\n', 1)
    assert prefix == 'CAIRN MEMORY'
    guidance, body = body.split('\n', 1)
    memory, prompt = body.split('\nTASK\n', 1)
    assert 'cairn_pull' in guidance and 'cairn_search' in guidance
    return json.loads(memory), prompt


def check(binary, root, environment, grant):
    env = dict(environment, CAIRN_DATABASE_URL='host=/absent-start-client dbname=denied',
               PGPASSWORD='synthetic-start-secret', PGPASSFILE='/absent/start-passfile',
               PGSERVICEFILE='/absent/start-service', CAIRN_START_SECRET='synthetic-start-secret',
               CAIRN_TEST_DATABASE_URL='host=/absent-start-test dbname=denied')
    agent = [binary, 'agent', '--socket', str(root / 'api.sock'),
             '--token-file', str(root / 'hosted-agent.token')]

    def call(args, payload):
        result = subprocess.run([binary, *args], input=json.dumps(payload), env=environment,
                                capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
        return json.loads(result.stdout)['data']

    marker = 'startup' + uuid.uuid4().hex
    body = marker + ': retained selected context. ' + 'Useful source detail. ' * 250 + 'Exact source ending.'
    scope = dict(repo='fixture:socket', task_id='compact-start', run_id='compact-start')
    saved = call(['agent', '--token-file', str(root / 'hosted-agent.token'), 'remember',
                  '--repo', scope['repo'], '--shareable', '--kind', 'lesson', '--', body], {})
    local = call(['agent', 'remember', '--repo', scope['repo'], '--', marker + ': private source'], {})
    required = call(['issue'], dict(request_id=str(uuid.uuid4()), grant_id=grant['grant_id'],
                    draft=dict(kind='instruction', body='Preserve the startup fixture provenance.',
                               claim_type='self', sensitivity='shareable', scope=scope), mandatory=True,
                    policy_key=marker, category='workflow', reason='Verify mandatory compact startup context'))
    prompt = "Inspect the selected source; preserve $(literal), `literal`, 'single' and \"double\" quotes, café and newlines.\nSecond line."
    start = [*agent, 'start', '--repo', scope['repo'], '--task', scope['task_id'], '--run', scope['run_id'],
             '--query', marker, '--prompt', prompt, '--tokens', '8192', '--pull-tool', 'cairn_pull',
             '--search-tool', 'cairn_search']
    inspect = "import os,sys,json; print(json.dumps(dict(pid=os.getpid(),args=sys.argv[1:],stdin=sys.stdin.buffer.read().decode('utf-8'),env={k:v for k,v in os.environ.items() if k.startswith('CAIRN_') or k in ('PGPASSWORD','PGPASSFILE','PGSERVICEFILE')})))"
    literal = 'one argument with spaces; $(not-executed)'
    child = subprocess.Popen([*start, '--', sys.executable, '-c', inspect, literal], env=env,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = child.communicate('original stdin', timeout=20)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)
    assert child.returncode == 0, stderr
    observed = json.loads(stdout)
    assert observed['pid'] == child.pid, 'start must replace itself, without a supervisor process'
    assert observed['args'][0] == literal and len(observed['args']) == 2
    assert observed['env'] == {} and observed['stdin'] == 'original stdin'
    initial = observed['args'][-1]
    view, actual_prompt = memory_input(initial)
    assert actual_prompt == prompt and len(initial.encode()) <= 8192
    assert view['scope'] == scope and view['destination'] == dict(name='hosted', allow_local=False)
    assert [e['record_id'] for e in view['index']] == [saved['record_id']]
    assert any(s['record']['record_id'] == required['record_id'] and s['mandatory'] for s in view['selected'])
    assert local['record_id'] not in initial and body not in initial
    assert 'pull_command' not in initial and str(root / 'hosted-agent.token') not in initial
    assert (root / 'hosted-agent.token').read_text().strip() not in initial
    piped = subprocess.run([*start, '--carrier', 'stdin', '--', sys.executable, '-c', inspect, literal],
                           input='replaced stdin', env=env, capture_output=True, text=True, timeout=20)
    assert piped.returncode == 0, piped.stderr
    piped_result = json.loads(piped.stdout)
    piped_view, piped_prompt = memory_input(piped_result['stdin'])
    assert piped_prompt == prompt and piped_result['args'] == [literal] and piped_result['env'] == {}
    assert [e['record_id'] for e in piped_view['index']] == [saved['record_id']]
    assert len(piped_result['stdin'].encode()) <= 8192
    # Reading a selected task file must preserve bytes that shell command
    # substitution would strip, including trailing newlines and CRLF.
    task_path = root / 'selected task.txt'
    task_text = prompt + '\r\nTrailing line.\n\n'
    task_path.write_bytes(task_text.encode())
    from_file = list(start)
    prompt_position = from_file.index('--prompt')
    from_file[prompt_position:prompt_position + 2] = ['--prompt-file', str(task_path)]
    for carrier in ('stdin', 'argv'):
        launched = subprocess.run([*from_file, '--carrier', carrier, '--', sys.executable, '-c', inspect],
                                  input='original stdin', env=env, capture_output=True, text=True, timeout=20)
        assert launched.returncode == 0, launched.stderr
        inspected = json.loads(launched.stdout)
        delivered = inspected['stdin'] if carrier == 'stdin' else inspected['args'][-1]
        file_view, file_prompt = memory_input(delivered)
        assert file_prompt == task_text
        assert len(delivered.encode()) <= 8192 and str(task_path) not in delivered
        assert [e['record_id'] for e in file_view['index']] == [saved['record_id']]
        assert any(s['record']['record_id'] == required['record_id'] for s in file_view['selected'])
        assert inspected['env'] == {}
        if carrier == 'argv':
            assert inspected['stdin'] == 'original stdin'
    assert task_path.read_bytes() == task_text.encode()
    # A caller may have closed stdin; the prepared descriptor must survive exec
    # even when the anonymous file is allocated as descriptor zero.
    closed = subprocess.run(['/bin/sh', '-c', 'exec 0<&-; exec "$@"', 'closed-stdin',
                             *start, '--carrier', 'stdin', '--', sys.executable, '-c', inspect],
                            env=env, capture_output=True, text=True, timeout=20)
    assert closed.returncode == 0, closed.stderr
    closed_result = json.loads(closed.stdout)
    assert memory_input(closed_result['stdin'])[1] == prompt and closed_result['args'] == []
    pull = view['index'][0]['pull_arguments']
    whole = subprocess.run([*agent, 'expand'], input=json.dumps(pull), env=environment,
                           capture_output=True, text=True, timeout=15)
    assert whole.returncode == 2 and json.loads(whole.stdout)['status'] == 'BUDGET_REFUSED', whole.stdout
    span = dict(pull, request_id=str(uuid.uuid4()), span=dict(offset=0, length=128))
    expanded = call([*agent[1:], 'expand'], span)
    assert expanded['span']['body'] == body[:128] and expanded['credits_remaining'] == 3
    assert call([*agent[1:], 'expand'], span) == expanded
    foreign = subprocess.run([binary, 'agent', '--token-file', str(root / 'hosted.token'), 'expand'],
                             input=json.dumps(pull), env=environment, capture_output=True, text=True, timeout=15)
    assert foreign.returncode == 6 and json.loads(foreign.stdout)['status'] == 'AUTHORITY_DENIED'

    result = subprocess.run([*start, '--', sys.executable, '-c', 'import sys; sys.exit(23)'],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 23 and result.stdout == '' and result.stderr == ''
    # Once exec succeeds, ordinary harness signal handling owns the process.
    child = subprocess.Popen([*start, '--', sys.executable, '-c',
                              'import time; print("ready",flush=True); time.sleep(30)'],
                             env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        with selectors.DefaultSelector() as ready:
            ready.register(child.stdout, selectors.EVENT_READ)
            assert ready.select(timeout=15), 'launched command did not become ready'
            assert child.stdout.readline().strip() == 'ready'
        child.send_signal(signal.SIGTERM)
        assert child.wait(timeout=5) == -signal.SIGTERM
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        child.stdout.close()
        child.stderr.close()

    # Refusals must not run even a harmless command or echo prepared task input.
    for flags, code in [(['--tokens', '256'], 'BUDGET_REFUSED'),
                        (['--repo', 'fixture:another-repo'], 'AUTHORITY_DENIED')]:
        denied = subprocess.run([*start, *flags, '--', '/bin/echo', 'UNEXPECTED-LAUNCH'], env=env,
                                capture_output=True, text=True, timeout=15)
        assert denied.returncode != 0 and json.loads(denied.stdout)['status'] == code
        assert 'UNEXPECTED-LAUNCH' not in denied.stdout and prompt not in denied.stdout
    local_start = list(start)
    local_start[local_start.index('--token-file') + 1] = str(root / 'agent.token')
    denied = subprocess.run([*local_start, '--', '/bin/echo', 'UNEXPECTED-LAUNCH'], env=env,
                            capture_output=True, text=True, timeout=15)
    assert denied.returncode == 2 and json.loads(denied.stdout)['status'] == 'DESTINATION_PROHIBITED'
    unavailable = list(start)
    unavailable[unavailable.index('--socket') + 1] = str(root / 'absent-api.sock')
    denied = subprocess.run([*unavailable, '--', '/bin/echo', 'UNEXPECTED-LAUNCH'], env=env,
                            capture_output=True, text=True, timeout=15)
    assert denied.returncode != 0 and 'UNEXPECTED-LAUNCH' not in denied.stdout
    # The native whole-body case uses enough room for the source and its metadata;
    # the smaller startup above protects the refusal and selective-span path.
    native_start = list(start)
    native_start[native_start.index('--tokens') + 1] = '16000'
    native_start.extend(['--carrier', 'stdin'])
    print('Explicit startup preserves mandatory context, literal argv, PID/exit/signals and caller-bound spans; local destination and exhausted budgets refuse')
    return dict(marker=marker, body=body, record_id=saved['record_id'], start=native_start, prompt=prompt,
                scope=scope, required_record_id=required['record_id'], local_record_id=local['record_id'],
                initial_bytes=len(initial.encode()), bootstrap_receipt_id=view['receipt_id'])
