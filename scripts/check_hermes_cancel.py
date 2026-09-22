"""Real-API Hermes cancellation integration check (disposable store only).

Provider/model substitution: no Hermes CLI, provider or model runs. The
native bridge is substituted by a fake Unix-socket bridge implementing the
deployed session/abort (request/turn-fenced) and session/tools_status
surface with verified peer identity; the cairn side runs against the real
authenticated API started from the built binary.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'integrations/lifecycle'))
import hermes_cancel  # noqa: E402


class FakeBridge:
    def __init__(self, root):
        self.path = str(root / 'bridge.sock')
        self.requests = []
        self.abort_result = {'aborted': True, 'turn_stop': 'interrupted',
                             'tools': [dict(item_id='tool-one', stop_state='terminated')]}
        self.status_result = {'tools': []}
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(self.path)
        self.listener.listen(4)
        self.listener.settimeout(5)

    def serve(self):
        def run():
            try:
                while True:
                    conn, _ = self.listener.accept()
                    with conn, conn.makefile('rb') as stream:
                        conn.settimeout(5)
                        while True:
                            raw = stream.readline()
                            if not raw:
                                break
                            request = json.loads(raw)
                            self.requests.append(request)
                            method = request['method']
                            result = (self.abort_result if method == 'session/abort'
                                      else self.status_result if method == 'session/tools_status' else {})
                            conn.sendall((json.dumps({'id': request['id'], 'result': result}) + '\n').encode())
            except OSError:
                pass

        threading.Thread(target=run, daemon=True).start()

    def process(self):
        pid = os.getpid()
        return dict(pid=pid,
                    start=int(Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]),
                    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip())


def check(binary, root):
    assert os.environ.get('CAIRN_TEST_DATABASE_URL')
    assert os.environ['CAIRN_DATABASE_URL'] == os.environ['CAIRN_TEST_DATABASE_URL']
    root = Path(root)
    root.mkdir(mode=0o700)
    repo = 'hermes-cancel-probe:' + str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    (root / 'agent.token').write_text(token)
    (root / 'agent.token').chmod(0o600)
    identities = [dict(principal='hermes-cancel-probe/agent', role='agent', repo=repo,
                       destination='hosted', token_sha256=hashlib.sha256(token.encode()).hexdigest())]
    (root / 'identities.json').write_text(json.dumps(identities))
    (root / 'identities.json').chmod(0o600)
    env = dict(os.environ, CAIRN_HOME=str(root))
    api = subprocess.Popen([binary, 'serve'], env=env, stdout=subprocess.DEVNULL,
                           stderr=(root / 'api.log').open('w'))
    bridge = FakeBridge(root)
    bridge.serve()
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

        def raw(operation, body):
            result = subprocess.run([binary, 'agent', '--socket', str(root / 'api.sock'),
                                     '--token-file', str(root / 'agent.token'), operation],
                                    input=json.dumps(body), text=True, capture_output=True, timeout=15)
            return json.loads(result.stdout)

        def operator(operation, body):
            result = subprocess.run([binary, operation], input=json.dumps(body), env=env,
                                    text=True, capture_output=True, timeout=20, check=True)
            return json.loads(result.stdout)['data']

        registered = agent('agent-register', dict(request_id=str(uuid.uuid4()), binding='hermes-probe',
            native_session_id='hermes-native', metadata=dict(harness='hermes', project='hermes-cancel',
            workspace=str(root), state='busy', delivery_mode='existing-session')))
        session = dict(agent_id=registered['agent_id'], execution_id=registered['execution_id'])
        note = agent('create', dict(request_id=str(uuid.uuid4()), draft=dict(kind='note',
            body='Disposable hermes cancellation fixture', scope=dict(repo=repo, task_id='*', run_id='*'),
            claim_type='self', sensitivity='shareable')))
        event = agent('event-publish', dict(request_id=str(uuid.uuid4()), kind='request',
            ref=dict(record_id=note['record_id'], version=1),
            destination=dict(type='agent', name=registered['inbox'])))
        delivery = agent('event-inspect', dict(event_id=event['event_id']))['deliveries'][0]['delivery_id']
        config = dict(cairn=binary, socket=str(root / 'api.sock'),
                      token_file=str(root / 'agent.token'), state_dir=str(root))

        # 1. Admission attestation: exclusive claim bound to the wake turn.
        claim = hermes_cancel.attest_admission(config, session, delivery, 'hermes-turn-one')
        assert claim['attempt']['turn_exclusive'] is True, claim
        assert claim['attempt']['native_turn_id'] == 'hermes-turn-one', claim

        # 2. Owner join revokes exclusively before any cancellation acts.
        hermes_cancel.revoke_exclusivity(config, session, claim['attempt']['attempt_id'])
        control = hermes_cancel.inbox_control(config, session)
        assert control.get('turn_exclusive') is not True and control.get('cancel') is None

        # 3. Non-exclusive attempts are never cancelled (gate).
        refusal = subprocess.run([binary, 'work-cancel'], input=json.dumps(dict(
            request_id=str(uuid.uuid4()), repo=repo, delivery_id=delivery,
            reason='Revoked admission cannot be cancelled as exclusive')), env=env,
            text=True, capture_output=True, timeout=20)
        assert json.loads(refusal.stdout)['status'] == 'UNSUPPORTED_CONTROL', refusal.stdout

        # Release the revoked attempt through its ordinary turn end so the
        # inbox can accept the next exclusive admission.
        closed = raw('session-inbox-reconcile', dict(request_id=str(uuid.uuid4()), session=session,
            attempt_id=claim['attempt']['attempt_id'], reason='turn_ended'))
        assert closed['status'] == 'OK', closed
        dstate = agent('event-inspect', dict(event_id=event['event_id']))['deliveries'][0]
        assert dstate['state'] == 'failed' and dstate['code'] == 'processing_failed', dstate

        # 4. Fresh exclusive admission, then full cancellation lifecycle.
        event2 = agent('event-publish', dict(request_id=str(uuid.uuid4()), kind='request',
            ref=dict(record_id=note['record_id'], version=1),
            destination=dict(type='agent', name=registered['inbox'])))
        delivery2 = agent('event-inspect', dict(event_id=event2['event_id']))['deliveries'][0]['delivery_id']
        claim2 = hermes_cancel.attest_admission(config, session, delivery2, 'hermes-turn-two')
        agent('session-tool-capture', dict(request_id=str(uuid.uuid4()), session=session,
            attempt_id=claim2['attempt']['attempt_id'],
            items=[dict(item_id='tool-one', process_id='4242', command='/bin/sleep 30',
                        native_turn_id='hermes-turn-two')]))
        operator('work-cancel', dict(request_id=str(uuid.uuid4()), repo=repo, delivery_id=delivery2,
                                     reason='Real-API hermes cancellation lifecycle'))
        attempt = hermes_cancel.inbox_control(config, session)
        assert attempt['cancel'] and attempt['turn_exclusive'], attempt
        result = hermes_cancel.execute_cancellation(config, session, attempt,
            hermes_cancel.BridgeClient(bridge.path, bridge.process()), 'hermes-native')
        assert result['confirmed'], result
        abort = next(r for r in bridge.requests if r['method'] == 'session/abort')
        assert abort['params']['expected_turn_id'] == 'hermes-turn-two', abort
        final = agent('event-inspect', dict(event_id=event2['event_id']))['deliveries'][0]
        assert final['state'] == 'failed' and final['code'] == 'operator_cancelled', final
        assert hermes_cancel.inbox_control(config, session) is None, 'hold released'
        print('Real-API hermes cancellation: exclusive admission, owner-join revocation, '
              'fenced abort cleanup and hold release passed')
    finally:
        api.terminate()
        try:
            api.wait(timeout=10)
        except subprocess.TimeoutExpired:
            api.kill()
            api.wait()


if __name__ == '__main__':
    check(sys.argv[1], sys.argv[2])
