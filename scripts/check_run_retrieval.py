"""Verify dynamic retrieval/outcome correspondence through real child and API calls."""
import hashlib
import json
import subprocess
import sys
import uuid

from trial_host import TrialHost


CHILD = '''
import json, subprocess, sys, uuid
binary, socket, token, scope_json, record_id = sys.argv[1:]
scope = json.loads(scope_json)
assert 'Synthetic socket lesson' not in sys.stdin.read()
def call(operation, request):
    result = subprocess.run([binary, 'agent', '--socket', socket, '--token-file', token, operation],
                            input=json.dumps(request), text=True, capture_output=True, check=True, timeout=10)
    return json.loads(result.stdout)['data']
receipts = []
for query in ['nomatchingbootstrapmarker', 'socket']:
    index = call('index', dict(request_id=str(uuid.uuid4()), scope=scope, query=query,
                              purpose='context', available_tokens=32000))
    receipt = index['package']['receipt_id']
    receipts.append(receipt)
    if query == 'nomatchingbootstrapmarker':
        assert not index['handles']
        continue
    handle = next(h for h in index['handles'] if h['record_id'] == record_id)
    pulled = call('expand', dict(request_id=str(uuid.uuid4()), receipt_id=receipt, handle=handle['handle']))
    assert pulled['selection']['record']['body'] == 'Synthetic socket lesson'
    for _ in range(2):
        call('usage', dict(request_id=str(uuid.uuid4()), receipt_id=receipt, record_id=record_id,
                           version=handle['version'], signal='cited', method='fixture:child-citation'))
print(json.dumps(dict(receipts=receipts, record_id=record_id, version=handle['version'])))
'''


def check(binary, root, environment, record):
    observer = [binary, 'agent', '--socket', str(root / 'api.sock'),
                '--token-file', str(root / 'observer.token')]
    scope = dict(repo='fixture:socket', task_id='dynamic-retrieval', run_id='dynamic-retrieval')
    host = TrialHost(root / 'dynamic-retrieval-host', observer, environment, scope)
    actual = host.run(['--query', 'nomatchingbootstrapmarker', '--prompt', 'Find the socket lesson.',
                       '--', sys.executable, '-c', CHILD, binary, str(root / 'api.sock'),
                       str(root / 'agent.token'), json.dumps(scope), record['record_id']], timeout=30)
    assert actual.returncode == 0, (actual.stdout, actual.stderr)
    outcome = json.loads(actual.stderr)['data']
    assert outcome['process_state'] == 'exited' and outcome['exit_code'] == 0 and outcome['outcome_id']
    child = json.loads(actual.stdout)
    host.finish('sha256:' + hashlib.sha256(actual.stdout).hexdigest())
    report_request = dict(repo=scope['repo'], record_id=record['record_id'], limit=200)
    before = host.call('use-report', report_request)
    source_row = next(r for r in before['rows'] if r['receipt_id'] == child['receipts'][1])
    assert source_row['process_state'] == 'unknown' and 'run_receipt_id' not in source_row
    for receipt in child['receipts']:
        request = dict(request_id=str(uuid.uuid4()), run_receipt_id=outcome['receipt_id'],
                       retrieval_receipt_id=receipt, expected_reader='agent:socket-fixture',
                       method='fixture:observed-child-response/1')
        denied = subprocess.run([binary, 'agent', 'link-run-retrieval'], input=json.dumps(request),
                                env=environment, capture_output=True, text=True, timeout=10)
        assert denied.returncode == 6 and json.loads(denied.stdout)['status'] == 'AUTHORITY_DENIED'
        link = host.call('link-run-retrieval', request)
        assert link['reader'] == 'agent:socket-fixture' and link['observer'] == 'host:socket-fixture'
        assert host.call('link-run-retrieval', request) == link

    def check_reports(task_outcome, assessment_version):
        uses = host.call('use-report', report_request)
        rows = [r for r in uses['rows'] if r['receipt_id'] in child['receipts']]
        assert len(rows) == 1, rows  # Two citations and two queries do not multiply this exposure.
        row = rows[0]
        assert row['receipt_id'] == child['receipts'][1] and row['run_receipt_id'] == outcome['receipt_id']
        assert row['usage'] == 'cited' and row['usage_witness'] == 'testimony'
        assert row['process_state'] == 'exited' and row['exit_code'] == 0
        assert row['task_outcome'] == task_outcome and row['assessment_version'] == assessment_version
        runs = host.call('run-report', dict(repo=scope['repo'], limit=200))
        runs = [r for r in runs['rows'] if r['scope'] == scope]
        assert len(runs) == 1 and runs[0]['receipt_id'] == outcome['receipt_id']
        assert runs[0]['linked_retrievals'] == 2 and runs[0]['task_outcome'] == task_outcome

    check_reports('unknown', 0)
    evidence = host.call('evidence', dict(request_id=str(uuid.uuid4()), repo=scope['repo'],
                         body=json.dumps(dict(gate='synthetic child assertions and parent report checks',
                                              child=child, process_outcome_id=outcome['outcome_id'])),
                         source='disposable dynamic retrieval API fixture', sensitivity='local'))
    assessment = host.call('assess-run', dict(request_id=str(uuid.uuid4()), receipt_id=outcome['receipt_id'],
                               expected_version=0, task_outcome='accepted', failure_domain='none',
                               method='fixture:exact-source-and-report-gate/1', evidence_ids=[evidence['evidence_id']],
                               reason='Synthetic integration assertions passed; no model benefit is measured.'))
    check_reports('accepted', 1)
    history_request = dict(receipt_id=outcome['receipt_id'])
    assert host.call('assessments', history_request) == [assessment]
    # The retrieval agent does not own the linked host's assessment history.
    denied = subprocess.run([binary, 'agent', 'assessments'], input=json.dumps(history_request),
                            env=environment, capture_output=True, text=True, timeout=10)
    assert denied.returncode == 6 and json.loads(denied.stdout)['status'] == 'AUTHORITY_DENIED'
    print('Authenticated CLI reads exact assessment narrative and evidence references; linked retrieval ownership does not grant host history access')
    print('Dynamic agent retrievals join an observed child outcome and explicit fixture assessment through the Unix API; one execution, original citation testimony')
