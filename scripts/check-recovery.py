#!/usr/bin/env python3
"""Compare an actual older dump with separately retained withdrawal evidence."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

root = Path(sys.argv[1])
pg_bin = Path(sys.argv[2])
binary = str(Path('bin/cairn').resolve())
uid = lambda: str(uuid.uuid4())
root_grant = json.loads((root / 'root.json').read_text())['grant_id']


def invoke(command, request=None, env=None, expected='OK'):
    args = [binary, command]
    payload = None
    if isinstance(request, (str, Path)):
        args.append(str(request))
    elif request is not None:
        payload = json.dumps(request).encode()
    result = subprocess.run(args, input=payload, env=env, capture_output=True, check=False)
    response = json.loads(result.stdout)
    assert response['status'] == expected, (command, response)
    assert (result.returncode == 0) == (expected == 'OK'), (command, result.returncode)
    return response.get('data')


repo = 'fixture:recovery:' + uid()
canary = 'recovery_payload_must_not_export_' + uid()
record = invoke('create', dict(request_id=uid(), draft=dict(kind='note', body=canary, scope=dict(repo=repo, task_id='*', run_id='*'), claim_type='self')))
grant = invoke('grant', dict(request_id=uid(), parent_id=root_grant, principal='recovery-fixture', repo=repo, capabilities=['issue'], reason='Install later-revoked recovery fixture grant'))
backup = root / 'before-withdrawals.dump'
subprocess.run([str(pg_bin / 'pg_dump'), '--format=custom', '--file', str(backup), os.environ['CAIRN_DATABASE_URL']], check=True)

# This run and its file custody did not exist in the backup.
process = subprocess.run([binary, 'run', '--repo', repo, '--query', canary, '--prompt', 'Synthetic recovery fixture', '--', '/bin/cat'], capture_output=True, check=True)
run = json.loads(process.stderr.splitlines()[-1])['data']
context = Path(run['artifacts']) / 'context.txt'
assert context.is_file() and canary in context.read_text()
invoke('revoke-grant', dict(request_id=uid(), grant_id=grant['grant_id'], authority_id=root_grant, expected_version=grant['version'], reason='Withdraw grant after the recovery fixture backup'))
preview = invoke('preview-delete', record['record_id'])
deletion = invoke('forget', dict(request_id=uid(), record_id=record['record_id'], expected_version=record['version'], grant_id=root_grant, preview_id=preview['preview_id']))
external = root / 'withdrawals-after-backup.json'
exported = invoke('recovery-export', external)
assert external.stat().st_mode & 0o777 == 0o600
assert canary not in external.read_text()
current = invoke('recovery-inspect', external)
assert current['consistent'] and current['outstanding_effects'] > 0
original_bytes = external.read_bytes()
repeat = subprocess.run([binary, 'recovery-export', str(external)], capture_output=True)
assert repeat.returncode != 0 and external.read_bytes() == original_bytes

socket = root / 'store' / 'socket'
restored = 'cairn_recovery_restore'
subprocess.run([str(pg_bin / 'createdb'), '-h', str(socket), restored], check=True)
subprocess.run([str(pg_bin / 'pg_restore'), '-h', str(socket), '--no-owner', '--no-privileges', '-d', restored, str(backup)], check=True)
env = dict(os.environ, CAIRN_DATABASE_URL=f'host={socket} dbname={restored} sslmode=disable')
report = invoke('recovery-inspect', external, env=env, expected='INTEGRITY_FAILURE')
assert not report['consistent']
gaps = {(g.get('subject_id'), g['reason']) for g in report['gaps']}
assert (grant['grant_id'], 'GRANT_REVIVED') in gaps
assert (record['record_id'], 'RECORD_REVIVED') in gaps
assert any(g['reason'] == 'AUDIT_MISSING' for g in report['gaps'])
assert any(g['reason'] == 'CONTEXT_CUSTODY_MISSING' for g in report['gaps'])
# Inspection itself must not mutate the restored DB or purge external files.
assert invoke('get', record['record_id'], env=env)['body'] == canary
assert context.is_file()
assert invoke('purge-deletion', deletion['deletion_id'])['state'] == 'limited'
current = invoke('recovery-inspect', external)
assert current['consistent'] and not context.exists() and current['residual_effects'] > 0
print('An actual pre-withdrawal restore is rejected against later external evidence: revived grant and record, missing audit and post-backup context custody; inspection is read-only')
