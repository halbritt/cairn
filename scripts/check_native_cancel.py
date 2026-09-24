"""Real-API native cancellation checks on an explicitly disposable store.

Drives integrations/lifecycle/native_control.py through the authenticated CLI
so generated mutation identities meet the same validation as any caller.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT / 'integrations/lifecycle'))
import native_control  # noqa: E402


def check(binary, root):
    assert os.environ.get('CAIRN_TEST_DATABASE_URL')
    assert os.environ['CAIRN_DATABASE_URL'] == os.environ['CAIRN_TEST_DATABASE_URL']
    root = Path(root)
    root.mkdir(mode=0o700)
    repo = 'native-cancel-probe:' + str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    (root / 'agent.token').write_text(token)
    (root / 'agent.token').chmod(0o600)
    identities = [dict(principal='native-cancel-probe/agent', role='agent', repo=repo,
                       destination='hosted', token_sha256=hashlib.sha256(token.encode()).hexdigest())]
    (root / 'identities.json').write_text(json.dumps(identities))
    (root / 'identities.json').chmod(0o600)
    env = dict(os.environ, CAIRN_HOME=str(root))
    api = subprocess.Popen([binary, 'serve'], env=env, stdout=subprocess.DEVNULL,
                           stderr=(root / 'api.log').open('w'))
    try:
        for _ in range(100):
            if (root / 'api.sock').exists():
                break
            time.sleep(0.1)
        assert (root / 'api.sock').exists(), 'API socket never appeared'

        def agent(operation, body):
            result = subprocess.run([binary, 'agent', '--socket', str(root / 'api.sock'),
                                     '--token-file', str(root / 'agent.token'), operation],
                                    input=json.dumps(body), text=True, capture_output=True, timeout=15)
            envelope = json.loads(result.stdout)
            assert envelope['status'] == 'OK', (operation, envelope, result.stderr)
            return envelope['data']

        def operator(operation, body):
            result = subprocess.run([binary, operation], input=json.dumps(body), env=env,
                                    text=True, capture_output=True, timeout=20, check=True)
            return json.loads(result.stdout)['data']

        registered = agent('agent-register', dict(request_id=str(uuid.uuid4()), binding='codex-default',
            native_session_id='thread-probe', metadata=dict(harness='codex', project='native-cancel',
            workspace=str(root), state='busy', delivery_mode='existing-session')))
        session = dict(agent_id=registered['agent_id'], execution_id=registered['execution_id'])
        note = agent('create', dict(request_id=str(uuid.uuid4()), draft=dict(kind='note',
            body='Disposable native cancellation fixture', scope=dict(repo=repo, task_id='*', run_id='*'),
            claim_type='self', sensitivity='shareable')))
        event = agent('event-publish', dict(request_id=str(uuid.uuid4()), kind='request',
            ref=dict(record_id=note['record_id'], version=1),
            destination=dict(type='agent', name=registered['inbox'])))
        delivery = agent('event-inspect', dict(event_id=event['event_id']))['deliveries'][0]['delivery_id']
        claim = agent('session-inbox-claim', dict(request_id=str(uuid.uuid4()), session=session,
            delivery_id=delivery, native_turn_id='turn-probe', turn_exclusive=True))
        attempt = claim['attempt']
        assert attempt['native_turn_id'] == 'turn-probe'

        config = dict(cairn=binary, socket=str(root / 'api.sock'),
                      token_file=str(root / 'agent.token'), binding='native-cancel-check',
                      state_dir=str(root))
        state_path = root / 'session.json'
        state = dict(agent=dict(agent_id=session['agent_id'], execution_id=session['execution_id'],
                                native_session_id='thread-probe'), process=dict(pid=os.getpid()))

        # Durable capture through the module's own UUID identities.
        item = dict(item_id='exec-probe', process_id='4242', command='/bin/sleep 30',
                    native_turn_id='turn-probe')
        captured = native_control.capture_tools(config, session, attempt['attempt_id'], item)
        assert captured['tools'][0]['stop_state'] == 'captured', captured
        again = native_control.capture_tools(config, session, attempt['attempt_id'], item)
        assert [t['stop_state'] for t in again['tools']] == ['captured'], 'retry changed the capture'
        stale = dict(item, process_id='9999')
        try:
            native_control.capture_tools(config, session, attempt['attempt_id'], stale)
            raise AssertionError('recapture with a different handle must refuse')
        except native_control.ControlError as exc:
            assert exc.code == 'IDEMPOTENCY_CONFLICT', exc.code

        control = native_control.inbox_control(config, session)
        assert control.get('cancel') is None and control['native_turn_id'] == 'turn-probe'

        # Fence: operator cancellation then completion refusal.
        cancelled = operator('work-cancel', dict(request_id=str(uuid.uuid4()), repo=repo,
            delivery_id=delivery, reason='Real-API native cancellation probe'))
        assert cancelled['native']['state'] == 'cancel_pending', cancelled
        completion = subprocess.run([binary, 'complete', '--socket', str(root / 'api.sock'),
            '--token-file', str(root / 'agent.token'), '--agent-id', session['agent_id'],
            '--execution-id', session['execution_id'], '--request-id', str(uuid.uuid4()),
            '--lease', attempt['delivery']['lease_id'], '--stdin', delivery],
            input=b'late result must not land', capture_output=True, timeout=15)
        envelope = json.loads(completion.stdout)
        assert envelope['status'] == 'REQUEST_CANCELLED', envelope

        # Ambiguity holds; positive evidence confirms through the module.
        issued = native_control.report_stop(config, session, attempt['attempt_id'],
                                            tools=[dict(item_id='exec-probe', stop_state='stop_issued')])
        assert issued['tools'][0]['stop_state'] == 'stop_issued', issued
        assert not issued['tools'][0].get('stop_at'), issued
        native_control.report_stop(config, session, attempt['attempt_id'], turn_stop='ambiguous',
                                   terminal_scan='clear',
                                   tools=[dict(item_id='exec-probe', stop_state='terminated')])
        pending = native_control.inbox_control(config, session)
        assert pending['cancel'] and not pending['cancel'].get('confirmed_at')
        try:
            native_control.reconcile(config, session, attempt['attempt_id'], 'cancel_confirmed', str(uuid.uuid4()))
            raise AssertionError('ambiguous turn stop must keep the hold')
        except native_control.ControlError as exc:
            assert exc.code == 'CLEANUP_UNCONFIRMED', exc.code
        completed = native_control.report_stop(config, session, attempt['attempt_id'],
                                               capture_state='complete')
        assert completed['capture_state'] == 'complete', completed
        try:
            native_control.reconcile(config, session, attempt['attempt_id'], 'cancel_confirmed', str(uuid.uuid4()))
            raise AssertionError('a latched ambiguity must keep the hold even with complete capture')
        except native_control.ControlError as exc:
            assert exc.code == 'CLEANUP_UNCONFIRMED', exc.code
        native_control.report_stop(config, session, attempt['attempt_id'], turn_stop='interrupted')
        native_control.reconcile(config, session, attempt['attempt_id'], 'cancel_confirmed', str(uuid.uuid4()))
        final = native_control.inbox_control(config, session)
        assert final is None, 'hold released'
        failed = agent('event-inspect', dict(event_id=event['event_id']))['deliveries'][0]
        assert failed['state'] == 'failed' and failed['code'] == 'operator_cancelled', failed
        print('Real-API native cancellation: capture, fence, ambiguity hold and confirmed cleanup passed')
    finally:
        api.terminate()
        try:
            api.wait(timeout=10)
        except subprocess.TimeoutExpired:
            api.kill()
            api.wait()


if __name__ == '__main__':
    check(sys.argv[1], sys.argv[2])
