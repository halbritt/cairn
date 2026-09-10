"""Opt-in native custom-tool checks; no model calls, only a disposable Cairn API."""
import json
from pathlib import Path
import subprocess
import uuid

from check_note_transport import check_opencode_session


def check(binary, root, environment, opencode, claim, support):
    work = root / 'opencode-tools'
    work.mkdir()
    installed = subprocess.run([binary, 'opencode-install', '--project', str(work),
        '--socket', str(root / 'api.sock'), '--token-file', str(root / 'hosted-agent.token'),
        '--repo', 'fixture:socket', '--tokens', '64000', '--task-phase', 'validation'], env=environment, capture_output=True,
        text=True, check=True, timeout=15)
    assert json.loads(installed.stdout)['ok'] is True
    settings_path = work / '.opencode/cairn.json'
    settings = json.loads(settings_path.read_text())
    # Native tool debugging needs a model catalog entry, but never invokes it.
    config = dict(model='fixture/probe', provider={'fixture': {
        'npm': '@ai-sdk/openai-compatible', 'name': 'No-model fixture',
        'options': {'baseURL': 'http://127.0.0.1:1/v1', 'apiKey': 'unused'},
        'models': {'probe': {'name': 'Probe'}}}}, permission={'*': 'deny'})
    for name in ('search', 'pull', 'pull_evidence', 'remember', 'edit'):
        config['permission']['cairn_' + name] = 'allow'
    config_path = work / 'opencode.json'
    config_path.write_text(json.dumps(config))
    env = {key: environment[key] for key in ('PATH', 'LANG') if key in environment}
    for key, folder in [('HOME', 'home'), ('XDG_CONFIG_HOME', 'config'),
                        ('XDG_CACHE_HOME', 'cache'), ('XDG_DATA_HOME', 'data'), ('XDG_STATE_HOME', 'state')]:
        directory = work / folder
        directory.mkdir()
        env[key] = str(directory)
    env.update(OPENCODE_CONFIG=str(config_path), OPENCODE_DISABLE_AUTOUPDATE='true',
               OPENCODE_DISABLE_MODELS_FETCH='true', CAIRN_DATABASE_URL='host=/absent-native-tool-db dbname=denied')

    def invoke(name, args, error=None):
        result = subprocess.run([opencode, 'debug', 'agent', 'build', '--pure', '--tool', 'cairn_' + name,
                                 '--params', json.dumps(args)], cwd=work, env=env, capture_output=True,
                                text=True, timeout=60)
        if error:
            assert result.returncode != 0 and error in result.stderr + result.stdout, (name, result.stderr, result.stdout)
            return
        assert result.returncode == 0, (name, result.stderr, result.stdout)
        return json.loads(json.loads(result.stdout)['result']['output'])

    marker = 'nativeopencode' + uuid.uuid4().hex
    capture = dict(request_id=str(uuid.uuid4()), body='Earlier setup context. ' * 30 + marker + ': selected lesson $(literal)',
                   kind='lesson', shareable=True)
    saved = invoke('remember', capture)
    assert set(saved) == {'record_id', 'version', 'request_id'} and saved['version'] == 1
    assert invoke('remember', capture) == saved
    invoke('remember', dict(capture, body='different payload'), 'IDEMPOTENCY_CONFLICT')
    local_args = dict(request_id=str(uuid.uuid4()), body=capture['body'])
    for invalid in ({'shareable': 'false'}, {'shareable': 0}, {'shareable': None},
                    {'kind': 7}, {'body': [capture['body']]}, {'unexpected': True}):
        invoke('remember', dict(local_args, **invalid), 'INVALID_REQUEST')
    # The refused calls did not reserve the UUID or create a different draft.
    local = invoke('remember', local_args)
    local_record = json.loads(subprocess.run([binary, 'get', local['record_id']], env=environment,
                             capture_output=True, text=True, check=True, timeout=15).stdout)['data']
    assert local_record['kind'] == 'note' and local_record['sensitivity'] == 'local'
    first = invoke('search', dict(query=marker))
    second = invoke('search', dict(query=marker))

    fallback = invoke('search', dict(query=marker, semantic=True))
    assert fallback['status'] == 'DEGRADED_NO_EMBEDDINGS'
    assert fallback['discovery']['state'] == 'unavailable'
    assert [e['record_id'] for e in fallback['index']] == [saved['record_id']]
    invoke('search', dict(browse=True, semantic=True), 'cannot be combined')
    assert first['schema'] == 'cairn.opencode-search/1'
    for view in (first, second):
        assert view['context']['task_phase'] == 'validation' and view['source_schema'] == 'cairn.semantic/10'
        assert view['scope']['repo'] == settings['repo']
        assert view['scope']['task_id'] == 'opencode/' + view['scope']['run_id']
        assert view['scope']['run_id'].startswith('ses_')
        assert [e['record_id'] for e in view['index']] == [saved['record_id']]
        assert 'pull_command' not in view['index'][0]
        assert local['record_id'] not in json.dumps(view)
        assert view['destination'] == dict(name='hosted', allow_local=False)
    assert first['scope'] != second['scope']
    for args in ({}, {'query': ' '}, {'query': marker, 'browse': True}, {'browse': 'true'}):
        invoke('search', args, 'INVALID_REQUEST')
    browse = invoke('search', dict(browse=True))
    assert browse['scope']['repo'] == settings['repo'] and browse['destination'] == first['destination']
    assert local['record_id'] not in json.dumps(browse)
    browsed = next(entry for entry in browse['index'] if entry['record_id'] == saved['record_id'])
    assert invoke('pull', browsed['pull_arguments'])['selection']['record']['body'] == capture['body']
    end_page = invoke('search', dict(browse=True, offset=10000))
    assert end_page['browse'] == dict(offset=10000) and end_page['index'] == []
    for args in ({'query': marker, 'offset': 1}, {'browse': True, 'offset': -1}, {'browse': True, 'offset': '1'}):
        invoke('search', args, 'INVALID_REQUEST')
    pull = first['index'][0]['pull_arguments']
    expanded = invoke('pull', pull)
    assert invoke('pull', pull) == expanded
    record = expanded['selection']['record']
    assert record['body'] == capture['body'] and record['class'] == 'A'
    assert record['witness'] == 'testimony' and record['observed_writer'] == 'agent:hosted-capture'
    location = first['index'][0]['summary_span']
    assert location['offset'] > 160
    partial_args = dict(pull, request_id=str(uuid.uuid4()), span=location)
    for bad in ({'offset': '0', 'length': 8}, {'offset': 0, 'length': 0}, {'offset': 0, 'length': 8, 'unknown': True}):
        invoke('pull', dict(partial_args, span=bad), 'INVALID_REQUEST')
    partial = invoke('pull', partial_args)
    assert partial['span']['body'] == capture['body'][location['offset']:location['offset']+location['length']]
    assert marker in partial['span']['body'] and partial['selection']['record']['body'] == ''
    assert partial['span']['total_bytes'] == len(capture['body'].encode()) and partial['credits_remaining'] == 2
    assert invoke('pull', partial_args) == partial
    invoke('pull', dict(partial_args, span=dict(offset=1, length=8)), 'IDEMPOTENCY_CONFLICT')
    draft = {key: record[key] for key in ('kind', 'body', 'scope', 'claim_type', 'sensitivity',
             'pins', 'relations', 'attributed_producer', 'attempt_id', 'result_ref') if key in record}
    draft['body'] = marker + ': corrected selected lesson'
    edit = dict(request_id=str(uuid.uuid4()), record_id=record['record_id'], expected_version=record['version'], draft=draft)
    revised = invoke('edit', edit)
    assert revised == dict(record_id=record['record_id'], version=2, request_id=edit['request_id'])
    assert invoke('edit', edit) == revised
    invoke('edit', dict(edit, request_id=str(uuid.uuid4())), 'VERSION_CONFLICT')
    invoke('pull', dict(pull, request_id=str(uuid.uuid4())), 'STALE_HANDLE')
    invoke('pull', partial_args, 'STALE_HANDLE')
    fresh = invoke('search', dict(query=marker))
    current = invoke('pull', fresh['index'][0]['pull_arguments'])['selection']['record']
    assert current['body'] == draft['body'] and current['version'] == 2
    body_edit = dict(request_id=str(uuid.uuid4()), record_id=record['record_id'], expected_version=2,
                     body=marker + ': body-only revision')
    revised_body = invoke('edit', body_edit)
    assert revised_body == dict(record_id=record['record_id'], version=3, request_id=body_edit['request_id'])
    assert invoke('edit', body_edit) == revised_body
    invoke('edit', dict(body_edit, body='changed intent'), 'IDEMPOTENCY_CONFLICT')
    invoke('edit', dict(body_edit, request_id=str(uuid.uuid4())), 'VERSION_CONFLICT')
    invoke('edit', dict(body_edit, draft=draft), 'INVALID_REQUEST')
    fresh_body = invoke('search', dict(query=marker))
    revised_record = invoke('pull', fresh_body['index'][0]['pull_arguments'])['selection']['record']
    assert revised_record['body'] == body_edit['body'] and revised_record['version'] == 3
    for field in ('kind', 'scope', 'sensitivity', 'claim_type'):
        assert revised_record[field] == current[field]
    evidence_view = invoke('search', dict(query='socket'))
    claim_entry = next(entry for entry in evidence_view['index'] if entry['record_id'] == claim['record_id'])
    invoke('pull', claim_entry['pull_arguments'])
    evidence_args = dict(claim_entry['pull_arguments'], request_id=str(uuid.uuid4()),
                         evidence_id=support['evidence_id'], expected_sha256=support['sha256'])
    evidence = invoke('pull_evidence', evidence_args)
    assert evidence['evidence']['body'] == 'explicit supporting socket evidence'
    assert invoke('pull_evidence', evidence_args) == evidence
    span_args = dict(evidence_args, request_id=str(uuid.uuid4()), span=dict(offset=9, length=10))
    for bad_span in ({'offset': 0, 'length': 0}, {'offset': '0', 'length': 10},
                     {'offset': 0, 'length': 10, 'unknown': True}):
        invoke('pull_evidence', dict(span_args, span=bad_span), 'INVALID_REQUEST')
    span = invoke('pull_evidence', span_args)
    assert span['span']['body'] == 'supporting' and span['span']['total_bytes'] == len('explicit supporting socket evidence')
    assert span['evidence']['body'] == '' and span['evidence']['sha256'] == support['sha256']
    assert span['credits_remaining'] == 1 and invoke('pull_evidence', span_args) == span
    invoke('pull_evidence', dict(span_args, span=dict(offset=0, length=10)), 'IDEMPOTENCY_CONFLICT')
    foreign_draft = dict(draft, scope=dict(repo='outside-fixture', task_id='*', run_id='*'))
    invoke('edit', dict(edit, draft=foreign_draft, request_id=str(uuid.uuid4())), 'AUTHORITY_DENIED')
    check_opencode_session(opencode, root / 'opencode-note-limit', settings, binary, environment)
    settings_path.write_text(json.dumps(dict(settings, repo='outside-fixture')))
    invoke('search', dict(query=marker), 'AUTHORITY_DENIED')
    settings_path.write_text(json.dumps(settings))
    config['permission']['cairn_search'] = 'deny'
    config_path.write_text(json.dumps(config))
    invoke('search', dict(query=marker), 'disabled')
    print('Native OpenCode session scope, validated capture/edit, exact body/evidence pulls, retries, hosted filtering and permission/refusal paths pass without model calls')
