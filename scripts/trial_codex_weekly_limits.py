"""Codex-specific measurement for the unchanged CAIRN-107 weekly limits.

This reads the Codex `exec --json` event schema. It does not reinterpret a
Claude stream as Codex evidence: missing MCP result payloads, hook rows,
capture timings, or a terminal turn event remain unknown and fail admission.
"""
import hashlib
import json
import math

import trial_legacy_limits as legacy
import trial_paired_request as paired
from trial_observation_storage import canonical_size

LIMITS = paired.ORIGINAL_LIMITS
SCHEMA = 'cairn.codex-weekly-limit-assessment/1'
REQUIRED_HOOK_EVENTS = {'SessionStart', 'UserPromptSubmit'}


def _timings(value, lines):
    if not isinstance(value, list) or len(value) != len(lines):
        return None
    seconds=[]
    for index, (row, text) in enumerate(zip(value, lines)):
        raw=(text+'\n').encode('utf-8')
        elapsed=row.get('received_seconds') if isinstance(row, dict) else None
        if (not isinstance(row, dict) or row.get('line') != index+1 or row.get('bytes') != len(raw)
                or row.get('sha256') != hashlib.sha256(raw).hexdigest()
                or type(elapsed) not in (int,float) or not math.isfinite(elapsed) or elapsed < 0):
            return None
        seconds.append(elapsed)
    return seconds


def assess(stream, hook_rows, *, input_bytes, timings=None):
    """Assess one retained Codex JSONL output; content defects never become passes."""
    try:
        return _assess(stream, hook_rows, input_bytes=input_bytes, timings=timings)
    except Exception:
        return dict(schema=SCHEMA, status='unknown', failures=[],
                    unknown=['assessment_input_malformed'], limits=dict(LIMITS))


def _assess(stream, hook_rows, *, input_bytes, timings):
    failures, unknown=set(),set()
    if not isinstance(stream, str):
        return dict(schema=SCHEMA,status='unknown',failures=[],unknown=['stream_malformed'],limits=dict(LIMITS))
    lines=stream.splitlines()
    if not stream.endswith('\n'):
        unknown.add('stream_malformed')
    events=[]
    for raw in lines:
        try: event=json.loads(raw)
        except ValueError:
            unknown.add('stream_malformed'); events.append({}); continue
        if not isinstance(event,dict):
            unknown.add('stream_shape_unknown'); events.append({}); continue
        events.append(event)
    receipt_times=_timings(timings,lines) if timings is not None else None
    if receipt_times is None: unknown.add('stream_timing_invalid')
    calls={}; completed=set(); searches=0; pulls=0; memory_bytes=0; last_memory=None; answer=''
    terminal=bool(events) and events[-1].get('type')=='turn.completed'
    for index,event in enumerate(events):
        kind=event.get('type')
        if kind in ('turn.failed','error'):
            failures.add('codex_turn_failed')
        if kind not in ('item.started','item.completed'): continue
        item=event.get('item')
        if not isinstance(item,dict):
            unknown.add('stream_shape_unknown'); continue
        item_type=item.get('type')
        if item_type=='agent_message' and kind=='item.completed':
            if type(item.get('text')) is not str: unknown.add('answer_unobserved')
            else: answer=item['text']
        if item_type!='mcp_tool_call' or item.get('server')!='cairn': continue
        ident=item.get('id'); tool=item.get('tool'); args=item.get('arguments',{})
        if type(ident) is not str or not ident or type(tool) is not str or not tool or not isinstance(args,dict):
            unknown.add('memory_identity_unknown'); continue
        if kind=='item.started':
            if ident in calls: unknown.add('memory_identity_unknown')
            calls[ident]=(tool,args)
            continue
        if ident in completed:
            unknown.add('memory_identity_unknown'); continue
        prior=calls.get(ident)
        if prior is not None and prior!=(tool,args):
            unknown.add('memory_identity_unknown'); continue
        calls[ident]=(tool,args); completed.add(ident)
        result=item.get('result')
        if not isinstance(result,dict) or not isinstance(result.get('content'),list):
            unknown.add('mcp_result_payload_unobserved'); continue
        if item.get('status')!='completed':
            unknown.add('mcp_call_completion_unobserved'); continue
        size=canonical_size(result)
        memory_bytes+=size
        if size>LIMITS['tool_room_bytes']: failures.add('tool_room_exceeded_32000_bytes')
        if tool=='cairn_search': searches+=1
        elif tool in ('cairn_pull','cairn_pull_evidence'): pulls+=1
        elif tool=='cairn_history' and type(args.get('version')) is int and args['version']>0: pulls+=1
        else: unknown.add('unclassified_memory_call')
        if receipt_times is not None: last_memory=receipt_times[index]
    if not terminal: unknown.add('terminal_turn_unobserved')
    if set(calls)!=completed: unknown.add('unfinished_memory_calls')
    if searches>LIMITS['native_searches'] or pulls>LIMITS['pulls']: failures.add('memory_call_limit')
    if not isinstance(hook_rows,list):
        unknown.add('hook_measurement_missing'); hook_rows=[]
    if not REQUIRED_HOOK_EVENTS <= {r.get('event') for r in hook_rows if isinstance(r,dict)}:
        unknown.add('required_hook_observations_missing')
    hook_bytes=0; hook_searches=0
    for row in hook_rows:
        if not isinstance(row,dict): unknown.add('hook_measurement_missing'); continue
        sizes=(row.get('stdout_bytes'),row.get('stderr_bytes')); elapsed=row.get('process_seconds'); counts=row.get('memory_call_attempts')
        if (any(type(x) is not int or x<0 for x in sizes) or type(elapsed) not in (int,float)
                or not math.isfinite(elapsed) or elapsed<0 or not isinstance(counts,dict)
                or any(type(counts.get(k)) is not int or counts[k]<0 for k in ('search','pull','pull-evidence','history','other'))):
            unknown.add('hook_measurement_missing'); continue
        hook_bytes+=sum(sizes); hook_searches+=counts['search']; pulls+=counts['pull']+counts['pull-evidence']+counts['history']
        if counts['other']: unknown.add('unclassified_hook_call')
        if elapsed>LIMITS['hook_seconds']: failures.add('hook_exceeded_5_seconds')
        if row.get('exit_code')!=0: failures.add('hook_failed')
    if searches>LIMITS['native_searches'] or pulls>LIMITS['pulls']: failures.add('memory_call_limit')
    if type(input_bytes) is not int or input_bytes<0:
        unknown.add('input_charge_unknown'); input_bytes=0
    total=input_bytes+hook_bytes+memory_bytes
    if total>LIMITS['selected_input_bytes']: failures.add('selected_input_exceeded_9500_bytes')
    if last_memory is not None and last_memory>LIMITS['last_native_memory_seconds']: failures.add('last_memory_exceeded_30_seconds')
    answer_bytes=len(answer.encode('utf-8'))
    if answer_bytes>LIMITS['final_answer_bytes']: failures.add('final_answer_exceeded_8192_bytes')
    status='failed' if failures else 'unknown' if unknown else 'within_codex_weekly_limits'
    return dict(schema=SCHEMA,status=status,failures=sorted(failures),unknown=sorted(unknown),input_bytes=input_bytes,
                hook_wire_bytes=hook_bytes,native_memory_result_bytes=memory_bytes,total_bytes=total,
                native_searches=searches,hook_searches=hook_searches,pulls=pulls,final_answer_bytes=answer_bytes,
                last_native_memory_seconds=last_memory,limits=dict(LIMITS))


def input_charge(prompt, memory_instruction):
    """Keep the selected task representation/framing charge shared with the original weekly ceiling."""
    return legacy.input_charge(prompt,memory_instruction)


def hook_context_bytes(prompt, memory_instruction):
    return LIMITS['selected_input_bytes']-input_charge(prompt,memory_instruction)

