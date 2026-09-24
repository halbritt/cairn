"""Native Hermes session presence and draft-preserving queue bridge."""
import atexit
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
BRIDGE_PROBE_SECONDS = 0.5


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
        adm_lock = getattr(cli, '_admission_lock', None)
        adm_ctx = adm_lock if adm_lock is not None else nullcontext()
        with adm_ctx:
            with lock:
                state = sessions.get(session_id)
                cli_active_turn = getattr(cli, '_active_turn_id', None) if (cli is not None and getattr(cli, 'session_id', None) == session_id) else None
                sess_turn = state.get('turn_id') if state else None
                sess_busy = state.get('busy') if state else False
                bound_turn = cli_active_turn or (sess_turn if sess_busy else None)

                # If an active bound turn exists, incoming hook MUST match it
                if bound_turn:
                    if not turn_id or turn_id != bound_turn:
                        return

                effective_turn = turn_id or bound_turn or ''
                sessions[session_id] = dict(context='', task=task_id, model=model, busy=True, turn_id=effective_turn)
                if cli is not None and getattr(cli, 'session_id', None) == session_id:
                    if effective_turn:
                        cli._active_turn_id = effective_turn
                        try:
                            from tools.approval import _approval_turn_id
                            _approval_turn_id.set(effective_turn)
                        except Exception:
                            pass
                try:
                    context = invoke(session_id, 'TurnStart', task_id, model, turn_id=effective_turn, prompt=user_message)
                    sessions[session_id]['context'] = context
                except (OSError, ValueError, subprocess.SubprocessError, RuntimeError):
                    logger.warning('Cairn native session registration/context unavailable')

    def request(request, session_id='', **_):
        with lock:
            context = sessions.get(session_id, {}).get('context')
            if context and isinstance(request.get('messages'), list):
                return {'request': dict(request, messages=[*request['messages'], {'role': 'user', 'content': context}])}

    def after(session_id='', turn_id='', task_id='', model='', platform='', **_):
        if not session_id or platform in ('cron', 'subagent', 'flush', 'auxiliary'):
            return
        cli = getattr(getattr(ctx, '_manager', None), '_cli_ref', None)
        adm_lock = getattr(cli, '_admission_lock', None)
        adm_ctx = adm_lock if adm_lock is not None else nullcontext()
        with adm_ctx:
            with lock:
                state = sessions.get(session_id)
                if state is None:
                    return
                active_turn = state.get('turn_id', '')
                cli_active_turn = getattr(cli, '_active_turn_id', None) if (cli is not None and getattr(cli, 'session_id', None) == session_id) else None
                bound_turn = active_turn or cli_active_turn

                # Validate incoming turn against active bound turn
                if bound_turn:
                    if not turn_id or turn_id != bound_turn:
                        return

                effective_turn = turn_id or bound_turn or ''
                state['busy'] = False
                try:
                    invoke(session_id, 'TurnEnd' if platform == 'cli' else 'SessionEnd', state['task'], state['model'], turn_id=effective_turn)
                except (OSError, ValueError, subprocess.SubprocessError, RuntimeError):
                    logger.warning('Cairn native session stop unavailable; presence will expire')
                finally:
                    state['context'] = ''
                    state['turn_id'] = ''
                    if platform != 'cli':
                        sessions.pop(session_id, None)

    def close():
        nonlocal bridge_running
        bridge_running = False
        cleanup_bridge()
        cli = getattr(getattr(ctx, '_manager', None), '_cli_ref', None)
        adm_lock = getattr(cli, '_admission_lock', None)
        adm_ctx = adm_lock if adm_lock is not None else nullcontext()
        with adm_ctx:
            with lock:
                for ident, state in list(sessions.items()):
                    try:
                        turn_id = state.get('turn_id', '')
                        invoke(ident, 'SessionEnd', state['task'], state['model'], turn_id=turn_id)
                    except (OSError, ValueError, subprocess.SubprocessError, RuntimeError):
                        logger.warning('Cairn native session leave unavailable; presence will expire')
                sessions.clear()

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

                        elif method == 'session/abort':
                            target_session = params.get('session_id')
                            expected_request_id = params.get('expected_request_id')
                            expected_turn_id = params.get('expected_turn_id')

                            if not target_session:
                                conn.sendall(json.dumps({'id': req_id, 'error': {'code': -32602, 'message': 'session_id required'}}).encode('utf-8') + b'\n')
                                continue

                            cli = getattr(getattr(ctx, '_manager', None), '_cli_ref', None)
                            if cli is None:
                                conn.sendall(json.dumps({'id': req_id, 'error': {'code': -32000, 'message': 'Hermes CLI instance unavailable; abort refused'}}).encode('utf-8') + b'\n')
                                continue

                            admission_lock = getattr(cli, '_admission_lock', None) or lock
                            with admission_lock:
                                current_sid = getattr(cli, 'session_id', None)
                                if target_session != current_sid:
                                    conn.sendall(json.dumps({'id': req_id, 'error': {'code': -32002, 'message': f'SESSION_MISMATCH: expected {target_session!r}, active session is {current_sid!r}'}}).encode('utf-8') + b'\n')
                                    continue

                                # 1. Check if the message is still in _pending_input (not yet admitted)
                                dequeued = False
                                if hasattr(cli, '_pending_input'):
                                    try:
                                        with cli._pending_input.mutex:
                                            for item in list(cli._pending_input.queue):
                                                matches = True
                                                if expected_request_id and getattr(item, 'request_id', None) != expected_request_id:
                                                    matches = False
                                                if expected_turn_id and getattr(item, 'turn_id', None) != expected_turn_id:
                                                    matches = False
                                                if not expected_request_id and not expected_turn_id:
                                                    matches = False
                                                if matches:
                                                    item.status = 'refused'
                                                    item.refusal_reason = 'cancelled_before_admission'
                                                    item.consumed_session_id = current_sid
                                                    cli._pending_input.queue.remove(item)
                                                    if getattr(item, 'on_consumed', None):
                                                        try:
                                                            item.on_consumed('refused', current_sid)
                                                        except Exception:
                                                            pass
                                                    dequeued = True
                                                    break
                                    except Exception as exc:
                                        logger.warning("Error inspecting _pending_input: %s", exc)

                                if dequeued:
                                    resp = {
                                        'id': req_id,
                                        'result': {
                                            'aborted': True,
                                            'turn_stop': 'dequeued_before_admission',
                                            'session_id': target_session,
                                            'request_id': expected_request_id,
                                            'turn_id': expected_turn_id,
                                            'tools': [],
                                        }
                                    }
                                    conn.sendall(json.dumps(resp).encode('utf-8') + b'\n')
                                    continue

                                adm_state = getattr(cli, '_admission_state', None)
                                is_running = getattr(cli, '_agent_running', False)
                                if adm_state is None:
                                    adm_state = 'running' if is_running else 'idle'

                                if adm_state == 'idle' and not is_running:
                                    resp = {
                                        'id': req_id,
                                        'result': {
                                            'aborted': False,
                                            'turn_stop': 'already_ended',
                                            'session_id': target_session,
                                            'request_id': expected_request_id,
                                            'turn_id': expected_turn_id,
                                            'tools': [],
                                        }
                                    }
                                    conn.sendall(json.dumps(resp).encode('utf-8') + b'\n')
                                    continue

                                active_req = getattr(cli, '_active_request_id', None)
                                active_turn = getattr(cli, '_active_turn_id', None)

                                # Never interrupt active turn on request/turn mismatch or unspecified abort:
                                if expected_request_id and active_req != expected_request_id:
                                    conn.sendall(json.dumps({
                                        'id': req_id,
                                        'error': {
                                            'code': -32001,
                                            'message': f'REQUEST_MISMATCH: active turn belongs to request {active_req!r} (expected {expected_request_id!r}); abort refused to protect active turn'
                                        }
                                    }).encode('utf-8') + b'\n')
                                    continue

                                if expected_turn_id and active_turn != expected_turn_id:
                                    conn.sendall(json.dumps({
                                        'id': req_id,
                                        'error': {
                                            'code': -32003,
                                            'message': f'TURN_MISMATCH: active turn is {active_turn!r} (expected {expected_turn_id!r}); abort refused to protect active turn'
                                        }
                                    }).encode('utf-8') + b'\n')
                                    continue

                                if not expected_request_id and not expected_turn_id:
                                    conn.sendall(json.dumps({
                                        'id': req_id,
                                        'error': {
                                            'code': -32004,
                                            'message': 'UNSPECIFIED_ABORT: expected_request_id or expected_turn_id required while agent is running; abort refused to protect active turn'
                                        }
                                    }).encode('utf-8') + b'\n')
                                    continue

                                if adm_state == 'admitting' and not is_running:
                                    cli._active_cancelled = True
                                    cli._admission_state = 'cancelled'
                                    resp = {
                                        'id': req_id,
                                        'result': {
                                            'aborted': True,
                                            'turn_stop': 'cancelled_before_running',
                                            'session_id': target_session,
                                            'request_id': expected_request_id or active_req,
                                            'turn_id': expected_turn_id or active_turn,
                                            'tools': [],
                                        }
                                    }
                                    conn.sendall(json.dumps(resp).encode('utf-8') + b'\n')
                                    continue

                                # 4. Request / turn matches! Signal turn interruption
                                agent = getattr(cli, 'agent', None)
                                if agent and hasattr(agent, 'interrupt') and callable(getattr(agent, 'interrupt')):
                                    agent.interrupt(hard_cancel=True)
                                    turn_stop = 'interrupted'
                                else:
                                    turn_stop = 'interruption_requested'
                                cli._last_turn_interrupted = True

                                # 5. Stop ONLY tools captured as belonging to the exact requested turn and verified process identity
                                tool_outcomes = []
                                tool_error = None
                                try:
                                    from tools.process_registry import process_registry, ProcessRegistry
                                    all_procs = process_registry.list_sessions(session_key=target_session)
                                    if not all_procs:
                                        all_procs = process_registry.list_sessions()
                                    for proc in all_procs:
                                        proc_id = proc.get('session_id')
                                        proc_req = proc.get('request_id')
                                        proc_turn = proc.get('turn_id')

                                        # Exact turn / request matching:
                                        if expected_request_id and proc_req != expected_request_id:
                                            continue
                                        if expected_turn_id and proc_turn != expected_turn_id:
                                            continue
                                        if not proc_req and not proc_turn:
                                            continue

                                        if proc.get('status') == 'running':
                                            pid = proc.get('pid')
                                            host_start = proc.get('host_start_time')
                                            # Verified process identity check: never kill a recycled PID
                                            if not ProcessRegistry._host_pid_is_ours(pid, host_start):
                                                tool_outcomes.append({
                                                    'item_id': proc_id,
                                                    'process_id': str(pid or ''),
                                                    'stop_state': 'unverified_process_identity',
                                                })
                                                continue

                                            process_registry.kill_process(proc_id, source="cairn.abort")
                                            # Bounded polling to positively verify termination
                                            verified = False
                                            poll_deadline = time.monotonic() + 3.0
                                            while time.monotonic() < poll_deadline:
                                                poll_info = process_registry.poll(proc_id)
                                                if poll_info.get('status') == 'exited' or poll_info.get('exited'):
                                                    verified = True
                                                    break
                                                time.sleep(0.05)
                                            stop_state = 'terminated' if verified else 'stop_issued'
                                            tool_outcomes.append({
                                                'item_id': proc_id,
                                                'process_id': str(pid or ''),
                                                'stop_state': stop_state,
                                            })
                                except Exception as exc:
                                    logger.warning("Error terminating tool processes: %s", exc)
                                    tool_error = str(exc)
                                    tool_outcomes.append({
                                        'item_id': 'tool_inventory',
                                        'process_id': '',
                                        'stop_state': f'unavailable: {exc}',
                                    })

                                resp = {
                                    'id': req_id,
                                    'result': {
                                        'aborted': True,
                                        'turn_stop': turn_stop,
                                        'session_id': target_session,
                                        'request_id': expected_request_id or active_req,
                                        'turn_id': expected_turn_id or active_turn,
                                        'tools': tool_outcomes,
                                    }
                                }
                                if tool_error:
                                    resp['result']['tool_error'] = tool_error
                                    resp['result']['tools_uncertain'] = True
                                conn.sendall(json.dumps(resp).encode('utf-8') + b'\n')

                        elif method == 'session/tools_status':
                            target_session = params.get('session_id')
                            tool_ids = params.get('tool_ids') or []
                            try:
                                from tools.process_registry import process_registry
                                all_procs = process_registry.list_sessions(session_key=target_session)
                                if tool_ids:
                                    id_set = set(tool_ids)
                                    filtered = [p for p in all_procs if p.get('session_id') in id_set or str(p.get('pid')) in id_set]
                                else:
                                    filtered = all_procs
                                resp = {
                                    'id': req_id,
                                    'result': {
                                        'session_id': target_session,
                                        'tools': filtered,
                                    }
                                }
                            except Exception as exc:
                                resp = {
                                    'id': req_id,
                                    'error': {'code': -32000, 'message': f'tools_status failed: {exc}'}
                                }
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
        except Exception as exc:
            try:
                conn.sendall(json.dumps({'id': None, 'error': {'code': -32000, 'message': str(exc)}}).encode('utf-8') + b'\n')
            except OSError:
                pass
        finally:
            client_semaphore.release()

    def bind_bridge():
        """Bind synchronously so unload/exit cleanup always sees the socket."""
        nonlocal bridge_server, bridge_bound
        if os.path.exists(bridge_path):
            # Probe the existing path with a bounded connect: register() runs on
            # Hermes startup, so an unresponsive listener must not block it. Only
            # a definite stale socket (refused, or already gone) is replaced;
            # a live, saturated or unknown listener is left in place.
            probe = socket.socket(socket.AF_UNIX)
            try:
                probe.settimeout(BRIDGE_PROBE_SECONDS)
                probe.connect(bridge_path)
            except (ConnectionRefusedError, FileNotFoundError):
                try:
                    os.unlink(bridge_path)
                except FileNotFoundError:
                    pass
            except OSError as exc:
                logger.warning("Existing bridge at %s did not answer (%s); not binding", bridge_path, exc)
                return False
            else:
                logger.warning("Another bridge is listening at %s; aborting bind", bridge_path)
                return False
            finally:
                probe.close()
        bridge_server = socket.socket(socket.AF_UNIX)
        bridge_server.bind(bridge_path)
        bridge_bound = True
        os.chmod(bridge_path, 0o600)
        bridge_server.listen(MAX_CONCURRENT_CLIENTS)
        bridge_server.settimeout(1.0)
        return True

    def serve_bridge():
        try:
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
        finally:
            cleanup_bridge()

    bridge_owner = os.getpid()

    def cleanup_bridge():
        # Idempotent; reachable from unload, the accept loop and atexit. A forked
        # child inherits atexit handlers but must never remove its parent's socket.
        nonlocal bridge_bound
        if os.getpid() != bridge_owner:
            return
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

    try:
        if bind_bridge():
            threading.Thread(target=serve_bridge, daemon=True).start()
            atexit.register(cleanup_bridge)  # Exits that skip plugin unload (Ctrl-C, SIGTERM handlers, sys.exit).
    except Exception as exc:
        cleanup_bridge()
        logger.warning('Cairn Hermes bridge server failed to initialize: %s', exc)

    def provider_observation(session_id, failure):
        if not os.environ.get('CAIRN_WAKE_CONTEXT'):
            return
        with lock:
            state = sessions.get(session_id)
            if state is None:
                return
            try:
                invoke(session_id, 'ProviderObservation', state['task'], state['model'], provider_failure=failure)
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
