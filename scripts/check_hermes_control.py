"""Hermes controller against a disposable authenticated Cairn API.

Native ownership/OS observations are explicit test doubles. This checks store
ordering and crash recovery, not native containment or deployed acceptance.
Run only through the disposable PostgreSQL harness.
"""
import copy
import hashlib
import importlib.util
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
import coordination
import hermes_control


def check(binary, home, controller_path=None):
    if not os.environ.get('CAIRN_TEST_DATABASE_URL') or os.environ.get('CAIRN_DATABASE_URL') != os.environ['CAIRN_TEST_DATABASE_URL']:
        raise RuntimeError('explicit matching disposable test database URLs required')
    controller = hermes_control
    if controller_path:
        spec = importlib.util.spec_from_file_location('reviewed_controller', controller_path)
        controller = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(controller)
    home = Path(home)
    home.mkdir(mode=0o700)
    repo = 'hermes-control-check:' + str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    (home / 'agent.token').write_text(token)
    (home / 'agent.token').chmod(0o600)
    (home / 'identities.json').write_text(json.dumps([dict(
        principal='hermes-control-check/agent', role='agent', repo=repo, destination='hosted',
        token_sha256=hashlib.sha256(token.encode()).hexdigest())]))
    (home / 'identities.json').chmod(0o600)
    env = dict(os.environ, CAIRN_HOME=str(home))
    config = dict(cairn=binary, socket=str(home / 'api.sock'), token_file=str(home / 'agent.token'))
    with (home / 'api.log').open('w') as log:
        api = subprocess.Popen([binary, 'serve'], env=env, stdout=subprocess.DEVNULL, stderr=log)
        try:
            for _ in range(100):
                if (home / 'api.sock').exists():
                    break
                if api.poll() is not None:
                    raise RuntimeError('disposable API exited before readiness')
                time.sleep(.1)
            if not (home / 'api.sock').exists():
                raise RuntimeError('disposable API did not become ready')
            call = lambda op, body: coordination.call(config, op, body)
            note = call('create', dict(request_id=str(uuid.uuid4()), draft=dict(kind='note',
                body='Disposable Hermes controller ordering check', scope=dict(repo=repo, task_id='*', run_id='*'),
                claim_type='self', sensitivity='shareable')))

            for scenario in ('first_revocation_already_clear', 'scan_invalidated_before_reconcile', 'lost_stop_response'):
                registered = call('agent-register', dict(request_id=str(uuid.uuid4()), binding='hermes-check',
                    native_session_id=scenario, metadata=dict(harness='hermes', project='controller-check',
                    workspace=str(home), state='busy', delivery_mode='existing-session')))
                session = {k: registered[k] for k in ('agent_id', 'execution_id')}
                event = call('event-publish', dict(request_id=str(uuid.uuid4()), kind='request',
                    ref=dict(record_id=note['record_id'], version=1), destination=dict(type='agent', name=registered['inbox'])))
                delivery = call('event-inspect', dict(event_id=event['event_id']))['deliveries'][0]['delivery_id']
                binding = dict(session_id=scenario, request_id=str(uuid.uuid4()), delivery_id=delivery,
                               turn_id=scenario + ':turn', ownership_token=str(uuid.uuid4()))
                attempt = call('session-inbox-claim', dict(request_id=str(uuid.uuid4()), session=session,
                    delivery_id=delivery, native_turn_id=binding['turn_id'], turn_exclusive=True))['attempt']
                cancelled = subprocess.run([binary, 'work-cancel'], input=json.dumps(dict(
                    request_id=str(uuid.uuid4()), repo=repo, delivery_id=delivery, reason='Disposable controller check')),
                    env=env, text=True, capture_output=True, timeout=20, check=True)
                assert json.loads(cancelled.stdout)['data']['native']['state'] == 'cancel_pending'
                revoked = scenario == 'first_revocation_already_clear'
                result = dict(ownership=dict(binding, exclusive=not revoked, revoked=revoked, cancelled=not revoked,
                    turn_ended=True, tool_admission_closed=True, active_tool_calls=0,
                    executor_capture_gap=False), tools=[], inventory_complete=True,
                    terminal_scan='clear')
                class NativeEvidence:
                    reads = 0
                    def rpc(self, method, params):
                        assert method == 'session/request_status', 'quiescent fixture must never send native mutations'
                        self.reads += 1
                        return copy.deepcopy(result)
                native = NativeEvidence()
                calls = []
                injected = False
                ledger_path = home / (scenario + '.ledger.json')
                ledger = dict(attempt_id=attempt['attempt_id'])
                def store(op, body):
                    nonlocal injected
                    calls.append((op, copy.deepcopy(body)))
                    if scenario == 'scan_invalidated_before_reconcile' and op == 'session-inbox-reconcile' and not injected:
                        injected = True
                        call('session-tool-stop', dict(request_id=str(uuid.uuid4()), session=session,
                             attempt_id=attempt['attempt_id'], terminal_scan='unknown_remaining', tools=[]))
                    observed = call(op, body)
                    if scenario == 'lost_stop_response' and op == 'session-tool-stop' and not injected:
                        injected = True
                        raise OSError('fixture lost an already-committed stop-report response')
                    return observed
                def save():
                    coordination.write_state(ledger_path, ledger)
                errors = []
                for _ in range(4):
                    try:
                        controller.Controller(ledger, save, store, native).poll(session, binding)
                    except (coordination.CoordinationError, OSError) as exc:
                        errors.append(getattr(exc, 'code', type(exc).__name__))
                    if ledger_path.exists():
                        ledger = json.loads(ledger_path.read_text())
                    current = call('session-inbox-control', session).get('attempt')
                    if current is None:
                        break
                assert current is None, (scenario, 'hold stuck despite fresh positive evidence', errors, native.reads)
                finished = call('session-inbox-reconcile', dict(request_id=str(uuid.uuid4()), session=session,
                    attempt_id=attempt['attempt_id'], reason='exclusivity_revoked' if revoked else 'cancel_confirmed'))
                assert finished['reason'] == ('exclusivity_revoked' if revoked else 'cancel_confirmed'), finished
                if revoked:
                    assert finished['cancel'].get('confirmed_at') is None
                    assert native.reads >= 2, 'revocation invalidates the first clear scan; fresh observation required'
                if scenario == 'lost_stop_response':
                    reports = [body for op, body in calls if op == 'session-tool-stop']
                    assert len(reports) >= 2 and reports[0] == reports[1], 'recovery changed the committed mutation UUID/payload'
                if scenario == 'scan_invalidated_before_reconcile':
                    assert injected and native.reads >= 2, 'definitive refusal must permit a fresh status observation'
                print(json.dumps(dict(scenario=scenario, passed=True, native_status_reads=native.reads,
                                      injected_or_observed_errors=errors)), flush=True)
        finally:
            api.terminate()
            try:
                api.wait(timeout=10)
            except subprocess.TimeoutExpired:
                api.kill()
                api.wait()


if __name__ == '__main__':
    check(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
