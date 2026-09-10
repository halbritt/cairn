"""Quoted intent through shipped interfaces, plus actual prior-binary replay."""
import json
import subprocess
import uuid

from check_note_transport import operator


def check_cli(binary, root, environment):
    scope = dict(repo='fixture:socket', task_id='quoted-intent', run_id='cli')
    literal = 'module' + uuid.uuid4().hex + '/target.go'
    query = 'repair review "' + literal + '"'
    draft = dict(kind='note', body='Inspect ' + literal + '.', claim_type='self',
                 sensitivity='shareable', scope=scope)
    exact = operator(binary, environment, 'create', dict(request_id=str(uuid.uuid4()), draft=draft))
    draft['body'] = literal.replace('/', ' ').replace('.', ' ') + ' repair review'
    other = operator(binary, environment, 'create', dict(request_id=str(uuid.uuid4()), draft=draft))
    client = [binary, 'agent', '--socket', str(root / 'api.sock'), '--token-file', str(root / 'hosted-agent.token')]
    client_env = dict(environment, CAIRN_DATABASE_URL='host=/absent-literal-client-db dbname=denied')
    request_id = str(uuid.uuid4())
    result = subprocess.run([*client, 'search', '--repo', scope['repo'], '--task', scope['task_id'],
                             '--run', scope['run_id'], '--request-id', request_id, query],
                            env=client_env, capture_output=True, text=True, check=True, timeout=15)
    view = json.loads(result.stdout)['data']
    assert view['ranking'] == 'lexical-scope-recency/5'
    assert [e['record_id'] for e in view['index']] == [exact['record_id'], other['record_id']]
    entry = view['index'][0]
    pulled = subprocess.run([*client, 'expand'], input=json.dumps(entry['pull_arguments']),
                            env=client_env, capture_output=True, text=True, check=True, timeout=15)
    assert json.loads(pulled.stdout)['data']['selection']['record']['body'] == exact['body']
    print('Quoted path ranks ahead of lexical overlap through authenticated CLI/API; exact pull succeeds without client DB access')
    previous = environment.get('CAIRN_PREVIOUS_BINARY')
    if previous:
        for mode in ('', 'index'):
            request = dict(request_id=str(uuid.uuid4()), scope=scope, query=query, purpose='context',
                           available_tokens=64000, mode=mode)
            old = operator(previous, environment, 'compile', request)
            previous_ranking = old['semantic']['ranking']
            assert previous_ranking in ('lexical-scope-recency/4', 'lexical-scope-recency/5')
            entries = old['semantic']['index'] if mode else [s['record'] for s in old['semantic']['selected']]
            expected = other if previous_ranking == 'lexical-scope-recency/4' else exact
            assert entries[0]['record_id'] == expected['record_id']
            replayed = operator(binary, environment, 'recompile', dict(receipt_id=old['receipt_id'], query=query))['package']
            assert replayed == old
            retry = subprocess.run([binary, 'compile'], input=json.dumps(request), env=environment,
                                   capture_output=True, text=True, timeout=15)
            if previous_ranking == 'lexical-scope-recency/4':
                assert retry.returncode != 0 and json.loads(retry.stdout)['status'] == 'STALE_PACKAGE'
            else:
                assert retry.returncode == 0 and json.loads(retry.stdout)['data'] == old
        print('Actual previous-binary quoted-query receipts recompile unchanged; retries preserve unchanged profiles and refuse changed ranking')


def check_harness(invoke):
    literal = 'module' + uuid.uuid4().hex + '/target.go'
    body = 'Earlier background. ' * 20 + 'Inspect ' + literal + '.'
    exact = invoke('cairn_remember', dict(request_id=str(uuid.uuid4()), body=body, shareable=True))
    other = invoke('cairn_remember', dict(request_id=str(uuid.uuid4()),
                   body=literal.replace('/', ' ').replace('.', ' ') + ' repair review', shareable=True))
    view = invoke('cairn_search', dict(query='repair review "' + literal + '"'))
    assert view['ranking'] == 'lexical-scope-recency/5'
    assert view['index'][0]['record_id'] == exact['record_id']
    assert other['record_id'] in [e['record_id'] for e in view['index']]
    entry = view['index'][0]
    assert literal in entry['summary'] and entry['summary_span']['offset'] > 0
    pulled = invoke('cairn_pull', entry['pull_arguments'])
    assert pulled['selection']['record']['body'] == body
    print('Native memory tools preserve quoted intent, show the matching source passage and pull exact bytes')
