#!/usr/bin/env python3
"""Check real checkpoint selection against synthetic old progress; no database."""
import argparse
import json
import tempfile
import time
from unittest.mock import patch
from test_claude_lifecycle import hook

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--claude', required=True)
    parser.add_argument('--model', required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='cairn-checkpoint-') as tmp:
        event = dict(cwd=tmp, session_id='checkpoint-quality', hook_event_name='SessionEnd', workstream='Migration')
        title = hook.workstream_prefix(event) + 'Migration'
        previous = dict(record_id='existing-checkpoint', version=4, kind='note',
                        body=title+'\n\nGoal: ship the migration. Constraint: disposable databases only. '
                        'State: implementation done. Next: run transaction tests, security scan and deployment. '
                        'Source: docs/migration.md.')
        memory = hook.Memory(dict(cairn='unused', socket='unused', token_file='unused',
                                  repo='fixture', claude=args.claude, model=args.model), 'checkpoint-quality')
        for name, text in [
            ('partial', 'Transaction tests passed and deployment succeeded. The security scan is still outstanding; run it next.'),
            ('complete', 'The migration implementation, transaction tests, security scan and deployment are all complete. '
                         'All tests passed, the security scan found no issues and the deployed service passed its health check. '
                         'This closes the entire migration workstream.'),
            ('completed-continue', 'Continue. The migration workstream was fully completed; there is no new work or correction.'),
        ]:
            if name == 'completed-continue':
                previous['body'] = title + '\n\nStatus: complete\nAll migration work completed and deployed; verification passed.'
            event['messages'] = [dict(role='user', text=text), dict(role='assistant', text='Recorded the reported migration state.')]
            writes = []
            def save(operation, args=(), payload=None):
                writes.append((operation, payload))
                return dict(record_id=payload.get('record_id', 'new'))
            start = time.monotonic()
            with patch.object(memory, 'checkpoint', return_value=previous), \
                 patch.object(hook, 'handoff_candidates', return_value=[previous]), \
                 patch.object(hook, 'durable_candidates', return_value=[]), \
                 patch.object(memory, 'call', side_effect=save):
                hook.capture(memory, event, {})
            if name == 'completed-continue':
                assert not writes, 'completed work reopened without new work'
                print(json.dumps(dict(case=name, seconds=time.monotonic()-start, passed=True)), flush=True)
                continue
            assert len(writes) == 1 and writes[0][0] == 'revise', (name, writes)
            body = writes[0][1]['body']
            assert writes[0][1]['record_id'] == previous['record_id'], name
            assert len(body.encode()) < hook.CHECKPOINT_BYTES + 160, name
            assert 'Next: run transaction tests, security scan and deployment.' not in body, name
            assert ('Status: complete' in body) == (name == 'complete'), name
            if name == 'partial':
                assert 'security' in body.lower() and 'disposable' in body.lower(), name
            print(json.dumps(dict(case=name, seconds=time.monotonic()-start, bytes=len(body.encode()), passed=True)), flush=True)

if __name__ == '__main__':
    main()
