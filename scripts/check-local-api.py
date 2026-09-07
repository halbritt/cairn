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
(root / 'agent.token').write_text(token + '\n')
(root / 'agent.token').chmod(0o600)
(root / 'identities.json').write_text(json.dumps([dict(
    token_sha256=hashlib.sha256(token.encode()).hexdigest(), principal='agent:socket-fixture',
    repo='fixture:socket', role='agent', destination='local')]))
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
    # A second listener must not remove or replace the active socket.
    collision = subprocess.run([binary, 'serve'], env=env, capture_output=True,
                               text=True, timeout=10)
    assert collision.returncode != 0
    result = subprocess.run([binary, 'agent', 'get'],
                            input=json.dumps(dict(record_id=record['record_id'])),
                            env=env, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)['data']['record_id'] == record['record_id']
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
