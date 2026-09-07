#!/usr/bin/env python3
"""Exercise deletion and abrupt purge-worker loss in a disposable lifecycle DB."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

root = Path(sys.argv[1])
pg_bin = Path(sys.argv[2])
binary = str(Path('bin/cairn').resolve())
uid = lambda: str(uuid.uuid4())
grant = json.loads((root / 'root.json').read_text())['grant_id']


def invoke(command, request=None, env=None, expected='OK'):
    args = [binary, command]
    payload = None
    if isinstance(request, str):
        args.append(request)
    elif request is not None:
        payload = json.dumps(request).encode()
    result = subprocess.run(args, input=payload, env=env, capture_output=True, check=False)
    response = json.loads(result.stdout)
    assert response['status'] == expected, (command, response)
    assert (result.returncode == 0) == (expected == 'OK'), (command, result.returncode)
    return response.get('data')


def sql(query):
    return subprocess.check_output([str(pg_bin / 'psql'), os.environ['CAIRN_DATABASE_URL'], '-XAt', '-v', 'ON_ERROR_STOP=1', '-c', query], stderr=subprocess.PIPE).decode().strip()


repo = 'fixture:deletion:' + uid()
create = dict(request_id=uid(), draft=dict(kind='note', body='deletion_cli_canary_' + uid(), scope=dict(repo=repo, task_id='*', run_id='*'), claim_type='self'))
record = invoke('create', create)
package = invoke('compile', dict(request_id=uid(), scope=dict(repo=repo, task_id='task', run_id='run'), query='', purpose='context', available_tokens=64000))
preview = invoke('preview-delete', record['record_id'])
delete = dict(request_id=uid(), record_id=record['record_id'], expected_version=record['version'], grant_id=grant, preview_id=preview['preview_id'])
deleted = invoke('forget', delete)
assert deleted['state'] == 'partial'
invoke('get', record['record_id'], expected='PAYLOAD_UNAVAILABLE')
invoke('replay', package['receipt_id'], expected='PAYLOAD_UNAVAILABLE')
invoke('create', create, expected='PAYLOAD_UNAVAILABLE')
assert invoke('forget', delete)['deletion_id'] == deleted['deletion_id']

# Take a backup while work is pending. Restoring it must preserve exclusion and
# let the same durable effects resume, without replaying the forgetting command.
backup = root / 'deletion-pending.dump'
subprocess.run([str(pg_bin / 'pg_dump'), '--format=custom', '--file', str(backup), os.environ['CAIRN_DATABASE_URL']], check=True)

# The trigger pauses only this synthetic receipt's purge statement. Observe the
# live worker backend before terminating our exact CLI process and backend.
name = 'deletion_crash_' + uuid.uuid4().hex
receipt = package['receipt_id']
sql(f"CREATE FUNCTION cairn.{name}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.receipt_id='{receipt}'::uuid AND NEW.semantic_body IS NULL THEN PERFORM pg_sleep(30); END IF; RETURN NEW; END $$; CREATE TRIGGER {name} BEFORE UPDATE OF semantic_body ON cairn.retrieval_receipt FOR EACH ROW EXECUTE FUNCTION cairn.{name}()")
app = 'cairn-deletion-crash-' + uuid.uuid4().hex
env = dict(os.environ, CAIRN_DATABASE_URL=os.environ['CAIRN_DATABASE_URL'] + ' application_name=' + app)
worker = subprocess.Popen([binary, 'purge-deletion', deleted['deletion_id']], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
backend = ''
try:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        backend = sql(f"SELECT pid FROM pg_stat_activity WHERE application_name='{app}' AND datname=current_database() AND state='active' AND wait_event='PgSleep'")
        if backend:
            break
        assert worker.poll() is None, 'purge worker terminated before fault boundary'
        time.sleep(.025)
    assert backend.isdigit(), 'did not observe exact live worker at purge boundary'
    worker.kill()
    worker.communicate(timeout=5)
    assert worker.returncode < 0
    assert sql(f'SELECT pg_terminate_backend({int(backend)})') == 't'
finally:
    if worker.poll() is None:
        worker.kill()
        worker.communicate(timeout=5)
    sql(f'DROP TRIGGER {name} ON cairn.retrieval_receipt; DROP FUNCTION cairn.{name}()')
status = invoke('deletion-status', deleted['deletion_id'])
assert status['state'] == 'partial'
assert sum(e['status'] == 'completed' for e in status['effects']) == 2
assert any(e['target_type'] == 'db_retrieval_package' and e['status'] == 'pending' and e['attempts'] == 0 for e in status['effects'])
assert invoke('purge-deletion', deleted['deletion_id'])['state'] == 'limited'
assert invoke('purge-deletion', deleted['deletion_id'])['state'] == 'limited'

restored = 'cairn_deletion_restore'
socket = root / 'store' / 'socket'
subprocess.run([str(pg_bin / 'createdb'), '-h', str(socket), restored], check=True)
subprocess.run([str(pg_bin / 'pg_restore'), '-h', str(socket), '--no-owner', '--no-privileges', '-d', restored, str(backup)], check=True)
restored_env = dict(os.environ, CAIRN_DATABASE_URL=f'host={socket} dbname={restored} sslmode=disable')
invoke('get', record['record_id'], env=restored_env, expected='PAYLOAD_UNAVAILABLE')
invoke('replay', receipt, env=restored_env, expected='PAYLOAD_UNAVAILABLE')
assert invoke('deletion-status', deleted['deletion_id'], env=restored_env)['state'] == 'partial'
assert invoke('purge-deletion', deleted['deletion_id'], env=restored_env)['state'] == 'limited'
print('Abrupt worker death rolls back its active effect; retry and restored pending deletion preserve exclusion and complete database purge')
