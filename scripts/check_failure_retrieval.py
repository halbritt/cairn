"""Reviewed signature reuse through shipped interfaces on the disposable store."""
import hashlib
import json
import subprocess
import uuid

from check_note_transport import operator


def association(binary, environment, note):
    signature = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
    repo = note['scope']['repo']
    ran = subprocess.run([binary, 'run', '--repo', repo, '--task', str(uuid.uuid4()),
                          '--task-class', 'repair', '--binding', 'fixture:old-harness',
                          '--capability', 'fixture:shell', '--query', str(uuid.uuid4()),
                          '--', '/bin/false'], env=environment, text=True,
                         capture_output=True, timeout=30)
    assert ran.returncode == 1, (ran.stdout, ran.stderr)
    receipt = json.loads(ran.stderr)['data']['receipt_id']
    evidence = operator(binary, environment, 'capture-evidence', dict(
        request_id=str(uuid.uuid4()), repo=repo, body='Synthetic process exited one.',
        source='failure-signature-fixture/1'))
    operator(binary, environment, 'assess-run', dict(request_id=str(uuid.uuid4()),
        receipt_id=receipt, task_outcome='rejected', failure_domain='task',
        failure_kind='fixture', error_signature_sha256=signature,
        method='fixture-review/1', evidence_ids=[evidence['evidence_id']],
        reason='Explicit synthetic failure assessment'))
    batch = operator(binary, environment, 'generate-proposals', dict(request_id=str(uuid.uuid4()), repo=repo))
    proposal = next(p for p in batch['proposals'] if p['failure_receipt'] == receipt)
    review = dict(request_id=str(uuid.uuid4()), proposal_id=proposal['proposal_id'],
                  expected_version=proposal['version'], disposition='converted',
                  result_record=note['record_id'], result_version=note['version'],
                  reason='Selected synthetic lesson; association initially local')
    converted = operator(binary, environment, 'review-proposal', review)
    review.update(request_id=str(uuid.uuid4()), expected_version=converted['version'],
                  signature_shareable=True, reason='Share this synthetic signature association')
    return signature, review


def check_cli(binary, root, environment):
    note = operator(binary, environment, 'create', dict(request_id=str(uuid.uuid4()),
        draft=dict(kind='lesson', body='Initialize the isolated database before starting the application.',
                   sensitivity='shareable', claim_type='self',
                   scope=dict(repo='fixture:socket', task_id='*', run_id='*'))))
    signature, review = association(binary, environment, note)
    client = [binary, 'agent', '--socket', str(root / 'api.sock'), '--token-file', str(root / 'hosted-agent.token')]
    env = dict(environment, CAIRN_DATABASE_URL='host=/absent-signature-client-db dbname=denied')
    args = client + ['search', '--repo', 'fixture:socket', '--task', 'different-task',
                     '--run', 'different-harness', '--error-signature-sha256', signature, '--offset', '0']

    def search():
        return json.loads(subprocess.run(args, env=env, text=True, capture_output=True, check=True, timeout=15).stdout)['data']

    assert search()['index'] == []
    operator(binary, environment, 'review-proposal', review)
    view = search()
    assert view['error_signature_sha256'] == signature and view['ranking'] == 'lexical-scope-recency/6'
    assert [entry['record_id'] for entry in view['index']] == [note['record_id']]
    pulled = subprocess.run(client + ['expand'], input=json.dumps(view['index'][0]['pull_arguments']),
                            env=env, text=True, capture_output=True, check=True, timeout=15)
    assert json.loads(pulled.stdout)['data']['selection']['record']['body'] == note['body']
    started = subprocess.run(client + ['start', '--repo', 'fixture:socket', '--task', 'signature-start',
        '--run', 'start', '--carrier', 'stdin', '--pull-tool', 'cairn_pull',
        '--search-tool', 'cairn_search', '--error-signature-sha256', signature, '--prompt', 'Inspect the suggested guidance.',
        '--', '/bin/cat'], env=env, text=True, capture_output=True, timeout=15)
    assert started.returncode == 0, (started.stdout, started.stderr)
    assert note['record_id'] in started.stdout and signature in started.stdout
    print('Authenticated CLI and explicit startup retrieve and pull a shared reviewed signature without client database access')


def check_harness(invoke, binary, environment):
    saved = invoke('cairn_remember', dict(request_id=str(uuid.uuid4()), shareable=True,
                   kind='lesson', body='Initialize the isolated database before starting the application.'))
    note = operator(binary, environment, 'get', record_id=saved['record_id'])
    signature, review = association(binary, environment, note)
    assert invoke('cairn_search', dict(error_signature_sha256=signature))['index'] == []
    operator(binary, environment, 'review-proposal', review)
    view = invoke('cairn_search', dict(error_signature_sha256=signature, offset=0))
    assert view['error_signature_sha256'] == signature
    assert [entry['record_id'] for entry in view['index']] == [saved['record_id']]
    pulled = invoke('cairn_pull', view['index'][0]['pull_arguments'])
    assert pulled['selection']['record']['body'] == note['body']
    print('Ordinary harness tools retrieve and pull an explicitly shared reviewed lesson across task/harness labels')
