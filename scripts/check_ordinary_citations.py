"""Ordinary note source updates through shipped CLI and native edit surfaces."""
import json
import subprocess
import uuid


def check_harness(invoke, support):
    marker = 'sourcecitation' + uuid.uuid4().hex
    saved = invoke('remember', dict(request_id=str(uuid.uuid4()), body=marker + ': check the retained source', shareable=True))
    old = invoke('search', dict(query=marker))['index'][0]['pull_arguments']
    citations = [dict(evidence_id=support['evidence_id'], expected_sha256=support['sha256'], spans=[dict(offset=9, length=10)])]
    update = dict(request_id=str(uuid.uuid4()), record_id=saved['record_id'], expected_version=1, evidence_citations=citations)
    invoke('edit', dict(update, body='ambiguous change'), 'INVALID_REQUEST')
    invoke('edit', dict(update, evidence_citations=[dict(citations[0], expected_sha256='0' * 64)]), 'EVIDENCE_UNAVAILABLE')
    cited = invoke('edit', update)
    assert cited == dict(record_id=saved['record_id'], version=2, request_id=update['request_id'])
    assert invoke('edit', update) == cited
    invoke('edit', dict(update, evidence_citations=[]), 'IDEMPOTENCY_CONFLICT')
    invoke('pull', old, 'STALE_HANDLE')
    revision = dict(request_id=str(uuid.uuid4()), record_id=saved['record_id'], expected_version=2, body=marker + ': revised source guidance')
    assert invoke('edit', revision)['version'] == 3
    fresh = invoke('search', dict(query=marker))['index'][0]['pull_arguments']
    pulled = invoke('pull', fresh)['selection']
    assert pulled['record']['class'] == 'A' and pulled['record']['body'] == revision['body']
    ref = pulled['evidence'][0]
    assert ref['citation'] == dict(sha256=support['sha256'], relation='supports', spans=citations[0]['spans'])
    source_args = dict(fresh, request_id=str(uuid.uuid4()), evidence_id=ref['evidence_id'], expected_sha256=ref['sha256'], span=ref['citation']['spans'][0])
    source = invoke('pull_evidence', source_args)
    assert source['span']['body'] == 'supporting'
    assert invoke('pull_evidence', source_args) == source
    clear = dict(update, request_id=str(uuid.uuid4()), expected_version=3, evidence_citations=[])
    assert invoke('edit', clear)['version'] == 4
    invoke('pull_evidence', source_args, 'STALE_HANDLE')
    fresh = invoke('search', dict(query=marker))['index'][0]['pull_arguments']
    assert invoke('pull', fresh)['selection']['evidence'] == []


def check_cli(binary, root, environment, call):
    support = call(['capture-evidence'], dict(request_id=str(uuid.uuid4()), repo='fixture:socket', body='selected CLI citation source', source='ordinary citation fixture', sensitivity='shareable'))
    marker = 'clicitation' + uuid.uuid4().hex
    command = ['agent', '--socket', str(root / 'api.sock'), '--token-file', str(root / 'hosted-agent.token')]
    env = dict(environment, CAIRN_DATABASE_URL='host=/absent-citation-client dbname=denied')
    note = call([*command, 'remember', '--repo', 'fixture:socket', '--shareable', marker], {}, env)
    req = dict(request_id=str(uuid.uuid4()), record_id=note['record_id'], expected_version=1, repo='fixture:socket',
               evidence_citations=[dict(evidence_id=support['evidence_id'], expected_sha256=support['sha256'])])
    cited = call([*command, 'cite'], req, env)
    assert cited == dict(record_id=note['record_id'], version=2)
    assert call([*command, 'cite'], req, env) == cited
    clear = dict(req, request_id=str(uuid.uuid4()), expected_version=2, evidence_citations=[])
    for refs in (None, 'invalid'):
        bad = subprocess.run([binary, *command, 'cite'], env=env, input=json.dumps(dict(clear, evidence_citations=refs)), capture_output=True, text=True, timeout=10)
        assert bad.returncode != 0 and json.loads(bad.stdout)['status'] == 'INVALID_REQUEST', bad.stdout
    # Operator and authenticated calls use the same implementation; absent client
    # DB access above cannot silently select a direct-store path.
    assert call(['cite'], clear)['version'] == 3
    assert call(['cite'], clear)['version'] == 3
    # Retirement review retains the source link on v2 after current v3 clears it.
    local = ['agent', '--socket', str(root / 'api.sock'), '--token-file', str(root / 'agent.token')]
    preview = call([*local, 'preview-retract'], dict(record_id=note['record_id']), env)
    supporting = preview['supporting_evidence']
    assert len(supporting) == 1 and supporting[0]['record_id'] == note['record_id']
    assert supporting[0]['version'] == 2 and supporting[0]['evidence'][0]['evidence_id'] == support['evidence_id']
    assert supporting[0]['evidence'][0]['state'] == 'resolvable'
    assert supporting[0]['evidence'][0]['citation']['sha256'] == support['sha256']
    assert 'selected CLI citation source' not in json.dumps(preview)
    direct = call(['preview-retract', note['record_id']], {})
    assert direct['supporting_evidence'] == supporting
    refused = subprocess.run([binary, *command, 'preview-retract'], input=json.dumps(dict(record_id=note['record_id'])),
                             env=env, capture_output=True, text=True, timeout=10)
    assert refused.returncode != 0 and json.loads(refused.stdout)['status'] == 'AUTHORITY_DENIED'
    assert support['evidence_id'] not in refused.stdout
    print('Local CLI/API retirement previews retain historical supporting citations; hosted inspection refuses')
