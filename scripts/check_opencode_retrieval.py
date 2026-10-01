"""Native retrieval reserve/preparation checks; supplied transport uses a disposable API."""
import json
import uuid


def check_presentation(invoke, settings_path, settings, root):
    response = root / 'retrieval-response.json'
    calls = root / 'retrieval-calls.json'
    executable = root / 'retrieval-cli'
    executable.write_text('#!/usr/bin/python3\nimport json,pathlib,sys\n'
        + f'pathlib.Path({str(calls)!r}).write_text(json.dumps(sys.argv[1:]))\n'
        + f'print(pathlib.Path({str(response)!r}).read_text())\n')
    executable.chmod(0o700)
    handles = {key: str(uuid.uuid4()) for key in ('request_id', 'receipt_id', 'handle')}
    source = dict(schema='cairn.agent-search/1', selected=[dict(body='whole 日本語 "required"\n\\ context')],
                  index=[dict(summary='source', entities_omitted=2, body_sha256='a' * 64, summary_span=dict(offset=20,length=6), pull_arguments=handles, pull_command='unused')],
                  bytes_remaining=3000, source_seal='retained-seal', omitted={'OPTIONAL_BUDGET': 7})

    def reply(view):
        response.write_text(json.dumps(dict(schema='cairn.response/1', ok=True, data=view)))

    def no_dispatch(name, args, code='INVALID_REQUEST'):
        calls.unlink(missing_ok=True)
        invoke(name, args, code)
        assert not calls.exists(), args

    def flags():
        argv = json.loads(calls.read_text())
        return argv, lambda name: argv[argv.index(name) + 1]

    def size(value):
        return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode())

    try:
        settings_path.write_text(json.dumps(dict(settings, executable=str(executable), tokens=32000)))
        reply(source)
        for name in ('search', 'prepare_note'):
            request = dict(query='current subject', memory_budget_bytes=6000, min_pull_bytes=2000,
                           request_id=str(uuid.uuid4()), entities=[], context={'task_class': 'repair'})
            result = invoke(name, request)
            argv, flag = flags()
            assert flag('--tokens') == '32000' and flag('--min-pull-bytes') == '2000'
            assert flag('--memory-budget-bytes') == str(6000 - (1024 if name == 'prepare_note' else 0))
            assert flag('--request-id') == request['request_id'] and flag('--task-class') == 'repair'
            assert 'create' not in argv and 'edit' not in argv
            assert result['selected'] == source['selected'] and result['omitted'] == source['omitted']
            assert result['index'][0]['entities_omitted'] == 2 and result['index'][0]['summary_span'] == source['index'][0]['summary_span']
            assert result['index'][0]['body_sha256'] == source['index'][0]['body_sha256']
            assert result['index'][0]['pull_arguments'] == handles and 'pull_command' not in result['index'][0]
            assert result['source_seal'] == source['source_seal'] and result['query_entities'] == []
            assert size(result) + result['bytes_remaining'] <= 6000
            if name == 'prepare_note':
                assert result['preparation']['note_saved'] is False
                assert 'Pull complete current bodies' in result['preparation']['guidance']
                assert 'requires protected preview' in result['preparation']['guidance']
            else:
                assert 'preparation' not in result
            assert invoke(name, request) == result  # adapter forwards exact retry arguments
            for invalid in (None, True, 0, -1, 1.5, '2000', 24001, 6001):
                no_dispatch(name, dict(request, min_pull_bytes=invalid))
            no_dispatch(name, dict(query='subject', min_pull_bytes=1))
            # A retry can return a spent balance; reserve is not replenished.
            reply(dict(source, bytes_remaining=1999))
            spent = invoke(name, request)
            assert spent['bytes_remaining'] == 1999
            reply(source)

        no_dispatch('prepare_note', dict(query='  '))
        for field in ('body', 'draft', 'pins', 'semantic', 'browse', 'kinds', 'offset'):
            no_dispatch('prepare_note', dict(query='subject', **{field: 'unsupported'}))
        no_dispatch('prepare_note', dict(query='subject', memory_budget_bytes=1279), 'BUDGET_REFUSED')
        no_dispatch('prepare_note', dict(query='subject', memory_budget_bytes=2000, min_pull_bytes=977))
        for name in ('search', 'prepare_note'):
            # Exact final UTF-8 presentation and remaining expansion allowance.
            reply(dict(source, bytes_remaining=2000))
            result = invoke(name, dict(query='subject', memory_budget_bytes=6000, min_pull_bytes=2000))
            exact = size(result) + 2000
            assert invoke(name, dict(query='subject', memory_budget_bytes=exact, min_pull_bytes=2000)) == result
            invoke(name, dict(query='subject', memory_budget_bytes=exact - 1, min_pull_bytes=2000), 'BUDGET_REFUSED')
            reply(dict(source, selected=[dict(body='mandatory' * 1000)]))
            invoke(name, dict(query='subject', memory_budget_bytes=6000, min_pull_bytes=2000), 'BUDGET_REFUSED')
            reply(source)
            invoke(name, dict(query='subject'))
            argv, flag = flags()
            assert '--min-pull-bytes' not in argv
            if name == 'search':
                assert '--memory-budget-bytes' not in argv
            else:
                assert flag('--memory-budget-bytes') == str(32000 - 1024)
        settings_path.write_text(json.dumps(dict(settings, executable=str(executable),
                                                 context={'task_phase': 'validation'})))
        for name in ('search', 'prepare_note'):
            no_dispatch(name, dict(query='subject', context={'task_phase': 'implementation'}))
        print('Native reserve/preparation: strict validation, forwarding, omission, retry inputs, whole context and exact UTF-8 accounting passed')
    finally:
        settings_path.write_text(json.dumps(settings))


def check_store(invoke, binary, environment, settings_path, settings):
    from check_note_transport import operator
    marker = 'nativereserve' + uuid.uuid4().hex
    fixed = dict(settings, task_id=marker, run_id='attempt')
    scope = dict(repo=settings['repo'], task_id=marker, run_id='*')

    def create(body, **overrides):
        draft = dict(kind='note', body=marker + ' ' + body, scope=scope,
                     sensitivity='shareable', claim_type='self')
        draft.update(overrides)
        return operator(binary, environment, 'create', dict(request_id=str(uuid.uuid4()), draft=draft))

    notes = [create(f'predecessor {i}: 日本語 "quoted" \\ source\n' + 'correct scope and pins. ' * 8)
             for i in range(3)]
    private = create('local only', sensitivity='local')
    foreign = create('other task', scope=dict(scope, task_id='unrelated'))
    try:
        settings_path.write_text(json.dumps(fixed))
        before = operator(binary, environment, 'list', record_id=settings['repo'])
        for name in ('search', 'prepare_note'):
            request = dict(query=marker, request_id=str(uuid.uuid4()), memory_budget_bytes=9000, min_pull_bytes=3000)
            result = invoke(name, request)
            assert result['memory_budget'] == dict(schema='cairn.memory-budget/2',
                bytes=9000 - (1024 if name == 'prepare_note' else 0), min_pull_bytes=3000)
            assert result['available_tokens'] == settings['tokens']
            assert result['bytes_remaining'] >= 3000 and result['index']
            ids = {entry['record_id'] for entry in result['index']}
            assert ids <= {note['record_id'] for note in notes}
            assert not {private['record_id'], foreign['record_id']} & ids
            size = len(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode())
            assert size <= 6000 and size + result['bytes_remaining'] <= 9000
            entry = result['index'][0]
            expanded = invoke('pull', entry['pull_arguments'])
            assert expanded['selection']['record']['body'] == next(n['body'] for n in notes if n['record_id'] == entry['record_id'])
            pull_size = len(json.dumps(expanded, ensure_ascii=False, separators=(',', ':')).encode())
            assert size + pull_size + expanded['bytes_remaining'] <= 9000
            assert invoke('pull', entry['pull_arguments']) == expanded
            retry = invoke(name, request)
            assert retry['receipt_id'] == result['receipt_id'] and retry['source_seal'] == result['source_seal']
            assert retry['bytes_remaining'] == expanded['bytes_remaining'] < result['bytes_remaining']
            for changed in (dict(request, min_pull_bytes=3001), dict(request, memory_budget_bytes=8999),
                            {k:v for k,v in request.items() if k != 'min_pull_bytes'}):
                invoke(name, changed, 'IDEMPOTENCY_CONFLICT')
            operator(binary, environment, 'revise', dict(request_id=str(uuid.uuid4()), record_id=entry['record_id'],
                expected_version=entry['version'], repo=settings['repo'], body=marker + ' revised current source'))
            # Preserve exact retries; only a fresh attempted read is subject to currentness.
            invoke('pull', dict(entry['pull_arguments'], request_id=str(uuid.uuid4())), 'STALE_HANDLE')
            for note in notes:
                if note['record_id'] == entry['record_id']:
                    note['body'] = marker + ' revised current source'
                    note['version'] = entry['version'] + 1
        after = operator(binary, environment, 'list', record_id=settings['repo'])
        assert len(after['records']) == len(before['records']), 'preparation wrote a note'
        assert invoke('prepare_note', dict(query='absent' + uuid.uuid4().hex))['preparation']['note_saved'] is False
        print('Native real API: sealed reserve, checked pull, exact retries/spent balances, scope/privacy and stale handles passed; preparation writes no note')
    finally:
        settings_path.write_text(json.dumps(settings))
