"""Real cancellation/deadline cgroup checks on an explicitly disposable store."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time
import uuid

from check_wakeups import wait_for


def check(binary, root):
    assert os.environ.get('CAIRN_TEST_DATABASE_URL')
    assert os.environ['CAIRN_DATABASE_URL'] == os.environ['CAIRN_TEST_DATABASE_URL']
    root = Path(root)
    root.mkdir(mode=0o700)
    repo = 'control-probe:' + str(uuid.uuid4())
    identities = []
    for name in ('agent', 'observer'):
        token = secrets.token_urlsafe(32)
        path = root / (name + '.token')
        path.write_text(token)
        path.chmod(0o600)
        identities.append(dict(principal='control-probe/' + name, role=name, repo=repo,
                               destination='hosted', token_sha256=hashlib.sha256(token.encode()).hexdigest()))
    (root / 'identities.json').write_text(json.dumps(identities))
    (root / 'identities.json').chmod(0o600)
    env = dict(os.environ, CAIRN_HOME=str(root))
    api = subprocess.Popen([binary, 'serve'], env=env, stdout=subprocess.DEVNULL,
                           stderr=(root / 'api.log').open('w'))
    supervisor = None
    units = set()

    def operator(operation, body):
        result = subprocess.run([binary, operation], input=json.dumps(body), env=env,
                                text=True, capture_output=True, timeout=20, check=True)
        return json.loads(result.stdout)['data']

    def call(operation, body, expected='OK'):
        result = subprocess.run([binary, 'agent', '--socket', str(root / 'api.sock'),
                                 '--token-file', str(root / 'agent.token'), operation],
                                input=json.dumps(body), text=True, capture_output=True, timeout=15)
        envelope = json.loads(result.stdout)
        assert envelope['status'] == expected, (operation, envelope, result.stderr)
        return envelope.get('data')

    def publish(**timing):
        note = call('create', dict(request_id=str(uuid.uuid4()), draft=dict(kind='note',
                    body='Disposable request control process fixture', scope=dict(repo=repo, task_id='*', run_id='*'),
                    claim_type='self', sensitivity='shareable')))
        return call('event-publish', dict(request_id=str(uuid.uuid4()), kind='request',
                    ref=dict(record_id=note['record_id'], version=1), destination=dict(type='pool', name='controls'),
                    pool=dict(workspace=str(root)), **timing))

    def status(event):
        return call('event-inspect', dict(event_id=event['event_id']))

    def active():
        attempts = call('wake-attempts', dict(active=True))['attempts']
        units.update('cairn-wake-attempt-' + a['attempt_id'] + '.service' for a in attempts)
        return attempts

    def child_running(pid):
        try:
            return ') Z ' not in Path(f'/proc/{pid}/stat').read_text()
        except FileNotFoundError:
            return False

    def unit_empty(unit):
        output = subprocess.run(['systemctl', '--user', 'show', unit,
                                  '--property=LoadState,ActiveState,ControlGroup'],
                                 text=True, capture_output=True, check=True, timeout=10).stdout
        state = dict(line.split('=', 1) for line in output.splitlines())
        if group := state.get('ControlGroup'):
            path = Path('/sys/fs/cgroup') / group.lstrip('/') / 'cgroup.events'
            if path.exists() and 'populated 1' in path.read_text():
                return False
        return state['LoadState'] == 'not-found' or state['ActiveState'] in ('inactive', 'failed')

    worker = root / 'worker.py'
    worker.write_text('''import json,os,subprocess,time
from pathlib import Path
context=json.loads(Path(os.environ['CAIRN_WAKE_CONTEXT']).read_text())
child=subprocess.Popen(['/usr/bin/sleep','300'],start_new_session=True)
Path(context['event_id']+'.started').write_text(json.dumps(dict(child=child.pid,context=context)))
time.sleep(300)
''')
    config = dict(name='control-probe', principal='control-probe/agent', repo=repo,
                  socket=str(root / 'api.sock'), agent_token=str(root / 'agent.token'),
                  observer_token=str(root / 'observer.token'), directory=str(root),
                  state_directory=str(root / 'artifacts'), timeout_seconds=60,
                  command=['/usr/bin/python3', str(worker)], worker=dict(name='control-probe',
                  harness='generic', workspace=str(root), pools=['controls'], launch_spacing_seconds=1))
    config_path = root / 'wake.json'
    config_path.write_text(json.dumps(config))

    def start():
        nonlocal supervisor
        supervisor = subprocess.Popen([binary, 'wake', 'serve', '--config', str(config_path)],
                                      stdout=subprocess.DEVNULL, stderr=(root / 'supervisor.log').open('a'))

    def launched(event):
        marker = root / (event['event_id'] + '.started')
        wait_for(marker.exists, 15)
        selected = json.loads(marker.read_text())
        attempt = next(a for a in active() if a['delivery']['event']['event_id'] == event['event_id'])
        return selected, attempt

    def stopped(event, selected, attempt, code):
        wait_for(lambda: not active(), 20)
        assert not child_running(selected['child']), 'hold released while detached child is executing'
        assert unit_empty('cairn-wake-attempt-' + attempt['attempt_id'] + '.service')
        delivery = status(event)['deliveries'][0]
        assert delivery['state'] == 'failed' and delivery['code'] == code, delivery

    try:
        wait_for(lambda: (root / 'api.sock').exists())
        operator('worker-pool-configure', dict(request_id=str(uuid.uuid4()), repo=repo, name='controls',
                 enabled=True, max_pending=20, max_pending_per_publisher=10))
        expired = publish(admission_expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
        operator('request-control-sweep', dict(repo=repo))
        assert status(expired)['pool']['control']['code'] == 'admission_expired'
        queued = publish()
        operator('work-cancel', dict(request_id=str(uuid.uuid4()), repo=repo,
                                    pool_event_id=queued['event_id'], reason='Cancel queued fixture'))
        assert status(queued)['pool']['control']['code'] == 'operator_cancelled'

        start()
        expiry = datetime.now(timezone.utc) + timedelta(seconds=5)
        event = publish(admission_expires_at=expiry.isoformat())
        selected, attempt = launched(event)
        assert selected['context']['admission_expires_at']
        wait_for(lambda: datetime.now(timezone.utc) > expiry + timedelta(seconds=.2), 8)
        assert child_running(selected['child']) and status(event)['deliveries'][0]['state'] == 'leased'
        cancelled = operator('work-cancel', dict(request_id=str(uuid.uuid4()), repo=repo,
                             delivery_id=attempt['delivery']['delivery_id'], reason='Stop running fixture'))
        assert cancelled['held'] and cancelled['control']['code'] == 'operator_cancelled'
        call('event-complete', dict(request_id=str(uuid.uuid4()), delivery_id=attempt['delivery']['delivery_id'],
             lease_id=attempt['delivery']['lease_id'], disposition='handled'), 'STALE_LEASE')
        stopped(event, selected, attempt, 'operator_cancelled')

        event = publish(task_deadline=(datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat())
        selected, attempt = launched(event)
        assert selected['context']['task_deadline']
        stopped(event, selected, attempt, 'task_deadline')

        # The transient unit also enforces the deadline if the supervising
        # process disappears. Its hold remains until the restarted host observes stop.
        event = publish(task_deadline=(datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat())
        selected, attempt = launched(event)
        supervisor.send_signal(signal.SIGKILL)
        supervisor.wait(timeout=5)
        supervisor = None
        wait_for(lambda: not child_running(selected['child']), 15)
        assert len(active()) == 1, 'worker self-released the hold without host cleanup'
        start()
        stopped(event, selected, attempt, 'task_deadline')
        matching = [a for a in call('wake-attempts', {})['attempts'] if a['delivery']['event']['event_id'] == event['event_id']]
        assert len(matching) == 1
        print('Request controls: queued expiry/cancellation, live TTL survival, completion fence, cgroup stop and deadline after supervisor death passed')
    finally:
        try:
            if supervisor is not None:
                supervisor.terminate()
                supervisor.wait(timeout=35)
            for unit in units:
                if not unit_empty(unit):
                    subprocess.run(['systemctl', '--user', 'stop', unit], capture_output=True, timeout=20, check=True)
                    assert unit_empty(unit)
        finally:
            api.terminate()
            api.wait(timeout=15)


if __name__ == '__main__':
    check(*sys.argv[1:])
