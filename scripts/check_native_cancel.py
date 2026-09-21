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

        # Durable capture through stable per-item UUID identities.
        item = dict(item_id='exec-probe', process_id='4242', command='/bin/sleep 30',
                    native_turn_id='turn-probe')

        def capture(process_id, request_id=None):
            return agent('session-tool-capture', dict(
                request_id=request_id or str(uuid.uuid4()), session=session,
                attempt_id=attempt['attempt_id'],
                items=[dict(item, process_id=process_id)]))

        stable = str(uuid.uuid4())
        captured = capture('4242', stable)
        assert captured['tools'][0]['stop_state'] == 'captured', captured
        again = capture('4242', stable)
        assert [t['stop_state'] for t in again['tools']] == ['captured'], 'retry changed the capture'

        def raw(operation, body):
            result = subprocess.run([binary, 'agent', '--socket', str(root / 'api.sock'),
                                     '--token-file', str(root / 'agent.token'), operation],
                                    input=json.dumps(body), text=True, capture_output=True, timeout=15)
            return json.loads(result.stdout)

        stale = raw('session-tool-capture', dict(request_id=str(uuid.uuid4()), session=session,
            attempt_id=attempt['attempt_id'], items=[dict(item, process_id='9999')]))
        assert stale['status'] == 'IDEMPOTENCY_CONFLICT', stale

        control = agent('session-inbox-control', dict(agent_id=session['agent_id'],
                                                      execution_id=session['execution_id'])).get('attempt')
        assert control is not None and control.get('cancel') is None and control['native_turn_id'] == 'turn-probe'

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

        def report(body):
            return agent('session-tool-stop', dict(request_id=str(uuid.uuid4()), session=session,
                                                   attempt_id=attempt['attempt_id'], **body))

        def reconcile(reason):
            return raw('session-inbox-reconcile', dict(request_id=str(uuid.uuid4()), session=session,
                                                       attempt_id=attempt['attempt_id'], reason=reason))

        # Ambiguity holds; positive evidence confirms.
        issued = report(dict(tools=[dict(item_id='exec-probe', stop_state='stop_issued')]))
        assert issued['tools'][0]['stop_state'] == 'stop_issued', issued
        assert not issued['tools'][0].get('stop_at'), issued
        report(dict(turn_stop='ambiguous', terminal_scan='clear',
                    tools=[dict(item_id='exec-probe', stop_state='terminated')]))
        pending = agent('session-inbox-control', dict(agent_id=session['agent_id'],
                                                      execution_id=session['execution_id'])).get('attempt')
        assert pending['cancel'] and not pending['cancel'].get('confirmed_at')
        refused = reconcile('cancel_confirmed')
        assert refused['status'] == 'CLEANUP_UNCONFIRMED', refused
        completed = report(dict(capture_state='complete'))
        assert completed['capture_state'] == 'complete', completed
        refused = reconcile('cancel_confirmed')
        assert refused['status'] == 'CLEANUP_UNCONFIRMED', 'latched ambiguity must hold despite complete capture'
        report(dict(turn_stop='interrupted'))
        report(dict(terminal_scan='clear'))
        confirmed = reconcile('cancel_confirmed')
        assert confirmed['status'] == 'OK', confirmed
        final = agent('session-inbox-control', dict(agent_id=session['agent_id'],
                                                    execution_id=session['execution_id'])).get('attempt')
        assert final is None, 'hold released'
        failed = agent('event-inspect', dict(event_id=event['event_id']))['deliveries'][0]
        assert failed['state'] == 'failed' and failed['code'] == 'operator_cancelled', failed

        # Real-API revocation exercise: a second exclusive attempt whose
        # owner-join revocation must NOT release the hold without the full
        # evidence family, then releases with a separate outcome label.
        event2 = agent('event-publish', dict(request_id=str(uuid.uuid4()), kind='request',
            ref=dict(record_id=note['record_id'], version=1),
            destination=dict(type='agent', name=registered['inbox'])))
        delivery2 = agent('event-inspect', dict(event_id=event2['event_id']))['deliveries'][0]['delivery_id']
        claim2 = agent('session-inbox-claim', dict(request_id=str(uuid.uuid4()), session=session,
            delivery_id=delivery2, native_turn_id='turn-revoke', turn_exclusive=True))['attempt']
        agent('session-tool-capture', dict(request_id=str(uuid.uuid4()), session=session,
            attempt_id=claim2['attempt_id'],
            items=[dict(item_id='exec-revoke', process_id='4243', command='/bin/sleep 30',
                        native_turn_id='turn-revoke')]))
        agent('session-tool-stop', dict(request_id=str(uuid.uuid4()), session=session,
            attempt_id=claim2['attempt_id'], tools=[dict(item_id='exec-revoke', stop_state='unavailable')]))
        cancelled2 = operator('work-cancel', dict(request_id=str(uuid.uuid4()), repo=repo,
            delivery_id=delivery2, reason='Real-API revocation probe'))
        assert cancelled2['native']['state'] == 'cancel_pending', cancelled2
        agent('session-tool-stop', dict(request_id=str(uuid.uuid4()), session=session,
            attempt_id=claim2['attempt_id'], owner_join=True))
        revoked = raw('session-inbox-reconcile', dict(request_id=str(uuid.uuid4()), session=session,
            attempt_id=claim2['attempt_id'], reason='exclusivity_revoked'))
        assert revoked['status'] == 'CLEANUP_UNCONFIRMED', revoked
        agent('session-tool-stop', dict(request_id=str(uuid.uuid4()), session=session,
            attempt_id=claim2['attempt_id'], turn_stop='ended'))
        agent('session-tool-stop', dict(request_id=str(uuid.uuid4()), session=session,
            attempt_id=claim2['attempt_id'], terminal_scan='clear'))
        closed = raw('session-inbox-reconcile', dict(request_id=str(uuid.uuid4()), session=session,
            attempt_id=claim2['attempt_id'], reason='exclusivity_revoked'))
        assert closed['status'] == 'OK', closed
        attempt_closed = closed["data"]
        assert attempt_closed['reason'] == 'exclusivity_revoked', attempt_closed
        assert attempt_closed['cancel']['confirmed_at'] is None, attempt_closed
        revoked_delivery = agent('event-inspect', dict(event_id=event2['event_id']))['deliveries'][0]
        assert revoked_delivery['code'] == 'operator_cancelled', revoked_delivery
        print('Real-API native cancellation: capture, fence, ambiguity hold, confirmed cleanup and revocation passed')
    finally:
        api.terminate()
        try:
            api.wait(timeout=10)
        except subprocess.TimeoutExpired:
            api.kill()
            api.wait()


if __name__ == '__main__':
    check(sys.argv[1], sys.argv[2])
