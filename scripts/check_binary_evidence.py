"""Exercise explicit binary capture and existing qualified pulls in the disposable API."""
import base64
import hashlib
import json
import subprocess
import uuid


def check(binary, root, env, call, grant):
    profile = ['agent', '--token-file', str(root / 'hosted-agent.token')]
    client_env = dict(env, CAIRN_DATABASE_URL='host=/nonexistent-binary-client dbname=denied')
    body = bytes(range(256)) * 4
    request = dict(request_id=str(uuid.uuid4()), repo='fixture:socket',
                   body_base64=base64.b64encode(body).decode('ascii'),
                   source='Selected binary transport fixture', sensitivity='shareable')
    evidence = call([*profile, 'evidence'], request, client_env)
    assert evidence['sha256'] == hashlib.sha256(body).hexdigest()
    assert evidence['witness'] == 'testimony'
    assert call([*profile, 'evidence'], request, client_env) == evidence
    retained = call(['evidence', evidence['evidence_id']], {})
    assert retained['body'] == '' and base64.b64decode(retained['body_base64']) == body
    # The operator CLI uses the same explicit representation and storage contract.
    direct = call(['capture-evidence'], dict(request, request_id=str(uuid.uuid4())), env)
    assert direct['sha256'] == evidence['sha256']
    claim = call([*profile, 'create'], dict(request_id=str(uuid.uuid4()), draft=dict(
        kind='note', body='Binary capture transport fixture source', claim_type='self',
        sensitivity='shareable', scope=dict(repo='fixture:socket', task_id='*', run_id='*'))), client_env)
    promoted = call(['promote'], dict(request_id=str(uuid.uuid4()), record_id=claim['record_id'],
        expected_version=claim['version'], grant_id=grant['grant_id'],
        evidence_citations=[dict(evidence_id=evidence['evidence_id'], expected_sha256=evidence['sha256'])],
        reason='Verify exact binary pull in disposable fixture'))
    index = call([*profile, 'index'], dict(request_id=str(uuid.uuid4()),
        scope=dict(repo='fixture:socket', task_id='binary-capture', run_id='binary-capture'),
        query='Binary capture transport fixture source', purpose='context', available_tokens=64000), client_env)
    handle = next(h['handle'] for h in index['handles'] if h['record_id'] == promoted['record_id'])
    pull = dict(request_id=str(uuid.uuid4()), receipt_id=index['package']['receipt_id'],
        handle=handle, evidence_id=evidence['evidence_id'], expected_sha256=evidence['sha256'])
    result = call([*profile, 'expand-evidence'], pull, client_env)
    assert base64.b64decode(result['evidence']['body_base64']) == body
    assert result['evidence']['actual_sha256'] == evidence['sha256']
    assert call([*profile, 'expand-evidence'], pull, client_env) == result
    rejected = subprocess.run([binary, *profile, 'evidence'],
        input=json.dumps(dict(request, body_base64='/x==')), env=client_env,
        capture_output=True, text=True, timeout=10)
    assert rejected.returncode != 0 and json.loads(rejected.stdout)['status'] == 'INVALID_REQUEST'
    print('Explicit binary evidence survives CLI/API capture, retained inspection and qualified hosted pull without client DB access')
