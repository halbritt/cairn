"""Native Hermes session presence and draft-preserving queue bridge."""
import json
import logging
import os
from contextlib import nullcontext
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import uuid

from hermes_constants import get_hermes_home
from tools.terminal_tool import get_session_cwd, resolve_task_overrides

logger = logging.getLogger(__name__)

MAX_PAYLOAD_BYTES = 65536
MAX_CONCURRENT_CLIENTS = 4


MAX_REQUEST_TOOLS = 64
OWNERSHIP_IDENTIFIERS = ('session_id', 'request_id', 'delivery_id', 'turn_id', 'ownership_token')
OWNERSHIP_FLAGS = ('exclusive', 'revoked', 'cancelled', 'foreground_ended', 'turn_ended',
                   'tool_admission_closed')
OWNERSHIP_COUNTS = ('active_native_runs', 'active_tool_calls')


class ControlRefusal(Exception):
    def __init__(self, message, code=-32004):
        super().__init__(message)
        self.code = code


def valid_identifier(value):
    return (isinstance(value, str) and bool(value.strip()) and len(value.encode()) <= 256
            and not any(ord(c) < 32 for c in value))


def selected_ownership(snapshot):
    if (not isinstance(snapshot, dict)
            or any(not valid_identifier(snapshot.get(k)) for k in OWNERSHIP_IDENTIFIERS)
            or any(type(snapshot.get(k)) is not bool for k in OWNERSHIP_FLAGS)
            or any(type(snapshot.get(k)) is not int or snapshot[k] < 0 for k in OWNERSHIP_COUNTS)):
        raise ControlRefusal('OWNERSHIP_INVALID')
    if (snapshot['exclusive'] == snapshot['revoked'] or
            (snapshot['turn_ended'] and
             (not snapshot['foreground_ended'] or snapshot['active_native_runs'] != 0))):
        raise ControlRefusal('OWNERSHIP_INVALID')
    gap = snapshot.get('executor_capture_gap', True)
    if type(gap) is not bool:
        raise ControlRefusal('OWNERSHIP_INVALID')
    selected = {key: snapshot[key] for key in (*OWNERSHIP_IDENTIFIERS, *OWNERSHIP_FLAGS, *OWNERSHIP_COUNTS)}
    return dict(selected, executor_capture_gap=gap)


def native_snapshot(cli, token=None):
    callback = getattr(cli, 'request_ownership_snapshot', None)
    if not callable(callback):
        raise ControlRefusal('OWNERSHIP_UNAVAILABLE')
    try:
        return selected_ownership(callback(expected_ownership_token=token))
    except ValueError as exc:
        raise ControlRefusal('OWNERSHIP_UNAVAILABLE') from exc


def selected_inventory(ownership):
    """Enumerate the entire registry, including child session keys.

    Registry flags are not OS termination evidence. Until a reviewed native
    verifier is available, every selected process remains merely captured.
    """
    try:
        from tools.process_registry import process_registry
        records = process_registry.list_sessions()
    except Exception:
        return [], False
    if not isinstance(records, list):
        return [], False
    tools, complete, seen = [], True, set()
    for record in records:
        if not isinstance(record, dict):
            complete = False
            continue
        request, turn = record.get('request_id'), record.get('turn_id')
        if not request or not turn:
            complete = False  # An unlabelled process cannot be excluded as unrelated.
            continue
        if request != ownership['request_id'] or turn != ownership['turn_id']:
            continue
        item, pid = record.get('session_id'), record.get('pid')
        if not valid_identifier(item) or type(pid) is not int or pid < 1 or item in seen:
            complete = False
            continue
        seen.add(item)
        if len(tools) >= MAX_REQUEST_TOOLS:
            return [], False  # Never present a truncated inventory as complete.
        tools.append(dict(item_id=item, process_id=str(pid), native_turn_id=turn, stop_state='captured'))
    return tools, complete


def register(ctx):
    home = get_hermes_home()
    config = json.loads((home / 'cairn-coordination.json').read_text())
    sessions = {}
    wake_history_file = home / f'cairn-wake-history-{os.getpid()}.json'
    wake_history = {}
    if wake_history_file.is_file():
        try:
            wake_history = json.loads(wake_history_file.read_text())
        except (OSError, ValueError):
            wake_history = {}
    lock = threading.RLock()
    bridge_running = True
    bridge_bound = False
    client_semaphore = threading.BoundedSemaphore(MAX_CONCURRENT_CLIENTS)

    def invoke(session_id, event, task_id='', model='', turn_id='', **selected):
        cwd = (get_session_cwd(task_id) or get_session_cwd(session_id)
               or resolve_task_overrides(task_id).get('cwd') or os.environ.get('TERMINAL_CWD') or os.getcwd())
        payload = dict(session_id=session_id, hook_event_name=event, cwd=cwd, host_pid=os.getpid(), model=model, **selected)
        if turn_id:
            payload['turn_id'] = turn_id
        result = subprocess.run([sys.executable, config['script'], 'hook', '--config', config['config']],
            input=json.dumps(payload),
            text=True, capture_output=True, timeout=15)
        if result.returncode:
            raise RuntimeError('Cairn session presence unavailable')
        return json.loads(result.stdout).get('hookSpecificOutput', {}).get('additionalContext', '')

    def before(session_id='', turn_id='', task_id='', model='', parent_session_id='', platform='', user_message='', **_):
        if not session_id or parent_session_id or platform in ('cron', 'subagent', 'flush', 'auxiliary'):
            return
        cli = getattr(getattr(ctx, '_manager', None), '_cli_ref', None)
        admission = getattr(cli, '_admission_lock', None) or nullcontext()
        ownership = None
        with admission:
            with lock:
                previous = sessions.get(session_id, {})
                current_cli = cli is not None and getattr(cli, 'session_id', None) == session_id
                if platform == 'cli' and cli is not None and not current_cli:
                    return
                native_turn = getattr(cli, '_active_turn_id', None) if current_cli else None
                bound = native_turn or (previous.get('turn_id') if previous.get('busy') else None)
                if bound and (not turn_id or turn_id != bound):
                    return
                if current_cli:
                    try:
                        candidate = native_snapshot(cli)
                        if candidate['session_id'] == session_id and candidate['turn_id'] == turn_id:
                            ownership = candidate
                    except ControlRefusal:
                        pass  # Ordinary turns/older hosts provide no exclusive evidence.
                state = dict(context='', task=task_id, model=model, busy=True, turn_id=turn_id)
                sessions[session_id] = state
        selected = {'request_ownership': ownership} if ownership is not None else {}
        try:
            context = invoke(session_id, 'TurnStart', task_id, model, turn_id=turn_id,
                             prompt=user_message, **selected)
        except (OSError, ValueError, subprocess.SubprocessError, RuntimeError):
            logger.warning('Cairn native session registration/context unavailable')
            return
        # An IPC reply must not overwrite a later end hook or replacement turn.
        with admission:
            with lock:
                if sessions.get(session_id) is not state or not state['busy']:
                    return
                if current_cli and (getattr(cli, 'session_id', None) != session_id
                                    or getattr(cli, '_active_turn_id', None) != turn_id):
                    return
                state['context'] = context

    def request(request, session_id='', **_):
        with lock:
            context = sessions.get(session_id, {}).get('context')
            if context and isinstance(request.get('messages'), list):
                return {'request': dict(request, messages=[*request['messages'], {'role': 'user', 'content': context}])}

    def after(session_id='', turn_id='', task_id='', model='', platform='', **_):
        if not session_id or platform in ('cron', 'subagent', 'flush', 'auxiliary'):
            return
        cli = getattr(getattr(ctx, '_manager', None), '_cli_ref', None)
        admission = getattr(cli, '_admission_lock', None) or nullcontext()
        with admission:
            with lock:
                state = sessions.get(session_id)
                if state is None or (state.get('turn_id') and state['turn_id'] != turn_id):
                    return
                if not state.get('busy'):
                    return
                ended = dict(state)
                state.update(busy=False, context='', turn_id='')
                if platform != 'cli':
                    sessions.pop(session_id, None)
        try:
            invoke(session_id, 'TurnEnd' if platform == 'cli' else 'SessionEnd',
                   ended['task'], ended['model'], turn_id=ended['turn_id'])
        except (OSError, ValueError, subprocess.SubprocessError, RuntimeError):
            logger.warning('Cairn native session stop unavailable; presence will expire')

    def close():
        nonlocal bridge_running
        with lock:
            bridge_running = False
            ended = list(sessions.items())
            sessions.clear()
        cleanup_bridge()
        if bridge_thread is not threading.current_thread():
            bridge_thread.join(timeout=2)
        for ident, state in ended:
            try:
                invoke(ident, 'SessionEnd', state['task'], state['model'], turn_id=state['turn_id'])
            except (OSError, ValueError, subprocess.SubprocessError, RuntimeError):
                logger.warning('Cairn native session leave unavailable; presence will expire')

    def request_control(method, params):
        keys = ('session_id', 'request_id', 'turn_id', 'ownership_token')
        if any(not valid_identifier(params.get(key)) for key in keys):
            raise ControlRefusal('EXACT_REQUEST_OWNERSHIP_REQUIRED', -32602)
        cli = getattr(getattr(ctx, '_manager', None), '_cli_ref', None)
        ownership = native_snapshot(cli, params['ownership_token'])
        if any(ownership[key] != params[key] for key in keys):
            raise ControlRefusal('OWNERSHIP_MISMATCH')
        if method in ('session/request_cancel', 'session/abort'):
            callback = getattr(cli, 'abort_owned_request', None)
            if not callable(callback):
                raise ControlRefusal('OWNERSHIP_UNAVAILABLE')
            try:
                result = callback(expected_request_id=params['request_id'],
                                  expected_turn_id=params['turn_id'],
                                  expected_ownership_token=params['ownership_token'])
            except ValueError as exc:
                if str(exc) in ('OWNERSHIP_UNAVAILABLE', 'OWNERSHIP_MISMATCH',
                                'OWNERSHIP_REVOKED', 'OWNERSHIP_AGENT_UNAVAILABLE'):
                    raise ControlRefusal(str(exc)) from exc
                raise RuntimeError('native cancellation outcome is unknown') from exc
            try:
                ownership = selected_ownership(result)
            except ControlRefusal as exc:
                raise RuntimeError('native cancellation response is invalid') from exc
            if (any(ownership[key] != params[key] for key in keys)
                    or not ownership['cancelled'] or not ownership['tool_admission_closed']):
                raise RuntimeError('native cancellation response does not confirm the exact closed gate')
        if method == 'session/request_cleanup':
            # A separate, durably captured item list is required. Do not turn
            # legacy kill/poll registry flags into terminal evidence.
            tools = params.get('tools')
            if (not isinstance(tools, list) or not tools or len(tools) > MAX_REQUEST_TOOLS
                    or any(not isinstance(t, dict) or
                           not valid_identifier(t.get('item_id')) or
                           not valid_identifier(t.get('process_id')) for t in tools)):
                raise ControlRefusal('CAPTURED_TOOL_IDENTITIES_REQUIRED', -32602)
            raise ControlRefusal('CLEANUP_UNAVAILABLE', -32020)
        tools, complete = selected_inventory(ownership)
        return dict(ownership=ownership, tools=tools, inventory_complete=complete,
                    terminal_scan='unknown_remaining')

    # Private local bridge for native queueing into Hermes
    bridge_path = f"/tmp/cairn-hermes-{os.getpid()}.sock"
    bridge_server = None

    def record_wake_outcome(client_id, status, current_sid):
        with lock:
            wake_history[client_id] = {
                'status': status,
                'consumed_session_id': current_sid,
                'updated_at': time.time(),
            }
            if len(wake_history) > 100:
                oldest = sorted(wake_history.keys(), key=lambda k: wake_history[k].get('updated_at', 0))[:20]
                for k in oldest:
                    wake_history.pop(k, None)
            try:
                tmp = wake_history_file.with_name(f"{wake_history_file.name}.tmp.{uuid.uuid4().hex}")
                tmp.write_text(json.dumps(wake_history))
                tmp.replace(wake_history_file)
            except OSError as exc:
                logger.warning('Failed to persist Cairn wake history: %s', exc)
                raise

    def handle_client(conn):
        try:
            with conn:
                conn.settimeout(4.0)
                buffer = b''
                while bridge_running:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    buffer += chunk
                    if len(buffer) > MAX_PAYLOAD_BYTES:
                        conn.sendall(json.dumps({'id': None, 'error': {'code': -32600, 'message': 'PAYLOAD_TOO_LARGE: exceeds 64KB limit'}}).encode('utf-8') + b'\n')
                        break
                    if b'\n' in buffer:
                        line, buffer = buffer.split(b'\n', 1)
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            req = json.loads(line.decode('utf-8'))
                        except Exception:
                            conn.sendall(json.dumps({'id': None, 'error': {'code': -32700, 'message': 'Parse error'}}).encode('utf-8') + b'\n')
                            continue

                        if not isinstance(req, dict) or not isinstance(req.get('params', {}), dict):
                            conn.sendall(json.dumps({'id': req.get('id') if isinstance(req, dict) else None,
                                'error': {'code': -32602, 'message': 'object request and params required'}}).encode() + b'\n')
                            continue
                        req_id = req.get('id')
                        method = req.get('method')
                        params = req.get('params', {})

                        if method == 'session/queue_message':
                            target_session = params.get('session_id')
                            text = params.get('text')
                            client_id = params.get('client_id')
                            expected_session = params.get('expected_session_id') or target_session

                            if not target_session or not text:
                                conn.sendall(json.dumps({'id': req_id, 'error': {'code': -32602, 'message': 'session_id and text required'}}).encode('utf-8') + b'\n')
                                continue
                            if not expected_session or not isinstance(expected_session, str) or not expected_session.strip():
                                conn.sendall(json.dumps({'id': req_id, 'error': {'code': -32602, 'message': 'expected_session_id must be a nonempty string'}}).encode('utf-8') + b'\n')
                                continue

                            # Explicit queue capability check; never advertise unverified fallback
                            if not hasattr(ctx, 'queue_message'):
                                conn.sendall(json.dumps({
                                    'id': req_id,
                                    'error': {
                                        'code': -32000,
                                        'message': 'queue_message capability unavailable on this Hermes runtime; unverified fallback refused'
                                    }
                                }).encode('utf-8') + b'\n')
                                continue

                            cli = getattr(getattr(ctx, '_manager', None), '_cli_ref', None)
                            if cli is not None:
                                current_sid = getattr(cli, 'session_id', None)
                                if current_sid:
                                    if expected_session and expected_session != current_sid:
                                        conn.sendall(json.dumps({
                                            'id': req_id,
                                            'error': {
                                                'code': -32002,
                                                'message': f'SESSION_MISMATCH: expected {expected_session!r}, active session is {current_sid!r}'
                                            }
                                        }).encode('utf-8') + b'\n')
                                        continue
                                    if target_session and target_session != current_sid:
                                        conn.sendall(json.dumps({
                                            'id': req_id,
                                            'error': {
                                                'code': -32002,
                                                'message': f'SESSION_MISMATCH: target {target_session!r}, active session is {current_sid!r}'
                                            }
                                        }).encode('utf-8') + b'\n')
                                        continue

                            cli_busy = bool(cli and getattr(cli, '_agent_running', False) and getattr(cli, 'session_id', None) == target_session)
                            with lock:
                                sess = sessions.get(target_session)
                                hook_busy = bool(sess and sess.get('busy'))
                            is_busy = cli_busy or hook_busy

                            # Define callback to capture durable admission/refusal state
                            def on_consumed_cb(status, current_sid, cid=client_id):
                                record_wake_outcome(cid, status, current_sid)

                            request_id = params.get('request_id')
                            delivery_id = params.get('delivery_id')
                            turn_id = params.get('turn_id')

                            # Exact public seam: content=text (keyword is content, not text!)
                            qm = ctx.queue_message(
                                content=text,
                                expected_session_id=expected_session,
                                role=params.get('role', 'user'),
                                source='cairn-wake',
                                on_consumed=on_consumed_cb,
                                request_id=request_id,
                                delivery_id=delivery_id,
                                turn_id=turn_id,
                            )
                            if qm is None:
                                conn.sendall(json.dumps({
                                    'id': req_id,
                                    'error': {
                                        'code': -32000,
                                        'message': 'Hermes CLI instance unavailable; queue refused'
                                    }
                                }).encode('utf-8') + b'\n')
                                continue

                            resp = {
                                'id': req_id,
                                'result': {
                                    'queued': True,
                                    'queued_id': client_id,
                                    'session_id': target_session,
                                    'busy': is_busy,
                                    'started': not is_busy,
                                    'request_id': request_id,
                                    'delivery_id': delivery_id,
                                    'turn_id': turn_id,
                                }
                            }
                            conn.sendall(json.dumps(resp).encode('utf-8') + b'\n')

                        elif method in ('session/request_status', 'session/request_cancel',
                                        'session/request_cleanup', 'session/abort', 'session/tools_status'):
                            if method == 'session/abort':
                                params = dict(params, request_id=params.get('request_id') or params.get('expected_request_id'),
                                              turn_id=params.get('turn_id') or params.get('expected_turn_id'))
                            try:
                                result = request_control(method, params)
                                resp = {'id': req_id, 'result': result}
                            except ControlRefusal as exc:
                                resp = {'id': req_id, 'error': {'code': exc.code, 'message': str(exc)}}
                            conn.sendall(json.dumps(resp).encode('utf-8') + b'\n')

                        elif method == 'session/status':
                            target_session = params.get('session_id')
                            cli = getattr(getattr(ctx, '_manager', None), '_cli_ref', None)
                            cli_busy = bool(cli and getattr(cli, '_agent_running', False) and getattr(cli, 'session_id', None) == target_session)
                            with lock:
                                sess = sessions.get(target_session)
                                hook_busy = bool(sess and sess.get('busy'))
                            status_val = 'busy' if (cli_busy or hook_busy) else 'idle'
                            resp = {'id': req_id, 'result': {'session_id': target_session, 'status': status_val}}
                            conn.sendall(json.dumps(resp).encode('utf-8') + b'\n')

                        elif method == 'session/wake_status':
                            client_id = params.get('client_id')
                            with lock:
                                outcome = wake_history.get(client_id, {'status': 'pending'})
                            resp = {'id': req_id, 'result': outcome}
                            conn.sendall(json.dumps(resp).encode('utf-8') + b'\n')

                        else:
                            conn.sendall(json.dumps({'id': req_id, 'error': {'code': -32601, 'message': f'Method {method} not found'}}).encode('utf-8') + b'\n')
        except Exception:
            try:
                conn.sendall(json.dumps({'id': None, 'error': {'code': -32000, 'message': 'native bridge operation outcome unavailable'}}).encode('utf-8') + b'\n')
            except OSError:
                pass
        finally:
            client_semaphore.release()

    def serve_bridge():
        nonlocal bridge_server, bridge_bound
        try:
            with lock:
                if not bridge_running:
                    return
                if os.path.exists(bridge_path):
                    try:
                        # Test if an active server is listening
                        with socket.socket(socket.AF_UNIX) as test_sock:
                            test_sock.settimeout(0.1)
                            test_sock.connect(bridge_path)
                        logger.warning("Another bridge is listening at %s; aborting bind", bridge_path)
                        return
                    except OSError:
                        try:
                            os.unlink(bridge_path)
                        except OSError:
                            pass

                bridge_server = socket.socket(socket.AF_UNIX)
                bridge_server.bind(bridge_path)
                bridge_bound = True
                os.chmod(bridge_path, 0o600)
                bridge_server.listen(MAX_CONCURRENT_CLIENTS)
                bridge_server.settimeout(1.0)
            while bridge_running:
                try:
                    conn, _ = bridge_server.accept()
                    if not client_semaphore.acquire(timeout=0.1):
                        conn.sendall(json.dumps({'id': None, 'error': {'code': -32000, 'message': 'SERVER_BUSY: max concurrent connections reached'}}).encode('utf-8') + b'\n')
                        conn.close()
                        continue
                    t = threading.Thread(target=handle_client, args=(conn,), daemon=True)
                    t.start()
                except socket.timeout:
                    continue
                except OSError:
                    break
        except Exception as exc:
            logger.warning('Cairn Hermes bridge server failed to initialize: %s', exc)

    def cleanup_bridge():
        nonlocal bridge_bound
        with lock:
            if bridge_server:
                try:
                    bridge_server.close()
                except OSError:
                    pass
            if bridge_bound:
                bridge_bound = False
                try:
                    if os.path.exists(bridge_path):
                        os.unlink(bridge_path)
                except OSError:
                    pass

    bridge_thread = threading.Thread(target=serve_bridge, daemon=True)
    bridge_thread.start()

    def provider_observation(session_id, failure):
        if not os.environ.get('CAIRN_WAKE_CONTEXT'):
            return
        with lock:
            state = sessions.get(session_id)
            if state is None:
                return
            task, model = state['task'], state['model']
        try:
            invoke(session_id, 'ProviderObservation', task, model, provider_failure=failure)
        except (OSError, ValueError, subprocess.SubprocessError, RuntimeError):
            logger.warning('Cairn provider observation could not be retained')

    def api_error(session_id='', status_code=None, reason=None, **_):
        failure = None
        if reason in ('rate_limit', 'billing'):
            status = status_code if type(status_code) is int and 100 <= status_code <= 599 else 0
            failure = dict(harness='hermes', source='native-hook', kind=reason, code=reason, status=status)
        provider_observation(session_id, failure)

    def api_success(session_id='', **_):
        provider_observation(session_id, None)

    ctx.register_hook('pre_llm_call', before)
    ctx.register_hook('post_llm_call', after)
    ctx.register_hook('api_request_error', api_error)
    ctx.register_hook('post_api_request', api_success)
    ctx.register_middleware('llm_request', request)
    ctx.on_unload(close)
