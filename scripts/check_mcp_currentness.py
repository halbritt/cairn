"""Check host-declared applicability through the public MCP executable."""
import json
import subprocess
import uuid

from check_mcp import session


def check(binary, root, environment, grant):
    def operator(command, payload):
        response = subprocess.run([binary, command], input=json.dumps(payload),
                                  env=environment, capture_output=True, text=True,
                                  check=True, timeout=10)
        return json.loads(response.stdout)['data']

    query = 'pinnedmcp' + uuid.uuid4().hex
    pins = dict(revision='a' * 40, workspace_sha256='b' * 64,
                task_class='repair', task_phase='validation', binding_id='fixture-binding',
                capability_id='fixture-capability')
    flags = dict(revision='--revision', workspace_sha256='--workspace-sha256',
                 task_class='--task-class', task_phase='--task-phase', binding_id='--binding',
                 capability_id='--capability')

    def arguments(context):
        return [arg for key, value in context.items() for arg in (flags[key], value)]

    draft = dict(kind='note', body=query + ': procedure for this exact build context',
                 claim_type='self', sensitivity='shareable', pins=pins,
                 scope=dict(repo='fixture:socket', task_id='mcp-integration', run_id='*'))
    capture = dict(request_id=str(uuid.uuid4()), body=draft['body'], pins=pins,
                   kind='procedure', shareable=True)
    with session(binary, root, environment, arguments(pins)) as tool:
        note = tool('cairn_remember', capture)
        assert tool('cairn_remember', capture) == note
        assert 'IDEMPOTENCY_CONFLICT' in tool('cairn_remember',
            dict(capture, pins=dict(pins, task_phase='implementation')), error=True)
        for invalid_pins in (dict(task_phaze='validation'), dict(task_phase=7), dict(task_phase='*')):
            assert tool('cairn_remember', dict(capture, request_id=str(uuid.uuid4()),
                        pins=invalid_pins), error=True)
        plain = tool('cairn_remember', dict(request_id=str(uuid.uuid4()),
                     body='Unpinned capture stays reusable', shareable=True))
        stored = json.loads(subprocess.run([binary, 'get', plain['record_id']],
            env=environment, capture_output=True, text=True, check=True).stdout)['data']
        assert not stored.get('pins'), stored
    with session(binary, root, environment) as tool:
        view = tool('cairn_search', dict(query=query))
        assert not view['index'] and view['omitted']['CONTEXT_MISSING'] == 1, view
        assert 'context' not in view

    with session(binary, root, environment, arguments(pins), generated=True) as tool:
        view = tool('cairn_search', dict(query=query))
        assert view['context'] == pins, view
        assert [entry['record_id'] for entry in view['index']] == [note['record_id']], view
        pulled = tool('cairn_pull', view['index'][0]['pull_arguments'])
        assert pulled['selection']['record']['body'] == draft['body'], pulled
        assert pulled['selection']['record']['pins'] == pins, pulled
        tool('cairn_edit', dict(request_id=str(uuid.uuid4()), record_id=note['record_id'],
             expected_version=1, body=draft['body'] + ' Updated verification.'))
        fresh = tool('cairn_search', dict(query=query))
        revised = tool('cairn_pull', fresh['index'][0]['pull_arguments'])['selection']['record']
        assert revised['version'] == 2 and revised['pins'] == pins
        assert 'additional' in tool('cairn_search', dict(query=query, context={}), error=True)

    for key in pins:
        mismatch = dict(pins, **{key: 'c' * len(pins[key])})
        with session(binary, root, environment, arguments(mismatch)) as tool:
            view = tool('cairn_search', dict(query=query))
            assert not view['index'] and view['omitted']['CURRENTNESS_MISMATCH'] == 1, (key, view)
        missing = {k: v for k, v in pins.items() if k != key}
        with session(binary, root, environment, arguments(missing)) as tool:
            view = tool('cairn_search', dict(query=query))
            assert not view['index'] and view['omitted']['CONTEXT_MISSING'] == 1, (key, view)

    invalid = dict(pins, revision='main')
    with session(binary, root, environment, arguments(invalid)) as tool:
        assert 'INVALID_REQUEST' in tool('cairn_search', dict(query=query), error=True)

    instruction = operator('issue', dict(request_id=str(uuid.uuid4()),
                           draft=dict(draft, kind='instruction', body='Use the declared build context.'),
                           grant_id=grant['grant_id'], mandatory=True,
                           policy_key='mcp-currentness', reason='Disposable mandatory context fixture'))
    with session(binary, root, environment) as tool:
        assert 'POLICY_UNENFORCEABLE' in tool('cairn_search', dict(query=query), error=True)
    with session(binary, root, environment, arguments(pins)) as tool:
        view = tool('cairn_search', dict(query=query))
        selected = next(s for s in view['selected'] if s['record']['record_id'] == instruction['record_id'])
        assert selected['mandatory'] and selected['record']['body'] == 'Use the declared build context.'
    print('MCP startup pins select applicable bodies and mandatory context; missing, mismatched, invalid and tool overrides are refused')
