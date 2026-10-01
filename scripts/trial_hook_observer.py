#!/usr/bin/env python3
"""Run a pinned evaluation hook unchanged, retaining metadata for each invocation.

The evaluated engines expose main, handle and recall. The observer retains this
invocation's status object and snapshots it after handle finishes updating it;
it never reads a later state file and attributes it to an earlier invocation.
"""
import argparse
import copy
import contextlib
import hashlib
import io
import fcntl
import importlib.util
import json
import re
from pathlib import Path
import sys
import time
from trial_source_delivery import hook_delivery


RECALL_FIELDS = (
    'outcome', 'bytes', 'discovery', 'optional_deferred', 'coverage', 'inspected', 'rejected',
    'semantic_pages', 'semantic_previews', 'preview_count', 'preview_input_bytes',
    'preview_seconds', 'preview_reported_cost_usd', 'preview_receipt_budget_dropped',
    'preview_admitted', 'receipt_attempts', 'receipt_pull_calls', 'pull_seconds',
    'selector_input_bytes', 'shortlist_candidates', 'shortlist_source_extents',
    'model_seconds', 'model_reported_cost_usd', 'source_extent', 'elapsed_seconds',
    'preview_process', 'model_process',
    'duration_ms', 'error_type', 'recall_task_scope',
)


class CountedStderr:
    """Count emitted error bytes while preserving immediate stderr forwarding."""
    def __init__(self, stream):
        self.stream, self.bytes = stream, 0
    def write(self, text):
        self.bytes += len(text.encode())
        return self.stream.write(text)
    def flush(self):
        self.stream.flush()


def bounded_identity(value):
    if value is None:
        return dict(state='absent', sha256=None)
    try:
        if not isinstance(value,str) or not value.strip() or len(value.encode()) > 256 or any(ord(c)<32 for c in value):
            raise ValueError('invalid identity')
        return dict(state='present', sha256=hashlib.sha256(value.encode()).hexdigest())
    except (ValueError,UnicodeError):
        return dict(state='invalid', sha256=None)


def prompt_metadata(event, frozen_sha256=None):
    identity=bounded_identity(event.get('prompt_id'))
    source=event.get('source')
    origin=(source if isinstance(source,str) and source in (
        'user','sdk','system','loop_wakeup','schedule_wakeup','poll_event',
        'startup','resume','compact','clear') else 'absent' if 'source' not in event else 'unknown')
    text=event.get('prompt')
    matches=None
    if frozen_sha256 is not None and isinstance(text,str):
        try:
            matches=hashlib.sha256(text.encode()).hexdigest()==frozen_sha256
        except UnicodeError:
            pass
    return dict(prompt_id_state=identity['state'],prompt_id_sha256=identity['sha256'],
                origin=origin,matches_frozen_prompt=matches)


def bounded_search_discovery(value):
    """Retain API discovery identity without arbitrary reason/query text."""
    states = ('ready', 'unavailable', 'invalid_result', 'not_needed')
    if not isinstance(value, dict) or value.get('state') not in states:
        return dict(state='unknown')
    result = dict(state=value['state'])
    for key in ('model_sha256', 'scores_sha256'):
        field = value.get(key)
        if isinstance(field, str) and re.fullmatch('[0-9a-f]{64}', field):
            result[key] = field
    algorithm = value.get('algorithm')
    if algorithm == 'bge-original-passages/1':
        result['algorithm'] = algorithm
    coverage = value.get('coverage')
    if (isinstance(coverage, dict)
            and all(type(coverage.get(k)) is int and 0 <= coverage[k] <= 2**63 - 1
                    for k in ('indexed', 'eligible'))
            and coverage['indexed'] <= coverage['eligible']):
        result['coverage'] = {k: coverage[k] for k in ('indexed', 'eligible')}
    return result


MAX_SEARCH_RECEIPTS = 16
# collectCandidates initializes the digest; compileSnapshot, withEntitySchema and
# rankIndexed change these schema labels without replacing Query.
DIGEST_SOURCE_SCHEMAS = tuple('cairn.semantic/' + str(version)
                              for version in (3, 8, 9, 10, 11, 12, 13, 14, 16, 17))


def bounded_search_receipt(value):
    """Project only known digest-bearing search views and canonical receipt identity."""
    if (not isinstance(value, dict) or value.get('schema') != 'cairn.agent-search/1'
            or value.get('source_schema') not in DIGEST_SOURCE_SCHEMAS):
        return dict(state='unknown')
    query, receipt = value.get('query'), value.get('receipt_id')
    if (not isinstance(query, str) or not re.fullmatch('sha256:[0-9a-f]{64}', query)
            or not isinstance(receipt, str)
            or not re.fullmatch('[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', receipt)):
        return dict(state='unknown')
    return dict(state='observed', query_sha256=query[7:], receipt_id=receipt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--observations', type=Path, required=True)
    parser.add_argument('--frozen-prompt-sha256')
    args = parser.parse_args()
    if args.frozen_prompt_sha256 is not None and not re.fullmatch('[0-9a-f]{64}',args.frozen_prompt_sha256):
        parser.error('invalid frozen prompt digest')
    started = time.monotonic()
    spec = importlib.util.spec_from_file_location('evaluated_hook', args.engine)
    engine = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = engine
    spec.loader.exec_module(engine)
    handle, recall = engine.handle, engine.recall
    record = dict(schema='cairn.task-hook-observation/1', event=None, source=None,
                  recall_attempted=False, recall=None, exit_code=None)
    recall_status = None
    record['memory_call_attempts'] = None
    record['search_receipts'] = None
    record['search_receipts_omitted'] = None
    if hasattr(engine, 'Memory') and hasattr(engine.Memory, 'call'):
        counts = {key: 0 for key in ('search', 'pull', 'pull-evidence', 'history', 'other')}
        record['memory_call_attempts'] = counts
        record['search_receipts'] = []
        record['search_receipts_omitted'] = 0
        original_call = engine.Memory.call
        def observed_call(memory, operation, *args, **kwargs):
            counts[operation if operation in counts else 'other'] += 1
            result = None
            try:
                result = original_call(memory, operation, *args, **kwargs)
                return result
            finally:
                if operation == 'search':
                    if len(record['search_receipts']) < MAX_SEARCH_RECEIPTS:
                        record['search_receipts'].append(bounded_search_receipt(result))
                    else:
                        record['search_receipts_omitted'] += 1
        engine.Memory.call = observed_call

    def observed_handle(config, event, **kwargs):
        name, source = event.get('hook_event_name'), event.get('source')
        record['prompt_metadata'] = prompt_metadata(event,args.frozen_prompt_sha256)
        record.update(event=name if name in ('SessionStart', 'UserPromptSubmit', 'PostToolUse',
                                            'PostToolUseFailure', 'PreCompact', 'SessionEnd', 'Stop') else 'unknown',
                      source=source if source in (None, 'startup', 'resume', 'compact', 'clear') else 'other')
        try:
            return handle(config, event, **kwargs)
        finally:
            if recall_status is not None:
                record['recall'] = copy.deepcopy({k: recall_status[k] for k in RECALL_FIELDS if k in recall_status})
                if 'search_discovery' in recall_status:
                    record['recall']['search_discovery'] = bounded_search_discovery(recall_status['search_discovery'])

    def observed_recall(memory, event, state=None):
        nonlocal recall_status
        record['recall_attempted'] = True
        before = state.get('last_recall') if state is not None else None
        before_value = copy.deepcopy(before)
        recall_started = time.monotonic()
        try:
            return recall(memory, event, state)
        finally:
            record['recall_seconds'] = time.monotonic() - recall_started
            after = state.get('last_recall') if state is not None else None
            if after is not None and (after is not before or after != before_value):
                recall_status = after

    engine.handle, engine.recall = observed_handle, observed_recall
    sys.argv = [str(args.engine), '--config', str(args.config)]
    captured = io.StringIO()
    errors = CountedStderr(sys.stderr)
    try:
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(errors):
            record['exit_code'] = engine.main()
        return record['exit_code']
    finally:
        raw = captured.getvalue()
        record['stdout_bytes'] = len(raw.encode())
        record['stderr_bytes'] = errors.bytes
        record['stdout_sha256'] = hashlib.sha256(raw.encode()).hexdigest()
        record['source_delivery'] = hook_delivery(raw)
        record['process_seconds'] = time.monotonic() - started
        # Multiple host hooks can finish concurrently. A partial write or disk
        # failure must remain visible instead of silently losing measurements.
        with args.observations.open('a') as output:
            fcntl.flock(output, fcntl.LOCK_EX)
            output.write(json.dumps(record, separators=(',', ':')) + '\n')
            output.flush()
        sys.stdout.write(raw)
        sys.stdout.flush()


if __name__ == '__main__':
    sys.exit(main())
