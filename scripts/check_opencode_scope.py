"""Native task scope checks against the caller's disposable API; no model calls."""
import json
import uuid

from check_note_transport import operator


def check(binary, environment, invoke, settings_path, settings, output):
    marker = 'task-scope-' + uuid.uuid4().hex
    task = 'repair 日本語 exact'
    run = 'attempt literal'

    def create(body, task_id, run_id='*', sensitivity='shareable'):
        return operator(binary, environment, 'create', dict(request_id=str(uuid.uuid4()),
            draft=dict(kind='note', body=marker + ' ' + body, claim_type='self', sensitivity=sensitivity,
                       scope=dict(repo=settings['repo'], task_id=task_id, run_id=run_id))))

    shared = create('task-scoped guidance', task)
    run_note = create('run-scoped guidance', task, run)
    foreign = create('other task guidance', 'unrelated task')
    private = create('local task guidance', task, sensitivity='local')
    before = invoke('search', dict(query=marker))
    assert not before['index'], before
    rows = []
    try:
        settings_path.write_text(json.dumps(dict(settings, task_id=task)))
        for _ in range(2):
            result = invoke('search', dict(query=marker))
            assert result['scope']['task_id'] == task and result['scope']['run_id'].startswith('ses_')
            assert [n['record_id'] for n in result['index']] == [shared['record_id']]
            pulled = invoke('pull', result['index'][0]['pull_arguments'])
            assert pulled['selection']['record']['body'] == shared['body']
            rows.append(result)
        assert rows[0]['scope']['run_id'] != rows[1]['scope']['run_id']
        settings_path.write_text(json.dumps(dict(settings, task_id=task, run_id=run)))
        request = dict(query=marker, request_id=str(uuid.uuid4()))
        fixed = invoke('search', request)
        assert fixed['scope'] == dict(repo=settings['repo'], task_id=task, run_id=run)
        assert {n['record_id'] for n in fixed['index']} == {shared['record_id'], run_note['record_id']}
        assert not {foreign['record_id'], private['record_id']} & {n['record_id'] for n in fixed['index']}
        retry = invoke('search', request)
        assert retry['receipt_id'] == fixed['receipt_id'] and retry['source_seal'] == fixed['source_seal']
        settings_path.write_text(json.dumps(dict(settings, task_id='unrelated task')))
        invoke('search', request, 'IDEMPOTENCY_CONFLICT')
        other = invoke('search', dict(query=marker))
        assert [n['record_id'] for n in other['index']] == [foreign['record_id']]
        # Tool arguments cannot override the host's declared scope.
        invoke('search', dict(query=marker, task_id=task), 'INVALID_REQUEST')
        invoke('search', dict(query=marker, run_id=run), 'INVALID_REQUEST')
        for value in ('', ' ', '*', '界' * 86, 'bad\x00scope', 'bad\ud800scope'):
            settings_path.write_text(json.dumps(dict(settings, task_id=value)))
            invoke('search', dict(query=marker), 'Use 1-256 UTF-8 bytes')
        settings_path.write_text(json.dumps(dict(settings, run_id=run)))
        invoke('search', dict(query=marker), 'requires task_id')
    finally:
        settings_path.write_text(json.dumps(settings))
    assert not invoke('search', dict(query=marker))['index']
    output.write_text(json.dumps(dict(native_session_scopes=[r['scope'] for r in rows],
        task_record_id=shared['record_id'],fixed_scope=fixed['scope'],default_scope_excludes_task_notes=True,
        current_profile_privacy_preserved=True,caller_scope_override_refused=True,
        native_sessions_are_not_independent_model_tasks=True), indent=2)+'\n')
    print('Native task-scoped guidance survives fresh sessions; explicit run, defaults and refusals verified')


def check_capture(binary, environment, invoke, settings_path, settings, output):
    """Capture through the native tool and retrieve under fresh session scopes."""
    marker = uuid.uuid4().hex
    task, run = 'capture 日本語 task', 'capture run'
    try:
        settings_path.write_text(json.dumps(dict(settings, task_id=task, run_id=run)))
        request = dict(request_id=str(uuid.uuid4()), body=marker, scope='task', shareable=True)
        saved = invoke('remember', request)
        record = operator(binary, environment, 'get', record_id=saved['record_id'])
        assert record['scope'] == dict(repo=settings['repo'], task_id=task, run_id='*'), record
        assert invoke('remember', request) == saved
        settings_path.write_text(json.dumps(dict(settings, task_id=task)))
        assert invoke('remember', request) == saved
        result = invoke('search', dict(query=marker))
        assert [e['record_id'] for e in result['index']] == [saved['record_id']]
        pulled = invoke('pull', result['index'][0]['pull_arguments'])
        assert pulled['selection']['record']['body'] == marker
        settings_path.write_text(json.dumps(dict(settings, task_id='other task')))
        assert not invoke('search', dict(query=marker))['index']
        invoke('remember', request, 'IDEMPOTENCY_CONFLICT')
        settings_path.write_text(json.dumps(dict(settings, task_id=task, run_id=run)))
        invoke('remember', dict(request, scope='run'), 'IDEMPOTENCY_CONFLICT')
        run_request = dict(request, request_id=str(uuid.uuid4()), scope='run', body=marker + ' run guidance')
        run_note = invoke('remember', run_request)
        assert invoke('remember', run_request) == run_note
        local_note = invoke('remember', dict(request_id=str(uuid.uuid4()), body=marker, scope='task'))
        local = operator(binary, environment, 'get', record_id=local_note['record_id'])
        assert local['sensitivity'] == 'local' and local['class'] == 'A'
        assert local['observed_writer'] == 'agent:hosted-capture' and local['witness'] == 'testimony'
        assert not local.get('pins'), local
        both = invoke('search', dict(query=marker))
        assert {e['record_id'] for e in both['index']} == {saved['record_id'], run_note['record_id']}, both
        settings_path.write_text(json.dumps(dict(settings, task_id=task, run_id='next run')))
        assert [e['record_id'] for e in invoke('search', dict(query=marker))['index']] == [saved['record_id']]
        invoke('remember', run_request, 'IDEMPOTENCY_CONFLICT')
        for value in ('', '*', 'session'):
            invoke('remember', dict(request, scope=value), 'INVALID_REQUEST')
        invoke('remember', dict(request, task_id='invented'), 'INVALID_REQUEST')
        # Defaults derive labels from the actual capturing session. Each debug
        # invocation starts another session, so inspect saved scope through the
        # operator, then configure those exact labels for an ordinary pull.
        settings_path.write_text(json.dumps(settings))
        native_note = invoke('remember', dict(request, request_id=str(uuid.uuid4()), scope='run', body=marker + ' native guidance'))
        native = operator(binary, environment, 'get', record_id=native_note['record_id'])
        native_scope = native['scope']
        assert native_scope['run_id'].startswith('ses_')
        assert native_scope['task_id'] == 'opencode/' + native_scope['run_id']
        assert not invoke('search', dict(query=marker))['index']
        settings_path.write_text(json.dumps(dict(settings, task_id=native_scope['task_id'], run_id=native_scope['run_id'])))
        found = invoke('search', dict(query=marker))
        assert [e['record_id'] for e in found['index']] == [native_note['record_id']]
        assert invoke('pull', found['index'][0]['pull_arguments'])['selection']['record']['scope'] == native_scope
        repository_ids = set()
        for explicit in (False, True):
            args = dict(request_id=str(uuid.uuid4()), body=marker + ' repository ' + str(explicit), shareable=True)
            if explicit:
                args['scope'] = 'repository'
            note = invoke('remember', args)
            stored = operator(binary, environment, 'get', record_id=note['record_id'])
            assert stored['scope'] == dict(repo=settings['repo'], task_id='*', run_id='*')
            repository_ids.add(note['record_id'])
        settings_path.write_text(json.dumps(settings))
        assert {e['record_id'] for e in invoke('search', dict(query=marker))['index']} == repository_ids
        output.write_text(json.dumps(dict(task_capture_retrieved_in_fresh_session=True,
            other_task_excluded=True, changed_task_retry_refused=True, run_capture_and_retry_verified=True,
            default_and_explicit_repository_capture_preserved=True, private_capture_excluded=True,
            native_capture_scope=native_scope, native_sessions_are_not_independent_model_tasks=True), indent=2)+'\n')
    finally:
        settings_path.write_text(json.dumps(settings))
