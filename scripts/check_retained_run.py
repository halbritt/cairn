"""Exercise retained-package CLI execution through the disposable Unix API."""
import json
import subprocess
import uuid


def check(binary, root, environment):
    env = dict(environment, CAIRN_DATABASE_URL='postgres://unavailable.invalid/cairn')
    observer = [binary, 'agent', '--socket', str(root / 'api.sock'),
                '--token-file', str(root / 'observer.token')]
    agent = [binary, 'agent', '--socket', str(root / 'api.sock'),
             '--token-file', str(root / 'agent.token')]

    def call(base, operation, request):
        result = subprocess.run(base + [operation], input=json.dumps(request),
                                env=env, text=True, capture_output=True, timeout=10, check=True)
        return json.loads(result.stdout)['data']

    scope = dict(repo='fixture:socket', task_id='retained-cli', run_id=str(uuid.uuid4()))
    draft = dict(kind='note', body='Retained CLI instruction fixture', claim_type='self', scope=scope)
    call(agent, 'create', dict(request_id=str(uuid.uuid4()), draft=draft))
    context = dict(task_class='build', task_phase='validation', binding_id='retained-cli', capability_id='shell')
    request = dict(request_id=str(uuid.uuid4()), scope=scope, query='retained', purpose='context',
                   available_tokens=32000, context=context, kinds=['note', 'decision'])
    package = call(observer, 'compile', request)
    draft = dict(draft, body='New optional retained CLI note')
    call(agent, 'create', dict(request_id=str(uuid.uuid4()), draft=draft))
    reference = dict(receipt_id=package['receipt_id'], seal=package['seal'])
    assert call(observer, 'run-package', reference) == package

    args = observer + ['run', '--repo', scope['repo'], '--task', scope['task_id'],
                       '--run', scope['run_id'], '--request-id', request['request_id'],
                       '--query', request['query'], '--prompt', 'Use the pinned package',
                       '--task-class', context['task_class'], '--task-phase', context['task_phase'], '--binding', context['binding_id'],
                       '--capability', context['capability_id'], '--receipt-id', package['receipt_id'],
                       '--seal', package['seal']]
    unfiltered_args = args
    args = args + ['--kind', 'decision', '--kind', 'note', '--kind', 'decision']
    # Flag pairing and intent mismatch must fail before the valid execution.
    for bad_args in [unfiltered_args, unfiltered_args[:-2], args + ['--query', 'different'],
                     args + ['--kind', 'unknown'], args + ['--task-phase', 'implementation'], args + ['--task-phase', ''],
                     args + ['--receipt-id', '', '--seal', '']]:
        refused = subprocess.run(bad_args + ['--', '/bin/cat'], env=env, text=True,
                                 capture_output=True, timeout=10)
        assert refused.returncode != 0 and 'INVALID_REQUEST' in refused.stdout + refused.stderr
        status = call(observer, 'run-status', dict(receipt_id=package['receipt_id']))
        assert not status['launch_claimed'] and not status['binding_observed']

    actual = subprocess.run(args + ['--', '/bin/cat'], env=env, text=True,
                            capture_output=True, timeout=10, check=True)
    result = json.loads(actual.stderr)['data']
    assert result['receipt_id'] == package['receipt_id'] and result['seal'] == package['seal']
    assert result['process_state'] == 'exited' and result['exit_code'] == 0
    assert json.loads(actual.stdout.split('\n')[2]) == package['semantic']
    assert 'New optional retained CLI note' not in actual.stdout
    status = call(observer, 'run-status', dict(receipt_id=package['receipt_id']))
    assert status['launch_claimed'] and status['outcome']['observation_id'] == result['outcome_id']
    repeated = subprocess.run(args + ['--', '/bin/cat'], env=env, text=True,
                              capture_output=True, timeout=10)
    assert repeated.returncode != 0 and 'RUN_ALREADY_STARTED' in repeated.stdout + repeated.stderr
    fresh = subprocess.run(observer + ['run', '--repo', scope['repo'], '--task', scope['task_id'],
                           '--run', scope['run_id'], '--kind', 'procedure', '--query', 'retained',
                           '--binding', 'filtered-fresh', '--', '/bin/cat'], env=env, text=True,
                           capture_output=True, timeout=10, check=True)
    memory = json.loads(fresh.stdout.split('\n')[2])
    assert memory['kinds'] == ['procedure'] and memory['selected'] == []
    print('Fresh and retained CLI execution accept normalized kind filters; changed or missing filters refuse before binding')
    print('Retained CLI execution preserves receipt/seal/context without client DB access; mismatches and duplicate launches refuse')
