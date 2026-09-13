"""Explicit associations through authenticated CLI, MCP and native OpenCode."""
import json
import subprocess
import uuid


def check_harness(invoke):
    name = 'module' + uuid.uuid4().hex + '/日本語 target.go'
    refs = [dict(kind='file', name=name)]
    body = 'Selected applicability guidance: check every known mismatch first.'
    capture = dict(request_id=str(uuid.uuid4()), body=body, shareable=True, entities=refs)
    saved = invoke('cairn_remember', capture)
    assert invoke('cairn_remember', capture) == saved
    invoke('cairn_remember', dict(request_id=str(uuid.uuid4()), body='Incidental reference: ' + name, shareable=True))
    view = invoke('cairn_search', dict(query='"' + name + '"', entities=refs))
    assert view['ranking'] == 'lexical-scope-recency/7'
    assert view['index'][0]['record_id'] == saved['record_id']
    assert view['index'][0]['entities'] == refs
    record = invoke('cairn_pull', view['index'][0]['pull_arguments'])['selection']['record']
    assert record['body'] == body and record['entities'] == refs
    edit = dict(request_id=str(uuid.uuid4()), record_id=saved['record_id'], expected_version=1, body=body + ' Verify the source.')
    assert invoke('cairn_edit', edit)['version'] == 2
    view = invoke('cairn_search', dict(entities=refs, offset=0))
    assert [e['record_id'] for e in view['index']] == [saved['record_id']]
    record = invoke('cairn_pull', view['index'][0]['pull_arguments'])['selection']['record']
    assert record['entities'] == refs and record['version'] == 2
    fields = ('kind', 'body', 'scope', 'pins', 'entities', 'sensitivity', 'relations', 'claim_type', 'attributed_producer', 'attempt_id', 'result_ref')
    draft = {key: record[key] for key in fields if key in record}
    draft['entities'] = [dict(kind='symbol', name=name)]
    assert invoke('cairn_edit', dict(request_id=str(uuid.uuid4()), record_id=saved['record_id'], expected_version=2, draft=draft))['version'] == 3
    assert not invoke('cairn_search', dict(entities=refs))['index']
    historical = invoke('cairn_history', dict(record_id=saved['record_id'], version=1))
    assert historical['versions'][0]['entities'] == refs
    print('Entity tools distinguish associations from incidental mentions and symbol names; edits preserve metadata and history retains earlier associations')


def check_cli(binary, root, environment):
    client = [binary, 'agent', '--socket', str(root / 'api.sock'), '--token-file', str(root / 'hosted-agent.token')]
    env = dict(environment, CAIRN_DATABASE_URL='host=/absent-entity-client-db dbname=denied')

    def call(args):
        result = subprocess.run([*client, *args], env=env, capture_output=True, text=True, check=True, timeout=15)
        return json.loads(result.stdout)['data']

    name = 'module' + uuid.uuid4().hex + '/currentness.go'
    saved = call(['remember', '--repo', 'fixture:socket', '--entity-file', name, '--shareable', 'Check every known mismatch first.'])
    view = call(['search', '--repo', 'fixture:socket', '--task', 'entity-cli', '--run', 'entity-cli', '--entity-file', name, '--offset', '0'])
    assert [e['record_id'] for e in view['index']] == [saved['record_id']]
    assert view['index'][0]['entities'] == [dict(kind='file', name=name)]
    pulled = subprocess.run([*client, 'expand'], input=json.dumps(view['index'][0]['pull_arguments']), env=env, capture_output=True, text=True, check=True, timeout=15)
    assert json.loads(pulled.stdout)['data']['selection']['record']['entities'] == view['index'][0]['entities']
    print('Authenticated CLI captures explicit file associations and performs entity-only paged search/pull without client database access')
