"""Selected native timing/accounting for the prospective path of trial_task_eval.

This module is not an executor entry point. It starts only the command supplied
by run_agent, retaining its original stream for the existing graders/importer.
"""
import hashlib
import json
import math
from pathlib import Path
import os
import selectors
import signal
import subprocess
import time
from trial_source_delivery import hook_delivery, native_delivery
from trial_hook_observer import prompt_metadata, bounded_identity
from trial_task_permissions import ALLOW, DENY, command_metadata, background_result


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


def action_path(value, workspace):
    """Post-run containment check; never attest the filesystem at action time."""
    if not isinstance(value, str) or not value or workspace is None:
        return None
    try:
        if len(value.encode()) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            return None
        path, root = Path(value), Path(workspace).resolve(strict=True)
        if '..' in path.parts:
            return None
        path = path if path.is_absolute() else root / path
        relative = path.relative_to(root)
        path.resolve().relative_to(root)  # Existing symlink escapes are not source paths.
        if not relative.parts:
            return None
        return relative.as_posix()
    except (OSError, RuntimeError, ValueError, UnicodeError):
        return None


class PublicActions:
    """Bounded projection of public tool events, independent of task grading."""
    LIMIT = 256
    TOOLS = {'Bash', 'Read', 'Edit', 'Write', 'MultiEdit', 'NotebookEdit', 'Glob', 'Grep'} | {
        name for name in ALLOW + DENY if name.startswith('mcp__')}

    def __init__(self, workspace, commands):
        self.workspace, self.commands = workspace, commands or {}
        self.actions, self.by_id, self.unknown = [], {}, set()
        self.omitted, self.unmatched, self.duplicates = 0, 0, 0

    def consume(self, part, event, received):
        kind = part.get('type')
        if kind not in ('tool_use', 'tool_result'):
            return
        if event.get('type') != ('assistant' if kind == 'tool_use' else 'user'):
            self.unknown.add('action_role_unknown')
            return
        time = received if type(received) in (int, float) and math.isfinite(received) and received >= 0 else None
        if time is None:
            self.unknown.add('action_timing_unknown')
        identity = bounded_identity(part.get('id' if kind == 'tool_use' else 'tool_use_id'))
        ident = identity['sha256']
        if ident is None:
            self.unknown.add('action_identity_unknown')
        if kind == 'tool_use':
            if ident is not None and ident in self.by_id:
                self.duplicates += 1
                self.by_id[ident]['state'] = 'unknown'
                self.unknown.add('duplicate_action_identity')
                return
            if len(self.actions) >= self.LIMIT:
                self.omitted += 1
                self.unknown.add('action_limit')
                return
            tool = part.get('name')
            tool = tool if isinstance(tool, str) and tool in self.TOOLS else 'unknown'
            row = dict(action_sha256=ident, tool=tool, started_seconds=time,
                       result_seconds=None, result_count=0, state='unfinished', exit_code=None)
            data = part.get('input')
            if not isinstance(data, dict) or tool == 'unknown':
                self.unknown.add('action_shape_unknown')
                data = {}
            if tool == 'Bash':
                command = data.get('command')
                row.update(command_metadata(command))
                row['command_sha256'] = None
                row['validation_label'] = None
                try:
                    encoded = command.encode() if isinstance(command, str) else None
                except UnicodeError:
                    encoded = None
                if encoded is not None and len(encoded) <= 65536:
                    row['command_sha256'] = hashlib.sha256(encoded).hexdigest()
                    row['validation_label'] = next((label for label, value in self.commands.items() if command == value), None)
                else:
                    self.unknown.add('command_identity_unknown')
                row['requested_background'] = data.get('run_in_background') is True
            elif tool in ('Read', 'Edit', 'Write', 'MultiEdit', 'NotebookEdit'):
                row['path'] = action_path(data.get('file_path'), self.workspace)
                if row['path'] is None:
                    self.unknown.add('action_path_unverified')
            self.actions.append(row)
            if ident is not None:
                self.by_id[ident] = row
            return
        row = self.by_id.get(ident)
        if row is None:
            self.unmatched += 1
            self.unknown.add('unmatched_action_result')
            return
        row['result_count'] += 1
        if row['result_count'] > 1:
            self.duplicates += 1
            row['state'] = 'unknown'
            self.unknown.add('duplicate_action_result')
            return
        row['result_seconds'] = time
        if time is not None and row['started_seconds'] is not None and time < row['started_seconds']:
            row['result_seconds'] = None
            self.unknown.add('action_timing_unknown')
        if row['state'] == 'unknown':
            return  # A duplicate call ID prevents a unique result association.
        error = part.get('is_error')
        if row['tool'] == 'Bash' and (row['requested_background'] or background_result(part) or background_result(event.get('tool_use_result'))):
            row['state'] = 'background'
            self.unknown.add('background_completion_unobserved')
        elif type(error) is bool:
            row['state'] = 'tool_error' if error else 'tool_success'
        else:
            row['state'] = 'unknown'
            self.unknown.add('action_result_state_unknown')
        native = event.get('tool_use_result')
        if row['tool'] == 'Bash' and isinstance(native, dict) and 'exit_code' in native:
            code = native['exit_code']
            result_blocks = event['message']['content']
            unique_result = sum(isinstance(block, dict) and block.get('type') == 'tool_result' for block in result_blocks) == 1
            if unique_result and type(code) is int and -128 <= code <= 255:
                row['exit_code'] = code
            else:
                self.unknown.add('action_exit_code_unknown')

    def finish(self):
        if not self.actions:
            self.unknown.add('no_observed_actions')
        if any(row['state'] == 'unfinished' for row in self.actions):
            self.unknown.add('unfinished_actions')
        return dict(schema='cairn.public-actions/1', status='unknown' if self.unknown else 'observed',
                    time_basis='capture_start_monotonic', time_unit='seconds',
                    actions=self.actions, actions_omitted=self.omitted,
                    unmatched_results=self.unmatched, duplicate_events=self.duplicates,
                    unknown=sorted(self.unknown), limit=self.LIMIT)


def measure(stream, timings, hook_rows, *, input_bytes, expects_hooks, model, prompt, workspace=None, public_validation_commands=None):
    """Observed selected-input wire bound, not the entire provider context window."""
    failures, unknown = [], []
    actions = PublicActions(workspace, public_validation_commands)
    def fail(reason):
        if reason not in failures:
            failures.append(reason)
    memory_bytes, hook_bytes, last_memory = 0, 0, None
    native_searches, hook_searches, pulls, terminal_seen = 0, 0, 0, False
    hook_started, hook_finished, hook_sources = {}, {}, {}
    source_deliveries = []
    native_prompt_events, native_prompt_events_omitted = [], 0
    frozen_prompt_sha256 = hashlib.sha256(prompt.encode()).hexdigest()
    source_items, source_events_omitted, source_empty_events = 0, 0, 0
    def retain_delivery(row):
        nonlocal source_items, source_events_omitted, source_empty_events
        # Empty successful hooks are still charged/correlated below, but cannot
        # displace actual source delivery or unknown/error evidence from this cap.
        if row['delivery']['status'] == 'observed' and row['delivery']['items'] == []:
            source_empty_events += 1
            return
        count = len(row['delivery']['items'])
        if len(source_deliveries) >= 128 or source_items + count > 512:
            source_events_omitted += 1
            if 'source_delivery_limit' not in unknown:
                unknown.append('source_delivery_limit')
            return
        source_items += count
        source_deliveries.append(row)
        if row['delivery']['status'] == 'unknown' and 'source_delivery_unknown' not in unknown:
            unknown.append('source_delivery_unknown')
    calls, completed, seen_init, seen_prompt = {}, set(), False, False
    lines = stream.splitlines(keepends=True)
    if len(lines) != len(timings):
        unknown.append('stream_timing_incomplete')
        actions.unknown.add('action_timing_unknown')
    for number, raw in enumerate(lines):
        timing = timings[number] if number < len(timings) else {}
        if timing.get('sha256') != hashlib.sha256(raw.encode()).hexdigest():
            unknown.append('stream_timing_mismatch')
            actions.unknown.add('action_timing_unknown')
        try:
            event = json.loads(raw)
        except ValueError:
            unknown.append('unparsed_native_event')
            actions.unknown.add('native_action_format_unknown')
            continue
        if not isinstance(event,dict):
            unknown.append('unparsed_native_event')
            actions.unknown.add('native_action_format_unknown')
            continue
        if event.get('type') not in ('system', 'assistant', 'user', 'result'):
            actions.unknown.add('native_action_format_unsupported')
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
                if event.get('outcome') == 'success' and event.get('exit_code',0) == 0 and isinstance(text,str):
                    hook_sources[ident] = dict(delivery=hook_delivery(text), received_seconds=timing.get('received_seconds'))
                if event.get('outcome') != 'success' or event.get('exit_code',0) != 0:
                    fail('native_hook_failed')
        if event.get('type') == 'result':
            terminal_seen = True
            denials = event.get('permission_denials')
            if not isinstance(denials,list):
                unknown.append('permission_denials_missing_or_malformed')
            elif denials:
                fail('native_permission_denial')
        if event.get('type') not in ('assistant','user'):
            continue
        message = event.get('message')
        blocks = message.get('content') if isinstance(message,dict) else None
        if not isinstance(blocks,list) and not (event['type'] == 'user' and isinstance(blocks,str)):
            unknown.append('native_message_shape_unknown')
            actions.unknown.add('native_action_format_unknown')
            continue
        if isinstance(blocks,list) and any(not isinstance(part,dict) or not isinstance(part.get('type'),str) or
                (part.get('type') == 'text' and not isinstance(part.get('text'),str)) for part in blocks):
            unknown.append('native_message_shape_unknown')
            actions.unknown.add('native_action_format_unknown')
        if event.get('type') == 'user':
            text = blocks if isinstance(blocks,str) else ''.join(p.get('text','') for p in blocks if isinstance(p,dict) and p.get('type') == 'text' and isinstance(p.get('text'),str)) if isinstance(blocks,list) else ''
            if isinstance(blocks,str) or any(isinstance(p,dict) and p.get('type') == 'text' for p in blocks):
                if len(native_prompt_events) < 32:
                    selected_event=dict(event,prompt=text)
                    if 'prompt_id' not in selected_event and 'promptId' in event:
                        selected_event['prompt_id']=event['promptId']
                    row=prompt_metadata(selected_event,frozen_prompt_sha256)
                    row.update(received_seconds=timing.get('received_seconds'))
                    for flag in ('isSynthetic','isReplay'):
                        row[flag]=event.get(flag) if type(event.get(flag)) is bool else None
                    parent=bounded_identity(event.get('parent_tool_use_id'))
                    row.update(parent_tool_use_id_state=parent['state'],parent_tool_use_id_sha256=parent['sha256'])
                    native_prompt_events.append(row)
                else:
                    native_prompt_events_omitted += 1
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
            actions.consume(part, event, timing.get('received_seconds'))
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
                tool = calls[ident]
                retain_delivery(dict(channel='native',tool=tool if tool in (
                    'mcp__cairn__cairn_search','mcp__cairn__cairn_pull','mcp__cairn__cairn_history',
                    'mcp__cairn__cairn_pull_evidence','mcp__cairn__cairn_client_info','mcp__cairn__cairn_assessments') else 'unknown',
                    received_seconds=last_memory,delivery=native_delivery(tool,part)))
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
        error_bytes = row.get('stderr_bytes')
        if type(error_bytes) is int and error_bytes >= 0:
            hook_bytes += error_bytes
        else:
            unknown.append('hook_error_bytes_missing_or_malformed')
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
    for ident, observed in hook_sources.items():
        if ident in hook_started and hook_started[ident] == hook_finished[ident][0]:
            retain_delivery(dict(channel='hook',event=hook_finished[ident][0] if hook_finished[ident][0] in ('SessionStart','UserPromptSubmit','PostToolUse','PostToolUseFailure') else 'unknown',stdout_sha256=hook_finished[ident][1],**observed))
    if unmatched_hooks:
        unknown.append('native_hook_telemetry_missing')
    if native_searches > 2 or pulls > 4:
        fail('memory_call_limit')
    total = input_bytes + hook_bytes + memory_bytes
    if total > 9500:
        fail('selected_input_exceeded_9500_bytes')
    if last_memory is not None and last_memory > 30:
        fail('last_memory_exceeded_30_seconds')
    return dict(schema='cairn.task-eval.selected-input/1', public_actions=actions.finish(),
                status='unknown' if unknown else 'failed' if failures else 'within_observed_limits',
                failures=failures, unknown=sorted(set(unknown)), input_bytes=input_bytes,
                hook_wire_bytes=hook_bytes, native_memory_result_bytes=memory_bytes,
                total_bytes=total, last_native_memory_seconds=last_memory,
                native_prompt_events=native_prompt_events, native_prompt_events_omitted=native_prompt_events_omitted,
                memory_attempted=bool(calls), memory_calls=len(calls), native_searches=native_searches, hook_searches=hook_searches,
                total_actual_pull_calls=pulls, source_deliveries=source_deliveries, source_delivery_events_omitted=source_events_omitted,
                source_delivery_empty_events=source_empty_events,
                limits=dict(selected_input_bytes=9500,hook_seconds=5,last_native_memory_seconds=30))
