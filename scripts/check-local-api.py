"""Exercise the installed-style Unix socket and client against the disposable DB."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time
import uuid

binary, home = sys.argv[1:]
root = Path(home)
root.mkdir(mode=0o700)
token = secrets.token_urlsafe(32)
observer_token = secrets.token_urlsafe(32)
(root / 'observer.token').write_text(observer_token + '\n')
(root / 'observer.token').chmod(0o600)
(root / 'agent.token').write_text(token + '\n')
(root / 'agent.token').chmod(0o600)
(root / 'identities.json').write_text(json.dumps([dict(
    token_sha256=hashlib.sha256(token.encode()).hexdigest(), principal='agent:socket-fixture',
    repo='fixture:socket', role='agent', destination='local'), dict(
    token_sha256=hashlib.sha256(observer_token.encode()).hexdigest(), principal='host:socket-fixture',
    repo='fixture:socket', role='observer', destination='local')]))
(root / 'identities.json').chmod(0o600)
env = dict(os.environ, CAIRN_HOME=str(root))
process = subprocess.Popen([binary, 'serve'], env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True)
try:
    deadline = time.monotonic() + 10
    while True:
        if process.poll() is not None:
            raise AssertionError('API exited before readiness: ' + process.stderr.read())
        if (root / 'api.sock').exists():
            ready = subprocess.run([binary, 'agent', 'get'],
                                   input=json.dumps(dict(record_id=str(uuid.uuid4()))),
                                   env=env, capture_output=True, text=True, timeout=5)
            if ready.returncode == 3:
                break
        if time.monotonic() >= deadline:
            raise AssertionError('API readiness deadline exceeded')
        time.sleep(.02)
    assert (root / 'api.sock').stat().st_mode & 0o777 == 0o600
    request = dict(request_id=str(uuid.uuid4()), draft=dict(
        kind='note', body='Synthetic socket lesson', claim_type='self',
        scope=dict(repo='fixture:socket', task_id='*', run_id='*')))
    result = subprocess.run([binary, 'agent', 'create'], input=json.dumps(request),
                            env=env, capture_output=True, text=True, check=True)
    record = json.loads(result.stdout)['data']
    assert record['observed_writer'] == 'agent:socket-fixture'
    assert record['witness'] == 'testimony'
    index_request = dict(request_id=str(uuid.uuid4()), scope=dict(repo='fixture:socket',task_id='fixture:task',run_id='fixture:run'), query='socket', purpose='context',available_tokens=32000)
    result = subprocess.run([binary,'agent','index'],input=json.dumps(index_request),env=env,capture_output=True,text=True,check=True)
    index = json.loads(result.stdout)['data']
    assert len(index['package']['semantic']['index']) == 1
    pull = dict(request_id=str(uuid.uuid4()),receipt_id=index['package']['receipt_id'],handle=index['handles'][0]['handle'])
    result = subprocess.run([binary,'agent','expand'],input=json.dumps(pull),env=env,capture_output=True,text=True,check=True)
    expansion=json.loads(result.stdout)['data']
    assert expansion['selection']['record']['record_id']==record['record_id'] and expansion['credits_remaining']==3
    refused = dict(index_request,request_id=str(uuid.uuid4()),available_tokens=256)
    result=subprocess.run([binary,'agent','index'],input=json.dumps(refused),env=env,capture_output=True,text=True)
    assert result.returncode != 0
    refusal=json.loads(result.stdout)
    assert refusal['status']=='BUDGET_REFUSED' and refusal['refusal_id']
    inspected=subprocess.run([binary,'agent','refusal'],input=json.dumps(dict(refusal_id=refusal['refusal_id'])),env=env,capture_output=True,text=True,check=True)
    assert json.loads(inspected.stdout)['data']['code']=='BUDGET_REFUSED'
    # A second listener must not remove or replace the active socket.
    collision = subprocess.run([binary, 'serve'], env=env, capture_output=True,
                               text=True, timeout=10)
    assert collision.returncode != 0
    result = subprocess.run([binary, 'agent', 'get'],
                            input=json.dumps(dict(record_id=record['record_id'])),
                            env=env, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)['data']['record_id'] == record['record_id']
    # The process client must work with an unusable database address. Only the
    # server owns DB access; the child receives neither CAIRN settings nor tokens.
    client_env = dict(env, CAIRN_DATABASE_URL='host=/nonexistent-cairn-host-socket dbname=denied',
                      CAIRN_HOST_SECRET='synthetic-host-secret')
    host = [binary, 'agent', '--token-file', str(root / 'observer.token')]
    request_id = str(uuid.uuid4())
    child = ('import os,sys; body=sys.stdin.read(); '
             'assert "Synthetic socket lesson" in body; '
             'assert "SOCKET-HOST-PROMPT" in body; '
             'assert not any(k.startswith("CAIRN_") for k in os.environ); '
             'print("observed-host-ok")')
    command = [*host, 'run', '--repo', 'fixture:socket', '--request-id', request_id,
               '--run', 'socket-host-run', '--prompt', 'SOCKET-HOST-PROMPT',
               '--query', 'socket', '--', sys.executable, '-c', child]
    run = subprocess.run(command, env=client_env, capture_output=True, text=True, timeout=15)
    assert run.returncode == 0 and run.stdout.strip() == 'observed-host-ok', (run.stdout, run.stderr)
    observed = json.loads(run.stderr)['data']
    assert observed['process_state'] == 'exited' and observed['outcome_id']
    status_request = json.dumps(dict(receipt_id=observed['receipt_id']))
    inspected = subprocess.run([*host, 'run-status'], input=status_request,
                               env=client_env, capture_output=True, text=True, check=True)
    status = json.loads(inspected.stdout)['data']
    assert status['launch_claimed'] and status['binding_observed']
    assert status['outcome']['observation_id'] == observed['outcome_id']
    assert status['outcome']['process_state'] == 'exited' and status['outcome']['exit_code'] == 0
    denied = subprocess.run([binary, 'agent', 'run-status'], input=status_request,
                            env=client_env, capture_output=True, text=True)
    assert denied.returncode != 0 and json.loads(denied.stdout)['status'] == 'AUTHORITY_DENIED'
    # Ordinary agents can inspect their own compile receipt, without acquiring
    # an observer role or a launch capability.
    own = subprocess.run([binary, 'agent', 'run-status'],
                         input=json.dumps(dict(receipt_id=index['package']['receipt_id'])),
                         env=client_env, capture_output=True, text=True, check=True)
    own_status = json.loads(own.stdout)['data']
    assert not own_status['launch_claimed'] and own_status['outcome'] is None
    context_file = Path(observed['artifacts']) / 'context.txt'
    assert 'SOCKET-HOST-PROMPT' not in context_file.read_text()
    retry = subprocess.run(command, env=client_env, capture_output=True, text=True, timeout=15)
    assert retry.returncode != 0 and not retry.stdout and 'RUN_ALREADY_STARTED' in retry.stderr
    for args, code, state in [(['/bin/sh', '-c', 'exit 7'], 1, 'exited'),
                              (['/bin/sleep', '10'], 124, 'timeout')]:
        run = subprocess.run([*host, 'run', '--repo', 'fixture:socket', '--timeout', '100ms',
                              '--', *args], env=client_env, capture_output=True, text=True, timeout=15)
        assert run.returncode == code and json.loads(run.stderr)['data']['process_state'] == state
    report = subprocess.run([*host, 'run-report'], input=json.dumps(dict(repo='fixture:socket', limit=10)),
                            env=client_env, capture_output=True, text=True, check=True)
    rows = json.loads(report.stdout)['data']['rows']
    assert len(rows) == 3 and all(r['outcome_observed'] and r['task_outcome'] == 'unknown' for r in rows)
    print('Owner-only run status works without client database access and does not grant observer authority')
    print('Authenticated host CLI records process outcomes without database access and preserves output/exit semantics')
finally:
    process.send_signal(signal.SIGTERM)
    try:
        process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise
assert process.returncode == 0
assert not (root / 'api.sock').exists()
print('Unix API authentication, client identity, socket ownership and graceful shutdown pass')
