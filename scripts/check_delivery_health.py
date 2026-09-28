"""Exercise the actual diagnostic API and operator attention on a disposable DB."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone


def check(binary, directory):
    dsn = os.environ.get('CAIRN_TEST_DATABASE_URL')
    assert dsn and dsn == os.environ.get('CAIRN_DATABASE_URL'), 'disposable test DSN required'
    root = Path(directory)
    root.mkdir(mode=0o700)
    repo = 'delivery-health-probe:' + str(uuid.uuid4())
    identities = []
    for name in ('sender', 'recipient', 'stranger'):
        token = secrets.token_urlsafe(32)
        path = root / (name + '.token')
        path.write_text(token)
        path.chmod(0o600)
        identities.append(dict(token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                               principal='agent/' + name, repo=repo, role='agent', destination='hosted'))
    config = root / 'identities.json'
    config.write_text(json.dumps(identities))
    config.chmod(0o600)
    env = dict(os.environ, CAIRN_HOME=str(root))

    def call(name, operation, body, expected='OK'):
        argv = [binary, 'agent', '--token-file', str(root / (name + '.token')), operation]
        payload = json.dumps(body)
        if operation == 'remember':
            argv += ['--request-id', body['request_id'], '--repo', repo, '--shareable', '--stdin']
            payload = body['body']
        result = subprocess.run(argv, input=payload, env=env, text=True,
                                capture_output=True, timeout=10)
        output = json.loads(result.stdout)
        assert output['status'] == expected, (operation, output, result.stderr)
        assert (result.returncode == 0) == (expected == 'OK'), output
        return output.get('data')

    def review(**extra):
        request = dict(repo=repo, state='pending', limit=100,
                       attention=dict(older_than_seconds=1, state_file=str(root / 'attention.json')))
        request.update(extra)
        result = subprocess.run([binary, 'coordination-review'], input=json.dumps(request),
                                env=env, text=True, capture_output=True, timeout=10, check=True)
        return json.loads(result.stdout)['data']

    process = subprocess.Popen([binary, 'serve'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not (root / 'api.sock').exists():
            assert process.poll() is None, process.stderr.read().decode()
            assert time.monotonic() < deadline, 'test API did not start'
            time.sleep(.02)
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
        process.terminate()
        process.communicate(timeout=10)
        assert process.returncode == 0


if __name__ == '__main__':
    check(*sys.argv[1:])
