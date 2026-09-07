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


def crash_worker(deletion_id, table, column, condition, absent_path=None):
    # Pause a statement belonging only to this disposable effect. Observe the
    # exact live backend before killing our own CLI and cancelling its SQL.
    name = 'deletion_crash_' + uuid.uuid4().hex
    sql(f"CREATE FUNCTION cairn.{name}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF {condition} THEN PERFORM pg_sleep(30); END IF; RETURN NEW; END $$; CREATE TRIGGER {name} BEFORE UPDATE OF {column} ON cairn.{table} FOR EACH ROW EXECUTE FUNCTION cairn.{name}()")
    app = 'cairn-deletion-crash-' + uuid.uuid4().hex
    env = dict(os.environ, CAIRN_DATABASE_URL=os.environ['CAIRN_DATABASE_URL'] + ' application_name=' + app)
    worker = subprocess.Popen([binary, 'purge-deletion', deletion_id], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        backend = ''
        while time.monotonic() < deadline:
            backend = sql(f"SELECT pid FROM pg_stat_activity WHERE application_name='{app}' AND datname=current_database() AND state='active' AND wait_event='PgSleep'")
            if backend:
                break
            assert worker.poll() is None, 'purge worker terminated before fault boundary'
            time.sleep(.025)
        assert backend.isdigit(), 'did not observe exact live worker at purge boundary'
        if absent_path is not None:
            assert not absent_path.exists(), 'file unlink did not precede completion observation'
        worker.kill()
        worker.communicate(timeout=5)
        assert worker.returncode < 0
        assert sql(f'SELECT pg_terminate_backend({int(backend)})') == 't'
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.communicate(timeout=5)
        sql(f'DROP TRIGGER {name} ON cairn.{table}; DROP FUNCTION cairn.{name}()')


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

receipt = package['receipt_id']
crash_worker(deleted['deletion_id'], 'retrieval_receipt', 'semantic_body', f"NEW.receipt_id='{receipt}'::uuid AND NEW.semantic_body IS NULL")
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


# A filesystem unlink survives worker death even when its DB completion rolls
# back. The durable intent lets a new worker confirm absence and finish it.
managed_repo = 'fixture:managed-context:' + uid()
managed_record = invoke('create', dict(request_id=uid(), draft=dict(kind='note', body='managed file crash fixture', scope=dict(repo=managed_repo, task_id='*', run_id='*'), claim_type='self')))
run = subprocess.run([binary, 'run', '--repo', managed_repo, '--prompt', 'Synthetic managed context fixture', '--', '/bin/cat'], capture_output=True, check=True)
run_receipt = json.loads(run.stderr.splitlines()[-1])['data']
context_file = Path(run_receipt['artifacts']) / 'context.txt'
assert context_file.is_file()
preview = invoke('preview-delete', managed_record['record_id'])
assert any(t['target_type']=='managed_context' for t in preview['deletion_targets'])
managed_deletion = invoke('forget', dict(request_id=uid(), record_id=managed_record['record_id'], expected_version=managed_record['version'], grant_id=grant, preview_id=preview['preview_id']))
managed_backup = root / 'managed-deletion-pending.dump'
subprocess.run([str(pg_bin / 'pg_dump'), '--format=custom', '--file', str(managed_backup), os.environ['CAIRN_DATABASE_URL']], check=True)
managed_id = managed_deletion['deletion_id']
crash_worker(managed_id, 'deletion_effect', 'status', f"NEW.deletion_id='{managed_id}'::uuid AND NEW.target_type='managed_context' AND NEW.status='completed'", context_file)
status = invoke('deletion-status', managed_id)
assert any(e['target_type']=='managed_context' and e['status']=='pending' for e in status['effects'])
assert not context_file.exists()
status = invoke('purge-deletion', managed_id)
assert any(e['target_type']=='managed_context' and e['status']=='completed' for e in status['effects'])
assert (context_file.parent / 'outcome.json').is_file()
subprocess.run([str(pg_bin / 'createdb'), '-h', str(socket), 'cairn_managed_restore'], check=True)
subprocess.run([str(pg_bin / 'pg_restore'), '-h', str(socket), '--no-owner', '--no-privileges', '-d', 'cairn_managed_restore', str(managed_backup)], check=True)
managed_env = dict(os.environ, CAIRN_DATABASE_URL=f'host={socket} dbname=cairn_managed_restore sslmode=disable')
status = invoke('purge-deletion', managed_id, env=managed_env)
assert any(e['target_type']=='managed_context' and e['status']=='completed' for e in status['effects'])
assert not context_file.exists()
print('Registered context unlink survives worker death; retry and restored pending effect confirm absence without deleting outcome files')
