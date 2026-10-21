"""Selected native timing/accounting for the prospective path of trial_task_eval.

This module is not an executor entry point. It starts only the command supplied
by run_agent, retaining its original stream for the existing graders/importer.
"""
import json
import math
from itertools import zip_longest
from pathlib import Path
from trial_native_capture import capture
from trial_observation_storage import text_size_sha256, canonical_size
from trial_source_delivery import hook_delivery, native_delivery
from trial_hook_observer import prompt_metadata, bounded_identity
from trial_task_permissions import ALLOW, DENY, command_metadata, background_result



def action_path(value, workspace):
    """Post-run containment check; never attest the filesystem at action time."""
    if not isinstance(value, str) or not value or workspace is None:
        return None
    try:
        if text_size_sha256(value)[0] > 512 or any(ord(c) < 32 or ord(c) == 127 for c in value):
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
        self.ambiguous_ids = set()
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
                self.ambiguous_ids.add(ident)
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
                    encoded = text_size_sha256(command) if isinstance(command, str) else None
                except UnicodeError:
                    encoded = None
                if encoded is not None and encoded[0] <= 65536:
                    row['command_sha256'] = encoded[1]
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
            self.ambiguous_ids.add(ident)
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
        if error is True:
            row['state'] = 'tool_error'
        elif row['tool'] == 'Bash' and (row['requested_background'] or background_result(part) or background_result(event.get('tool_use_result'))):
            row['state'] = 'background'
        elif error is False:
            row['state'] = 'tool_success'
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

    def observe_denials(self, denials):
        for denial in denials:
            if not isinstance(denial, dict):
                continue
            ident = bounded_identity(denial.get('tool_use_id'))['sha256']
            row = self.by_id.get(ident)
            if row is not None and ident not in self.ambiguous_ids and row['tool'] == denial.get('tool_name'):
                row['state'] = 'tool_error'
                row['exit_code'] = None

    def finish(self):
        if not self.actions:
            self.unknown.add('no_observed_actions')
        if any(row['state'] == 'unfinished' for row in self.actions):
            self.unknown.add('unfinished_actions')
        if any(row['state'] == 'background' for row in self.actions):
            self.unknown.add('background_completion_unobserved')
        return dict(schema='cairn.public-actions/1', status='unknown' if self.unknown else 'observed',
                    time_basis='capture_start_monotonic', time_unit='seconds',
                    actions=self.actions, actions_omitted=self.omitted,
                    unmatched_results=self.unmatched, duplicate_events=self.duplicates,
                    unknown=sorted(self.unknown), limit=self.LIMIT)


def measure(stream, timings, hook_rows, *, input_bytes, expects_hooks, model, prompt, workspace=None, public_validation_commands=None):
    """Observed selected-input wire bound, not the entire provider context window."""
    return measure_lines(stream.splitlines(keepends=True), timings, hook_rows,
                         input_bytes=input_bytes, expects_hooks=expects_hooks,
                         model=model, prompt=prompt, workspace=workspace,
                         public_validation_commands=public_validation_commands)


def measure_lines(lines, timings, hook_rows, *, input_bytes, expects_hooks, model, prompt, workspace=None, public_validation_commands=None):
    """Consume complete text lines and matching timing rows once, in lockstep.

    Callers own line framing, size limits and provenance. This removes the need
    to retain a complete stream. Correlations and projections have fixed limits;
    per-line parsing, SDK controls and custody remain the caller's obligations.
    Iterator failures propagate without returning a partial success report.
    """
    observer = LineMeasurement(input_bytes=input_bytes, expects_hooks=expects_hooks,
                               model=model, prompt=prompt, workspace=workspace,
                               public_validation_commands=public_validation_commands)
    try:
        for raw, timing in zip_longest(lines, timings, fillvalue=_MISSING):
            observer.consume(raw, timing)
        return observer.finish(hook_rows)
    finally:
        observer.close()


_MISSING = object()
_FINISH = object()


class _ParsedDocument:
    def __init__(self, graph, raw_bytes):
        self.graph, self.raw_bytes = graph, raw_bytes


class LineMeasurement:
    """Single-use incremental observer; consumes each borrowed line synchronously.

    The caller supplies complete original text lines and capture timing, then
    final hook observations. Failure consumes this observer. This owns the same
    accounting semantics as the batch evaluator. Correlation overflow is unknown;
    this library supplies neither an allocation ticket nor stream authority.
    """
    def __init__(self, *, input_bytes, expects_hooks, model, prompt, workspace=None,
                 public_validation_commands=None):
        self._steps = _measurement_steps(input_bytes=input_bytes, expects_hooks=expects_hooks,
            model=model, prompt=prompt, workspace=workspace,
            public_validation_commands=public_validation_commands)
        self._closed = False
        next(self._steps)

    def consume(self, line, timing):
        if self._closed:
            raise ValueError('measurement_consumed')
        try:
            self._steps.send((line, timing))
        except BaseException:
            self.close()
            raise

    def consume_document(self, document, raw_bytes, timing):
        """Borrow an already parsed graph; caller owns original-byte provenance.

        This method supplies no custody authority. The SDK owner separately
        validates original text, receipt offsets/digest and final retained stream.
        """
        if not isinstance(document, dict) or type(raw_bytes) is not int or raw_bytes <= 0:
            self.close()
            raise ValueError('measurement_document')
        self.consume(_ParsedDocument(document, raw_bytes), timing)

    def finish(self, hook_rows):
        if self._closed:
            raise ValueError('measurement_consumed')
        self._closed = True
        try:
            self._steps.send((_FINISH, hook_rows))
        except StopIteration as result:
            return result.value
        finally:
            self._steps.close()
        raise RuntimeError('measurement_finish_incomplete')

    def close(self):
        self._closed = True
        self._steps.close()


class _Reasons(list):
    # Closed source-defined reason vocabulary; repeated malformed frames cannot
    # grow retained state. No caller strings are admitted here.
    def append(self, reason):
        if reason not in self:
            super().append(reason)


CORRELATION_LIMIT = 256


def _measurement_steps(*, input_bytes, expects_hooks, model, prompt, workspace=None,
                       public_validation_commands=None):
    failures, unknown = [], _Reasons()
    actions = PublicActions(workspace, public_validation_commands)
    def fail(reason):
        if reason not in failures:
            failures.append(reason)
    memory_bytes, hook_bytes, last_memory = 0, 0, None
    native_searches, hook_searches, pulls, terminal_seen = 0, 0, 0, False
    hook_started, hook_finished, hook_sources = {}, {}, {}
    source_deliveries = []
    native_prompt_events, native_prompt_events_omitted = [], 0
    frozen_prompt_sha256 = text_size_sha256(prompt)[1]
    source_items, source_events_omitted, source_empty_events = 0, 0, 0
    hook_source_items = 0
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
    missing = _MISSING
    while True:
        # End borrowed line/timing and transient parsed graphs before suspension.
        item = raw = timing = event = message = blocks = part = args = text = selected_event = row = ident = name = None
        item = yield
        if item[0] is _FINISH:
            hook_rows = item[1]
            break
        raw, timing = item
        if raw is missing or timing is missing:
            unknown.append('stream_timing_incomplete')
            actions.unknown.add('action_timing_unknown')
            if raw is missing:
                continue
            timing = {}
        if type(raw) is _ParsedDocument:
            event, raw_size = raw.graph, raw.raw_bytes
        else:
            raw_size, digest = text_size_sha256(raw)
            if timing.get('sha256') != digest:
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
            if (bounded_identity(ident)['state'] != 'present' or not isinstance(name,str)
                    or text_size_sha256(name)[0] > 64):
                unknown.append('native_hook_identity_missing')
            elif (ident not in hook_started and len(hook_started) >= CORRELATION_LIMIT
                  or ident not in hook_finished and len(hook_finished) >= CORRELATION_LIMIT):
                unknown.append('hook_correlation_limit')
            elif event['subtype'] == 'hook_started':
                if ident in hook_started:
                    unknown.append('duplicate_native_hook')
                hook_started[ident] = name
            else:
                if ident in hook_finished:
                    unknown.append('duplicate_native_hook')
                text = event.get('stdout')
                hook_finished[ident] = (name, text_size_sha256(text)[1] if isinstance(text,str) else None)
                code = event.get('exit_code')
                known_exit = type(code) is int and -128 <= code <= 255
                if not known_exit:
                    unknown.append('native_hook_exit_unobserved')
                if event.get('outcome') == 'success' and known_exit and code == 0 and isinstance(text,str):
                    delivery = hook_delivery(text)
                    count = len(delivery['items'])
                    if hook_source_items + count > 512:
                        unknown.append('source_delivery_limit')
                    else:
                        hook_source_items += count
                        hook_sources[ident] = dict(delivery=delivery, received_seconds=timing.get('received_seconds'))
                    delivery = None
                if event.get('outcome') != 'success' or (known_exit and code != 0):
                    fail('native_hook_failed')
        if event.get('type') == 'result':
            terminal_seen = True
            denials = event.get('permission_denials')
            if not isinstance(denials,list):
                unknown.append('permission_denials_missing_or_malformed')
            elif denials:
                fail('native_permission_denial')
                actions.observe_denials(denials)
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
                if raw_size > input_bytes:
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
                if bounded_identity(ident)['state'] != 'present':
                    unknown.append('memory_identity_unknown')
                elif ident not in calls and len(calls) >= CORRELATION_LIMIT:
                    unknown.append('memory_correlation_limit')
                else:
                    calls[ident] = name if text_size_sha256(name)[0] <= 128 else 'unknown'
            elif part.get('type') == 'tool_result' and ident in calls:
                # Include error envelopes and every repeated result, never only successful bodies.
                memory_bytes += canonical_size(part)
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
    for ident in set(hook_started) & set(hook_finished):
        if hook_started[ident] != hook_finished[ident][0]:
            unknown.append('native_hook_event_mismatch')
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
