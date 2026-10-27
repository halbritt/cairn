"""Exercise the actual diagnostic API and operator attention on a disposable DB."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

import disposable_host


def check(binary, directory):
    database = disposable_host.ProvidedDatabase.from_environment()
    root = Path(directory)
    repo = 'delivery-health-probe:' + str(uuid.uuid4())
    store = disposable_host.DisposableHost.start(
        database, binary=binary, repo=repo, home=root, roles=('sender', 'recipient', 'stranger'),
        principals=lambda name: 'agent/' + name, label='delivery_health')
    env = store.env

    def call(name, operation, body, expected='OK'):
        argv = ['agent', '--token-file', str(root / (name + '.token')), operation]
        payload = body
        if operation == 'remember':
            argv += ['--request-id', body['request_id'], '--repo', repo, '--shareable', '--stdin']
            payload = body['body']
        result = store.command(argv, payload, expected=expected, timeout=10, label=operation)
        return result if expected == 'OK' else result.get('data')

    def review(**extra):
        request = dict(repo=repo, state='pending', limit=100,
                       attention=dict(older_than_seconds=1, state_file=str(root / 'attention.json')))
        request.update(extra)
        return store.operator('coordination-review', request, timeout=10)

    try:
        agent = call('recipient', 'agent-register', dict(request_id=str(uuid.uuid4()), binding='probe',
                     native_session_id=str(uuid.uuid4()), metadata=dict(harness='codex', project='fixture',
                     workspace=str(root), state='idle', delivery_mode='existing-session')))
        session = {key: agent[key] for key in ('agent_id', 'execution_id')}
        source = call('sender', 'remember', dict(request_id=str(uuid.uuid4()), body='Selected diagnostic probe',
                      shareable=True))
        event = call('sender', 'event-publish', dict(request_id=str(uuid.uuid4()), kind='request',
                     ref=dict(record_id=source['record_id'], version=1),
                     destination=dict(type='agent', name=agent['inbox'])))
        status = call('sender', 'event-inspect', dict(event_id=event['event_id']))
        delivery = status['deliveries'][0]['delivery_id']
        assert status['deliveries'][0]['diagnosis']['host']['condition'] == 'unknown'
        observation = dict(session=session, delivery_id=delivery, condition='native_transport_unavailable',
                           observed_at=datetime.now(timezone.utc).isoformat())
        first = call('recipient', 'session-delivery-observe', observation)
        duplicate = call('recipient', 'session-delivery-observe', observation)
        assert first['applied'] and not duplicate['applied']
        assert first['observation']['received_at'] == duplicate['observation']['received_at']
        call('stranger', 'session-delivery-observe', observation, expected='NOT_FOUND')
        status = call('sender', 'event-inspect', dict(event_id=event['event_id']))
        host = status['deliveries'][0]['diagnosis']['host']
        assert (host['condition'], host['freshness'], host['applies']) == ('native_transport_unavailable', 'current', 'exact'), host
        time.sleep(1.05)
        first_review = review()
        assert len(first_review['attention']['changed']) == 1, first_review
        assert not review()['attention']['changed']
        partial = review(after=9223372036854775806, limit=1)
        assert not partial['attention']['complete'] and not partial['attention']['cleared'], partial
        attempt = call('recipient', 'session-inbox-claim', dict(request_id=str(uuid.uuid4()), session=session))['attempt']
        status = call('sender', 'event-inspect', dict(event_id=event['event_id']))
        assert status['deliveries'][0]['diagnosis']['stage'] == 'held', status
        assert 'host' not in status['deliveries'][0]['diagnosis'], status
        claimed = attempt['delivery']
        complete = subprocess.run([binary, 'complete', '--token-file', str(root / 'recipient.token'),
                     '--agent-id', session['agent_id'], '--execution-id', session['execution_id'],
                     '--request-id', str(uuid.uuid4()), '--lease', claimed['lease_id'], '--shareable', '--stdin', delivery],
                     input='Diagnostic probe handled; no reply or task acceptance inferred.', env=env,
                     text=True, capture_output=True, timeout=10, check=True)
        assert json.loads(complete.stdout)['data']['state'] == 'handled'
        final = review()
        assert len(final['attention']['cleared']) == 1 and not final['attention']['changed'], final
        call('recipient', 'session-inbox-reconcile', dict(request_id=str(uuid.uuid4()), session=session,
             attempt_id=attempt['attempt_id'], reason='turn_ended'))
        print(json.dumps(dict(probe='delivery-health', result='passed', claims=[
            'unknown before observation', 'current exact host diagnosis', 'duplicate cannot refresh',
            'foreign profile refused', 'unchanged summary quiet', 'partial absence preserved',
            'claim supersedes host cause', 'completion clears attention'])) )
    finally:
        assert store.close() == 0


if __name__ == '__main__':
    check(*sys.argv[1:])
