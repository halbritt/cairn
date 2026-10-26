#!/usr/bin/env python3
"""Offline JSONL transcript-proof recovery verifiers and helpers for lifecycle coordination."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time


class TranscriptRecoveryError(Exception):
    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code


CLAUDE_NON_TURN_RECORDS = (
    'last-prompt', 'cost-state', 'ai-title', 'agent-name', 'custom-title', 'summary',
    'artifact-comment-monitor', 'artifact-autoreact-ledger',
)
CLAUDE_TRANSCRIPT_LIMIT = 256 * 1024 * 1024
NATIVE_REFUSAL_MESSAGE = (
    'Cairn coordination: NATIVE_PROMPT_REFUSED: bound inbox recall unavailable; '
    'no source or memory grant delivered; channel text rejected, owner prompt continues'
)
NATIVE_RECORD_LIMIT = 65536
NATIVE_REFUSAL_KEYS = frozenset((
    'parentUuid', 'isSidechain', 'type', 'subtype', 'content', 'isMeta', 'timestamp', 'uuid',
    'level', 'preventContinuation', 'userType', 'entrypoint', 'cwd', 'sessionId', 'version',
    'gitBranch', 'slug',
))
NATIVE_HOOK_TIMEOUT_SECONDS = 15
CODEX_ENDED_TURN_REGION_LIMIT = 8 * 1024 * 1024
CODEX_ENDED_TURN_TAIL_LIMIT = 64 * 1024 * 1024
CODEX_ERROR_SCAN_LIMIT = 8 * 1024 * 1024
UUID_TEXT = r'[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}'
CODEX_QUEUE_OUTCOMES = ('admitted_active', 'completed', 'cancelled', 'never_admitted', 'unknown')


def _err(coord, code, message):
    err_cls = getattr(coord, 'CoordinationError', TranscriptRecoveryError) if coord is not None else TranscriptRecoveryError
    return err_cls(code, message)


def native_locator(text, *, coord=None):
    """OFFSET:LENGTH:SHA256 for one bounded record; nothing else is read."""
    match = re.fullmatch(r'(0|[1-9][0-9]{0,15}):([1-9][0-9]{0,5}):([0-9a-f]{64})', text or '')
    if not match or int(match[2]) > NATIVE_RECORD_LIMIT:
        raise _err(coord, 'INVALID_REQUEST', 'native record locator must be OFFSET:LENGTH:SHA256 within bounds')
    return dict(offset=int(match[1]), length=int(match[2]), sha256=match[3])


def claude_transcript_path(config, state, *, coord=None):
    """Derive the session file from the trusted binding and held state; never from the caller."""
    home, workspace, native = config.get('config_home'), state.get('workspace'), state['agent']['native_session_id']
    if (not isinstance(home, str) or not Path(home).is_absolute() or not isinstance(workspace, str)
            or not Path(workspace).is_absolute() or not re.fullmatch(r'[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}', native)):
        raise _err(coord, 'INVALID_REQUEST', 'native session file cannot be derived from the binding')
    return Path(home) / 'projects' / re.sub(r'[^A-Za-z0-9]', '-', workspace) / (native + '.jsonl')


def read_native_record(path, locator, *, coord=None):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or locator['offset'] + locator['length'] > info.st_size:
            raise _err(coord, 'INVALID_REQUEST', 'native session file is not a readable owner record source')
        raw = os.pread(fd, locator['length'], locator['offset'])
    finally:
        os.close(fd)
    if (len(raw) != locator['length'] or hashlib.sha256(raw).hexdigest() != locator['sha256']
            or not raw.endswith(b'\n') or b'\n' in raw[:-1]):
        raise _err(coord, 'INVALID_REQUEST', 'native record bytes differ from their pin or are not one record')
    return strict_native_line(raw, coord=coord)


def native_time(value, *, coord=None):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z', value):
        raise _err(coord, 'INVALID_REQUEST', 'native record timestamp shape is unknown')
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def strict_native_line(raw, *, coord=None):
    def pairs(items):
        keys = [k for k, _ in items]
        if len(keys) != len(set(keys)):
            raise ValueError('duplicate key')
        return dict(items)
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs)
    except (UnicodeDecodeError, ValueError) as exc:
        raise _err(coord, 'INVALID_REQUEST', 'native record is not strict JSON') from exc
    if not isinstance(value, dict):
        raise _err(coord, 'INVALID_REQUEST', 'native record is not an object')
    return value


def last_jsonl_record(path, window=65536):
    """The last complete JSON object of a JSON-lines file, or None."""
    try:
        with open(path, 'rb') as file:
            file.seek(0, os.SEEK_END)
            size = file.tell()
            file.seek(max(0, size - window))
            tail = file.read()
    except OSError:
        return None
    lines = [line for line in tail.split(b'\n') if line.strip()]
    if not lines or (size > window and len(lines) == 1):
        return None
    try:
        value = json.loads(lines[-1])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def daemon_start_claim_evidence(config, state, attempt, *, coord=None):
    """The transcript records no turn at or after the claim: only non-turn metadata follows the last turn."""
    path = claude_transcript_path(config, state, coord=coord)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > CLAUDE_TRANSCRIPT_LIMIT:
            raise _err(coord, 'INVALID_REQUEST', 'native transcript is not a readable bounded owner file')
        raw = os.pread(fd, info.st_size, 0)
    finally:
        os.close(fd)
    claimed = datetime.fromisoformat(attempt['created_at'].replace('Z', '+00:00'))
    records = []
    for line in raw.splitlines():
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise _err(coord, 'INVALID_REQUEST', 'native transcript has an unreadable record') from exc
        if not isinstance(record, dict):
            raise _err(coord, 'INVALID_REQUEST', 'native transcript has a non-object record')
        records.append(record)
    stamped = [i for i, r in enumerate(records) if isinstance(r.get('timestamp'), str)]
    if not stamped:
        raise _err(coord, 'INVALID_REQUEST', 'native transcript has no timestamped turn record')
    last = stamped[-1]
    if native_time(records[last]['timestamp'], coord=coord) >= claimed:
        raise _err(coord, 'INVALID_REQUEST', 'native transcript records activity at or after the claim')
    for record in records[last + 1:]:
        if record.get('type') not in CLAUDE_NON_TURN_RECORDS or 'timestamp' in record:
            raise _err(coord, 'INVALID_REQUEST', 'native transcript records a turn or unknown record after the claim')
    return dict(last_turn_record_at=records[last]['timestamp'], records_after=len(records) - last - 1,
                transcript_bytes=len(raw))


def recover_daemon_start_claim(config, native_session_id, attempt_id, *, check_only=False, coord=None):
    """Settle a start claim stranded on a Claude daemon-managed worker that never ran a turn (CAIRN-178)."""
    path = coord.state_path(config, native_session_id)
    with coord.session_lock(path):
        state = json.loads(path.read_text())
        attempt, intent = state.get('inbox_attempt') or {}, state.get('inbox_intent') or {}
        if (config.get('harness') != 'claude' or state.get('agent', {}).get('native_session_id') != native_session_id
                or state.get('ending') or state.get('retired') or attempt.get('attempt_id') != attempt_id
                or intent.get('request_id') != attempt_id or attempt.get('native_turn_id') or intent.get('native_turn_id')
                or attempt.get('session') != coord.session_ref(state['agent']) or attempt.get('finished_at')
                or attempt.get('cancel') or attempt.get('tools') or state.get('tool_calls') or state.get('active_prompt')
                or any(state.get(k) for k in ('inbox_close', 'inbox_turn_ended'))
                or not coord.attempt_commands_absent(config, attempt)):
            raise _err(coord, 'INVALID_REQUEST', 'held state is not an untouched turn-less start claim')
        try:
            daemon = coord.proc_view(state['process']['pid'])
        except (OSError, ValueError, IndexError) as exc:
            raise _err(coord, 'INVALID_REQUEST', 'bound process is not a live Claude daemon') from exc
        if not coord.process_alive(state['process']) or coord.supervisor_role(daemon) != 'daemon' \
                or not coord.same_process(daemon['ref'], state['process']):
            raise _err(coord, 'INVALID_REQUEST', 'bound process is not a live Claude daemon')
        worker = coord.find_daemon_worker(config, state)
        evidence = daemon_start_claim_evidence(config, state, attempt, coord=coord)
        proof = dict(schema='cairn.daemon-start-claim/1', daemon={k: state['process'][k] for k in ('pid', 'start', 'boot')},
                     worker={k: worker[k] for k in ('pid', 'start', 'boot')}, **evidence)
        if check_only:
            return dict(status='eligible', attempt_id=attempt_id, worker=proof['worker'], native_record=proof)
        state['daemon_start_claim'] = dict(proof, attempt_id=attempt_id, at=time.time(), settlement='pending')
        state['inbox_turn_ended'] = True
        state['delivered_since_idle'] = False
        state['process'] = worker
        coord.write_state(path, state)
        try:
            settled = coord.flush_inbox_intents(config, state) and coord.end_turn_inbox(config, state, path)
        except (coord.CoordinationError, OSError, ValueError, KeyError, TypeError):
            settled = False
        record = state['daemon_start_claim']
        record['settlement'] = (state.get('inbox_close') or {}).get('reason', 'turn_ended') if settled else 'turn_ended_pending'
        coord.write_state(path, state)
        return dict(record)


def recover_refused_wake(config, native_session_id, attempt_id, native_turn_id, *, coord=None):
    """Operator step for a refusal this host recorded but could not settle at the time."""
    path = coord.state_path(config, native_session_id)
    with coord.session_lock(path):
        state = json.loads(path.read_text())
        attempt = state.get('inbox_attempt') or {}
        recorded = state.get('wake_refusal_diagnostic') or {}
        if (state.get('agent', {}).get('native_session_id') != native_session_id
                or state.get('ending') or state.get('retired') or not coord.process_alive(state['process'])
                or attempt.get('attempt_id') != attempt_id
                or recorded.get('phase') != 'bridge_after_claim' or recorded.get('origin') != 'hook'
                or recorded.get('settlement') != 'held' or recorded.get('attempt_id') != attempt_id
                or recorded.get('native_turn_id') != native_turn_id
                or recorded.get('delivery_id') != (attempt.get('delivery') or {}).get('delivery_id')):
            raise _err(coord, 'INVALID_REQUEST', 'no host-recorded refusal names this held attempt and prompt')
        if not coord.refused_claude_wake_settleable(config, state, native_turn_id):
            raise _err(coord, 'INVALID_REQUEST', 'held attempt is not exactly this refused, unprocessed prompt')
        grant = coord.recall_grant_state(config, state, attempt)
        if grant != 'absent':
            raise _err(coord, 'INVALID_REQUEST', 'held attempt cannot be proven unprocessed (grant ' + grant + ')')
        state.pop('wake_refusal_diagnostic')
        return coord.settle_refused_claude_wake(
            config, state, path, native_turn_id,
            _err(coord, 'NATIVE_PROMPT_REFUSED', 'host-recorded refusal'),
            origin='operator_recorded', require_active=False,
        )


def native_refusal_proof(config, config_path, state, attempt, locators, *, coord=None):
    """Prove, from three pinned contiguous native records, that this held attempt's wake was refused after claim."""
    enqueue, dequeue, refusal = locators
    if (enqueue['offset'] + enqueue['length'] != dequeue['offset']
            or dequeue['offset'] + dequeue['length'] != refusal['offset']):
        raise _err(coord, 'INVALID_REQUEST', 'native records are not one contiguous enqueue, dequeue, refusal sequence')
    path = claude_transcript_path(config, state, coord=coord)
    records = [read_native_record(path, item, coord=coord) for item in locators]
    native, agent, delivery = state['agent']['native_session_id'], state['agent'], attempt['delivery']
    channels = [
        (f'<channel source="cairn-events" native_session_id="{native}" agent_id="{agent["agent_id"]}" '
         f'execution_id="{agent["execution_id"]}" delivery_id="{delivery["delivery_id"]}">\n' + text + '\n</channel>')
        for text in coord.wake_messages(dict(session=coord.session_ref(agent), delivery_id=delivery['delivery_id']))
    ]
    first, second, third = records
    channel = first.get('content') if first.get('content') in channels else None
    if (set(first) != {'type', 'operation', 'timestamp', 'sessionId', 'content'} or first['type'] != 'queue-operation'
            or first['operation'] != 'enqueue' or first['sessionId'] != native or channel is None):
        raise _err(coord, 'INVALID_REQUEST', 'native enqueue record is not this exact channel wake')
    if (set(second) != {'type', 'operation', 'timestamp', 'sessionId'} or second['type'] != 'queue-operation'
            or second['operation'] != 'dequeue' or second['sessionId'] != native):
        raise _err(coord, 'INVALID_REQUEST', 'native dequeue record shape is unknown')
    command = re.fullmatch(
        r'(/[^\s\]]+) (/[^\s\]]+/coordination\.py) hook --config (/[^\s\]]+)',
        third.get('content', '').partition('UserPromptSubmit operation blocked by hook:\n[')[2].partition(']: ')[0],
    )
    if (set(third) != NATIVE_REFUSAL_KEYS or third['type'] != 'system' or third['subtype'] != 'informational'
            or third['level'] != 'warning' or third['preventContinuation'] is not True or third['isSidechain'] is not False
            or third['isMeta'] is not False or third['sessionId'] != native or not command
            or Path(command[3]).resolve() != Path(config_path).resolve()
            or third['content'] != ('UserPromptSubmit operation blocked by hook:\n[' + command[0] + ']: ' + NATIVE_REFUSAL_MESSAGE
                                    + '\n\n\nOriginal prompt: ' + channel)):
        raise _err(coord, 'INVALID_REQUEST', 'native refusal record is not this binding\'s post-claim refusal of this wake')
    enqueued, dequeued, refused = (native_time(r['timestamp'], coord=coord) for r in records)
    try:
        claimed = datetime.fromisoformat(attempt['created_at'])
    except (KeyError, TypeError, ValueError) as exc:
        raise _err(coord, 'INVALID_REQUEST', 'held attempt has no claim time') from exc
    if (claimed.tzinfo is None or not enqueued <= dequeued <= claimed <= refused
            or (refused - enqueued).total_seconds() > NATIVE_HOOK_TIMEOUT_SECONDS
            or delivery.get('attempts') != 1 or delivery.get('state') != 'leased'):
        raise _err(coord, 'INVALID_REQUEST', 'native chronology does not bracket the single claim of this delivery')
    return dict(refusal_uuid=third['uuid'], refused_at=third['timestamp'], claude_version=third['version'],
                records=[dict(item) for item in locators])


def recover_native_refused_wake(config, config_path, native_session_id, attempt_id, locators, *, coord=None):
    """Operator step: settle an attempt stuck before the repair, from pinned native refusal records."""
    path = coord.state_path(config, native_session_id)
    with coord.session_lock(path):
        state = json.loads(path.read_text())
        attempt, intent = state.get('inbox_attempt') or {}, state.get('inbox_intent') or {}
        turn = attempt.get('native_turn_id')
        if (state.get('agent', {}).get('native_session_id') != native_session_id or state.get('ending') or state.get('retired')
                or not coord.process_alive(state['process']) or attempt.get('attempt_id') != attempt_id
                or not isinstance(turn, str) or not turn.startswith('claude-channel:') or intent.get('native_turn_id') != turn
                or (state.get('active_prompt') or {}).get('id') == turn):
            raise _err(coord, 'INVALID_REQUEST', 'held state does not name exactly one finished-owner claim for this attempt')
        proof = native_refusal_proof(config, config_path, state, attempt, locators, coord=coord)
        if not coord.refused_claude_wake_settleable(config, state, turn):
            raise _err(coord, 'INVALID_REQUEST', 'held attempt is not exactly this refused, unprocessed claim')
        grant = coord.recall_grant_state(config, state, attempt)
        if grant != 'absent':
            raise _err(coord, 'INVALID_REQUEST', 'held attempt cannot be proven unprocessed (grant ' + grant + ')')
        return coord.settle_refused_claude_wake(
            config, state, path, turn, _err(coord, 'NATIVE_PROMPT_REFUSED', 'native record'),
            origin='native_record', require_active=False, evidence=proof,
        )


def codex_rollout_path(config, state, *, coord=None):
    """Use the live owner's open rollout, never a caller-selected transcript."""
    home = config.get('config_home')
    native = state['agent']['native_session_id']
    if (not isinstance(home, str) or not Path(home).is_absolute()
            or not re.fullmatch(r'[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}', native)
            or not coord.process_alive(state['process'])):
        raise _err(coord, 'INVALID_REQUEST', 'Codex rollout owner is unavailable')
    root = (Path(home) / 'sessions').resolve()
    paths = set()
    for descriptor in Path(f"/proc/{state['process']['pid']}/fd").iterdir():
        try:
            target = Path(os.readlink(descriptor))
        except FileNotFoundError:
            continue
        if target.name.startswith('rollout-') and target.name.endswith('-' + native + '.jsonl'):
            if not target.is_absolute() or target.resolve() != target or not target.is_relative_to(root):
                raise _err(coord, 'INVALID_REQUEST', 'Codex rollout is outside its configured session directory')
            info = target.stat()
            opened = descriptor.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise _err(coord, 'INVALID_REQUEST', 'Codex rollout is not an owner-controlled regular file')
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise _err(coord, 'INVALID_REQUEST', 'Codex rollout path no longer names its open file')
            paths.add(target)
    if len(paths) != 1 or not coord.process_alive(state['process']):
        raise _err(coord, 'INVALID_REQUEST', 'Codex owner does not hold exactly one matching rollout')
    return paths.pop()


def latest_codex_turn_end(config, state, *, coord=None):
    """Pin the latest turn record in the live owner's open rollout."""
    rollout_fn = getattr(coord, 'codex_rollout_path', None)
    path = rollout_fn(config, state) if rollout_fn is not None else codex_rollout_path(config, state, coord=coord)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        size = os.fstat(fd).st_size
        base = max(0, size - CODEX_ERROR_SCAN_LIMIT)
        tail = os.pread(fd, size - base, base)
    finally:
        os.close(fd)
    end = tail.rfind(b'\n') + 1
    start = 0 if base == 0 else tail.find(b'\n') + 1
    position = end
    err_cls = getattr(coord, 'CoordinationError', TranscriptRecoveryError) if coord is not None else TranscriptRecoveryError
    while position > start:
        line_start = tail.rfind(b'\n', start, position - 1) + 1
        line_start = max(line_start, start)
        line = tail[line_start:position]
        position = line_start
        if b'"task_started"' not in line and b'"task_complete"' not in line:
            continue
        try:
            record = strict_native_line(line, coord=coord)
        except err_cls:
            continue
        payload = record.get('payload')
        if record.get('type') != 'event_msg' or not isinstance(payload, dict):
            continue
        if payload.get('type') == 'task_started':
            return None
        if payload.get('type') != 'task_complete':
            continue
        if len(line) > NATIVE_RECORD_LIMIT:
            raise _err(coord, 'INVALID_REQUEST', 'Codex turn end record exceeds its read limit')
        locator = dict(offset=base + line_start, length=len(line), sha256=hashlib.sha256(line).hexdigest())
        if read_native_record(path, locator, coord=coord) != record:
            raise _err(coord, 'INVALID_REQUEST', 'Codex turn end record changed while reading')
        return record, locator
    return None


def codex_refusal_proof(config, state, attempt, recorded, locators, *, coord=None):
    """Accept only a contiguous queued start, context, and empty completion for the refused turn."""
    start, context, end = locators
    if (start['offset'] + start['length'] != context['offset']
            or context['offset'] + context['length'] != end['offset']):
        raise _err(coord, 'INVALID_REQUEST', 'Codex refusal records are not contiguous')
    rollout_fn = getattr(coord, 'codex_rollout_path', None)
    path = rollout_fn(config, state) if rollout_fn is not None else codex_rollout_path(config, state, coord=coord)
    inode = (path.stat().st_dev, path.stat().st_ino)
    records = [read_native_record(path, item, coord=coord) for item in locators]
    turn = attempt['native_turn_id']
    ordinal = records[0].get('ordinal')
    if type(ordinal) is not int or ordinal < 0:
        raise _err(coord, 'INVALID_REQUEST', 'Codex record ordinal is unavailable')
    for index, (record, kind) in enumerate(zip(records, ('event_msg', 'turn_context', 'event_msg'))):
        payload = record.get('payload')
        if (set(record) != {'timestamp', 'ordinal', 'type', 'payload'} or record['type'] != kind
                or type(record['ordinal']) is not int or record['ordinal'] != ordinal + index
                or not isinstance(payload, dict) or payload.get('turn_id') != turn
                or payload.get('root_turn_id') != turn):
            raise _err(coord, 'INVALID_REQUEST', 'Codex native records do not identify the refused root turn')
    first, middle, last = [r['payload'] for r in records]
    attribution = first.get('turn_attribution')
    if (set(first) != {'type', 'turn_attribution', 'turn_id', 'root_turn_id', 'started_at',
                       'model_context_window', 'collaboration_mode_kind'}
            or first['type'] != 'task_started' or not isinstance(attribution, dict)
            or attribution != dict(turn_id=turn, turn_trigger='queue', parent_turn_id=None,
                                   initiating_agent_path=None, root_turn_id=turn)
            or set(last) != {'type', 'turn_id', 'root_turn_id', 'last_agent_message',
                             'started_at', 'completed_at', 'duration_ms'}
            or last['type'] != 'task_complete' or last['last_agent_message'] is not None
            or middle.get('cwd') != state['workspace']):
        raise _err(coord, 'INVALID_REQUEST', 'Codex records do not prove an empty completed queue turn')
    times = [native_time(r['timestamp'], coord=coord).timestamp() for r in records]
    claimed = datetime.fromisoformat(attempt['created_at'].replace('Z', '+00:00')).timestamp()
    refused = recorded.get('at')
    if (type(refused) not in (int, float) or not times[0] <= times[1] <= claimed <= refused <= times[2]
            or times[2] - times[0] > NATIVE_HOOK_TIMEOUT_SECONDS
            or type(first['started_at']) is not int or first['started_at'] != int(times[0])
            or last['started_at'] != first['started_at'] or type(last['completed_at']) is not int
            or last['completed_at'] != int(times[2]) or type(last['duration_ms']) is not int
            or not 0 <= last['duration_ms'] <= NATIVE_HOOK_TIMEOUT_SECONDS * 1000):
        raise _err(coord, 'INVALID_REQUEST', 'Codex refusal chronology does not bracket this claim')
    rechecked = rollout_fn(config, state) if rollout_fn is not None else codex_rollout_path(config, state, coord=coord)
    if rechecked != path or (path.stat().st_dev, path.stat().st_ino) != inode:
        raise _err(coord, 'INVALID_REQUEST', 'Codex rollout owner changed during verification')
    return dict(schema='cairn.codex-refused-turn/1', records=locators,
                native_session_id=state['agent']['native_session_id'], native_turn_id=turn)


def recover_codex_refused_wake(config, native_session_id, attempt_id, locators, *, check_only=False, coord=None):
    """Explicitly settle a proven ended Codex refusal; never claim, requeue, or replay it."""
    path = coord.state_path(config, native_session_id)
    with coord.session_lock(path):
        state = json.loads(path.read_text())
        attempt = state.get('inbox_attempt') or {}
        recorded = state.get('wake_refusal_diagnostic') or {}
        turn = attempt.get('native_turn_id')
        if (config.get('harness') != 'codex' or state.get('agent', {}).get('native_session_id') != native_session_id
                or state.get('ending') or state.get('retired') or not coord.process_alive(state['process'])
                or attempt.get('attempt_id') != attempt_id or not isinstance(turn, str)
                or not re.fullmatch(r'[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}', turn)
                or recorded.get('phase') != 'bridge_after_claim' or recorded.get('origin') != 'hook'
                or recorded.get('settlement') != 'held' or recorded.get('attempt_id') != attempt_id
                or recorded.get('native_turn_id') != turn
                or recorded.get('delivery_id') != attempt.get('delivery', {}).get('delivery_id')
                or not coord.refused_wake_unprocessed(config, state, turn)):
            raise _err(coord, 'INVALID_REQUEST', 'held state is not this unprocessed Codex hook refusal')
        grant_evidence = {}
        grant = coord.recall_grant_state(config, state, attempt)
        if grant == 'unknown' and coord.codex_pre_grant_ledger_absent(config, state, attempt, recorded):
            grant, grant_evidence = 'absent', dict(grant_evidence='pre_grant_refusal_without_ledger')
        if grant != 'absent':
            raise _err(coord, 'INVALID_REQUEST', 'Codex attempt has a present or unknown memory grant')
        proof = codex_refusal_proof(config, state, attempt, recorded, locators, coord=coord)
        if check_only:
            return dict(status='eligible', attempt_id=attempt_id, grant='absent', native_record=proof, **grant_evidence)
        state['wake_refusal_diagnostic'] = dict(recorded, settlement='turn_ended', grant='absent',
            recovery_origin='codex_native_records', native_record=proof, **grant_evidence)
        state['inbox_turn_ended'] = True
        state['delivered_since_idle'] = False
        coord.write_state(path, state)
        try:
            settled = coord.flush_inbox_intents(config, state) and coord.end_turn_inbox(config, state, path)
            if not settled:
                state['wake_refusal_diagnostic']['settlement'] = 'turn_ended_pending'
                coord.write_state(path, state)
        except (coord.CoordinationError, OSError, ValueError, KeyError, TypeError):
            state['wake_refusal_diagnostic']['settlement'] = 'turn_ended_pending'
            coord.write_state(path, state)
        return state['wake_refusal_diagnostic']


def codex_ended_turn_proof(config, state, attempt, locators, *, coord=None):
    """Prove from the live owner's open rollout that this attempt's queue turn ended on a provider error."""
    start, end = locators
    if end['offset'] < start['offset'] + start['length']:
        raise _err(coord, 'INVALID_REQUEST', 'Codex end record does not follow its start record')
    rollout_fn = getattr(coord, 'codex_rollout_path', None)
    path = rollout_fn(config, state) if rollout_fn is not None else codex_rollout_path(config, state, coord=coord)
    inode = (path.stat().st_dev, path.stat().st_ino)
    first = read_native_record(path, start, coord=coord)
    last = read_native_record(path, end, coord=coord)
    turn = attempt['native_turn_id']
    begin, stop = start['offset'], end['offset'] + end['length']
    if stop - begin > CODEX_ENDED_TURN_REGION_LIMIT:
        raise _err(coord, 'INVALID_REQUEST', 'Codex ended turn span exceeds its read limit')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        size = os.fstat(fd).st_size
        region = os.pread(fd, stop - begin, begin)
        if size - stop > CODEX_ENDED_TURN_TAIL_LIMIT:
            raise _err(coord, 'INVALID_REQUEST', 'Codex rollout tail exceeds its read limit')
        tail = os.pread(fd, size - stop, stop)
    finally:
        os.close(fd)
    if len(region) != stop - begin or not region.endswith(b'\n'):
        raise _err(coord, 'INVALID_REQUEST', 'Codex ended turn span changed while reading')
    records = [strict_native_line(line + b'\n', coord=coord) for line in region[:-1].split(b'\n')]
    if records[0] != first or records[-1] != last or len(records) < 2:
        raise _err(coord, 'INVALID_REQUEST', 'Codex ended turn span does not start and end at its pins')
    ordinal = first.get('ordinal')
    for index, record in enumerate(records):
        payload = record.get('payload')
        if (set(record) - {'metadata'} != {'timestamp', 'ordinal', 'type', 'payload'}
                or type(record['ordinal']) is not int or type(ordinal) is not int or record['ordinal'] != ordinal + index
                or not isinstance(payload, dict) or payload.get('turn_id', turn) != turn):
            raise _err(coord, 'INVALID_REQUEST', 'Codex ended turn span is not one contiguous turn')
        kind, inner = record['type'], payload.get('type')
        edge = index in (0, len(records) - 1)
        allowed = (
            (kind == 'event_msg' and edge and inner in ('task_started', 'task_complete'))
            or (kind == 'event_msg' and not edge and inner == 'token_count')
            or (kind == 'event_msg' and not edge and inner == 'item_completed'
                and isinstance(payload.get('item'), dict) and payload['item'].get('type') == 'UserMessage')
            or (kind in ('turn_context', 'world_state') and not edge)
            or (kind == 'response_item' and not edge and inner == 'message'
                and payload.get('role') in ('user', 'developer'))
        )
        if not allowed:
            raise _err(coord, 'INVALID_REQUEST', 'Codex turn shows tool, assistant or unknown activity')
    started, finished = first['payload'], last['payload']
    attribution = started.get('turn_attribution')
    error = finished.get('error')
    if (first['type'] != 'event_msg' or started.get('type') != 'task_started'
            or started.get('turn_id') != turn or started.get('root_turn_id') != turn
            or not isinstance(attribution, dict) or attribution.get('turn_id') != turn
            or attribution.get('turn_trigger') != 'queue'
            or last['type'] != 'event_msg' or finished.get('type') != 'task_complete'
            or finished.get('turn_id') != turn or finished.get('root_turn_id') != turn
            or finished.get('last_agent_message') is not None or not isinstance(error, dict)
            or not isinstance(error.get('codex_error_info'), str)
            or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', error['codex_error_info'])):
        raise _err(coord, 'INVALID_REQUEST', 'Codex records do not prove a queue turn ended on a provider error')
    if turn.encode() in tail:
        raise _err(coord, 'INVALID_REQUEST', 'Codex rollout records this turn again after its end')
    claimed = datetime.fromisoformat(attempt['created_at'].replace('Z', '+00:00')).timestamp()
    if not native_time(first['timestamp'], coord=coord).timestamp() <= claimed <= native_time(last['timestamp'], coord=coord).timestamp():
        raise _err(coord, 'INVALID_REQUEST', 'Codex turn chronology does not bracket this claim')
    rechecked = rollout_fn(config, state) if rollout_fn is not None else codex_rollout_path(config, state, coord=coord)
    if rechecked != path or (path.stat().st_dev, path.stat().st_ino) != inode:
        raise _err(coord, 'INVALID_REQUEST', 'Codex rollout owner changed during verification')
    return dict(
        schema='cairn.codex-ended-turn/1', records=locators,
        native_session_id=state['agent']['native_session_id'],
        native_turn_id=turn, codex_error_info=error['codex_error_info'], ended_at=last['timestamp'],
    )


def recover_codex_ended_turn(config, native_session_id, attempt_id, locators, *, check_only=False, coord=None):
    """Explicitly settle a held Codex attempt whose queue turn ended on a provider error (CAIRN-179)."""
    path = coord.state_path(config, native_session_id)
    with coord.session_lock(path):
        state = json.loads(path.read_text())
        attempt, intent = state.get('inbox_attempt') or {}, state.get('inbox_intent') or {}
        turn = attempt.get('native_turn_id')
        if (config.get('harness') != 'codex' or state.get('agent', {}).get('native_session_id') != native_session_id
                or state.get('ending') or state.get('retired') or not coord.process_alive(state['process'])
                or attempt.get('attempt_id') != attempt_id or not isinstance(turn, str)
                or not re.fullmatch(UUID_TEXT, turn) or intent.get('request_id') != attempt_id
                or intent.get('native_turn_id') != turn or attempt.get('session') != coord.session_ref(state['agent'])
                or attempt.get('finished_at') or attempt.get('cancel')
                or any(state.get(k) for k in ('inbox_close', 'inbox_turn_ended'))):
            raise _err(coord, 'INVALID_REQUEST', 'held state is not this unsettled Codex attempt')
        health = coord.probe_codex_native_health(config, state, force=True) or {}
        if health.get('status_type') not in ('systemError', 'idle'):
            raise _err(
                coord, 'INVALID_REQUEST',
                f"native Codex thread is not proven inactive ({health.get('status_type') or 'unavailable'})",
            )
        proof = codex_ended_turn_proof(config, state, attempt, locators, coord=coord)
        proof['thread_status'] = health['status_type']
        if check_only:
            return dict(status='eligible', attempt_id=attempt_id, native_record=proof)
        state['codex_ended_turn'] = dict(proof, attempt_id=attempt_id, at=time.time(), settlement='pending')
        state['inbox_turn_ended'] = True
        state['delivered_since_idle'] = False
        coord.write_state(path, state)
        try:
            settled = coord.flush_inbox_intents(config, state) and coord.end_turn_inbox(config, state, path)
        except (coord.CoordinationError, OSError, ValueError, KeyError, TypeError):
            settled = False
        record = state['codex_ended_turn']
        record['settlement'] = (state.get('inbox_close') or {}).get('reason', 'turn_ended') if settled else 'turn_ended_pending'
        coord.write_state(path, state)
        return dict(record, recovery_origin='codex_ended_turn')


def codex_queue_rollout_proof(config, state, wake, locators, *, coord=None):
    """Classify contiguous pinned open-rollout records for a retained Codex queue submission."""
    locators = [native_locator(item, coord=coord) if isinstance(item, str) else dict(item) for item in (locators or [])]
    if not locators:
        raise _err(coord, 'INVALID_REQUEST', 'Codex rollout proof requires at least one record locator')
    for prev, curr in zip(locators, locators[1:]):
        if prev['offset'] + prev['length'] != curr['offset']:
            raise _err(coord, 'INVALID_REQUEST', 'Codex rollout records are not contiguous')
    rollout_fn = getattr(coord, 'codex_rollout_path', None)
    path = rollout_fn(config, state) if rollout_fn is not None else codex_rollout_path(config, state, coord=coord)
    stat_before = path.stat()
    inode, size = (stat_before.st_dev, stat_before.st_ino), stat_before.st_size
    records = [read_native_record(path, item, coord=coord) for item in locators]
    ordinal = records[0].get('ordinal')
    if type(ordinal) is not int or ordinal < 0:
        raise _err(coord, 'INVALID_REQUEST', 'Codex record ordinal is unavailable')
    for index, record in enumerate(records):
        if (set(record) != {'timestamp', 'ordinal', 'type', 'payload'} or type(record['ordinal']) is not int
                or record['ordinal'] != ordinal + index or not isinstance(record['payload'], dict)):
            raise _err(coord, 'INVALID_REQUEST', 'Codex rollout record shape is invalid')
    times = [native_time(r['timestamp'], coord=coord).timestamp() for r in records]
    if any(a > b for a, b in zip(times, times[1:])):
        raise _err(coord, 'INVALID_REQUEST', 'Codex rollout record timestamps are not monotonic')
    attempted_at = wake.get('attempted_at')
    if not isinstance(attempted_at, (int, float)):
        raise _err(coord, 'INVALID_REQUEST', 'retained wake has no attempt timestamp')
    rechecked = rollout_fn(config, state) if rollout_fn is not None else codex_rollout_path(config, state, coord=coord)
    if rechecked != path or (path.stat().st_dev, path.stat().st_ino) != inode:
        raise _err(coord, 'INVALID_REQUEST', 'Codex rollout owner changed during verification')
    native_id = state['agent']['native_session_id']
    if len(records) == 3 and [r['type'] for r in records] == ['event_msg', 'turn_context', 'event_msg']:
        first, middle, last = [r['payload'] for r in records]
        turn = first.get('turn_id')
        attr = first.get('turn_attribution')
        if (isinstance(turn, str) and turn and first.get('type') == 'task_started'
                and isinstance(attr, dict) and attr.get('turn_trigger') == 'queue'
                and middle.get('turn_id') == turn and middle.get('cwd') == state['workspace']
                and last.get('turn_id') == turn and times[0] >= attempted_at):
            if last.get('type') == 'task_complete' and isinstance(last.get('last_agent_message'), str) and last['last_agent_message'].strip():
                return dict(schema='cairn.codex-queue-rollout/1', outcome='completed',
                            native_session_id=native_id, native_turn_id=turn, records=locators)
            if (last.get('type') == 'task_complete' and last.get('last_agent_message') is None) or last.get('type') == 'turn_aborted':
                return dict(schema='cairn.codex-queue-rollout/1', outcome='cancelled',
                            native_session_id=native_id, native_turn_id=turn, records=locators)
    first_payload, last_payload = records[0]['payload'], records[-1]['payload']
    if (first_payload.get('type') == 'task_started' and
            isinstance(first_payload.get('turn_attribution'), dict) and
            first_payload['turn_attribution'].get('turn_trigger') == 'queue' and
            times[0] >= attempted_at and
            all(r['payload'].get('type') not in ('task_complete', 'turn_aborted') for r in records)):
        return dict(schema='cairn.codex-queue-rollout/1', outcome='admitted_active',
                    native_session_id=native_id, native_turn_id=first_payload.get('turn_id'), records=locators)
    serialized = json.dumps(records)
    wake_msgs = coord.wake_messages(wake) if coord is not None and hasattr(coord, 'wake_messages') else ()
    markers = [m for m in (wake.get('delivery_id'), wake.get('queued_submission_id'), *wake_msgs) if m]
    queue_turns = [
        r for r in records
        if r['payload'].get('type') == 'task_started' and
        isinstance(r['payload'].get('turn_attribution'), dict) and
        r['payload']['turn_attribution'].get('turn_trigger') == 'queue' and
        native_time(r['timestamp'], coord=coord).timestamp() >= attempted_at
    ]
    if (locators[-1]['offset'] + locators[-1]['length'] == size and
            times[0] <= attempted_at <= times[-1] and not queue_turns and
            not any(marker in serialized for marker in markers) and
            last_payload.get('type') == 'task_complete'):
        return dict(schema='cairn.codex-queue-rollout/1', outcome='never_admitted',
                    native_session_id=native_id, records=locators)
    raise _err(coord, 'INVALID_REQUEST', 'Codex rollout records do not prove a supported queue outcome')


def find_codex_refusal_locators(config, state, turn, *, coord=None):
    """Locate the three contiguous rollout records of an empty refused Codex queue turn."""
    rollout_fn = getattr(coord, 'codex_rollout_path', None)
    path = rollout_fn(config, state) if rollout_fn is not None else codex_rollout_path(config, state, coord=coord)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 16 * 1024 * 1024:
            return None
        content = os.pread(fd, info.st_size, 0) if info.st_size else b''
    finally:
        os.close(fd)
    entries = []
    turn_indices = []
    offset = 0
    for raw in content.splitlines(keepends=True):
        length = len(raw)
        item = None
        if raw.endswith(b'\n') and length <= NATIVE_RECORD_LIMIT:
            try:
                parsed = json.loads(raw.decode('utf-8'))
            except (UnicodeDecodeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                item = (dict(offset=offset, length=length, sha256=hashlib.sha256(raw).hexdigest()), parsed)
                payload = parsed.get('payload')
                if isinstance(payload, dict) and (payload.get('turn_id') == turn or payload.get('root_turn_id') == turn):
                    turn_indices.append(len(entries))
        entries.append(item)
        offset += length
    if len(turn_indices) != 3 or turn_indices != [turn_indices[0], turn_indices[0] + 1, turn_indices[0] + 2]:
        return None
    return [entries[i][0] for i in turn_indices]


def auto_settle_codex_refused_wake(config, state, path, *, coord=None):
    """Automatically settle a held Codex post-claim refusal once its open rollout proves the turn ended empty."""
    attempt = state.get('inbox_attempt') or {}
    recorded = state.get('wake_refusal_diagnostic') or {}
    turn = attempt.get('native_turn_id')
    attempt_id = attempt.get('attempt_id')
    if (config.get('harness') != 'codex' or state.get('ending') or state.get('retired')
            or not coord.process_alive(state.get('process') or {})
            or not attempt_id or not isinstance(turn, str)
            or not re.fullmatch(r'[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}', turn)
            or recorded.get('phase') != 'bridge_after_claim' or recorded.get('origin') != 'hook'
            or recorded.get('settlement') != 'held' or recorded.get('attempt_id') != attempt_id
            or recorded.get('native_turn_id') != turn
            or recorded.get('delivery_id') != (attempt.get('delivery') or {}).get('delivery_id')
            or not coord.refused_wake_unprocessed(config, state, turn)):
        return False
    if coord.recall_grant_state(config, state, attempt) != 'absent':
        return False
    err_cls = getattr(coord, 'CoordinationError', TranscriptRecoveryError) if coord is not None else TranscriptRecoveryError
    try:
        find_fn = getattr(coord, 'find_codex_refusal_locators', None)
        locators = find_fn(config, state, turn) if find_fn is not None else find_codex_refusal_locators(config, state, turn, coord=coord)
        if not locators:
            return False
        proof_fn = getattr(coord, 'codex_refusal_proof', None)
        proof = proof_fn(config, state, attempt, recorded, locators) if proof_fn is not None else codex_refusal_proof(config, state, attempt, recorded, locators, coord=coord)
    except (err_cls, OSError, ValueError, KeyError, TypeError):
        return False
    state['wake_refusal_diagnostic'] = dict(recorded, settlement='turn_ended', grant='absent',
        recovery_origin='codex_native_records', native_record=proof)
    state['inbox_turn_ended'] = True
    state['delivered_since_idle'] = False
    coord.write_state(path, state)
    try:
        settled = coord.flush_inbox_intents(config, state) and coord.end_turn_inbox(config, state, path)
        if not settled:
            state['wake_refusal_diagnostic']['settlement'] = 'turn_ended_pending'
            coord.write_state(path, state)
    except (err_cls, OSError, ValueError, KeyError, TypeError):
        state['wake_refusal_diagnostic']['settlement'] = 'turn_ended_pending'
        coord.write_state(path, state)
    return True


def codex_turn_RECOVERY(mode, *args, coord=None, **kwargs):
    """Unified Codex offline transcript-proof recovery verifier."""
    if mode in ('refused_wake', 'codex_refused_wake', 'recover-codex-refused-wake'):
        return recover_codex_refused_wake(*args, coord=coord, **kwargs)
    if mode in ('refused_wake_proof', 'codex_refusal_proof'):
        return codex_refusal_proof(*args, coord=coord, **kwargs)
    if mode in ('ended_turn', 'codex_ended_turn', 'recover-codex-ended-turn'):
        return recover_codex_ended_turn(*args, coord=coord, **kwargs)
    if mode in ('ended_turn_proof', 'codex_ended_turn_proof'):
        return codex_ended_turn_proof(*args, coord=coord, **kwargs)
    if mode in ('queue_rollout_proof', 'codex_queue_rollout_proof'):
        return codex_queue_rollout_proof(*args, coord=coord, **kwargs)
    if mode in ('auto_settle_refused_wake', 'auto_settle_codex_refused_wake'):
        return auto_settle_codex_refused_wake(*args, coord=coord, **kwargs)
    if mode in ('find_refusal_locators', 'find_codex_refusal_locators'):
        return find_codex_refusal_locators(*args, coord=coord, **kwargs)
    if mode in ('latest_turn_end', 'latest_codex_turn_end'):
        return latest_codex_turn_end(*args, coord=coord, **kwargs)
    raise _err(coord, 'INVALID_REQUEST', f'unsupported Codex recovery mode: {mode}')


def claude_turn_RECOVERY(mode, *args, coord=None, **kwargs):
    """Unified Claude offline transcript-proof recovery verifier."""
    if mode in ('refused_wake', 'recover-refused-wake'):
        return recover_refused_wake(*args, coord=coord, **kwargs)
    if mode in ('native_refused_wake', 'recover-native-refused-wake'):
        return recover_native_refused_wake(*args, coord=coord, **kwargs)
    if mode in ('native_refusal_proof', 'refused_wake_proof'):
        return native_refusal_proof(*args, coord=coord, **kwargs)
    if mode in ('daemon_start_claim', 'recover-daemon-start-claim'):
        return recover_daemon_start_claim(*args, coord=coord, **kwargs)
    if mode in ('daemon_start_claim_evidence', 'daemon_start_claim_proof'):
        return daemon_start_claim_evidence(*args, coord=coord, **kwargs)
    raise _err(coord, 'INVALID_REQUEST', f'unsupported Claude recovery mode: {mode}')


def opencode_turn_RECOVERY(mode, *args, coord=None, **kwargs):
    """Unified OpenCode offline transcript-proof recovery verifier."""
    raise _err(coord, 'INVALID_REQUEST', f'unsupported OpenCode offline transcript recovery mode: {mode}')


def hermes_turn_RECOVERY(mode, *args, coord=None, **kwargs):
    """Unified Hermes offline transcript-proof recovery verifier."""
    raise _err(coord, 'INVALID_REQUEST', f'unsupported Hermes offline transcript recovery mode: {mode}')


TURN_RECOVERY_VERIFIERS = {
    'codex': codex_turn_RECOVERY,
    'claude': claude_turn_RECOVERY,
    'opencode': opencode_turn_RECOVERY,
    'hermes': hermes_turn_RECOVERY,
}


def verify_turn_recovery(harness, mode, *args, coord=None, **kwargs):
    """Single entry point dispatching offline transcript-proof recovery verification by harness."""
    verifier = TURN_RECOVERY_VERIFIERS.get(harness)
    if verifier is None:
        raise _err(coord, 'INVALID_REQUEST', f'unsupported recovery harness: {harness}')
    return verifier(mode, *args, coord=coord, **kwargs)
