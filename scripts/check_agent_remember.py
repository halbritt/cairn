"""Verify authenticated note capture and retrieval across separate API clients."""
import json
import shlex
import subprocess
import uuid


def check(binary, root, environment):
    client_env = dict(environment, CAIRN_DATABASE_URL='host=/absent-capture-client dbname=denied')
    for name in ('HOME', 'CAIRN_HOME'):
        client_env.pop(name, None)

    def client(token, args, text=None, expected='OK'):
        command = [binary, 'agent', '--socket', str(root / 'api.sock'), '--token-file', str(root / token), *args]
        result = subprocess.run(command, input=text, env=client_env, capture_output=True, text=True, timeout=15)
        response = json.loads(result.stdout)
        assert response['status'] == expected, response
        assert (result.returncode == 0) == (expected == 'OK'), result.returncode
        return response.get('data')

    query = 'captureproof' + uuid.uuid4().hex
    body = query + '\nPreserve literal $(printf nope), "quotes", and 日本語.\n'
    request_id = str(uuid.uuid4())
    remember = ['remember', '--repo', 'fixture:socket', '--kind', 'lesson', '--shareable', '--request-id', request_id, '--stdin']
    saved = client('agent.token', remember, body)
    assert saved['class'] == 'A' and saved['witness'] == 'testimony'
    assert saved['observed_writer'] == 'agent:socket-fixture' and saved['attribution_state'] == 'self'
    assert saved['body'] == body and saved['sensitivity'] == 'shareable'
    assert saved['scope'] == dict(repo='fixture:socket', task_id='*', run_id='*')
    assert client('agent.token', remember, body) == saved
    client('agent.token', remember, body + 'changed', expected='IDEMPOTENCY_CONFLICT')
    # Same request UUID belongs independently to the second authenticated writer.
    second = client('hosted-agent.token', remember, body + 'A separate note from the second writer.\n')
    assert second['record_id'] != saved['record_id'] and second['observed_writer'] == 'agent:hosted-capture'
    assert second['class'] == 'A' and second['witness'] == 'testimony'
    local = client('agent.token', ['remember', '--repo', 'fixture:socket', query + ' local-only note'])
    assert local['sensitivity'] == 'local'
    client('agent.token', ['remember', '--repo', 'fixture:other', 'outside profile'], expected='AUTHORITY_DENIED')
    client('agent.token', ['remember', '--repo', 'fixture:socket', '--kind', 'instruction', 'not authorized'], expected='INVALID_REQUEST')
    view = client('hosted-agent.token', ['search', '--repo', 'fixture:socket', '--task', 'later-task', '--run', 'later-run', query])
    assert view['destination'] == dict(name='hosted', allow_local=False)
    assert {entry['record_id'] for entry in view['index']} == {saved['record_id'], second['record_id']}
    entry = next(entry for entry in view['index'] if entry['record_id'] == saved['record_id'])
    result = subprocess.run(shlex.split(entry['pull_command']), env=client_env, capture_output=True, text=True, timeout=15, check=True)
    pulled = json.loads(result.stdout)['data']['selection']['record']
    assert pulled == saved
    revise = dict(request_id=str(uuid.uuid4()), record_id=saved['record_id'], expected_version=1,
                  repo='fixture:socket', body=body + 'Selected correction.\n')
    revised = client('hosted-agent.token', ['revise'], json.dumps(revise))
    assert revised == dict(record_id=saved['record_id'], version=2)
    assert client('hosted-agent.token', ['revise'], json.dumps(revise)) == revised
    client('hosted-agent.token', ['revise'], json.dumps(dict(revise, body='changed intent')), expected='IDEMPOTENCY_CONFLICT')
    client('hosted-agent.token', ['revise'], json.dumps(dict(revise, request_id=str(uuid.uuid4()))), expected='VERSION_CONFLICT')
    fresh = client('hosted-agent.token', ['search', '--repo', 'fixture:socket', '--task', 'later-task', '--run', 'later-run', query])
    entry = next(e for e in fresh['index'] if e['record_id'] == saved['record_id'])
    updated = client('hosted-agent.token', ['expand'], json.dumps(entry['pull_arguments']))['selection']['record']
    assert updated['body'] == revise['body'] and updated['version'] == 2
    history = client('hosted-agent.token', ['history'], json.dumps(dict(record_id=saved['record_id'], limit=1)))
    assert history['historical'] and history['current_version'] == 2
    assert len(history['versions']) == 1 and history['versions'][0]['version'] == 2
    assert 'body' not in history['versions'][0] and history['next_before_version'] == 2
    earlier = client('hosted-agent.token', ['history'], json.dumps(dict(record_id=saved['record_id'], version=1)))
    assert earlier['versions'][0]['body'] == body
    assert earlier['versions'][0]['observed_writer'] == saved['observed_writer']
    client('hosted-agent.token', ['history'], json.dumps(dict(record_id=local['record_id'])), expected='NOT_FOUND')
    print('Authenticated CLI inspects retained note versions and exact prior guidance without database access')
    assert updated['observed_writer'] == 'agent:hosted-capture'
    for field in ('kind', 'scope', 'sensitivity', 'claim_type'):
        assert updated[field] == saved[field]
    # Operator convenience uses exactly the same parser but retains operator identity.
    result = subprocess.run([binary, 'remember', '--repo', 'fixture:operator-capture', '--stdin'],
                            input='Operator multiline\ntext stays intact.\n', env=environment,
                            capture_output=True, text=True, timeout=15, check=True)
    operator = json.loads(result.stdout)['data']
    assert operator['body'] == 'Operator multiline\ntext stays intact.\n'
    assert operator['sensitivity'] == 'local' and operator['observed_writer'].startswith('local-uid:')
    print('Authenticated remember preserves text, A testimony and retry identity; a second hosted agent retrieves only shareable notes without HOME or database access')
