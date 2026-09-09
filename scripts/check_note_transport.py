"""Maximum ordinary note bodies through the real CLI and harness ingress."""
import json
import subprocess
import uuid


def operator(binary, environment, operation, payload=None, record_id=None):
    args = [binary, operation]
    if record_id is not None:
        args.append(record_id)
    result = subprocess.run(args, input=None if payload is None else json.dumps(payload),
                            env=environment, capture_output=True, text=True, check=True, timeout=15)
    return json.loads(result.stdout)['data']


def check_cli(binary, environment, client):
    remembered = client('hosted-agent.token', ['remember', '--repo', 'fixture:socket',
                        '--shareable', '--stdin'], '<' * 65536)
    assert remembered['body'] == '<' * 65536
    for name, call in [('agent', lambda op, req: client('hosted-agent.token', [op], json.dumps(req))),
                       ('operator', lambda op, req: operator(binary, environment, op, req))]:
        draft = dict(kind='note', body='\x01' * 65536, claim_type='self', sensitivity='shareable',
                     scope=dict(repo='fixture:socket', task_id='*', run_id='*'))
        create = dict(request_id=str(uuid.uuid4()), draft=draft)
        note = call('create', create)
        assert note['body'] == draft['body'] and call('create', create) == note
        edited_draft = dict(draft, body='\x02' * 65536)
        edit = dict(request_id=str(uuid.uuid4()), record_id=note['record_id'], expected_version=1, draft=edited_draft)
        edited = call('edit', edit)
        assert edited['version'] == 2 and edited['body'] == edited_draft['body']
        revise = dict(request_id=str(uuid.uuid4()), record_id=note['record_id'], expected_version=2,
                      repo='fixture:socket', body='\x03' * 65536)
        revised = call('revise', revise)
        assert revised['version'] == 3 and call('revise', revise) == revised
        stored = operator(binary, environment, 'get', record_id=note['record_id'])
        assert stored['body'] == revise['body'] and stored['scope'] == draft['scope']
        assert call('edit', edit) == edited
        print(name + ' JSON create/edit/revise preserve maximum escaped note bodies and retries')

    previous = environment.get('CAIRN_PREVIOUS_BINARY')
    if previous:
        draft = dict(draft, body='Previously accepted ordinary note')
        create = dict(request_id=str(uuid.uuid4()), draft=draft)
        note = operator(previous, environment, 'create', create)
        assert operator(binary, environment, 'create', create) == note
        edit = dict(request_id=str(uuid.uuid4()), record_id=note['record_id'], expected_version=1,
                    draft=dict(draft, body='Previously accepted full edit'))
        edited = operator(previous, environment, 'edit', edit)
        revise = dict(request_id=str(uuid.uuid4()), record_id=note['record_id'], expected_version=2,
                      repo='fixture:socket', body='Previously accepted body revision')
        revised = operator(previous, environment, 'revise', revise)
        assert operator(binary, environment, 'edit', edit) == edited
        assert operator(binary, environment, 'revise', revise) == revised
        print('Previous-binary ordinary mutation responses retry exactly through the new CLI')


def check_harness(invoke, binary, environment):
    body = '<' * 65536
    saved = invoke('cairn_remember', dict(request_id=str(uuid.uuid4()), body=body, shareable=True))
    note = operator(binary, environment, 'get', record_id=saved['record_id'])
    assert note['body'] == body and note['sensitivity'] == 'shareable'
    edit = dict(request_id=str(uuid.uuid4()), record_id=note['record_id'], expected_version=1, body='>' * 65536)
    revised = invoke('cairn_edit', edit)
    assert revised['version'] == 2
    stored = operator(binary, environment, 'get', record_id=note['record_id'])
    assert stored['body'] == edit['body'] and stored['scope'] == note['scope']
    draft = {k: stored[k] for k in ('kind', 'body', 'scope', 'claim_type', 'sensitivity',
             'pins', 'relations', 'attributed_producer', 'attempt_id', 'result_ref') if k in stored}
    draft['body'] = '&' * 65536
    full = dict(request_id=str(uuid.uuid4()), record_id=note['record_id'], expected_version=2, draft=draft)
    assert invoke('cairn_edit', full)['version'] == 3
    assert operator(binary, environment, 'get', record_id=note['record_id'])['body'] == draft['body']
    assert invoke('cairn_edit', edit) == revised


def check_opencode_session(opencode, output, connection, binary, environment):
    from check_opencode_defaults import check

    draft = dict(kind='note', body='Selected fixture for normal-session large edits',
                 scope=dict(repo=connection['repo'], task_id='*', run_id='*'),
                 claim_type='self', sensitivity='shareable')
    seed = operator(binary, environment, 'create', dict(request_id=str(uuid.uuid4()), draft=draft))
    capture = dict(request_id=str(uuid.uuid4()), body='<' * 65536, shareable=True)
    revise = dict(request_id=str(uuid.uuid4()), record_id=seed['record_id'], expected_version=1, body='>' * 65536)
    edit = dict(request_id=str(uuid.uuid4()), record_id=seed['record_id'], expected_version=2,
                draft=dict(draft, body='&' * 65536))
    cases = [('large-capture', 'cairn_remember', capture), ('large-revise', 'cairn_edit', revise),
             ('large-edit', 'cairn_edit', edit), ('large-retry', 'cairn_edit', revise)]
    report = check(opencode, output, connection=connection, extra_cases=cases)
    results = {name: json.loads(report['results'][name]) for name, _, _ in cases}
    assert results['large-revise']['version'] == 2 and results['large-edit']['version'] == 3
    assert results['large-retry'] == results['large-revise']
    captured = operator(binary, environment, 'get', record_id=results['large-capture']['record_id'])
    assert captured['body'] == capture['body'] and captured['sensitivity'] == 'shareable'
    stored = operator(binary, environment, 'get', record_id=seed['record_id'])
    assert stored['body'] == edit['draft']['body'] and stored['scope'] == seed['scope']
    earlier = operator(binary, environment, 'history', dict(record_id=seed['record_id'], version=2))
    assert earlier['versions'][0]['body'] == revise['body']
    print('Normal OpenCode session stores exact maximum escaped capture/full-edit/body-revision with stable retry; no inference')
