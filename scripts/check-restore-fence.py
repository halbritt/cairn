#!/usr/bin/env python3
"""Use a real restored DB and authenticated host API across a restore fence."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import uuid

root = Path(sys.argv[1])
binary = str(Path('bin/cairn').resolve())
socket = root / 'restore-api.sock'
env = dict(os.environ, CAIRN_DATABASE_URL=f'host={root}/store/socket dbname=cairn_restore sslmode=disable')
old = json.loads((root / 'package.json').read_text())['data']
uid = lambda: str(uuid.uuid4())
token = secrets.token_urlsafe(32)
token_file = root / 'restore-observer.token'
identities = root / 'restore-identities.json'
for path, content in [(token_file, token), (identities, json.dumps([dict(token_sha256=hashlib.sha256(token.encode()).hexdigest(), principal=f'local-uid:{os.geteuid()}', repo='fixture:restore', role='observer', destination='local')]))]:
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        stream.write(content)


def invoke(command, request, agent=False, expected='OK'):
    args = [binary, 'agent', '--socket', str(socket), '--token-file', str(token_file), command] if agent else [binary, command]
    result = subprocess.run(args, input=json.dumps(request).encode(), env=env, capture_output=True, check=False)
    response = json.loads(result.stdout)
    assert response['status'] == expected, (command, response)
    assert (result.returncode == 0) == (expected == 'OK'), (command, result.returncode)
    return response.get('data')


server = subprocess.Popen([binary, 'serve', '--socket', str(socket), '--identities', str(identities)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
try:
    deadline = time.monotonic() + 10
    while not socket.exists():
        assert server.poll() is None, 'restored API terminated before listening'
        assert time.monotonic() < deadline, 'restored API did not listen'
        time.sleep(.025)
    bind = dict(request_id=uid(), receipt_id=old['receipt_id'], task_class='build', binding_id='fixture', capability_id='fixture', command_sha256='a'*64)
    invoke('bind-run', bind, agent=True)
    fence_request = dict(request_id=uid(), reason='Fence restored receipts before allowing a fresh host launch')
    fence = invoke('fence-restore', fence_request)
    invoke('claim-run', dict(receipt_id=old['receipt_id']), agent=True, expected='STALE_PACKAGE')
    invoke('bind-run', bind, agent=True, expected='STALE_PACKAGE')
    status = invoke('run-status', dict(receipt_id=old['receipt_id']), agent=True)
    assert status['binding_observed'] and not status['launch_claimed'] and status['outcome'] is None
    # An observable false claim on a restored receipt is never permission to run.
    invoke('claim-run', dict(receipt_id=old['receipt_id']), agent=True, expected='STALE_PACKAGE')
    direct = json.loads(subprocess.check_output([binary, 'run-status', old['receipt_id']], env=env))['data']
    assert {k: v for k, v in direct.items() if k != 'observed_at'} == {k: v for k, v in status.items() if k != 'observed_at'}
    replay = json.loads(subprocess.check_output([binary, 'replay', old['receipt_id']], env=env))['data']['package']
    assert replay['seal'] == old['seal'] and replay['semantic'] == old['semantic']
    fresh = invoke('compile', dict(request_id=uid(), scope=dict(repo='fixture:restore', task_id='restore', run_id='fresh'), query='Restore fixture', purpose='context', available_tokens=64000), agent=True)
    assert invoke('fence-restore', fence_request) == fence
    invoke('claim-run', dict(receipt_id=fresh['receipt_id']), agent=True)
    invoke('claim-run', dict(receipt_id=fresh['receipt_id']), agent=True, expected='RUN_ALREADY_STARTED')
    print('Restored receipt status stays readable through API and direct CLI without authorizing launch')
    print('Authenticated host cannot launch or rebind a restored receipt after fencing; history survives, fresh compile claims once, and fence retry preserves it')
finally:
    server.terminate()
    try:
        server.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
        server.communicate(timeout=5)
        raise
