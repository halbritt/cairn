#!/usr/bin/env python3
"""Opt-in real-model checkpoint selection check; synthetic dialogue, no DB writes."""
import argparse
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from test_claude_lifecycle import hook


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--claude', required=True)
    parser.add_argument('--model', required=True)
    args = parser.parse_args()
    config = dict(cairn='unused', claude=args.claude, socket='unused', token_file='unused',
                  repo='fixture:selection', model=args.model)
    session_id = 'c2449a2f-af0d-4c97-9931-78a36b5b68c9'
    memory = hook.Memory(config, session_id)
    cases = [
        ('decision', ['For this project, use PostgreSQL instead of SQLite because concurrent workers need transactional updates. The implementation and tests remain unfinished.',
                      'I will use PostgreSQL and implement the transaction tests next.']),
        ('lookup', ['What is 2+2?', '4.']),
        ('excluded', ['Do not save this conversation or its decisions in memory. For this exercise choose PostgreSQL.', 'Understood.']),
    ]
    with tempfile.TemporaryDirectory(prefix='cairn-real-selection-') as tmp:
        transcript = Path(tmp) / 'transcript.jsonl'
        for name, dialogue in cases:
            transcript.write_text('\n'.join(json.dumps(dict(type=role, message=dict(content=text)))
                                  for role, text in zip(['user', 'assistant'], dialogue)) + '\n')
            event = dict(hook_event_name='PreCompact', session_id=session_id, cwd=tmp, transcript_path=str(transcript))
            with patch.object(memory, 'checkpoint', return_value=None), \
                 patch.object(hook, 'durable_candidates', return_value=[]), \
                 patch.object(memory, 'call', return_value={'record_id': 'fixture-record'}) as write:
                hook.capture(memory, event)
                if name == 'decision':
                    drafts = [c.kwargs['payload']['draft'] for c in write.call_args_list]
                    assert any(d['kind'] == 'decision' for d in drafts), 'Decision was not separated'
                    assert any(d['kind'] == 'note' for d in drafts), 'Unfinished work was not checkpointed'
                    body = str(drafts)
                    assert 'PostgreSQL' in body and any(word in body.lower() for word in ('pending', 'unfinished', 'next')), 'Selection lost the decision or unfinished state'
                else:
                    assert write.call_count == 0, name + ' incorrectly selected for capture'
                print(name + ': passed; no database writes', flush=True)


if __name__ == '__main__':
    main()
