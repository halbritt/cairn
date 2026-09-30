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
    'duration_ms', 'error_type',
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--observations', type=Path, required=True)
    args = parser.parse_args()
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
    if hasattr(engine, 'Memory') and hasattr(engine.Memory, 'call'):
        counts = {key: 0 for key in ('search', 'pull', 'pull-evidence', 'history', 'other')}
        record['memory_call_attempts'] = counts
        original_call = engine.Memory.call
        def observed_call(memory, operation, *args, **kwargs):
            counts[operation if operation in counts else 'other'] += 1
            return original_call(memory, operation, *args, **kwargs)
        engine.Memory.call = observed_call

    def observed_handle(config, event, **kwargs):
        name, source = event.get('hook_event_name'), event.get('source')
        record.update(event=name if name in ('SessionStart', 'UserPromptSubmit', 'PostToolUse',
                                            'PostToolUseFailure', 'PreCompact', 'SessionEnd', 'Stop') else 'unknown',
                      source=source if source in (None, 'startup', 'resume', 'compact', 'clear') else 'other')
        try:
            return handle(config, event, **kwargs)
        finally:
            if recall_status is not None:
                record['recall'] = copy.deepcopy({k: recall_status[k] for k in RECALL_FIELDS if k in recall_status})

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
    try:
        with contextlib.redirect_stdout(captured):
            record['exit_code'] = engine.main()
        return record['exit_code']
    finally:
        raw = captured.getvalue()
        record['stdout_bytes'] = len(raw.encode())
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
