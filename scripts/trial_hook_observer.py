#!/usr/bin/env python3
"""Run a pinned evaluation hook unchanged, retaining metadata for each invocation.

The evaluated engines expose main, handle and recall. The observer snapshots
recall status before handle releases its session lock; it never reads a later
state file and attributes that state to an earlier invocation.
"""
import argparse
import copy
import fcntl
import importlib.util
import json
from pathlib import Path
import sys
import time


RECALL_FIELDS = (
    'outcome', 'bytes', 'discovery', 'optional_deferred', 'coverage', 'inspected', 'rejected',
    'semantic_pages', 'semantic_previews', 'preview_count', 'preview_input_bytes',
    'preview_seconds', 'preview_reported_cost_usd', 'preview_receipt_budget_dropped',
    'preview_admitted', 'receipt_attempts', 'receipt_pull_calls', 'pull_seconds',
    'selector_input_bytes', 'shortlist_candidates', 'shortlist_source_extents',
    'model_seconds', 'model_reported_cost_usd', 'source_extent', 'elapsed_seconds',
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

    def observed_handle(config, event):
        name, source = event.get('hook_event_name'), event.get('source')
        record.update(event=name if name in ('SessionStart', 'UserPromptSubmit', 'PostToolUse',
                                            'PostToolUseFailure', 'PreCompact', 'SessionEnd', 'Stop') else 'unknown',
                      source=source if source in (None, 'startup', 'resume', 'compact', 'clear') else 'other')
        return handle(config, event)

    def observed_recall(memory, event, state=None):
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
                record['recall'] = copy.deepcopy({k: after[k] for k in RECALL_FIELDS if k in after})

    engine.handle, engine.recall = observed_handle, observed_recall
    sys.argv = [str(args.engine), '--config', str(args.config)]
    try:
        record['exit_code'] = engine.main()
        return record['exit_code']
    finally:
        record['process_seconds'] = time.monotonic() - started
        # Multiple host hooks can finish concurrently. A partial write or disk
        # failure must remain visible instead of silently losing measurements.
        with args.observations.open('a') as output:
            fcntl.flock(output, fcntl.LOCK_EX)
            output.write(json.dumps(record, separators=(',', ':')) + '\n')
            output.flush()


if __name__ == '__main__':
    sys.exit(main())
