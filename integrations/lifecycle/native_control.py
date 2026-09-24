"""Per-request native cancellation support for interactive sessions.

Durable tool-ownership capture from subscribed item notifications, a bounded
stop sequence for the exact pinned native turn, and confirmed-cleanup release.
The collection API owns intent, fencing and holds; this module only observes
the binding's own native process and reports. It never signals the shared
gateway or the interactive process itself.
"""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import threading
import time
import uuid


class ControlError(Exception):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code


def call(config, operation, request, timeout=8):
    """One CLI JSON operation against the authenticated Unix API."""
    argv = [config['cairn'], 'agent', '--socket', config['socket'], '--token-file', config['token_file'], operation]
    try:
        result = subprocess.run(argv, input=json.dumps(request).encode(), capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ControlError('API_UNAVAILABLE', f'cairn API call failed: {type(exc).__name__}') from exc
    try:
        envelope = json.loads(result.stdout or b'{}')
    except ValueError as exc:
        raise ControlError('API_UNAVAILABLE', 'cairn API returned an invalid envelope') from exc
    if result.returncode != 0 or not envelope.get('ok'):
        status = envelope.get('status') or 'API_UNAVAILABLE'
        raise ControlError(status, str(envelope.get('message') or status))
    return envelope.get('data') if envelope.get('data') is not None else {}


def stable_uuid(kind, *parts):
    # Identities for idempotent single-shot mutations only; an observation
    # report represents one event and takes a fresh UUID each time.
    """Deterministic UUID so a retried mutation repeats its exact payload."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'cairn-native-control:' + kind + ':' + '\0'.join(parts)))


def write_state(path, state):
    """Atomic replace with owner-only permissions, like the coordinator."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as file:
        pending = Path(file.name)
        try:
            json.dump(state, file, ensure_ascii=False)
            file.write('\n')
            file.flush()
            os.fsync(file.fileno())
            file.close()
            pending.replace(path)
        finally:
            pending.unlink(missing_ok=True)


def inbox_control(config, session):
    """Read the unfinished attempt owning this inbox, or None."""
    return call(config, 'session-inbox-control', dict(agent_id=session['agent_id'],
                                                      execution_id=session['execution_id'])).get('attempt')


def capture_tools(config, session, attempt_id, item):
    """Capture one observed tool under a stable per-item request identity."""
    return call(config, 'session-tool-capture', dict(
        request_id=stable_uuid('capture', attempt_id, item['item_id'], item['process_id']),
        session=session, attempt_id=attempt_id, items=[item]))


def report_stop(config, session, attempt_id, turn_stop=None, tools=(), capture_state=None, terminal_scan=None):
    request = dict(request_id=str(uuid.uuid4()), session=session,
                   attempt_id=attempt_id, tools=list(tools))
    if turn_stop:
        request['turn_stop'] = turn_stop
    if capture_state:
        request['capture_state'] = capture_state
    if terminal_scan:
        request['terminal_scan'] = terminal_scan
    return call(config, 'session-tool-stop', request)


def reconcile(config, session, attempt_id, reason, request_id):
    return call(config, 'session-inbox-reconcile', dict(
        request_id=request_id, session=session, attempt_id=attempt_id, reason=reason))


class NativeControl:
    """JSON-RPC client over the binding's own app-server Unix socket."""

    def __init__(self, endpoint, process, timeout=4, peer=None):
        import websocket
        self.endpoint = endpoint
        self.process = process
        # peer is the endpoint owner's exact identity {pid,start,boot} as
        # returned by a verified discovery seam; the default stays the
        # observed native process itself.
        self.peer = peer or process
        self.timeout = timeout
        self.sequence = 0
        self.connection = None
        self.notifications = []
        try:
            self.transport = socket.socket(socket.AF_UNIX)
            self.transport.settimeout(timeout)
            self.transport.connect(endpoint)
            peer, _, _ = struct.unpack('3i', self.transport.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if peer != self.peer['pid']:
                raise ControlError('WRONG_PROCESS', 'native socket belongs to a different process')
            start = int(Path(f'/proc/{peer}/stat').read_text().rsplit(')', 1)[1].split()[19])
            boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            if start != self.peer['start'] or boot != self.peer['boot']:
                raise ControlError('WRONG_PROCESS', 'native process identity has changed')
            self.connection = websocket.create_connection('ws://localhost', socket=self.transport, timeout=timeout)
            self._timeout = websocket.WebSocketTimeoutException
            self._transport_errors = (websocket.WebSocketException,)
            self.rpc('initialize', dict(clientInfo=dict(name='cairn-native-control', version='1'),
                                        capabilities=dict(experimentalApi=True)))
            self.notify('initialized')
        except ControlError:
            self.close()
            raise
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.close()
            raise ControlError('NATIVE_UNAVAILABLE', f'native control socket failed: {type(exc).__name__}') from exc

    def close(self):
        if self.connection is not None:
            try:
                self.connection.close(timeout=0)
            except Exception:
                pass
            self.connection = None
        if getattr(self, 'transport', None) is not None:
            try:
                self.transport.close()
            except OSError:
                pass
            self.transport = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _remaining(self, deadline):
        left = deadline - time.monotonic()
        if left <= 0:
            raise ControlError('NATIVE_TIMEOUT', 'native control timed out')
        self.connection.settimeout(left)

    def notify(self, method):
        self.connection.send(json.dumps(dict(method=method)))

    def rpc(self, method, params, deadline=None):
        self.sequence += 1
        marker = self.sequence
        if deadline is not None:
            self._remaining(deadline)
        try:
            self.connection.send(json.dumps(dict(id=marker, method=method, params=params)))
            while True:
                if deadline is not None:
                    self._remaining(deadline)
                message = json.loads(self.connection.recv())
                if not isinstance(message, dict):
                    continue
                if message.get('id') != marker:
                    if 'method' in message:
                        self.notifications.append(message)  # Keep items seen mid-call.
                    continue
                if 'error' in message:
                    return dict(error=message['error'])
                return dict(result=message.get('result'))
        except self._transport_errors + (OSError, ValueError) as exc:
            raise ControlError('NATIVE_UNAVAILABLE', f'native control rpc failed: {type(exc).__name__}') from exc

    def receive(self, deadline):
        """One queued or incoming notification, or None when only the bounded
        wait lapsed. A transport loss raises so the listener reports a durable
        coverage gap instead of spinning as attached."""
        if self.notifications:
            return self.notifications.pop(0)
        try:
            self._remaining(deadline)
            message = json.loads(self.connection.recv())
        except self._timeout:
            return None
        except self._transport_errors + (OSError, ValueError, TypeError, KeyError) as exc:
            # ConnectionClosed and siblings inherit WebSocketException, not
            # OSError; normalize every transport loss to a durable gap.
            raise ControlError('NATIVE_UNAVAILABLE', f'native notification stream lost: {type(exc).__name__}') from exc
        return message if isinstance(message, dict) and 'method' in message else None


def subscribe_loaded(control, thread_id):
    """Join a currently loaded thread without changing its configuration.

    Codex 0.154 thread/read does not subscribe to item notifications. Resume
    rejoins a loaded thread; never use it as discovery for an unloaded one.
    """
    cursor = None
    while True:
        reply = control.rpc('thread/loaded/list', dict(limit=100, **({'cursor': cursor} if cursor else {})))
        page = reply.get('result')
        if not isinstance(page, dict) or not isinstance(page.get('data'), list):
            raise ControlError('SUBSCRIBE_REFUSED', 'loaded thread list unavailable')
        if thread_id in page['data']:
            break
        cursor = page.get('nextCursor')
        if not cursor:
            raise ControlError('SUBSCRIBE_REFUSED', 'native thread is not loaded')
    reply = control.rpc('thread/resume', dict(threadId=thread_id, excludeTurns=True))
    result = reply.get('result')
    if not isinstance(result, dict) or result.get('thread', {}).get('id') != thread_id:
        raise ControlError('SUBSCRIBE_REFUSED', 'native thread subscription refused')


def subscribe_capture(control, thread_id, turn_id, deadline):
    """Subscribe to the thread and return item/started tool handles pinned to
    exactly this turn. Historical item reads omit running tools, so ownership
    must come from the notification stream itself."""
    subscribe_loaded(control, thread_id)
    tools = []
    ended = False
    while not ended and time.monotonic() < deadline:
        note = control.receive(deadline)
        if note is None:
            break
        method, params = note.get('method') or '', note.get('params') or {}
        if method == 'item/started' and params.get('turnId') == turn_id:
            item = params.get('item') or {}
            if item.get('type') == 'commandExecution' and item.get('id') and item.get('processId'):
                tools.append(dict(item_id=item['id'], process_id=str(item['processId']),
                                  command=str(item.get('command') or '')[:1024],
                                  native_turn_id=turn_id))
        elif method == 'turn/completed' and params.get('turn', {}).get('id') == turn_id:
            ended = True
    control.rpc('thread/unsubscribe', dict(threadId=thread_id))
    return tools


def interrupt_turn(control, thread_id, turn_id, deadline):
    """Interrupt exactly the pinned turn. An inactive or replaced active turn
    is reported as ended: a stale cancellation must never hit a later turn."""
    reply = control.rpc('turn/interrupt', dict(threadId=thread_id, turnId=turn_id), deadline)
    if 'result' in reply:
        return 'interrupted'
    error = reply.get('error') or {}
    message = str(error.get('message') or '')
    if error.get('code') == -32600 and 'active turn id' in message:
        return 'ended'
    # An unexplained refusal proves nothing either way; the hold stays.
    return 'ambiguous\0' + message[:200]


def turn_ended(control, thread_id, turn_id, deadline):
    """An interrupt acknowledgement is not a generation-stop observation."""
    while time.monotonic() < deadline:
        cursor = None
        found = False
        while True:
            reply = control.rpc('thread/turns/list', dict(threadId=thread_id, limit=100,
                                **({'cursor': cursor} if cursor else {})), deadline)
            page = reply.get('result')
            if not isinstance(page, dict) or not isinstance(page.get('data'), list):
                raise ControlError('TURN_UNCONFIRMED', 'native turn status unavailable')
            for turn in page['data']:
                if not isinstance(turn, dict):
                    raise ControlError('TURN_UNCONFIRMED', 'native turn page malformed')
                if turn.get('id') == turn_id:
                    found = True
                    if turn.get('status') in ('completed', 'interrupted', 'failed'):
                        return True
                    break
            cursor = page.get('nextCursor')
            if found or not cursor:
                break
        if not found:
            return False
        time.sleep(min(.05, max(0, deadline - time.monotonic())))
    return False


def terminate_tools(control, thread_id, tools, deadline):
    """Terminate captured tools only. Unrelated terminals are never addressed.
    A refusal keeps the tool stop-issued; the terminal list verification below
    decides whether it actually stopped."""
    outcomes = []
    for tool in tools:
        if tool['stop_state'] in ('terminated', 'unavailable'):
            outcomes.append((tool, tool['stop_state']))
            continue
        reply = control.rpc('thread/backgroundTerminals/terminate',
                            dict(threadId=thread_id, processId=tool['process_id']), deadline)
        if reply.get('result', {}).get('terminated'):
            outcomes.append((tool, 'terminated'))
        elif 'error' in reply:
            outcomes.append((tool, 'stop_issued'))
        else:
            outcomes.append((tool, 'stop_issued'))
    return outcomes


def scan_terminals(control, thread_id, process_ids, deadline):
    """One schema-valid full scan. Returns (captured_present, unknown_ids).
    A malformed reply raises: it cannot prove cleanup either way."""
    cursor = None
    captured = False
    unknown = set()
    while True:
        params = dict(threadId=thread_id, limit=100)
        if cursor:
            params['cursor'] = cursor
        reply = control.rpc('thread/backgroundTerminals/list', params, deadline)
        if 'error' in reply or not isinstance(reply.get('result'), dict):
            raise ControlError('LIST_REFUSED', 'background terminal list did not return a valid page')
        page = reply['result'].get('data')
        if not isinstance(page, list):
            raise ControlError('LIST_REFUSED', 'background terminal list did not return a valid list')
        for item in page:
            if not isinstance(item, dict):
                raise ControlError('LIST_REFUSED', 'background terminal entry is malformed')
            handle = str(item.get('processId'))
            if handle in process_ids:
                captured = True
            elif handle:
                unknown.add(handle)
        cursor = reply['result'].get('nextCursor')
        if not cursor:
            return captured, unknown


def stop_sequence(config, state, path, attempt, endpoint, budget_seconds=45.0, peer=None):
    """Bounded stop for one cancelled attempt: persist intent, interrupt the
    pinned turn, terminate captured tools, scan terminals, then confirm only
    on positive evidence. The durable hold releases solely through the
    confirmed reconciliation; ambiguity keeps it."""
    session = attempt['session']
    if not attempt.get('turn_exclusive'):
        # A joined or unproven prompt is not single-request ownership; an
        # interrupt could stop unrelated owner work.
        return dict(confirmed=False, reason='turn_not_exclusive', attempt_id=attempt['attempt_id'])
    thread_id = state['agent']['native_session_id']
    turn_id = attempt.get('native_turn_id') or ''
    if not turn_id:
        return dict(confirmed=False, reason='turn_unpinned', attempt_id=attempt['attempt_id'])
    stage = state.get('native_cancel') or {}
    if stage.get('attempt_id') != attempt['attempt_id']:
        stage = dict(attempt_id=attempt['attempt_id'], delivery_id=attempt['delivery']['delivery_id'], stage='intent')
        state['native_cancel'] = stage
        write_state(path, state)
    deadline = time.monotonic() + budget_seconds
    with NativeControl(endpoint, state['process'], peer=peer) as control:
        if attempt.get('turn_stop_state') not in ('interrupted', 'ended'):
            stage['stage'] = 'interrupt'
            write_state(path, state)
            turn_stop = interrupt_turn(control, thread_id, turn_id, deadline)
            detail = ''
            if turn_stop.startswith('ambiguous\0'):
                turn_stop, detail = turn_stop.split('\0', 1)
                stage['interrupt_detail'] = detail
                write_state(path, state)
            if turn_stop in ('interrupted', 'ended') and not turn_ended(control, thread_id, turn_id, deadline):
                return dict(confirmed=False, reason='generation_running', attempt_id=attempt['attempt_id'])
            report_stop(config, session, attempt['attempt_id'], turn_stop=turn_stop)
            attempt['turn_stop_state'] = turn_stop
        latest = inbox_control(config, session)
        if latest is None or latest['attempt_id'] != attempt['attempt_id']:
            return dict(confirmed=False, reason='attempt_changed', attempt_id=attempt['attempt_id'])
        tools = latest.get('tools') or []
        stage['stage'] = 'terminate'
        write_state(path, state)
        outcomes = terminate_tools(control, thread_id, tools, deadline)
        if outcomes:
            report_stop(config, session, attempt['attempt_id'],
                        tools=[dict(item_id=tool['item_id'], stop_state=stop) for tool, stop in outcomes])
            names = {tool['item_id']: stop for tool, stop in outcomes}
            tools = [dict(tool, stop_state=names.get(tool['item_id'], tool['stop_state'])) for tool in tools]
        stage['stage'] = 'verify'
        write_state(path, state)
        captured_ids = {tool['process_id'] for tool in tools}
        try:
            while time.monotonic() < deadline:
                captured_present, unknown = scan_terminals(control, thread_id, captured_ids, deadline)
                if not captured_present:
                    break
                time.sleep(0.5)
            else:
                return dict(confirmed=False, reason='tools_remaining', attempt_id=attempt['attempt_id'])
        except ControlError:
            return dict(confirmed=False, reason='verification_unavailable', attempt_id=attempt['attempt_id'])
        if unknown:
            # Terminals this request never captured are still running; missing
            # capture coverage cannot be declared complete.
            report_stop(config, session, attempt['attempt_id'], terminal_scan='unknown_remaining')
            return dict(confirmed=False, reason='capture_coverage_missing', attempt_id=attempt['attempt_id'])
        report_stop(config, session, attempt['attempt_id'], terminal_scan='clear')
        absent = [dict(item_id=tool['item_id'], stop_state='unavailable')
                  for tool in tools if tool['stop_state'] not in ('terminated', 'unavailable')]
        if absent:
            # Handles that could not be terminated through the native surface
            # are verified absent by the clear scan instead.
            report_stop(config, session, attempt['attempt_id'], tools=absent)
        stage['stage'] = 'confirm'
        write_state(path, state)
    request_id = stage.setdefault('reconcile_request_id', str(uuid.uuid4()))
    write_state(path, state)
    try:
        reconcile(config, session, attempt['attempt_id'], 'cancel_confirmed', request_id)
    except ControlError as exc:
        if exc.code == 'CLEANUP_UNCONFIRMED':
            return dict(confirmed=False, reason='cleanup_unconfirmed', attempt_id=attempt['attempt_id'])
        raise
    state.pop('native_cancel', None)
    write_state(path, state)
    return dict(confirmed=True, attempt_id=attempt['attempt_id'])


def pending_cancellation(config, state, path, endpoint, peer=None):
    """Watcher entry: run the stop sequence when this inbox has a pending
    operator cancellation. Returns a status dict or None."""
    agent = state.get('agent')
    if not agent or state.get('ending'):
        return None
    attempt = inbox_control(config, dict(agent_id=agent['agent_id'], execution_id=agent['execution_id']))
    if not attempt:
        state.pop('native_cancel', None)
        return None
    cancel = attempt.get('cancel')
    if not cancel or cancel.get('confirmed_at'):
        return None
    if not endpoint:
        return dict(confirmed=False, reason='no_native_control_endpoint', attempt_id=attempt['attempt_id'])
    if not attempt.get('native_turn_id'):
        return dict(confirmed=False, reason='turn_unpinned', attempt_id=attempt['attempt_id'])
    if not attempt.get('turn_exclusive'):
        return dict(confirmed=False, reason='turn_not_exclusive', attempt_id=attempt['attempt_id'])
    return stop_sequence(config, state, path, attempt, endpoint, peer=peer)


# One subscription per live session. It is ready before queue admission, so
# item notifications cannot outrun the first periodic watcher tick.
_capture_threads = {}


def ensure_capture(config, state, path, endpoint, harness='codex', peer=None):
    agent = state.get('agent')
    if not agent or not endpoint or harness != 'codex':
        return False
    key = (config['binding'], agent['agent_id'], agent['execution_id'])
    active = _capture_threads.get(key)
    if active and active.is_alive():
        return active.ready.wait(5) and active.error is None
    listener = CaptureListener(config, state, path, endpoint, peer=peer)
    listener.daemon = True
    _capture_threads[key] = listener
    listener.start()
    return listener.ready.wait(5) and listener.error is None


class CaptureListener(threading.Thread):
    """A session subscription maps each tool event to its exact durable claim.

    The native UserPromptSubmit hook commits the claim before tool execution.
    Owner turns without that binding are ignored. This thread never writes
    the coordinator state file; its only writes are serialized store reports.
    """

    def __init__(self, config, state, path, endpoint, attempt=None, peer=None):
        super().__init__(name='cairn-native-capture')
        self.config = config
        self.state = dict(state)
        self.endpoint = endpoint
        self.peer = peer
        self.scoped_attempt = attempt
        self.attempt_id = None
        self.turn_id = None
        self.session = {key: state['agent'][key] for key in ('agent_id', 'execution_id')}
        self.native_thread_id = state['agent']['native_session_id']
        self.stopped = threading.Event()
        self.ready = threading.Event()
        self.error = None

    def stop(self):
        self.stopped.set()

    def current_attempt(self):
        return self.scoped_attempt or inbox_control(self.config, self.session)

    def attach(self, attempt):
        if attempt['attempt_id'] != self.attempt_id:
            self.attempt_id = attempt['attempt_id']
            self.turn_id = attempt['native_turn_id']
            report_stop(self.config, self.session, self.attempt_id, capture_state='attached')

    def run(self):
        try:
            with NativeControl(self.endpoint, self.state['process'], peer=self.peer) as control:
                subscribe_loaded(control, self.native_thread_id)
                existing = self.current_attempt()
                if existing and not existing.get('finished_at'):
                    self.attach(existing)
                    if not self.scoped_attempt:
                        # A restarted observer cannot recover earlier events.
                        report_stop(self.config, self.session, self.attempt_id, capture_state='lost')
                        report_stop(self.config, self.session, self.attempt_id, capture_state='attached')
                self.ready.set()
                while not self.stopped.is_set():
                    note = control.receive(time.monotonic() + .5)
                    if note is None:
                        continue
                    method, params = note.get('method') or '', note.get('params') or {}
                    if params.get('threadId') != self.native_thread_id or method not in ('item/started', 'turn/completed'):
                        continue
                    attempt = self.current_attempt()
                    turn_id = (params.get('turn', {}).get('id') if method == 'turn/completed'
                               else params.get('turnId'))
                    if (not attempt or attempt.get('finished_at') or
                            turn_id != attempt.get('native_turn_id')):
                        continue
                    self.attach(attempt)
                    if method == 'item/started':
                        item = params.get('item') or {}
                        if item.get('type') == 'commandExecution' and item.get('id') and item.get('processId'):
                            capture_tools(self.config, self.session, self.attempt_id,
                                          dict(item_id=item['id'], process_id=str(item['processId']),
                                               command=str(item.get('command') or '')[:1024],
                                               native_turn_id=self.turn_id))
                    else:
                        report_stop(self.config, self.session, self.attempt_id, capture_state='complete')
                        self.attempt_id = None
                        self.turn_id = None
                        if self.scoped_attempt:
                            return
        except (ControlError, OSError, ValueError, KeyError, TypeError) as exc:
            self.error = exc
        finally:
            if self.attempt_id:
                try:
                    report_stop(self.config, self.session, self.attempt_id, capture_state='lost')
                except ControlError as exc:
                    self.error = exc
            self.ready.set()
