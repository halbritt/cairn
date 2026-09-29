"""Receipt consumption through the lifecycle hook's actual CLI JSON boundary."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    'receipt_hook', Path(__file__).resolve().parents[1] / 'integrations/lifecycle/memory.py')
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


class ReceiptCLI:
    """Expansion state follows core.Expand: refusal spends nothing; success spends one."""
    def __init__(self, bodies, spans=None):
        self.bodies = bodies
        self.credits = 4
        self.remaining = 24000
        self.pulls = []
        self.entries = [dict(record_id=name, version=1, summary='candidate',
                             pull_arguments=dict(receipt_id='receipt', handle=name, request_id=name))
                        for name in bodies]
        for entry in self.entries:
            if entry['record_id'] in (spans or {}):
                entry['match_span'] = dict(offset=0, length=spans[entry['record_id']])

    def __call__(self, command, *, body, **kwargs):
        if command[6] == 'search':
            data = dict(status='READY', destination=dict(name='hosted'), index=self.entries,
                        receipt_id='receipt', credits_remaining=self.credits, selected=[])
        else:
            assert command[6] == 'pull', command
            args = json.loads(body)
            name = args['handle']
            self.pulls.append((name, 'span' if 'span' in args else 'full'))
            record = dict(record_id=name, version=1, body=self.bodies[name], **{'class': 'A'})
            data = dict(selection=dict(record=record))
            if 'span' in args:
                start, length = args['span']['offset'], args['span']['length']
                excerpt = record['body'][start:start + length]
                data['span'] = dict(offset=start, end=start + len(excerpt), total_bytes=len(record['body']),
                                    body=excerpt, sha256=hashlib.sha256(excerpt.encode()).hexdigest(),
                                    source_sha256=hashlib.sha256(record['body'].encode()).hexdigest())
                record['body'] = ''
            cost = len(hook.encoded(data).encode()) + 256
            if self.credits == 0 or cost > self.remaining:
                return subprocess.CompletedProcess(command, 1, json.dumps(dict(status='BUDGET_REFUSED')), '')
            self.credits -= 1
            self.remaining -= cost
            data.update(credits_remaining=self.credits, bytes_remaining=self.remaining)
        return subprocess.CompletedProcess(command, 0, json.dumps(dict(ok=True, data=data)), '')


class ReceiptAccountingTests(unittest.TestCase):
    def test_delivered_notes_do_not_hide_fresh_guidance_or_consume_pull_slots(self):
        for count in (4, 10):
            with self.subTest(delivered=count), tempfile.TemporaryDirectory() as directory:
                Path(directory, '.git').mkdir()
                bodies = {str(i): 'Already delivered guidance' for i in range(count)}
                bodies['fresh'] = 'Use fresh guidance for this repair.'
                cli = ReceiptCLI(bodies)
                memory = hook.Memory(dict(cairn='fixture', socket='fixture', token_file='fixture',
                                          repo='fixture', semantic_fallback=True, context_bytes=9500),
                                     'unseen-guidance')
                event = dict(hook_event_name='UserPromptSubmit', cwd=directory, prompt='repair')
                state = dict(seen={str(i): 1 for i in range(count)})

                def selector(config, schema, prompt, request, timeout, stage='capture'):
                    if schema is hook.PREVIEW_SCHEMA:
                        return dict(structured_output=dict(indices=list(range(min(4, len(request['previews']))))))
                    return dict(structured_output=dict(index=0))

                with patch.object(hook, 'bounded_command', side_effect=cli), \
                     patch.object(hook, 'select_json', side_effect=selector):
                    result = hook.recall(memory, event, state)
                self.assertIn('Use fresh guidance', result.get('hookSpecificOutput', {}).get('additionalContext', ''))
                self.assertEqual(cli.pulls, [('fresh', 'full')])
                self.assertEqual(cli.credits, 3)
                self.assertEqual(state['seen']['fresh'], 1)

    def test_seen_semantic_page_does_not_hide_later_revised_guidance(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, '.git').mkdir()
            cli = ReceiptCLI({**{str(i): 'Already delivered guidance' for i in range(32)},
                              'revised': 'Renew the original lease before it expires.'})
            cli.entries[-1]['version'] = 2
            offsets = []

            def command(args, *, body, **kwargs):
                response = cli(args, body=body, **kwargs)
                envelope = json.loads(response.stdout)
                data = envelope['data']
                if args[6] == 'search':
                    data['index'] = []
                    if '--semantic' in args:
                        offset = int(args[args.index('--offset') + 1])
                        offsets.append(offset)
                        data.update(index=cli.entries[offset:offset + 32],
                                    discovery=dict(state='ready'),
                                    page=dict(next_offset=32) if offset == 0 else {})
                        data['selected'] = [dict(record=dict(body='Keep required context intact.'), mandatory=True)]
                else:
                    data['selection']['record']['version'] = 2
                return subprocess.CompletedProcess(args, response.returncode, json.dumps(envelope), response.stderr)

            def selector(config, schema, prompt, request, timeout, stage='capture'):
                if schema is hook.PREVIEW_SCHEMA:
                    return dict(structured_output=dict(indices=list(range(min(4, len(request['previews']))))))
                return dict(structured_output=dict(index=0))

            memory = hook.Memory(dict(cairn='fixture', socket='fixture', token_file='fixture',
                                      repo='fixture', semantic_fallback=True, context_bytes=9500),
                                 'unseen-semantic-guidance')
            event = dict(hook_event_name='UserPromptSubmit', cwd=directory, prompt='repair lease')
            state = dict(seen={**{str(i): 1 for i in range(32)}, 'revised': 1})
            with patch.object(hook, 'bounded_command', side_effect=command), \
                 patch.object(hook, 'select_json', side_effect=selector):
                result = hook.recall(memory, event, state)
            text = result.get('hookSpecificOutput', {}).get('additionalContext', '')
            self.assertIn('Renew the original lease', text)
            self.assertIn('Keep required context intact.', text)
            self.assertEqual(offsets, [0, 32])
            self.assertEqual(cli.pulls, [('revised', 'full')])
            self.assertEqual(cli.credits, 3)
            self.assertEqual(state['seen']['revised'], 2)

    def recall(self, cli, budget=9500):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, '.git').mkdir()
            memory = hook.Memory(dict(cairn='fixture', socket='fixture', token_file='fixture', repo='fixture',
                                      semantic_fallback=True, context_bytes=budget), 'receipt-test')
            event = dict(hook_event_name='SessionStart', cwd=directory, prompt='', workstream='Receipt accounting')
            state = {}
            with patch.object(hook, 'bounded_command', side_effect=cli), \
                 patch.object(hook, 'select_json', return_value=dict(structured_output=dict(index=0))):
                result = hook.recall(memory, event, state)
        return result, state

    def test_byte_refusal_does_not_hide_later_small_guidance(self):
        cli = ReceiptCLI(dict(large='x' * 30000, small='Use disposable databases for tests.'))
        result, state = self.recall(cli)
        self.assertIn('hookSpecificOutput', result)
        self.assertIn('Use disposable databases', result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(state['seen'], {'small': 1})
        self.assertEqual(cli.pulls, [('large', 'full'), ('small', 'full')])
        self.assertEqual(cli.credits, 3)
        self.assertEqual(state['last_recall']['receipt_attempts'], {'lexical': 2})
        self.assertEqual(state['last_recall']['receipt_pull_calls'], {'lexical': 2})

    def test_context_rejection_preserves_credit_for_later_small_guidance(self):
        cli = ReceiptCLI(dict(large1='x' * 5000, large2='y' * 5000, small='Useful small note'),
                         dict(large1=1800, large2=1800))
        result, state = self.recall(cli, budget=1500)
        self.assertIn('Useful small note', result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(state['seen'], {'small': 1})
        self.assertEqual(cli.credits, 1)
        self.assertEqual(cli.pulls, [('large1', 'full'), ('large2', 'full'), ('small', 'full')])
        self.assertEqual(state['last_recall']['receipt_attempts'], {'lexical': 3})
        self.assertEqual(state['last_recall']['receipt_pull_calls'], {'lexical': 3})
        self.assertEqual(state['last_recall']['rejected']['context_budget'], 2)

    def test_excerpts_from_full_bodies_spend_only_one_pull_each(self):
        cli = ReceiptCLI(dict(large1='x' * 5000, large2='y' * 5000, small='Useful small note'),
                         dict(large1=500, large2=500))
        result, state = self.recall(cli, budget=3000)
        self.assertIn('hookSpecificOutput', result)
        self.assertEqual(state['seen'], {})  # a span does not mark the whole source delivered
        self.assertEqual(state['last_recall']['partial_record'], 'large1')
        self.assertEqual(cli.credits, 1)
        self.assertEqual(cli.pulls, [('large1', 'full'), ('large2', 'full'), ('small', 'full')])
        self.assertEqual(state['last_recall']['receipt_attempts'], {'lexical': 3})
        self.assertEqual(state['last_recall']['receipt_pull_calls'], {'lexical': 3})
        self.assertEqual(state['last_recall']['shortlist_source_extents'],
                         dict(full_body=1, partial_span=2))

    def test_refused_full_body_can_still_use_span(self):
        cli = ReceiptCLI(dict(large='Use disposable databases. ' * 1500), dict(large=500))
        result, state = self.recall(cli)
        self.assertIn('Use disposable databases', result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(cli.credits, 3)
        self.assertEqual(cli.pulls, [('large', 'full'), ('large', 'span')])
        self.assertEqual(state['last_recall']['receipt_attempts'], {'lexical': 1})
        self.assertEqual(state['last_recall']['receipt_pull_calls'], {'lexical': 2})

    def test_last_credit_full_pull_does_not_send_span_request(self):
        cli = ReceiptCLI(dict(large='x' * 5000), dict(large=500))
        cli.credits = 1
        result, state = self.recall(cli, budget=3000)
        self.assertIn('"source_extent":"partial_span"', result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(state['seen'], {})
        self.assertEqual(cli.credits, 0)
        self.assertEqual(cli.pulls, [('large', 'full')])
        self.assertEqual(state['last_recall']['receipt_pull_calls'], {'lexical': 1})

    def test_replayed_response_cannot_restore_consumed_credits(self):
        memory = hook.Memory(dict(cairn='fixture', socket='fixture', token_file='fixture', repo='fixture'),
                             'replay-test')
        def response(credits):
            return subprocess.CompletedProcess([], 0, json.dumps(dict(ok=True, data=dict(credits_remaining=credits))), '')
        with patch.object(hook, 'bounded_command', side_effect=[response(3), response(1), response(3), response(0)]) as cli:
            for request in ('first', 'later', 'first'):
                memory.call('pull', payload=dict(receipt_id='receipt', request_id=request, handle=request))
            self.assertEqual(memory.receipt_credits['receipt'], 1)
            memory.call('pull', payload=dict(receipt_id='receipt', request_id='last', handle='last'))
            with self.assertRaises(hook.BudgetRefused):
                memory.call('pull', payload=dict(receipt_id='receipt', request_id='extra', handle='extra'))
        self.assertEqual(cli.call_count, 4)


if __name__ == '__main__':
    unittest.main()
