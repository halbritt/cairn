#!/usr/bin/env python3
"""Associate native sessions with Cairn UUIDs; watch their host process liveness."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import signal
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import uuid


class CoordinationError(Exception):
    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code


class NativePromptRefused(CoordinationError):
    """A joined channel wake is rejected natively: the hook exits 2 so Claude
    erases only the channel text while the owner prompt continues (proven on
    Claude 2.1.273 by the owner's live rejection probe)."""

    def __init__(self, message):
        super().__init__('NATIVE_PROMPT_REFUSED', message)


def process_reference(pid):
    raw = Path(f"/proc/{pid}/stat").read_text()
    fields = raw.rsplit(")", 1)[1].split()
    return dict(pid=pid, start=int(fields[19]), state=fields[0], parent=int(fields[1]),
                boot=Path("/proc/sys/kernel/random/boot_id").read_text().strip())


def process_alive(ref):
    try:
        current = process_reference(ref["pid"])
    except (FileNotFoundError, ProcessLookupError):
        return False
    return (current["state"] not in ("Z", "X") and
            all(current[k] == ref[k] for k in ("pid", "start", "boot")))


def herdr_environment(process):
    """Select only the live native process's host socket, never another session."""
    values = {}
    with Path(f"/proc/{process['pid']}/environ").open('rb') as file:
        raw = file.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise CoordinationError('INVALID_HOST', 'native environment exceeds limit')
    for entry in raw.split(b'\0'):
        key, _, value = entry.partition(b'=')
        if key in (b'HERDR_ENV', b'HERDR_SOCKET_PATH'):
            values[key.decode()] = value.decode()
    if values.get('HERDR_ENV') != '1' or not Path(values.get('HERDR_SOCKET_PATH', '')).is_absolute():
        return None
    # Remove the watcher's inherited pane/remote context. The explicit target
    # and the observed process's socket select this operation.
    return dict({k: v for k, v in os.environ.items() if not k.startswith('HERDR_')}, **values)


def herdr_call(config, environment, *args):
    result = subprocess.run([config['idle_wakeup'], *args], env=environment,
                            capture_output=True, text=True, timeout=4)
    if result.returncode:
        try:
            code = json.loads(result.stderr).get('error', {}).get('code')
        except (ValueError, AttributeError):
            code = None
        if code in ('pane_not_found', 'agent_not_found', 'agent_not_running'):
            raise CoordinationError('HOST_TARGET_GONE', 'Herdr target no longer exists')
        raise CoordinationError('HOST_UNAVAILABLE', 'Herdr refused the host operation')
    try:
        response = json.loads(result.stdout)
        return response['result']
    except (ValueError, KeyError, TypeError) as exc:
        raise CoordinationError('HOST_UNAVAILABLE', 'Herdr returned an invalid response') from exc


def host_matches(config, state, host, environment):
    if (host.get('agent') != config['harness'] or host.get('agent_status') not in ('idle', 'done')
            or host.get('focused') is not False):
        return False
    native = state['agent']['native_session_id']
    session = host.get('agent_session')
    if session:
        if session.get('kind') != 'id' or session.get('value') != native or session.get('agent') != config['harness']:
            return False
    elif config['harness'] == 'codex':
        # This installed Codex integration has no Herdr session field. Its open
        # rollout path identifies the actual conversation without reading text.
        sessions = set()
        for path in Path(f"/proc/{state['process']['pid']}/fd").iterdir():
            try:
                match = re.search(r'/rollout-[^/]*-([0-9a-f-]{36})\.jsonl$', os.readlink(path))
            except FileNotFoundError:
                continue
            if match:
                sessions.add(match.group(1))
        if sessions != {native}:
            return False
    else:
        return False
    try:
        info = herdr_call(config, environment, 'pane', 'process-info', '--pane', host['pane_id'])['process_info']
    except CoordinationError as exc:
        if exc.code == 'HOST_TARGET_GONE':
            return False  # A different candidate can still be the live target.
        raise
    return (info.get('foreground_process_group_id') == os.getpgid(state['process']['pid'])
            and any(p['pid'] == state['process']['pid'] for p in info.get('foreground_processes', []))
            and process_alive(state['process']))


def queue_helper():
    """Load the queue helper with a bounded diagnostic when it is missing."""
    try:
        import codex_queue
    except ImportError as exc:
        raise CoordinationError('WAKE_UNAVAILABLE', f'codex queue helper is unavailable: {exc}') from exc
    return codex_queue


def drop_idle_wake(path, wake):
    with session_lock(path):
        state = json.loads(path.read_text())
        if state.get('idle_wake') == wake:
            state.pop('idle_wake')
            write_state(path, state)


def prepare_idle_wake(config, state, path):
    if (not config.get('idle_wakeup') or not config.get('native_delivery') or
            state.get('inbox_intent') or state.get('ending') or state.get('retired')):
        return None
    agent = state['agent']
    if coordination_excluded(state['workspace']) or coordination_excluded(agent['metadata']['workspace']):
        return None
    if agent['metadata']['state'] != 'idle' or agent['metadata']['delivery_mode'] != 'existing-session':
        return None
    ready = call(config, 'session-inbox-ready', session_ref(agent))
    delivery = ready.get('delivery_id')
    if not delivery:
        return None
    endpoint = codex_queue_endpoint(config, state['process'])
    prior = state.get('idle_wake', {})
    if prior.get('delivery_id') == delivery and prior.get('session') == session_ref(agent):
        if (endpoint and prior.get('transport') == 'codex-queue' and prior.get('status') == 'queued' and
                prior.get('queued_submission_id') and prior.get('endpoint') == endpoint['endpoint'] and
                prior.get('native_id') == agent['native_session_id']):
            return dict(wake=prior, process=state['process'])  # Retry only this retained start; never another add.
        return None  # Submitted or uncertain; only native handling permits a new nudge.
    request_id = ready.get('request_id') or str(uuid.uuid4())
    if endpoint:
        state['idle_wake'] = dict(delivery_id=delivery, request_id=request_id, session=session_ref(agent),
            transport='codex-queue', endpoint=endpoint['endpoint'], owner=endpoint['owner'],
            native_id=agent['native_session_id'],
            status='uncertain', attempted_at=time.time())
        write_state(path, state)
        return dict(wake=state['idle_wake'], process=state['process'])
    channel = claude_channel_endpoint(config, state['process'])
    if channel:
        state['idle_wake'] = dict(delivery_id=delivery, request_id=request_id, session=session_ref(agent),
            transport='claude-channel', endpoint=channel['socket'], bridge=channel['process'],
            parent=channel['parent'], native_id=agent['native_session_id'],
            status='uncertain', attempted_at=time.time())
        write_state(path, state)
        return dict(wake=state['idle_wake'], process=state['process'])
    opencode = opencode_queue_endpoint(config, state['process'])
    if opencode:
        state['idle_wake'] = dict(delivery_id=delivery, request_id=request_id, session=session_ref(agent),
            transport='opencode-queue', endpoint=opencode, native_id=agent['native_session_id'],
            status='uncertain', attempted_at=time.time())
        write_state(path, state)
        return dict(wake=state['idle_wake'], process=state['process'])
    hermes = hermes_queue_endpoint(config, state['process'])
    if hermes:
        state['idle_wake'] = dict(delivery_id=delivery, request_id=request_id, session=session_ref(agent),
            transport='hermes-queue', endpoint=hermes, native_id=agent['native_session_id'],
            status='uncertain', attempted_at=time.time())
        write_state(path, state)
        return dict(wake=state['idle_wake'], process=state['process'])
    agy = agy_queue_endpoint(config, state['process'])
    if agy:
        state['idle_wake'] = dict(delivery_id=delivery, request_id=request_id, session=session_ref(agent),
            transport='agy-queue', endpoint=agy, native_id=agent['native_session_id'],
            status='uncertain', attempted_at=time.time())
        write_state(path, state)
        return dict(wake=state['idle_wake'], process=state['process'])
    if config.get('claude_channel_dir') or config.get('harness') in ('codex', 'claude', 'opencode', 'hermes', 'agy'):
        # An explicitly configured native channel or known native harness
        # never falls back to the terminal route when its registry/socket is
        # stale, malformed or missing.
        return None
    environment = herdr_environment(state['process'])
    if environment is None:
        return None
    candidates = herdr_call(config, environment, 'agent', 'list')['agents']
    matches = [h for h in candidates if host_matches(config, state, h, environment)]
    if len(matches) != 1:
        return None
    host = matches[0]
    current = herdr_call(config, environment, 'agent', 'get', host['pane_id'])['agent']
    keys = ('pane_id', 'terminal_id', 'revision', 'state_change_seq')
    if any(current.get(k) != host.get(k) for k in keys) or not host_matches(config, state, current, environment):
        return None
    # Revalidate the Cairn execution and delivery after host observations. This
    # remains a hint, not a lease; the prompt contains no source/work payload.
    if call(config, 'session-inbox-ready', session_ref(agent)).get('delivery_id') != delivery:
        return None
    state['idle_wake'] = dict(delivery_id=delivery, request_id=request_id, session=session_ref(agent),
        target={k: current[k] for k in keys}, status='uncertain', attempted_at=time.time())
    write_state(path, state)
    return dict(environment=environment, wake=state['idle_wake'], process=state['process'])


def process_identity(ref):
    """Stable cross-language process identity.

    The Go bridge registry emits {pid,start,boot} while this engine's
    process_reference adds Linux state/parent fields around the same
    identity; selection compares only the stable projection.
    """
    if not isinstance(ref, dict):
        return None
    identity = (ref.get('pid'), ref.get('start'), ref.get('boot'))
    return identity if all(isinstance(value, (int, str)) for value in identity) else None


def claude_channel_endpoint(config, process):
    """Select the channel bridge registry for exactly this native process."""
    directory = config.get('claude_channel_dir')
    if config['harness'] != 'claude' or not directory:
        return None
    try:
        record = json.loads((Path(directory) / f"{process['pid']}.json").read_text())
    except (OSError, ValueError):
        return None
    if (not isinstance(record, dict) or record.get('schema') != 'cairn.claude-channel/1' or
            process_identity(record.get('parent')) != process_identity(process) or
            not isinstance(record.get('process'), dict)):
        return None  # A stale or foreign registry never matches this process.
    socket = record.get('socket')
    if not isinstance(socket, str) or not Path(socket).is_absolute() or not process_alive(record['process']):
        return None
    return dict(socket=socket, process=record['process'], parent=record['parent'])


def channel_helper():
    """Load the channel client with a bounded diagnostic when it is missing."""
    try:
        import claude_channel
    except ImportError as exc:
        raise CoordinationError('WAKE_UNAVAILABLE', f'claude channel client is unavailable: {exc}') from exc
    return claude_channel


def opencode_queue_endpoint(config, process):
    if config.get('harness') != 'opencode':
        return None
    path = Path(f"/tmp/cairn-opencode-{process['pid']}.sock")
    return str(path) if path.is_socket() else None


def hermes_queue_endpoint(config, process):
    if config.get('harness') != 'hermes':
        return None
    path = Path(f"/tmp/cairn-hermes-{process['pid']}.sock")
    return str(path) if path.is_socket() else None


def agy_queue_endpoint(config, process):
    if config.get('harness') != 'agy':
        return None
    path = Path(f"/tmp/cairn-agy-{process['pid']}.sock")
    if path.is_socket():
        return str(path)
    return None


def codex_queue_endpoint(config, process):
    """Select the native queue endpoint for exactly one Codex process.

    An app-server with an explicit unix listener owns its socket (process
    identity). An interactive TUI started with --remote unix://PATH shares
    that app-server: its wakes use the same socket with user-level peer
    verification, because the serving process is not the observed TUI.
    """
    if config['harness'] != 'codex':
        return None
    try:
        argv = Path(f"/proc/{process['pid']}/cmdline").read_bytes().decode().split('\0')
    except OSError:
        return None
    candidates = []
    if 'app-server' in argv and '--remote' not in argv:
        candidates.append('process')
    elif '--remote' in argv:
        candidates.append('uid')
    for owner in candidates:
        for i, argument in enumerate(argv):
            if argument in ('--listen', '--remote') and i+1 < len(argv):
                address = argv[i+1]
            elif argument.startswith('--listen='):
                address = argument.removeprefix('--listen=')
            elif argument.startswith('--remote='):
                address = argument.removeprefix('--remote=')
            else:
                continue
            if address.startswith('unix://') and len(address) > 7 and Path(address[7:]).is_absolute():
                return dict(endpoint=address[7:], owner=owner)
    return None


def wake_message(wake):
    return ("This is a new live turn from the configured Cairn automatic inbox wakeup. "
            "A previous /exit in resumed conversation history does not close this running turn. "
            f"Current agent {wake['session']['agent_id']}, "
            f"execution {wake['session']['execution_id']}. "
            f"Wake delivery {wake['delivery_id']}. "
            "Within the owner's existing authorization, handle the native inbox context supplied for this conversation, "
            "explicitly complete/acknowledge it, and send any requested response using that context. "
            "If no matching native context was supplied, report that and stop. "
            "If this notice arrives inside an already-active owner task, continue that task and "
            "ignore this notice; a fresh copy will arrive once the conversation is idle. "
            "Do not register, manually claim an inbox, or launch a replacement conversation.")


def queued_wake_binding(state, event):
    wake = state.get('idle_wake', {})
    turn = event.get('turn_id')
    if (wake.get('transport') != 'codex-queue' or wake.get('session') != session_ref(state['agent']) or
            event.get('hook_event_name') != 'UserPromptSubmit' or not isinstance(turn, str) or
            not turn.strip() or len(turn.encode()) > 256 or '\0' in turn or
            event.get('prompt') != wake_message(wake)):
        return {}
    return dict(delivery_id=wake['delivery_id'], native_turn_id=turn)


def channel_wake_binding(state, event, joined=False):
    """Bind one channel-delivered wake to its namespaced Claude prompt ownership.

    The wake message embeds this session's agent/execution/delivery UUIDs, so
    only the exact channel rendering of this pending wake can claim it; the
    key is Claude's stable prompt_id, never the display turn. A prompt_id the
    session already observed cannot be claimed onto: while a prompt is active,
    Claude injects further channel messages into that same prompt, so a wake
    joining it never creates a fresh request boundary.
    """
    wake = state.get('idle_wake', {})
    prompt = event.get('prompt')
    prompt_id = event.get('prompt_id')
    if (wake.get('transport') != 'claude-channel' or wake.get('session') != session_ref(state['agent']) or
            event.get('hook_event_name') != 'UserPromptSubmit' or joined or
            not isinstance(prompt, str) or
            not prompt.startswith('<channel') or wake_message(wake) not in prompt or
            not isinstance(prompt_id, str) or not prompt_id.strip() or
            len(prompt_id.encode()) > 256 or '\0' in prompt_id):
        return {}
    return dict(delivery_id=wake['delivery_id'], native_turn_id='claude-channel:' + prompt_id)


def claude_prompt_admission(state, observation, event):
    """Record the first sighting and any joins of each native Claude prompt.

    Returns 'fresh' when this event starts a previously unseen prompt,
    'joined' when its prompt was already active — the observed native
    behavior in BOTH directions: a channel wake joining an owner prompt, and
    owner input joining a wake-owned prompt — and None otherwise. A joined
    channel wake is a durable refusal: its delivery stays pending and the
    cleared marker lets a later idle cycle retry it. A joined prompt is
    marked shared: first sighting never implies exclusivity, so any joined
    prompt stays outside future cancellation contracts.
    """
    key = observation.get('native_turn_id') or ''
    name = observation['event']
    if name == 'UserPromptSubmit' and key:
        active = state.get('active_prompt')
        if active and active.get('id') == key:
            wake = state.get('idle_wake', {})
            refusal = None
            if (wake.get('transport') == 'claude-channel' and
                    wake.get('session') == session_ref(state['agent']) and
                    isinstance(event.get('prompt'), str) and
                    event['prompt'].startswith('<channel') and
                    wake_message(wake) in event['prompt']):
                state.pop('idle_wake', None)
                refusal = wake['delivery_id']
            shared = dict(active, joined=True)
            if refusal is not None:
                shared['refused_delivery'] = refusal
            state['active_prompt'] = shared
            return dict(joined=True, refused_delivery=refusal)
        state['active_prompt'] = dict(id=key)
        return dict(joined=False, refused_delivery=None)
    if name == 'Stop' and key:
        active = state.get('active_prompt')
        if active and active.get('id') == key:
            state.pop('active_prompt', None)
    return None


def hermes_wake_binding(state, event):
    wake = state.get('idle_wake', {})
    turn = event.get('turn_id')
    session_id = event.get('session_id')
    if (wake.get('transport') != 'hermes-queue' or wake.get('session') != session_ref(state['agent']) or
            event.get('hook_event_name') != 'TurnStart' or not isinstance(turn, str) or
            not turn.strip() or len(turn.encode()) > 256 or '\0' in turn or
            event.get('prompt') != wake_message(wake)):
        return {}
    if session_id is not None:
        if not isinstance(session_id, str) or not session_id.strip():
            return {}
        if wake.get('native_id') and session_id != wake['native_id']:
            return {}
        if ':' in turn and not turn.startswith(session_id + ':'):
            return {}
    return dict(delivery_id=wake['delivery_id'], native_turn_id=turn)


def wake_binding(config, state, event, joined=False):
    if config['harness'] == 'codex':
        return queued_wake_binding(state, event)
    if config['harness'] == 'claude':
        return channel_wake_binding(state, event, joined)
    if config['harness'] == 'hermes':
        return hermes_wake_binding(state, event)
    return {}


def submit_idle_wake(config, path, prepared):
    # No session lock spans terminal submission: its prompt hook needs that lock.
    wake = prepared['wake']
    if not process_alive(prepared['process']):
        return
    text = wake_message(wake)
    queued_id = None
    started = None
    if wake.get('transport') == 'codex-queue':
        try:
            queue = queue_helper()
        except CoordinationError as exc:
            print(f"Cairn presence {config['binding']}: {exc}", file=sys.stderr)
            if wake.get('queued_submission_id'):
                return  # A retained submission exists; retry its start once the helper returns.
            drop_idle_wake(path, wake)  # Nothing was sent; a later cycle retries cleanly.
            return
        if wake.get('status') == 'queued' and wake.get('queued_submission_id'):
            # The add already committed; only this exact pending submission's start retries.
            try:
                started = queue.start(wake['endpoint'], prepared['process'], wake['native_id'],
                                      wake['queued_submission_id'], owner=wake.get('owner', 'process'))
            except queue.QueueUnavailable as exc:
                print(f"Cairn presence {config['binding']}: {exc}", file=sys.stderr)
                return
            if not started:
                return  # Still queued, busy or raced; a later idle cycle retries the same start.
        else:
            try:
                queued_id, started = queue.enqueue(wake['endpoint'], prepared['process'],
                                                   wake['native_id'], text, wake['delivery_id'],
                                                   owner=wake.get('owner', 'process'))
            except queue.QueueUnavailable as exc:
                # Nothing was sent; drop the marker so a later cycle can retry the add.
                print(f"Cairn presence {config['binding']}: {exc}", file=sys.stderr)
                drop_idle_wake(path, wake)
                return
            except queue.QueueError as exc:
                raise CoordinationError('WAKE_UNCERTAIN', str(exc)) from exc
    elif wake.get('transport') == 'claude-channel':
        try:
            channel = channel_helper()
        except CoordinationError as exc:
            print(f"Cairn presence {config['binding']}: {exc}", file=sys.stderr)
            drop_idle_wake(path, wake)  # Nothing was sent; a later cycle retries cleanly.
            return
        meta = dict(native_session_id=wake['native_id'], agent_id=wake['session']['agent_id'],
                    execution_id=wake['session']['execution_id'], delivery_id=wake['delivery_id'])
        try:
            channel.write(wake['endpoint'], wake['bridge'], wake['parent'], text, meta)
        except channel.ChannelUnavailable as exc:
            # Proven pre-send refusal; never a terminal prompt fallback.
            print(f"Cairn presence {config['binding']}: {exc}", file=sys.stderr)
            drop_idle_wake(path, wake)
            return
        except channel.ChannelError as exc:
            # Transport written is not processed: retain the uncertain marker,
            # never resend and never fall back to terminal submission.
            raise CoordinationError('WAKE_UNCERTAIN', str(exc)) from exc
        started = True
    elif wake.get('transport') == 'opencode-queue':
        try:
            import opencode_queue
        except ImportError as exc:
            raise CoordinationError('WAKE_UNAVAILABLE', f'opencode queue client is unavailable: {exc}') from exc
        try:
            queued_id, started = opencode_queue.enqueue(wake['endpoint'], prepared['process'],
                                                        wake['native_id'], text, wake['delivery_id'])
        except opencode_queue.QueueUnavailable as exc:
            print(f"Cairn presence {config['binding']}: {exc}", file=sys.stderr)
            drop_idle_wake(path, wake)
            return
        except opencode_queue.QueueError as exc:
            raise CoordinationError('WAKE_UNCERTAIN', str(exc)) from exc
    elif wake.get('transport') == 'hermes-queue':
        try:
            import hermes_queue
        except ImportError as exc:
            raise CoordinationError('WAKE_UNAVAILABLE', f'hermes queue client is unavailable: {exc}') from exc
        try:
            queued_id, started = hermes_queue.enqueue(wake['endpoint'], prepared['process'],
                                                      wake['native_id'], text, wake['delivery_id'],
                                                      request_id=wake.get('request_id'),
                                                      delivery_id=wake['delivery_id'])
        except hermes_queue.QueueUnavailable as exc:
            print(f"Cairn presence {config['binding']}: {exc}", file=sys.stderr)
            drop_idle_wake(path, wake)
            return
        except hermes_queue.QueueError as exc:
            raise CoordinationError('WAKE_UNCERTAIN', str(exc)) from exc
    elif wake.get('transport') == 'agy-queue':
        try:
            import agy_queue
        except ImportError as exc:
            raise CoordinationError('WAKE_UNAVAILABLE', f'agy queue client is unavailable: {exc}') from exc
        try:
            queued_id, started = agy_queue.enqueue(wake['endpoint'], prepared['process'],
                                                   wake['native_id'], text, wake['delivery_id'])
        except agy_queue.QueueUnavailable as exc:
            print(f"Cairn presence {config['binding']}: {exc}", file=sys.stderr)
            drop_idle_wake(path, wake)
            return
        except agy_queue.QueueError as exc:
            raise CoordinationError('WAKE_UNCERTAIN', str(exc)) from exc
    else:
        response = herdr_call(config, prepared['environment'], 'agent', 'prompt', wake['target']['pane_id'], text)
        confirmed = response.get('agent', {})
        if (response.get('type') != 'agent_prompted' or
                any(confirmed.get(k) != wake['target'][k] for k in ('pane_id', 'terminal_id'))):
            raise CoordinationError('WAKE_UNCERTAIN', 'host did not confirm prompt submission; no automatic resend')
        started = True
    try:
        with session_lock(path):
            state = json.loads(path.read_text())
            if state.get('idle_wake') == wake:
                state['idle_wake']['status'] = 'submitted' if started else 'queued'
                if queued_id:
                    state['idle_wake']['queued_submission_id'] = queued_id
                write_state(path, state)
    except CoordinationError as exc:
        if exc.code != 'SESSION_BUSY':
            raise
        # A live hook owns this file. The retained marker already
        # suppresses retries, and native handling clears it when delivered.


def same_process(a, b):
    return all(a[k] == b[k] for k in ("pid", "start", "boot"))


def owner_process(config, event):
    """Plugins supply their own PID; command hooks find a named native ancestor."""
    declared = event.get("host_pid")
    if declared is not None and (type(declared) is not int or declared < 2):
        raise CoordinationError("INVALID_HOST", "invalid native process ID")
    pid = os.getppid()
    for _ in range(128):
        if pid < 2:
            break
        ref = process_reference(pid)
        name = Path(f"/proc/{pid}/comm").read_text().strip()
        if pid == declared or (declared is None and name in config.get("process_names", [])):
            return ref
        pid = ref["parent"]
    raise CoordinationError("INVALID_HOST", "native process is not an observed ancestor")


def normalize(config, event, event_name=None):
    harness = config["harness"]
    name = event_name or event.get("hook_event_name")
    if harness == "agy":
        native = event.get("conversationId")
        roots = event.get("workspacePaths")
        cwd = roots[0] if isinstance(roots, list) and roots else None
        model = event.get("modelName", "")
    else:
        native, cwd, model = event.get("session_id"), event.get("cwd"), event.get("model", "")
    if not isinstance(native, str) or not native.strip() or len(native) > 256 or any(ord(c) < 32 for c in native):
        raise CoordinationError("INVALID_HOST", "native conversation ID required")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute() or not Path(cwd).is_dir():
        raise CoordinationError("INVALID_HOST", "native workspace must be an existing absolute directory")
    if not isinstance(model, str) or len(model) > 256:
        raise CoordinationError("INVALID_HOST", "invalid observed model")
    phases = {"SessionStart": "start", "UserPromptSubmit": "busy", "PreInvocation": "busy",
              "TurnStart": "busy", "Stop": "idle", "TurnEnd": "idle", "SessionEnd": "leave",
              "Interrupt": "interrupted", "ProviderObservation": "provider"}
    if name not in phases:
        raise CoordinationError("INVALID_HOST", "unsupported coordination hook event")
    if name == "Interrupt" and harness != "codex":
        raise CoordinationError("INVALID_HOST", "interrupt observation is a Codex-only hook")
    phase = phases[name]
    if harness == "agy" and name == "Stop" and event.get("fullyIdle") is not True:
        phase = "busy"
    workspace = str(Path(cwd).resolve())
    root = Path(workspace)
    for parent in [root, *root.parents]:
        if (parent / ".git").exists():
            root = parent
            break
    # Codex and Hermes own turns by native turn ID. Claude owns a request by the
    # stable prompt_id of its channel submission; the display turn is separate.
    owner = ''
    if harness in ('codex', 'claude', 'hermes'):
        owner = event.get('prompt_id' if harness == 'claude' else 'turn_id', '')
        if not isinstance(owner, str) or len(owner.encode()) > 256 or '\0' in owner or any(ord(c) < 32 for c in owner):
            raise CoordinationError('INVALID_HOST', 'invalid native ownership key')
        if owner:
            owner = ('claude-channel:' + owner) if harness == 'claude' else owner
    return dict(native_id=native, workspace=workspace, project=root.name or workspace,
                observed_model=model, phase=phase, event=name, native_turn_id=owner)


def write_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
        pending = Path(file.name)
        try:
            json.dump(state, file, ensure_ascii=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
            file.close()
            pending.replace(path)
        finally:
            pending.unlink(missing_ok=True)


@contextmanager
def session_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(path.with_suffix(".lock")), os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "a") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CoordinationError("SESSION_BUSY", "another lifecycle operation is active") from exc
        yield


def call(config, operation, request, timeout=4, session=None):
    if not config.get("cairn") or not config.get("socket") or not config.get("token_file"):
        return {"attempt": None}
    command = [config["cairn"], "agent", "--socket", config["socket"],
               "--token-file", config["token_file"]]
    if session:
        command += ['--agent-id', session['agent_id'], '--execution-id', session['execution_id']]
    command.append(operation)
    result = subprocess.run(command, input=json.dumps(request), capture_output=True, text=True, timeout=timeout)
    try:
        response = json.loads(result.stdout)
    except ValueError as exc:
        raise CoordinationError("API_UNAVAILABLE", "Cairn did not return a JSON response") from exc
    if result.returncode or not response.get("ok"):
        raise CoordinationError(response.get("status", "API_UNAVAILABLE"), "Cairn refused the lifecycle operation")
    return response.get("data")


def session_ref(agent):
    return {k: agent[k] for k in ("agent_id", "execution_id")}


def registration(config, observation, metadata=None):
    return dict(request_id=str(uuid.uuid4()), repo=config["repo"], binding=config["binding"],
                native_session_id=observation["native_id"], metadata=metadata or dict(
                    harness=config["harness"], model=config.get("model", ""),
                    observed_model=observation["observed_model"], project=observation["project"],
                    workspace=observation["workspace"], state="idle" if observation["phase"] == "start" else observation["phase"],
                    delivery_mode="fresh-worker" if config.get('_wake') else "existing-session"))


def wake_context(config):
    path = os.environ.get('CAIRN_WAKE_CONTEXT')
    if not path:
        return None
    with Path(path).open('rb') as file:
        raw = file.read(32769)
    if len(raw) > 32768:
        raise CoordinationError('INVALID_HOST', 'wake context is oversized')
    wake = json.loads(raw)
    if not isinstance(wake, dict):
        raise CoordinationError('INVALID_HOST', 'wake context must be a JSON object')
    if wake.get('schema') != 'cairn.wake-context/1' or not wake.get('native_registration'):
        return None  # An older supervisor does not support the association.
    if wake['native_registration'] is not True or any(not isinstance(wake.get(key), str)
            for key in ('attempt_id', 'collection', 'socket', 'token_file')):
        raise CoordinationError('INVALID_HOST', 'wake association needs typed identifiers and paths')
    uuid.UUID(wake['attempt_id'])
    if wake['collection'] != config['repo'] or wake['socket'] != config['socket']:
        raise CoordinationError('INVALID_HOST', 'wake context and native profile must share API and collection')
    if not Path(wake['token_file']).is_absolute():
        raise CoordinationError('INVALID_HOST', 'wake profile path must be absolute')
    return dict(wake, path=path)


def associate_wake(config, state, path, observation):
    wake = config.get('_wake')
    if not wake:
        return
    ref = session_ref(state['agent'])
    request = state.get('wake_link')
    if not request or request['attempt_id'] != wake['attempt_id'] or request['session'] != ref:
        request = dict(request_id=str(uuid.uuid4()), attempt_id=wake['attempt_id'], operation='session', session=ref)
        state['wake_link'] = request
        write_state(path, state)
    # The original slot owns the delivery; the native profile owns the continuing
    # conversation. Linking these reports does not move either inbox.
    call(dict(config, token_file=wake['token_file']), 'wake-change', request)
    context = {k: v for k, v in wake.items() if k != 'path'}
    context.update(session=ref, native_session_id=observation['native_id'], session_inbox=state['agent']['inbox'])
    write_state(Path(wake['path']), context)


def observe_provider(config, wake, event):
    if not wake or not wake.get('provider_observation_file'):
        return  # Interactive sessions and older supervisors have no worker health.
    target = Path(wake['provider_observation_file'])
    expected = Path(wake['path']).parent / (wake['attempt_id'] + '.provider.json')
    if target != expected or wake.get('provider_harness') != config['harness']:
        raise CoordinationError('INVALID_HOST', 'provider observation differs from wake binding')
    failure = event.get('provider_failure')
    if failure is not None:
        if (not isinstance(failure, dict) or set(failure) != {'harness', 'source', 'kind', 'code', 'status'} or
                failure['harness'] != config['harness'] or failure['source'] != 'native-hook' or
                failure['kind'] not in ('quota', 'rate_limit', 'billing') or
                not isinstance(failure['code'], str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', failure['code']) or
                type(failure['status']) is not int or failure['status'] != 0 and not 100 <= failure['status'] <= 599):
            raise CoordinationError('INVALID_HOST', 'invalid selected provider failure')
    write_state(target, dict(schema='cairn.provider-observation/1', attempt_id=wake['attempt_id'],
                            harness=config['harness'], failure=failure))
    # Retain the directory entry as well as the atomic file contents.
    fd = os.open(str(target.parent), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def state_path(config, native):
    key = hashlib.sha256((config["binding"] + "\0" + native).encode()).hexdigest()
    return Path(config["state_dir"]) / (key + ".json")


def heartbeat(config, state):
    return call(config, "agent-heartbeat", session_ref(state["agent"]))


def recover_inbox(config, state, path):
    if state.get('inbox_intent') and not state.get('inbox_attempt'):
        result = call(config, 'session-inbox-claim', state['inbox_intent'])
        if result['attempt']:
            state['inbox_attempt'] = result['attempt']
        else:
            state.pop('inbox_intent')
        write_state(path, state)


def release_inbox(config, state, path, reason, fenced=False):
    if not fenced:
        recover_inbox(config, state, path)
    intent = state.get('inbox_intent')
    if not intent:
        return True
    request = dict(session=intent['session'], attempt_id=intent['request_id'], reason=reason)
    saved = state.get('inbox_close', {})
    if any(saved.get(k) != v for k, v in request.items()):
        state['inbox_close'] = dict(request_id=str(uuid.uuid4()), **request)
        write_state(path, state)
    try:
        call(config, 'session-inbox-reconcile', state['inbox_close'])
    except CoordinationError as exc:
        if exc.code == 'DELIVERY_ACTIVE' and reason == 'delivery_completed':
            return False
        # Leaving or a restore fence prevents a delayed claim from committing
        # after an unknown attempt was found absent.
        if not (fenced and exc.code == 'NOT_FOUND'):
            raise
    for key in ('inbox_intent', 'inbox_attempt', 'inbox_close', 'inbox_completion', 'inbox_response'):
        state.pop(key, None)
    write_state(path, state)
    return True


def watch_inbox(config, state, path):
    if not state.get('inbox_intent') or release_inbox(config, state, path, 'delivery_completed'):
        return
    attempt = state['inbox_attempt']
    delivery = attempt['delivery']
    attempt['delivery'] = call(config, 'event-renew', dict(delivery_id=delivery['delivery_id'],
        lease_id=delivery['lease_id'], lease_seconds=90), session=attempt['session'])
    write_state(path, state)


def inbox_context(config, state, path, observation, wake_binding=None):
    if not config.get('native_delivery'):
        return ''
    owner_turn = state.get('inbox_intent', {}).get('native_turn_id')
    if owner_turn and owner_turn != observation.get('native_turn_id'):
        raise CoordinationError('NATIVE_TURN_MISMATCH', 'another native turn cannot take over or end this request')
    if (not state.get('inbox_intent') and config.get('idle_wakeup') and
            (codex_queue_endpoint(config, state['process']) or
             hermes_queue_endpoint(config, state['process']) or
             (config['harness'] == 'claude' and config.get('claude_channel_dir'))) and not wake_binding):
        return ''  # This native wake transport owns admission; other prompts do not claim work.
    if observation['event'] == 'Stop' and observation['phase'] != 'idle':
        return ''  # Agy still has active background work.
    if observation['phase'] == 'idle':
        if state.get('delivered_since_idle') or state.get('inbox_intent'):
            release_inbox(config, state, path, 'turn_ended')
            state['delivered_since_idle'] = False
            write_state(path, state)
            return ''
        if config['harness'] not in ('codex', 'claude', 'hermes', 'agy'):
            return ''  # These adapters next deliver at their pre-turn boundary.
    recover_inbox(config, state, path)
    if not state.get('inbox_attempt'):
        if state.get('delivered_since_idle'):
            return ''
        state['inbox_intent'] = dict(request_id=str(uuid.uuid4()), session=session_ref(state['agent']), **(wake_binding or {}))
        write_state(path, state)
        recover_inbox(config, state, path)
    attempt = state.get('inbox_attempt')
    if not attempt:
        return ''
    if attempt.get('finished_at'):
        release_inbox(config, state, path, 'delivery_completed')
        return ''
    # Reconcile a completed message before reinjecting context at another model
    # call in the same turn. Do not claim a second message until the next turn.
    if release_inbox(config, state, path, 'delivery_completed'):
        return ''
    delivery = attempt['delivery']
    delivery = call(config, 'event-renew', dict(delivery_id=delivery['delivery_id'],
        lease_id=delivery['lease_id'], lease_seconds=90), session=attempt['session'])
    attempt['delivery'] = delivery
    event = delivery['event']
    if not state.get('inbox_completion'):
        state['inbox_completion'] = str(uuid.uuid4())
    if not state.get('inbox_response'):
        state['inbox_response'] = str(uuid.uuid4())
    common = ['--socket', config['socket'], '--token-file', config['token_file'],
              '--agent-id', attempt['session']['agent_id'], '--execution-id', attempt['session']['execution_id'],
              '--request-id', state['inbox_completion'], '--lease', delivery['lease_id']]
    context = dict(schema='cairn.session-inbox/1', **attempt['session'], attempt_id=attempt['attempt_id'],
        event_id=event['event_id'], kind=event['kind'], sender=event['from'], source=event['ref'],
        delivery_id=delivery['delivery_id'], lease_id=delivery['lease_id'],
        read=[config['cairn'], 'agent', '--socket', config['socket'], '--token-file', config['token_file'], 'history'],
        read_input=event['ref'], completion=[config['cairn'], 'complete', *common, '--shareable', '--stdin', delivery['delivery_id']],
        acknowledgement=[config['cairn'], 'ack', *common, delivery['delivery_id']])
    if attempt.get('native_turn_id'):
        context['native_turn_id'] = attempt['native_turn_id']
    if event['kind'] == 'request':
        context['response'] = [config['cairn'], 'publish', '--socket', config['socket'], '--token-file', config['token_file'],
            '--agent-id', attempt['session']['agent_id'], '--execution-id', attempt['session']['execution_id'],
            '--request-id', state['inbox_response'], '--to', event['from'], '--kind', 'response', '--causation-id', event['event_id']]
        if event.get('correlation_id'):
            context['response'] += ['--correlation-id', event['correlation_id']]
    target = Path(config['state_dir']) / 'inbox' / (attempt['attempt_id'] + '.json')
    write_state(target, context)
    state['delivered_since_idle'] = True
    state.pop('idle_wake', None)
    write_state(path, state)
    return (f"Cairn has a {event['kind']} from {event['from']} for this conversation. "
        f"Read the structured inbox context at {target}. Read its exact selected source with the read argv and read_input JSON. "
        "For a request, handle it and run completion with a concise selected result on stdin; "
        "then use the response argv with --version RESULT_VERSION RESULT_RECORD_UUID from completion's result reference. "
        "That command preserves this conversation's UUID, sender and causation; do not substitute the shared profile name. "
        "For a response or notice, read it and run acknowledgement; do not start a request worker or recursively reply. "
        "The host renews this lease while the turn is active. Do not start a second inbox consumer. "
        "An ended turn without explicit completion is recorded as failed, not replayed automatically. "
        f"Completion command: {shlex.join(context['completion'])}")


def finish_presence(config, state, path, timeout=4, reason='process_exited'):
    # Persist the logical end before contacting the API. A long-lived gateway
    # process must not cause its finished conversation to be revived on recovery.
    state['ending'] = True
    state.setdefault('ending_reason', reason)
    write_state(path, state)
    if not state.get('agent'):
        state['agent'] = call(config, 'agent-register', state['registration'], timeout=timeout)
        write_state(path, state)
    try:
        call(config, 'agent-leave', session_ref(state['agent']), timeout=timeout)
    except CoordinationError as exc:
        if exc.code not in ('STALE_SESSION', 'NOT_FOUND'):
            raise
    release_inbox(config, state, path, state['ending_reason'], fenced=True)
    state['retired'] = True
    write_state(path, state)


def coordination_excluded(workspace):
    root = Path(workspace)
    return any((p / name).exists() for p in [root, *root.parents]
               for name in ('.cairn-no-memory', '.cairn-no-coordination'))


def handle(config, event, event_name=None):
    if os.environ.get('CAIRN_COORDINATION_DISABLED') == '1':
        return {}
    if config.get("config_home") and config["harness"] in ("codex", "claude"):
        variable = "CODEX_HOME" if config["harness"] == "codex" else "CLAUDE_CONFIG_DIR"
        active_home = Path(os.environ.get(variable, str(Path.home() / ("." + config["harness"])))).resolve()
        if active_home != Path(config["config_home"]).resolve():
            return {}  # Merged config layers can include another account's hooks.
    wake = wake_context(config)
    if not wake and (any(os.environ.get(k) == "1" for k in ("CAIRN_LIFECYCLE_DISABLED", "CAIRN_LIFECYCLE_CHILD")) or os.environ.get("CAIRN_WAKE_CONTEXT")):
        return {}
    if wake:
        config = dict(config, _wake=wake, native_delivery=False)
    observation = normalize(config, event, event_name)
    if wake and wake.get('native_session_id') not in (None, observation['native_id']):
        return {}  # A child conversation must not inherit its parent's wake.
    if coordination_excluded(observation['workspace']):
        return {}
    process = owner_process(config, event)
    if observation['phase'] == 'provider':
        observe_provider(config, wake, event)
        return {}
    path = state_path(config, observation["native_id"])
    with session_lock(path):
        state = json.loads(path.read_text()) if path.exists() else {}
        if state.get('ending') and not state.get('retired'):
            finish_presence(config, state, path)
        if state and not same_process(process, state['process']) and not process_alive(state['process']) and not state.get('retired'):
            finish_presence(config, state, path)
        if state and not same_process(process, state["process"]) and process_alive(state["process"]) and not state.get("retired"):
            raise CoordinationError("SESSION_BUSY", "this conversation is associated with another live process")
        if observation["phase"] == "leave":
            if state and same_process(process, state["process"]) and not state.get('retired'):
                finish_presence(config, state, path, timeout=1.5, reason='turn_ended')
            return {}
        if not state or state.get("retired") or not same_process(process, state["process"]):
            retained = None
            if state.get("agent"):
                page = call(config, "agent-directory", dict(repo=config["repo"], agent_id=state["agent"]["agent_id"], include_offline=True))
                if page["agents"]:
                    retained = dict(page["agents"][0]["metadata"])
                    retained.update(harness=config["harness"], model=config.get("model", ""))
                    retained['delivery_mode'] = 'fresh-worker' if wake else 'existing-session'
                    if retained["workspace"] != observation["workspace"]:
                        retained.update(project=observation["project"], workspace=observation["workspace"])
                        retained.pop("project_aliases", None)
                    if observation["observed_model"]:
                        retained["observed_model"] = observation["observed_model"]
                    retained["state"] = "idle" if observation["phase"] == "start" else observation["phase"]
            state = dict(schema="cairn.native-session/1", process=process,
                         registration=registration(config, observation, retained), workspace=observation["workspace"])
            # Retain the retry UUID before the API call, including across crashes.
            write_state(path, state)
        if not state.get("agent"):
            state["agent"] = call(config, "agent-register", state["registration"])
            write_state(path, state)
        else:
            try:
                state["agent"] = heartbeat(config, state)
            except CoordinationError as exc:
                if exc.code != "STALE_SESSION":
                    raise
                page = call(config, "agent-directory", dict(repo=config["repo"], agent_id=state["agent"]["agent_id"], include_offline=True))
                current = page["agents"][0] if page["agents"] else None
                # Restore can invalidate an otherwise unchanged execution. A
                # real hook may resume it; a watcher never replaces executions.
                if current is None or current["execution_id"] != state["agent"]["execution_id"] or current["stopped"]:
                    raise
                if state.get('inbox_intent'):
                    if observation['phase'] != 'idle':
                        raise
                    release_inbox(config, state, path, 'turn_ended', fenced=True)
                state["registration"] = registration(config, observation, current["metadata"])
                state.pop("agent")
                write_state(path, state)
                state["agent"] = call(config, "agent-register", state["registration"])
                write_state(path, state)
        current = state["agent"]
        metadata = dict(current["metadata"])
        metadata['delivery_mode'] = 'fresh-worker' if wake else 'existing-session'
        if observation["workspace"] != state["workspace"]:
            metadata.update(project=observation["project"], workspace=observation["workspace"])
            metadata.pop("project_aliases", None)
        if observation["observed_model"]:
            metadata["observed_model"] = observation["observed_model"]
        if observation["phase"] in ("busy", "idle", "interrupted"):
            # An interrupted Codex turn is actually idle for presence, but it
            # proves nothing about tool cleanup or turn-end reconciliation.
            metadata["state"] = "busy" if observation["phase"] == "busy" else "idle"
        if metadata != current["metadata"]:
            state["agent"] = call(config, "agent-context", dict(request_id=str(uuid.uuid4()),
                session=session_ref(current), expected_revision=current["context_revision"], metadata=metadata))
        state["workspace"] = observation["workspace"]
        write_state(path, state)
        associate_wake(config, state, path, observation)
        # Interrupt only restores actual presence; it must not claim, release
        # or reconcile inbox work, whose interrupted tools can still be running.
        inbox = ''
        joined = False
        if config['harness'] == 'claude':
            admission = claude_prompt_admission(state, observation, event) or {}
            write_state(path, state)
            joined = bool(admission.get('joined'))
            if admission.get('refused_delivery'):
                # The durable refusal is already persisted above; reject just
                # the joined channel message natively before it reaches the
                # model. The owner prompt and any other hook output continue.
                raise NativePromptRefused('joined channel wake rejected; a fresh copy retries when idle')
        if observation["phase"] != "interrupted":
            inbox = inbox_context(config, state, path, observation, wake_binding(config, state, event, joined))
        if observation["phase"] == "idle":
            if inbox:
                current = state['agent']
                state['agent'] = call(config, 'agent-context', dict(request_id=str(uuid.uuid4()), session=session_ref(current),
                    expected_revision=current['context_revision'], metadata=dict(current['metadata'], state='busy')))
                write_state(path, state)
                return dict(decision='continue' if config['harness'] == 'agy' else 'block', reason=inbox)
            return {}
        agent = state["agent"]
        message = (f"Cairn session {agent['display_name']} has inbox {agent['inbox']}. "
                   f"Use agent UUID {agent['agent_id']} and execution UUID {agent['execution_id']} "
                   f"with the existing profile token at {config['token_file']} for session inbox work. "
                   "Use cairn agents context to maintain a concise selected task description; "
                   "harness/model/project are metadata. Ordinary memory keeps its existing profile. "
                   "Presence is maintained by the host watcher. "
                   + ("Inbox delivery uses supported turn boundaries. " if config.get('native_delivery') else "Native message delivery is not enabled. "))
        if wake:
            message += ("This conversation is linked to the current fresh-worker attempt. "
                        "Complete its original slot delivery using CAIRN_WAKE_CONTEXT; do not claim a second inbox. ")
        if inbox:
            message += '\n' + inbox
        if config["harness"] == "agy":
            return {"injectSteps": [{"ephemeralMessage": message}]}
        return {"hookSpecificOutput": {"hookEventName": observation["event"], "additionalContext": message}}


def watch_once(config):
    for path in sorted(Path(config["state_dir"]).glob("*.json")):
        try:
            prepared = None
            with session_lock(path):
                state = json.loads(path.read_text())
                if state.get("retired"):
                    continue
                if state.get('ending'):
                    finish_presence(config, state, path)
                elif process_alive(state["process"]):
                    if not state.get("agent"):
                        state["agent"] = call(config, "agent-register", state["registration"])
                    else:
                        state["agent"] = heartbeat(config, state)
                        watch_inbox(config, state, path)
                        prepared = prepare_idle_wake(config, state, path)
                elif not state.get("agent"):
                    # An uncertain registration may have committed; without an
                    # observed response it expires naturally within 90 seconds.
                    state["retired"] = True
                else:
                    finish_presence(config, state, path)
                write_state(path, state)
            if prepared:
                submit_idle_wake(config, path, prepared)
        except CoordinationError as exc:
            if exc.code == "SESSION_BUSY":
                continue  # The hook owns presence until it releases this lock.
            print(f"Cairn presence {config['binding']}: {exc}", file=sys.stderr)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            print(f"Cairn presence {config['binding']}: {type(exc).__name__}; presence may expire", file=sys.stderr)


def validate_config(config):
    for key in ("cairn", "socket", "token_file", "state_dir"):
        if not isinstance(config.get(key), str) or not Path(config[key]).is_absolute():
            raise CoordinationError("INVALID_CONFIG", f"absolute {key} required")
    if config.get("harness") not in ("codex", "claude", "agy", "opencode", "hermes") or not config.get("repo") or not config.get("binding"):
        raise CoordinationError("INVALID_CONFIG", "harness, collection and binding required")
    binding = config['binding']
    if config.get('idle_wakeup') is not None:
        if not isinstance(config['idle_wakeup'], str) or not Path(config['idle_wakeup']).is_absolute() or not config.get('native_delivery'):
            raise CoordinationError('INVALID_CONFIG', 'idle_wakeup requires an absolute Herdr executable and native_delivery')
    if not isinstance(binding, str) or len(binding) > 128 or binding in ('.', '..') or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-' for c in binding):
        raise CoordinationError("INVALID_CONFIG", "invalid binding name")
    return config


def load_config(path):
    return validate_config(json.loads(path.read_text()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("hook", "watch"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--event")
    parser.add_argument("--config-dir", type=Path)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    try:
        if args.mode == "hook":
            if args.config is None:
                parser.error("hook requires --config")
            raw = sys.stdin.buffer.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise CoordinationError("INVALID_HOST", "hook input exceeds limit")
            print(json.dumps(handle(load_config(args.config), json.loads(raw), args.event)))
        else:
            if args.config_dir is None:
                parser.error("watch requires --config-dir")
            stop = threading.Event()
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
            signal.signal(signal.SIGINT, lambda *_: stop.set())
            with session_lock(args.config_dir / ".watch"):
                while not stop.is_set():
                    started = time.monotonic()
                    for path in sorted(args.config_dir.glob("*.json")):
                        watch_once(load_config(path))
                    if args.once:
                        break
                    stop.wait(max(1, 30 - (time.monotonic() - started)))
    except CoordinationError as exc:
        if exc.code == 'NATIVE_PROMPT_REFUSED':
            print(f"Cairn coordination: {exc}; channel text rejected, owner prompt continues", file=sys.stderr)
            return 2  # Claude erases only the joined channel message.
        print(f"Cairn coordination: {exc}; session presence unavailable", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f"Cairn coordination: {type(exc).__name__}; session presence unavailable", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
