"""Selected native timing/accounting for the prospective path of trial_task_eval.

This module is not an executor entry point. It starts only the command supplied
by run_agent, retaining its original stream for the existing graders/importer.
"""
import hashlib
import json
import os
import selectors
import signal
import subprocess
import time


def capture(command, timeout, timing_path, *, input_bytes=None):
    """Capture one child once, with receipt times; no retry or provider fallback."""
    started = time.monotonic()
    stdout, stderr, pending = bytearray(), bytearray(), bytearray()
    timings, lines, code = [], 0, None
    with subprocess.Popen(command, stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          start_new_session=True) as child, selectors.DefaultSelector() as selector:
        if input_bytes is not None:
            try:
                child.stdin.write(input_bytes)
                child.stdin.close()
            except BrokenPipeError:
                child.stdin.close()
        selector.register(child.stdout, selectors.EVENT_READ, 'stdout')
        selector.register(child.stderr, selectors.EVENT_READ, 'stderr')
        try:
            while selector.get_map():
                if time.monotonic() - started >= timeout:
                    code = 124
                    break
                for key, _ in selector.select(min(.1, max(0, timeout - (time.monotonic()-started)))):
                    block = os.read(key.fileobj.fileno(),65536)
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    if key.data == 'stderr':
                        stderr.extend(block)
                        del stderr[:-20000]
                        continue
                    stdout.extend(block)
                    pending.extend(block)
                    while b'\n' in pending:
                        line, _, pending = pending.partition(b'\n')
                        lines += 1
                        timings.append(dict(line=lines, received_seconds=time.monotonic()-started,
                                            bytes=len(line)+1, sha256=hashlib.sha256(line+b'\n').hexdigest()))
                    if len(pending) > 8*1024*1024 or len(stdout) > 64*1024*1024:
                        code = 125
                        break
                if code is not None:
                    break
            if pending:
                lines += 1
                timings.append(dict(line=lines, received_seconds=time.monotonic()-started,
                                    bytes=len(pending), sha256=hashlib.sha256(pending).hexdigest()))
            if code is None:
                try:
                    code = child.wait(timeout=max(.01,timeout-(time.monotonic()-started)))
                except subprocess.TimeoutExpired:
                    code = 124
        finally:
            if child.poll() is None:
                os.killpg(child.pid,signal.SIGTERM)
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid,signal.SIGKILL)
                    child.wait(timeout=2)
            timing_path.write_text(json.dumps(timings,indent=2))
    return bytes(stdout), bytes(stderr), code, timings


def measure(stream, timings, hook_rows, *, input_bytes, expects_hooks, model, prompt):
    """Observed selected-input wire bound, not the entire provider context window."""
    failures, unknown = [], []
    def fail(reason):
        if reason not in failures:
            failures.append(reason)
    memory_bytes, hook_bytes, last_memory = 0, 0, None
    native_searches, hook_searches, pulls, terminal_seen = 0, 0, 0, False
    hook_started, hook_finished = {}, {}
    calls, completed, seen_init, seen_prompt = {}, set(), False, False
    lines = stream.splitlines(keepends=True)
    if len(lines) != len(timings):
        unknown.append('stream_timing_incomplete')
    for number, raw in enumerate(lines):
        timing = timings[number] if number < len(timings) else {}
        if timing.get('sha256') != hashlib.sha256(raw.encode()).hexdigest():
            unknown.append('stream_timing_mismatch')
        try:
            event = json.loads(raw)
        except ValueError:
            unknown.append('unparsed_native_event')
            continue
        if not isinstance(event,dict):
            unknown.append('unparsed_native_event')
            continue
        if event.get('type') == 'system' and event.get('subtype') == 'init':
            seen_init = True
            if event.get('model') != model:
                fail('reported_model_mismatch')
        if event.get('type') == 'system' and event.get('subtype') in ('hook_started','hook_response'):
            ident, name = event.get('hook_id'), event.get('hook_event')
            if not isinstance(ident,str) or not isinstance(name,str):
                unknown.append('native_hook_identity_missing')
            elif event['subtype'] == 'hook_started':
                if ident in hook_started:
                    unknown.append('duplicate_native_hook')
                hook_started[ident] = name
            else:
                if ident in hook_finished:
                    unknown.append('duplicate_native_hook')
                text = event.get('stdout')
                hook_finished[ident] = (name, hashlib.sha256(text.encode()).hexdigest() if isinstance(text,str) else None)
                if event.get('outcome') != 'success' or event.get('exit_code',0) != 0:
                    fail('native_hook_failed')
        if event.get('type') == 'result':
            terminal_seen = True
            denials = event.get('permission_denials')
            if not isinstance(denials,list):
                unknown.append('permission_denials_missing_or_malformed')
            elif denials:
                fail('native_permission_denial')
        blocks = (event.get('message') or {}).get('content',[])
        if event.get('type') == 'user':
            text = blocks if isinstance(blocks,str) else ''.join(p.get('text','') for p in blocks if isinstance(p,dict) and p.get('type') == 'text') if isinstance(blocks,list) else ''
            if text == prompt:
                if seen_prompt:
                    fail('duplicate_task_input')
                seen_prompt = True
                if len(raw.encode()) > input_bytes:
                    fail('task_framing_reserve_exceeded')
        if not isinstance(blocks,list):
            continue
        for part in blocks:
            if not isinstance(part,dict):
                continue
            ident = part.get('id') if part.get('type') == 'tool_use' else part.get('tool_use_id')
            if not isinstance(ident,str):
                continue
            if part.get('type') == 'tool_use' and str(part.get('name','')).startswith('mcp__cairn__'):
                if ident in calls:
                    unknown.append('duplicate_memory_call')
                name = part.get('name')
                args = part.get('input') or {}
                native_searches += int(name == 'mcp__cairn__cairn_search')
                pulls += int(name in ('mcp__cairn__cairn_pull','mcp__cairn__cairn_pull_evidence')
                             or name == 'mcp__cairn__cairn_history' and isinstance(args,dict) and type(args.get('version')) is int and args['version'] > 0)
                calls[ident] = name
            elif part.get('type') == 'tool_result' and ident in calls:
                # Include error envelopes and every repeated result, never only successful bodies.
                memory_bytes += len(json.dumps(part,ensure_ascii=False,separators=(',',':')).encode())
                last_memory = timing.get('received_seconds')
                completed.add(ident)
    if set(calls) != completed:
        unknown.append('unfinished_memory_calls')
    if not terminal_seen:
        unknown.append('terminal_event_missing')
    if not seen_prompt:
        unknown.append('task_input_not_observed')
    if not seen_init:
        unknown.append('native_init_missing')
    if expects_hooks and not {'SessionStart','UserPromptSubmit'} <= {row.get('event') for row in hook_rows}:
        unknown.append('required_hook_observations_missing')
    if set(hook_started) != set(hook_finished):
        unknown.append('unfinished_native_hooks')
    unmatched_hooks = list(hook_finished.values())
    for row in hook_rows:
        identity = (row.get('event'),row.get('stdout_sha256'))
        if identity not in unmatched_hooks:
            unknown.append('hook_telemetry_uncorrelated')
        else:
            unmatched_hooks.remove(identity)
        size, elapsed = row.get('stdout_bytes'), row.get('process_seconds')
        if type(size) is not int or size < 0 or not isinstance(elapsed,(int,float)):
            unknown.append('hook_measurement_missing')
            continue
        hook_bytes += size
        counts = row.get('memory_call_attempts')
        if not isinstance(counts,dict) or any(type(counts.get(k)) is not int or counts[k] < 0 for k in ('search','pull','pull-evidence','history','other')):
            unknown.append('hook_call_accounting_missing')
        else:
            hook_searches += counts['search']
            pulls += counts['pull'] + counts['pull-evidence'] + counts['history']
            if counts['other']:
                unknown.append('unclassified_hook_call')
        if elapsed > 5:
            fail('hook_exceeded_5_seconds')
        if row.get('exit_code') != 0:
            fail('hook_failed')
    if unmatched_hooks:
        unknown.append('native_hook_telemetry_missing')
    if native_searches > 2 or pulls > 4:
        fail('memory_call_limit')
    total = input_bytes + hook_bytes + memory_bytes
    if total > 9500:
        fail('selected_input_exceeded_9500_bytes')
    if last_memory is not None and last_memory > 30:
        fail('last_memory_exceeded_30_seconds')
    return dict(schema='cairn.task-eval.selected-input/1',
                status='unknown' if unknown else 'failed' if failures else 'within_observed_limits',
                failures=failures, unknown=sorted(set(unknown)), input_bytes=input_bytes,
                hook_wire_bytes=hook_bytes, native_memory_result_bytes=memory_bytes,
                total_bytes=total, last_native_memory_seconds=last_memory,
                memory_attempted=bool(calls), memory_calls=len(calls), native_searches=native_searches, hook_searches=hook_searches,
                total_actual_pull_calls=pulls,
                limits=dict(selected_input_bytes=9500,hook_seconds=5,last_native_memory_seconds=30))
