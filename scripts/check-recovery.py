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
    if isinstance(request, list):
        args.extend(request)
    elif isinstance(request, (str, Path)):
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
instruction = invoke('issue', dict(request_id=uid(), draft=dict(kind='instruction', body='Synthetic recovery instruction', scope=dict(repo=repo, task_id='*', run_id='*'), claim_type='self'), grant_id=root_grant, policy_key='recovery-instruction', reason='Install later-retracted instruction'))
# Retain a fixture whose bytes are independent of the later forgotten source.
fixture_request = dict(request_id=uid(), scope=dict(repo='fixture:admission-control:' + uid(), task_id='t', run_id='r'), query='', purpose='context', available_tokens=64000)
invoke('create', dict(request_id=uid(), draft=dict(kind='note', body='Retained admission control fixture.', scope=fixture_request['scope'], claim_type='self')))
fixture = invoke('compile', fixture_request)
checkpoint = invoke('checkpoint', dict(request_id=uid(), export_id='before-withdrawals.dump'))
backup = root / 'before-withdrawals.dump'
subprocess.run([str(pg_bin / 'pg_dump'), '--format=custom', '--file', str(backup), os.environ['CAIRN_DATABASE_URL']], check=True)

# This run and its file custody did not exist in the backup.
process = subprocess.run([binary, 'run', '--repo', repo, '--query', canary, '--prompt', 'Synthetic recovery fixture', '--', '/bin/cat'], capture_output=True, check=True)
run = json.loads(process.stderr.splitlines()[-1])['data']
context = Path(run['artifacts']) / 'context.txt'
assert context.is_file() and canary in context.read_text()
invoke('revoke-grant', dict(request_id=uid(), grant_id=grant['grant_id'], authority_id=root_grant, expected_version=grant['version'], reason='Withdraw grant after the recovery fixture backup'))
instruction_preview = invoke('preview-retract', instruction['record_id'])
invoke('retract', dict(request_id=uid(), record_id=instruction['record_id'], expected_version=instruction['version'], grant_id=root_grant, preview_id=instruction_preview['preview_id'], reason='Withdraw instruction after the backup'))
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
session_request = dict(request_id=uid(), target=backup.name, reason='Pause isolated older restore before reconciliation')
session = invoke('begin-restore', session_request, env=env)
assert invoke('begin-restore', session_request, env=env) == session
assert invoke('restore-status', env=env)['paused']
invoke('get', record['record_id'], env=env, expected='RESTORE_PAUSED')
fresh_request = dict(fixture_request, request_id=uid())
invoke('compile', fresh_request, env=env, expected='RESTORE_PAUSED')
# Reapplication commits new restrictions and imported custody, without claiming
# that the lost source events or launch receipt have been reconstructed.
apply_args = ['--request-id', uid(), '--expected-sha256', exported['sha256'],
              '--reason', 'Reapply independently retained fixture withdrawals', str(external)]
application = invoke('recovery-reapply', apply_args, env=env)
assert invoke('recovery-reapply', apply_args, env=env) == application
by_subject = {a['subject_id']: a for a in application['actions']}
for subject in (grant['grant_id'], instruction['record_id'], record['record_id']):
    action = by_subject[subject]
    assert action['outcome'] == 'reapplied' and action['current_event_id'] != action['event_id']
reconciled = invoke('recovery-inspect', external, env=env, expected='INTEGRITY_FAILURE')
assert {g['reason'] for g in reconciled['gaps']} == {'AUDIT_MISSING'}
assert reconciled['outstanding_effects'] > 0 and context.is_file()
verification = dict(session_id=session['session_id'],
    checkpoint=dict(checkpoint_id=checkpoint['checkpoint_id'], expected_sha256=checkpoint['sha256'], expected_export_id=checkpoint['export_id']),
    recovery=json.loads(external.read_text()), fixtures=[dict(receipt_id=fixture['receipt_id'], query='')])
pending = invoke('verify-restore', verification, env=env, expected='RESTORE_INCOMPLETE')
assert any(problem.startswith('OUTSTANDING_EFFECTS:') for problem in pending['problems'])
resume_request = dict(request_id=uid(), verification=verification, policy='local-restore/1', reason='Resume only after current checks and effect recovery under selected policy')
invoke('resume-restore', resume_request, env=env, expected='RESTORE_INCOMPLETE')
assert invoke('restore-status', env=env)['paused']

new_export = root / 'reapplied-restore-expectations.json'
invoke('recovery-export', new_export, env=env)
assert canary not in new_export.read_text()
new_report = invoke('recovery-inspect', new_export, env=env, expected='INTEGRITY_FAILURE')
assert {g['reason'] for g in new_report['gaps']} == {'AUDIT_MISSING'}
assert {g['event_id'] for g in new_report['gaps']} == {g['event_id'] for g in reconciled['gaps']}
# Each CLI call opens a fresh connection. The durable outbox survives the
# application process exit; it purges a post-backup file without a fake receipt.
query = "SELECT count(*) FROM cairn.retrieval_receipt WHERE receipt_id='" + run['receipt_id'] + "'"
receipt_count = subprocess.run([str(pg_bin / 'psql'), env['CAIRN_DATABASE_URL'], '-Atc', query], capture_output=True, text=True, check=True).stdout.strip()
assert receipt_count == '0'
recovered_deletion = by_subject[record['record_id']]['deletion_id']
purged = invoke('purge-deletion', recovered_deletion, env=env)
assert purged['state'] == 'limited' and not context.exists()
assert invoke('purge-deletion', recovered_deletion, env=env) == purged
pending_ids = subprocess.run([str(pg_bin / 'psql'), env['CAIRN_DATABASE_URL'], '-Atc',
    "SELECT DISTINCT deletion_id FROM cairn.deletion_effect WHERE status IN ('pending','running','failed') ORDER BY deletion_id"], capture_output=True, text=True, check=True).stdout.splitlines()
for pending_id in pending_ids:
    invoke('purge-deletion', pending_id, env=env)
rebuild_request = dict(request_id=uid(), session_id=session['session_id'])
rebuilt = invoke('rebuild-restore', rebuild_request, env=env)
assert invoke('rebuild-restore', rebuild_request, env=env) == rebuilt
ready = invoke('verify-restore', verification, env=env)
assert ready['ready'] and ready['reapplied_missing_events'] and ready['residual_effects'] > 0
admitted = invoke('resume-restore', resume_request, env=env)
assert invoke('resume-restore', resume_request, env=env) == admitted
assert not invoke('restore-status', env=env)['paused']
fresh = invoke('compile', fresh_request, env=env)
assert fresh['receipt_id'] != fixture['receipt_id']
assert invoke('purge-deletion', deletion['deletion_id'])['state'] == 'limited'
current = invoke('recovery-inspect', external)
assert current['consistent'] and not context.exists() and current['residual_effects'] > 0
print('Actual paused/resumed older restore: grant, instruction and deletion restrictions reapplied atomically; original audit gaps retained in subsequent exports; post-backup file purged through durable imported custody without recreating its receipt')
