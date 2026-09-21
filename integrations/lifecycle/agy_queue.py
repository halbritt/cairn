"""Queue a wakeup on an already running Agy session without touching its TUI composer.

Boundary Evidence & Interface Specification:
--------------------------------------------
1. Binary Inspection:
   The native Antigravity CLI binary (/home/halbritt/.local/bin/agy) is a 217MB monolithic
   Go binary (cortex runtime). In standalone interactive CLI mode, the process runs a
   Bubbletea TUI application whose input loop is blocked in a synchronous `read(0, ...)`
   system call on stdin/PTY when awaiting user keystrokes.

2. Closed Standalone Boundary & Idle Limitation:
   - Lifecycle Hooks (`hooks.json`): Agy supports synchronous command hooks on
     `PreInvocation`, `PostInvocation`, `PreToolUse`, `PostToolUse`, and `Stop`.
     Crucially, while an agent turn is running, the `Stop` hook provides a proven native
     turn continuation seam: returning `{"decision": "continue", "reason": "<context>"}`
     re-enters execution without returning to the idle prompt or touching the composer.
     However, once the agent is fully stopped and sitting at the idle prompt, NO hooks execute.
   - Internal Messaging Hub (`cortex/messages`): Undelivered messages written to
     `~/.gemini/antigravity-cli/brain/<id>/.system_generated/messages/undelivered/` are only
     drained at `PreInvocation` when a turn is launched; `FileStore` has no inotify watcher
     tied to the Bubbletea event loop, so dropping files there while idle does NOT wake the CLI.
   - Connect-RPC (`exa.language_server_pb.LanguageServerService`): In standalone CLI mode,
     ephemeral loopback ports and private in-memory CSRF tokens are unexported and unpersisted.
     Furthermore, `SendAgentMessage` routes into `cortex/messages`, which still lacks an idle
     TUI wakeup mechanism.
   - Remote Control (`agy remote-control`): Manages an optional WebRTC mobile daemon, not
     a local CLI queue bridge.

3. Actionable Source-Level Extension:
   To support draft-preserving background wakeups when idle, cortex requires either:
   a) An `fsnotify.Watcher` on `messages/undelivered/` that dispatches a `tea.Msg` into the
      active Bubbletea program to launch a turn while preserving textarea draft state.
   b) A dedicated local Unix domain socket bridge (`/tmp/cairn-agy-<pid>.sock`) implemented
      as an adapter wrapper (launcher) around the PTY, providing `session/prompt_async`.

4. Contract Commitments:
   - No Guessed RPCs: Inferred or unverified HTTP endpoints are rejected.
   - No Fabricated Busy/Idle State: Status is never inferred from PID liveness alone.
     Without a validated status bridge, `status()` raises `QueueUnavailable`.
   - Draft Preservation: Delivers prompts directly via native bridge or hook continuation,
     never synthetically clobbering an unsubmitted user draft in the composer.
   - Uncertain Write Reconciliation: Distinguishes `QueueUnavailable` (pre-send refusal;
     safe to retry) from `QueueError` (post-write timeout or disconnect; outcome
     uncertain, never automatically resend).
   - Cancellation Ownership (Agent 24 Contract):
     Refuses unfenced cancellation (`-32004 UNSUPPORTED_CONTROL`), ensuring active
     turns and newer owner turns are never aborted without atomic turn-fencing upstream.
"""
import json
import os
from pathlib import Path
import socket
import struct


class AgyQueueError(Exception):
    """Base error for Agy native queue operations."""
    pass


class QueueUnavailable(AgyQueueError):
    """Precondition refused before the queue submission was committed; safe to retry."""
    pass


class QueueRefused(AgyQueueError):
    """The session or host explicitly refused the queue submission."""
    pass


class QueueBusy(QueueRefused):
    """The session is currently busy executing an active turn; refusal preserves ordering."""
    pass


class QueueError(AgyQueueError):
    """An uncertain failure occurred during or after write; do NOT automatically resend."""
    pass


def _discard(transport):
    if transport:
        try:
            transport.close()
        except OSError:
            pass


def _verify_process(process):
    """Verify that the target process is running and matches recorded identity."""
    if not process or not isinstance(process, dict) or 'pid' not in process:
        raise QueueUnavailable('invalid process specification')
    pid = process['pid']
    stat_path = Path(f'/proc/{pid}/stat')
    if not stat_path.is_file():
        raise QueueUnavailable(f'target Agy process {pid} is not running')
    try:
        stat_data = stat_path.read_text().rsplit(')', 1)[1].split()
        start_time = int(stat_data[19])
        boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        if 'start' in process and start_time != process['start']:
            raise QueueUnavailable('Agy process start time mismatch; process was replaced')
        if 'boot' in process and boot_id != process['boot']:
            raise QueueUnavailable('system boot ID mismatch; host was rebooted')
    except (OSError, IndexError, ValueError) as exc:
        raise QueueUnavailable(f'unable to verify Agy process identity: {exc}') from exc


def _open_unix_socket(endpoint, process):
    """Connect to a local Unix domain socket bridge and verify peer credentials."""
    _verify_process(process)
    transport = socket.socket(socket.AF_UNIX)
    try:
        transport.settimeout(4.0)
        transport.connect(endpoint)
        peer, _, _ = struct.unpack('3i', transport.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if peer != process['pid']:
            raise QueueUnavailable(f'native socket belongs to PID {peer}, expected PID {process["pid"]}')
        return transport
    except QueueUnavailable:
        _discard(transport)
        raise
    except OSError as exc:
        _discard(transport)
        raise QueueUnavailable(f'native Agy queue endpoint {endpoint} is unavailable') from exc


def inspect_presence_lock(conversation_id, app_data_dir=None):
    """Inspect the native Agy presence lock file for an active conversation.

    Returns the PID holding the flock, or None if unlocked/not found.
    """
    if not conversation_id or not isinstance(conversation_id, str):
        return None
    base = Path(app_data_dir) if app_data_dir else (Path.home() / '.gemini' / 'antigravity-cli')
    lock_file = base / 'presence' / f'{conversation_id}.lock'
    if not lock_file.is_file():
        return None

    # Check which process holds an active flock on the presence file
    try:
        import fcntl
        with open(lock_file, 'r+') as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f, fcntl.LOCK_UN)
                return None  # Unlocked: no running process owns this conversation
            except (BlockingIOError, OSError):
                pass  # Locked: an active process owns this session
    except OSError:
        pass

    # Find PID holding the file open via /proc
    for proc_entry in Path('/proc').iterdir():
        if not proc_entry.name.isdigit():
            continue
        fd_dir = proc_entry / 'fd'
        try:
            for fd in fd_dir.iterdir():
                try:
                    if fd.resolve() == lock_file.resolve():
                        return int(proc_entry.name)
                except OSError:
                    continue
        except OSError:
            continue
    return None


def enqueue(endpoint, process, native_id, text, client_id, expected_session_id=None):
    """Submit a wakeup prompt to an active Agy session via a validated bridge.

    Returns (queued_submission_id, started).
    QueueUnavailable guarantees nothing was sent.
    QueueError signals an uncertain write; caller must NOT resend.
    """
    if not endpoint or not isinstance(endpoint, str) or not endpoint.strip():
        raise QueueUnavailable('endpoint must be a nonempty string path to a Unix domain socket bridge')
    if endpoint.startswith('http://') or endpoint.startswith('https://'):
        raise QueueUnavailable('inferred HTTP Connect-RPC endpoint is unsupported; standalone Agy requires a validated bridge')
    if not native_id or not isinstance(native_id, str) or not native_id.strip():
        raise QueueUnavailable('native_id must be a nonempty string')
    exp_id = expected_session_id or native_id
    if not exp_id or not isinstance(exp_id, str) or not exp_id.strip():
        raise QueueUnavailable('expected_session_id must be a nonempty string')
    if exp_id != native_id:
        raise QueueUnavailable(f'session mismatch: expected {exp_id!r}, target {native_id!r}')
    if not text or not isinstance(text, str):
        raise QueueUnavailable('text must be a nonempty string')
    if not client_id or not isinstance(client_id, str) or not client_id.strip():
        raise QueueUnavailable('client_id must be a nonempty string')

    transport = _open_unix_socket(endpoint, process)
    try:
        req = {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'session/prompt_async',
            'params': {
                'session_id': native_id,
                'text': text,
                'client_id': client_id,
                'expected_session_id': exp_id,
            }
        }
        payload = (json.dumps(req) + '\n').encode('utf-8')
        transport.sendall(payload)

        file_obj = transport.makefile('r', encoding='utf-8')
        line = file_obj.readline()
        if not line:
            raise QueueError('native queue outcome uncertain; Agy bridge closed connection without response')
        msg = json.loads(line)
        if 'error' in msg:
            err = msg['error']
            code = err.get('code')
            message = err.get('message', '')
            if 'BUSY' in message or code == -32600:
                raise QueueBusy(f'Agy session is busy: {message}')
            if code in (-32001, -32002) or 'SESSION_NOT_FOUND' in message or 'SESSION_MISMATCH' in message:
                raise QueueUnavailable(f'native session unavailable: {message}')
            raise QueueError(f'native Agy refused prompt_async: {message}')

        result = msg.get('result')
        if not isinstance(result, dict):
            raise QueueError(f'invalid bridge response schema: expected dict result, got {type(result).__name__}')
        if 'started' not in result or not isinstance(result['started'], bool):
            raise QueueError('invalid bridge response schema: missing or invalid boolean "started" field')
        queued_id = result.get('queued_id', client_id)
        if not queued_id or not isinstance(queued_id, str):
            queued_id = client_id
        started = result['started']
        return queued_id, started
    except (QueueUnavailable, QueueRefused):
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise QueueError('native queue outcome uncertain; do not automatically resend') from exc
    finally:
        _discard(transport)


def status(endpoint, process, native_id):
    """Retrieve session status (idle, busy, or error) from a validated bridge.

    Refuses to fabricate 'idle' status from PID existence alone.
    """
    if not endpoint or not isinstance(endpoint, str) or endpoint.startswith('http://') or endpoint.startswith('https://'):
        _verify_process(process)
        raise QueueUnavailable(
            'UNSUPPORTED_STATUS: native Agy CLI does not expose an unauthenticated turn status '
            'inspection endpoint; status cannot be inferred from PID liveness'
        )

    transport = _open_unix_socket(endpoint, process)
    try:
        req = {
            'jsonrpc': '2.0',
            'id': 2,
            'method': 'session/status',
            'params': {'session_id': native_id}
        }
        transport.sendall((json.dumps(req) + '\n').encode('utf-8'))
        file_obj = transport.makefile('r', encoding='utf-8')
        line = file_obj.readline()
        if not line:
            raise QueueError('native Agy bridge closed connection without response')
        msg = json.loads(line)
        if 'error' in msg:
            raise QueueError(f"native Agy status error: {msg['error']}")
        result = msg.get('result')
        if not isinstance(result, dict) or 'status' not in result:
            raise QueueError(f'invalid bridge status schema: {result!r}')
        return result
    finally:
        _discard(transport)


def abort(endpoint, process, native_id, expected_turn_id=None):
    """Attempt session abort.

    In coordination with cancellation owner, unfenced cancellation is
    strictly unavailable (UNSUPPORTED_CONTROL) until atomic turn-fencing upstream
    is supported, preventing clobbering of newer owner work.
    """
    raise QueueUnavailable(
        'UNSUPPORTED_CONTROL: native Agy session abort is unavailable until native turn fencing is supported'
    )
