"""Hermes native exclusive admission, revocation and owned-tool cleanup,
integrated with the dormant migration 049 cancellation core.

Exclusive ownership is attested ONLY when the Hermes bridge admits a queued
Cairn wake from idle, atomically inside the admission callback under the
admission lock. Owner input joining the conversation revokes exclusivity
before any cancellation can act. Cancellation drives the bridge's existing
request/turn-fenced abort and verified process-registry cleanup, translating
its outcomes into 049 evidence; failed cleanup retains the durable hold.
"""
import json
from pathlib import Path
import socket
import struct
import subprocess
import time
import uuid


class CancelError(Exception):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code


def call(config, operation, request, timeout=8, session=None):
    argv = [config['cairn'], 'agent', '--socket', config['socket'],
            '--token-file', config['token_file'], operation]
    if session:
        argv += ['--agent-id', session['agent_id'], '--execution-id', session['execution_id']]
    try:
        result = subprocess.run(argv, input=json.dumps(request).encode(),
                                capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CancelError('API_UNAVAILABLE', f'cairn API call failed: {type(exc).__name__}') from exc
    try:
        envelope = json.loads(result.stdout or b'{}')
    except ValueError as exc:
        raise CancelError('API_UNAVAILABLE', 'cairn API returned an invalid envelope') from exc
    if result.returncode != 0 or not envelope.get('ok'):
        status = envelope.get('status') or 'API_UNAVAILABLE'
        raise CancelError(status, str(envelope.get('message') or status))
    return envelope.get('data') if envelope.get('data') is not None else {}


def attest_admission(config, session, delivery_id, native_turn_id, request_id=None):
    """Claim the admitted wake's delivery with exclusive single-request turn
    ownership. Called only from the bridge's admission callback, which Hermes
    runs under its admission lock, so admission and attestation are atomic
    with respect to owner input."""
    if not delivery_id or not native_turn_id:
        return None
    return call(config, 'session-inbox-claim', dict(
        request_id=request_id or str(uuid.uuid4()), session=session,
        delivery_id=delivery_id, native_turn_id=native_turn_id,
        turn_exclusive=True))


def inbox_control(config, session):
    return call(config, 'session-inbox-control', dict(
        agent_id=session['agent_id'], execution_id=session['execution_id'])).get('attempt')


def revoke_exclusivity(config, session, attempt_id, request_id=None):
    """Owner input joined the conversation: revoke exclusive ownership before
    any cancellation path can act. One-way; a revoked attempt can never be
    cancelled as exclusively owned."""
    return call(config, 'session-tool-stop', dict(
        request_id=request_id or str(uuid.uuid4()), session=session,
        attempt_id=attempt_id, owner_join=True))


def report_stop(config, session, attempt_id, turn_stop=None, terminal_scan=None, tools=()):
    request = dict(request_id=str(uuid.uuid4()), session=session,
                   attempt_id=attempt_id, tools=[
                       dict(item_id=tool['item_id'], stop_state=tool['stop_state'])
                       for tool in tools])
    if turn_stop:
        request['turn_stop'] = turn_stop
    if terminal_scan:
        request['terminal_scan'] = terminal_scan
    return call(config, 'session-tool-stop', request)


def reconcile(config, session, attempt_id, reason, request_id=None):
    return call(config, 'session-inbox-reconcile', dict(
        request_id=request_id or str(uuid.uuid4()), session=session,
        attempt_id=attempt_id, reason=reason))


class BridgeClient:
    """JSON-RPC client over the Hermes bridge's verified Unix socket."""

    def __init__(self, endpoint, process, timeout=6):
        self.endpoint = endpoint
        self.process = process
        self.timeout = timeout

    def rpc(self, method, params):
        transport = socket.socket(socket.AF_UNIX)
        transport.settimeout(self.timeout)
        try:
            transport.connect(self.endpoint)
            peer, _, _ = struct.unpack('3i', transport.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if peer != self.process['pid']:
                raise CancelError('WRONG_PROCESS', 'bridge socket belongs to a different process')
            start = int(Path(f'/proc/{peer}/stat').read_text().rsplit(')', 1)[1].split()[19])
            boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            if start != self.process['start'] or boot != self.process['boot']:
                raise CancelError('WRONG_PROCESS', 'bridge process identity has changed')
            transport.sendall((json.dumps(dict(id=1, method=method, params=params)) + '\n').encode())
            line = transport.makefile('r', encoding='utf-8').readline()
            if not line:
                raise CancelError('BRIDGE_UNAVAILABLE', 'bridge closed without a response')
            message = json.loads(line)
            if 'error' in message:
                raise CancelError('ABORT_REFUSED', str(message['error'].get('message') or message['error']))
            return message.get('result') or {}
        except (OSError, ValueError) as exc:
            raise CancelError('BRIDGE_UNAVAILABLE', f'bridge rpc failed: {type(exc).__name__}') from exc
        finally:
            try:
                transport.close()
            except OSError:
                pass

    def abort(self, session_id, request_id=None, turn_id=None):
        return self.rpc('session/abort', dict(session_id=session_id,
                                              expected_request_id=request_id,
                                              expected_turn_id=turn_id))

    def tools_status(self, session_id):
        return self.rpc('session/tools_status', dict(session_id=session_id))


TURN_STOP_MAP = {'interrupted': 'interrupted', 'interruption_requested': 'ambiguous',
                 'dequeued_before_admission': 'ended', 'cancelled_before_running': 'ended'}
TOOL_STOP_MAP = {'terminated': 'terminated', 'stop_issued': 'stop_issued',
                 'unverified_process_identity': 'stop_issued'}


def execute_cancellation(config, session, attempt, bridge, native_session_id,
                         verify_seconds=6.0):
    """Drive one bounded cancellation for an exclusively owned attempt:
    fenced bridge abort, owned-tool outcome translation, verified terminal
    scan, then the 049 evidence contract. Failed cleanup keeps the hold."""
    attempt_id = attempt['attempt_id']
    cancel = attempt.get('cancel') or {}
    if not attempt.get('turn_exclusive'):
        # A shared or unproven turn can never be cancelled as exclusively
        # owned; surface the refusal instead of acting.
        return dict(confirmed=False, reason='turn_not_exclusive', attempt_id=attempt_id)
    if cancel.get('confirmed_at'):
        return dict(confirmed=False, reason='already_confirmed', attempt_id=attempt_id)
    result = bridge.abort(native_session_id,
                          request_id=attempt.get('native_request_id'),
                          turn_id=attempt.get('native_turn_id') or None)
    turn_stop = TURN_STOP_MAP.get(result.get('turn_stop'), 'ambiguous')
    tools = []
    for tool in result.get('tools') or []:
        state = TOOL_STOP_MAP.get(tool.get('stop_state'), 'stop_issued')
        tools.append(dict(item_id=str(tool.get('item_id') or ''), stop_state=state))
    report_stop(config, session, attempt_id, turn_stop=turn_stop, tools=tools)
    # Verified terminal scan: only a bridge tools_status that shows no running
    # owned tools proves cleanup; otherwise the hold is retained.
    deadline = time.monotonic() + verify_seconds
    while time.monotonic() < deadline:
        try:
            status = bridge.tools_status(native_session_id)
        except CancelError:
            return dict(confirmed=False, reason='verification_unavailable', attempt_id=attempt_id)
        running = [tool for tool in (status.get('tools') or [])
                   if tool.get('status') == 'running'
                   and (not attempt.get('native_turn_id') or tool.get('turn_id') == attempt.get('native_turn_id'))]
        if not running:
            report_stop(config, session, attempt_id, terminal_scan='clear')
            break
        time.sleep(0.25)
    else:
        return dict(confirmed=False, reason='tools_remaining', attempt_id=attempt_id)
    reconcile(config, session, attempt_id, 'cancel_confirmed')
    return dict(confirmed=True, attempt_id=attempt_id)


def handle_watcher_cycle(config, state, path, bridge_factory):
    """One watcher cycle: attest nothing (admission does that), revoke on
    owner joins, and drive any pending cancellation for this inbox."""
    agent = state.get('agent')
    if not agent or state.get('ending'):
        return None
    session = dict(agent_id=agent['agent_id'], execution_id=agent['execution_id'])
    attempt = inbox_control(config, session)
    if not attempt:
        return None
    native_session_id = agent.get('native_session_id') or ''
    if attempt.get('cancel') and not attempt['cancel'].get('confirmed_at'):
        if not attempt.get('turn_exclusive'):
            return dict(confirmed=False, reason='turn_not_exclusive', attempt_id=attempt['attempt_id'])
        bridge = bridge_factory()
        return execute_cancellation(config, session, attempt, bridge, native_session_id)
    return None
