"""Original paired limits for one legacy-harness attempt (CAIRN-107).

The legacy ``trial_task_eval.run_agent`` path launches Claude Code with a
positional prompt and retains ``stream.jsonl`` and the hook observer's
``hook-state/observations.jsonl``. This module provides the original limit
values for that path and checks one retained attempt against them.

The values come only from ``trial_paired_request.ORIGINAL_LIMITS``. The
counting rules follow ``trial_native_observation`` (the prospective
measurement):

- Selected input is the task wire, a 512-byte framing reserve, the memory
  instruction, hook stdout and stderr, and every native memory tool result.
- At most 2 native searches; at most 4 pulls in total (native pull,
  pull_evidence and versioned history, plus hook pull, pull-evidence and
  history calls).
- Each hook must take 5 s or less and exit 0.
- The last native memory result must arrive within 30 s.

This module adds two checks for the legacy path: the final answer must be at
most 8192 UTF-8 bytes, and a single memory result must be at most the
32000-byte tool room.

Per-line timing comes from ``trial_native_capture.capture`` rows (``line``,
``bytes``, ``sha256``, ``received_seconds``), checked against the exact line
bytes. Without valid timing, a run with any native memory call leaves the
last-memory bound ``unknown``, never within limits.
A missing, malformed or truncated stream, a stream without a terminal
``result`` event, invalid timings or malformed hook rows are ``unknown`` too.
``unknown`` and ``failed`` are both row failures for the weekly runner.
"""
import hashlib
import json
import math

from trial_observation_storage import canonical_size
import trial_paired_request as paired

LIMITS = paired.ORIGINAL_LIMITS
FRAMING_RESERVE = 512  # trial_task_eval.run_agent prospective input charge
SCHEMA = 'cairn.legacy-limit-assessment/1'
NATIVE_SEARCH = 'mcp__cairn__cairn_search'
NATIVE_PULLS = ('mcp__cairn__cairn_pull', 'mcp__cairn__cairn_pull_evidence')
REQUIRED_HOOK_EVENTS = {'SessionStart', 'UserPromptSubmit'}


def hook_timeout_seconds():
    return LIMITS['hook_seconds']


def mcp_tool_room_arguments():
    """The paired MCP ceiling arguments (trial_paired_request.mcp_arguments)."""
    room = str(LIMITS['tool_room_bytes'])
    return ['--tokens', room, '--max-tokens', room]


def input_charge(prompt, memory_instruction):
    wire = (json.dumps(dict(type='user', message=dict(role='user', content=prompt)), ensure_ascii=False) + '\n').encode()
    return len(wire) + FRAMING_RESERVE + len(memory_instruction.encode())


def hook_context_bytes(prompt, memory_instruction):
    """Hook context allowance once the task wire and framing are charged."""
    return LIMITS['selected_input_bytes'] - input_charge(prompt, memory_instruction)


def _final_answer(events):
    answer = ''
    for event in events:
        if event.get('type') == 'assistant':
            for part in event['message']['content']:
                if part.get('type') == 'text' and isinstance(part.get('text'), str):
                    answer = part['text']
        elif event.get('type') == 'result' and isinstance(event.get('result'), str) and event['result']:
            answer = event['result']
    return answer


def _received_seconds(timings, lines):
    """Per-line receive times from trial_native_capture.capture rows, bound to the exact line bytes.

    Returns None unless every line has exactly one row with its index, byte
    count, SHA-256 and a finite, nonnegative ``received_seconds``.
    """
    if not isinstance(timings, list) or len(timings) != len(lines):
        return None
    seconds = []
    for index, (row, line) in enumerate(zip(timings, lines)):
        raw = (line + '\n').encode('utf-8')
        value = row.get('received_seconds') if isinstance(row, dict) else None
        if (not isinstance(row, dict) or row.get('line') != index + 1 or row.get('bytes') != len(raw)
                or row.get('sha256') != hashlib.sha256(raw).hexdigest()
                or type(value) not in (int, float) or not math.isfinite(value) or value < 0):
            return None
        seconds.append(value)
    return seconds


def _events(stream, unknown):
    """Parse every line; any malformed, truncated or wrong-shaped line is unknown, never skipped."""
    events = []
    if not isinstance(stream, str):
        unknown.add('stream_malformed')
        return events, []
    if stream and not stream.endswith('\n'):
        unknown.add('stream_malformed')  # a truncated final line
    # Split on LF only, as the capture does; JSON text may contain other line separators.
    lines = stream.split('\n')[:-1] if stream.endswith('\n') else stream.split('\n') if stream else []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            unknown.add('stream_malformed')
            events.append({})
            continue
        if not isinstance(event, dict):
            unknown.add('stream_shape_unknown')
            events.append({})
            continue
        if event.get('type') in ('assistant', 'user'):
            message = event.get('message')
            content = message.get('content') if isinstance(message, dict) else None
            if not isinstance(content, list) or not all(isinstance(part, dict) for part in content):
                unknown.add('stream_shape_unknown')
                events.append({})
                continue
        events.append(event)
    if not events or events[-1].get('type') != 'result':
        unknown.add('terminal_event_missing')
    return events, lines


def assess(stream, hook_rows, *, input_bytes, timings=None):
    """Check one memory-arm attempt against the original limits.

    Never raises on content: any malformed, missing or truncated evidence is
    ``unknown``. Absent evidence is never within limits.
    """
    try:
        return _assess(stream, hook_rows, input_bytes=input_bytes, timings=timings)
    except Exception:  # classified, never a pass
        return dict(schema=SCHEMA, status='unknown', failures=[], unknown=['assessment_input_malformed'],
                    limits=dict(LIMITS))


def _assess(stream, hook_rows, *, input_bytes, timings):
    failures, unknown = [], set()

    def fail(reason):
        if reason not in failures:
            failures.append(reason)

    events, lines = _events(stream, unknown)
    if timings is not None:
        timings = _received_seconds(timings, lines)
        if timings is None:
            unknown.add('stream_timing_invalid')
    calls, completed = {}, set()
    native_searches, pulls, memory_bytes, last_memory = 0, 0, 0, None
    for index, event in enumerate(events):
        if event.get('type') not in ('assistant', 'user'):
            continue
        for part in event['message']['content']:
            if part.get('type') == 'tool_use' and str(part.get('name', '')).startswith('mcp__cairn__'):
                name, arguments = part['name'], part.get('input') or {}
                if not isinstance(part.get('id'), str) or part['id'] in calls:
                    unknown.add('memory_identity_unknown')
                calls[part.get('id')] = name
                native_searches += name == NATIVE_SEARCH
                pulls += (name in NATIVE_PULLS or name == 'mcp__cairn__cairn_history' and isinstance(arguments, dict)
                          and type(arguments.get('version')) is int and arguments['version'] > 0)
            elif part.get('type') == 'tool_result' and part.get('tool_use_id') in calls:
                size = canonical_size(part)
                memory_bytes += size
                completed.add(part['tool_use_id'])
                if size > LIMITS['tool_room_bytes']:
                    fail('tool_room_exceeded_32000_bytes')
                if timings is None:
                    unknown.add('last_native_memory_timing_unobserved')
                else:
                    last_memory = timings[index]
    if set(calls) != completed:
        unknown.add('unfinished_memory_calls')
    hook_bytes, hook_searches = 0, 0
    if not isinstance(hook_rows, list):
        unknown.add('hook_measurement_missing')
        hook_rows = []
    if not REQUIRED_HOOK_EVENTS <= {row.get('event') for row in hook_rows if isinstance(row, dict)}:
        unknown.add('required_hook_observations_missing')
    for row in hook_rows:
        if not isinstance(row, dict):
            unknown.add('hook_measurement_missing')
            continue
        sizes = (row.get('stdout_bytes'), row.get('stderr_bytes'))
        elapsed, counts = row.get('process_seconds'), row.get('memory_call_attempts')
        if (any(type(size) is not int or size < 0 for size in sizes)
                or type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0
                or not isinstance(counts, dict)
                or any(type(counts.get(k)) is not int or counts[k] < 0
                       for k in ('search', 'pull', 'pull-evidence', 'history', 'other'))):
            unknown.add('hook_measurement_missing')
            continue
        hook_bytes += sum(sizes)
        hook_searches += counts['search']
        pulls += counts['pull'] + counts['pull-evidence'] + counts['history']
        if counts['other']:
            unknown.add('unclassified_hook_call')
        if elapsed > LIMITS['hook_seconds']:
            fail('hook_exceeded_5_seconds')
        if row.get('exit_code') != 0:
            fail('hook_failed')
    if native_searches > LIMITS['native_searches'] or pulls > LIMITS['pulls']:
        fail('memory_call_limit')
    if type(input_bytes) is not int or input_bytes < 0:
        unknown.add('input_charge_unknown')
        input_bytes = 0
    total = input_bytes + hook_bytes + memory_bytes
    if total > LIMITS['selected_input_bytes']:
        fail('selected_input_exceeded_9500_bytes')
    if last_memory is not None and last_memory > LIMITS['last_native_memory_seconds']:
        fail('last_memory_exceeded_30_seconds')
    answer_bytes = len(_final_answer(events).encode('utf-8'))
    if answer_bytes > LIMITS['final_answer_bytes']:
        fail('final_answer_exceeded_8192_bytes')
    return dict(schema=SCHEMA, status='failed' if failures else 'unknown' if unknown else 'within_original_limits',
                failures=failures, unknown=sorted(unknown), input_bytes=input_bytes, hook_wire_bytes=hook_bytes,
                native_memory_result_bytes=memory_bytes, total_bytes=total, native_searches=native_searches,
                hook_searches=hook_searches, pulls=pulls, final_answer_bytes=answer_bytes,
                last_native_memory_seconds=last_memory, limits=dict(LIMITS))
