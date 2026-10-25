import hashlib
import json
from pathlib import Path
import unittest

from check_source_fit_panel import source_body
from trial_codex_source_fit_stream import CODE_MODE_GUARD_ERROR, parse_codex_stream


PANEL = Path(__file__).resolve().parents[1] / 'trials/source-fit/recovery-2026-10-24/FROZEN.json'


class CodexSourceFitStreamTests(unittest.TestCase):
    def fixture_stream(self, *, pull_body=None, partial=False, pull_status='completed', extra_events=(), answer=None):
        panel = json.loads(PANEL.read_text())
        body = source_body(panel['records']['relay_mixed']) if pull_body is None else pull_body
        digest = hashlib.sha256(body.encode()).hexdigest()
        record_id = '12345678-1234-4234-8234-123456789abc'
        search_pull_args = dict(handle='search-handle', receipt_id='receipt-1', request_id='pull-request')
        search = dict(schema='cairn.mcp-search/1', scope=dict(repo='trial:task-eval'),
                      index=[dict(record_id=record_id, version=1, body_sha256=digest,
                                  summary='Cairn relay source', pull_arguments=search_pull_args)])
        if partial:
            pull = dict(selection=dict(record=dict(record_id=record_id, version=1,
                                                   scope=dict(repo='trial:task-eval'), body='')),
                        span=dict(offset=0, end=len(body[:100].encode()), total_bytes=len(body.encode()),
                                  source_sha256=digest, sha256=hashlib.sha256(body[:100].encode()).hexdigest(),
                                  body=body[:100]))
        else:
            pull = dict(selection=dict(record=dict(record_id=record_id, version=1,
                                                   scope=dict(repo='trial:task-eval'), body=body)))
        events = [
            dict(type='thread.started', thread_id='thread-fixture'),
            dict(type='turn.started'),
            dict(type='item.started', item=dict(id='item-search', type='mcp_tool_call', server='cairn',
                                                tool='cairn_search', arguments=dict(query='live relay'))),
            dict(type='item.completed', item=dict(id='item-search', type='mcp_tool_call', server='cairn',
                                                  tool='cairn_search', arguments=dict(query='live relay'),
                                                  status='completed', result=dict(content=[dict(type='text', text=json.dumps(search))]))),
            dict(type='item.started', item=dict(id='item-pull', type='mcp_tool_call', server='cairn',
                                                tool='cairn_pull', arguments=search_pull_args)),
            dict(type='item.completed', item=dict(id='item-pull', type='mcp_tool_call', server='cairn',
                                                  tool='cairn_pull', arguments=search_pull_args,
                                                  status=pull_status, result=dict(content=[dict(type='text', text=json.dumps(pull))]))),
            *extra_events,
            dict(type='item.completed', item=dict(id='item-answer', type='agent_message',
                                                  text=answer or 'Use 512 KiB live; 2048 KiB is offline history.')),
            dict(type='turn.completed', usage=dict(input_tokens=92111, cached_input_tokens=77056,
                                                   output_tokens=383)),
        ]
        return '\n'.join(json.dumps(event) for event in events) + '\n', panel

    @staticmethod
    def record_names():
        return {'12345678-1234-4234-8234-123456789abc': 'relay_mixed'}

    def test_success_records_one_turn_usage_tool_lineage_and_whole_source_without_body(self):
        stream, panel = self.fixture_stream()
        body = source_body(panel['records']['relay_mixed'])
        parsed = parse_codex_stream(stream, panel, 'buried_live_qualification',
                                    'current', self.record_names())
        self.assertEqual(parsed['turn_count'], 1)
        self.assertEqual(parsed['usage_semantics'], 'codex_exec_turn_reported')
        self.assertEqual(parsed['usage']['input_tokens'], 92111)
        self.assertEqual(parsed['usage']['cached_input_tokens'], 77056)
        self.assertEqual(parsed['reported_tokens'], 92494)
        self.assertEqual(parsed['resolved_model_id'], None)
        self.assertTrue(parsed['tool_results'][1]['pull_arguments_match_search'])
        delivery = parsed['tool_results'][1]['source_delivery']['items'][0]
        self.assertEqual(delivery['extent'], 'full_body')
        self.assertEqual(delivery['source_sha256'], hashlib.sha256(body.encode()).hexdigest())
        self.assertNotIn(body, json.dumps(parsed))

    def test_two_completed_turns_are_refused(self):
        stream, panel = self.fixture_stream(extra_events=[dict(type='turn.completed', usage=dict(
            input_tokens=1, cached_input_tokens=0, output_tokens=1))])
        with self.assertRaisesRegex(RuntimeError, 'codex_events_after_terminal|codex_turn_count_mismatch'):
            parse_codex_stream(stream, panel, 'buried_live_qualification', 'current',
                               self.record_names())

    def test_missing_result_is_refused(self):
        stream, panel = self.fixture_stream()
        events = [json.loads(line) for line in stream.splitlines()]
        events = [event for event in events if not (
            event.get('type') == 'item.completed' and event.get('item', {}).get('id') == 'item-pull')]
        with self.assertRaisesRegex(RuntimeError, 'codex_tool_result_missing'):
            parse_codex_stream('\n'.join(json.dumps(event) for event in events) + '\n', panel,
                               'buried_live_qualification', 'current',
                               self.record_names())

    def test_native_error_result_is_refused_and_partial_source_stays_explicit(self):
        stream, panel = self.fixture_stream(pull_status='failed')
        with self.assertRaisesRegex(RuntimeError, 'codex_tool_result_error'):
            parse_codex_stream(stream, panel, 'buried_live_qualification', 'current',
                               self.record_names())

        body = source_body(panel['records']['relay_mixed'])
        stream, panel = self.fixture_stream(partial=True)
        parsed = parse_codex_stream(stream, panel, 'buried_live_qualification',
                                    'current', self.record_names())
        delivery = parsed['tool_results'][1]['source_delivery']['items'][0]
        self.assertEqual(delivery['extent'], 'partial_span')
        self.assertEqual(delivery['source_sha256'], hashlib.sha256(body.encode()).hexdigest())
        self.assertEqual(parsed['source_validation'], 'match')
        self.assertFalse(parsed['whole_source_delivered'])

        stream, panel = self.fixture_stream(pull_body='wrong source body')
        with self.assertRaisesRegex(RuntimeError, 'codex_source_mismatch'):
            parse_codex_stream(stream, panel, 'buried_live_qualification',
                               'current', self.record_names())

    def test_malformed_missing_usage_answer_and_unexpected_tool_fail_closed(self):
        stream, panel = self.fixture_stream()
        with self.assertRaisesRegex(RuntimeError, 'codex_jsonl_malformed'):
            parse_codex_stream(stream + '{broken\n', panel, 'buried_live_qualification',
                               'current', self.record_names())

        events = [json.loads(line) for line in stream.splitlines()]
        events[-1].pop('usage')
        with self.assertRaisesRegex(RuntimeError, 'codex_usage_missing'):
            parse_codex_stream('\n'.join(map(json.dumps, events)), panel,
                               'buried_live_qualification', 'current', self.record_names())

        events = [json.loads(line) for line in stream.splitlines()]
        events = [event for event in events if not (
            event.get('type') == 'item.completed' and event.get('item', {}).get('type') == 'agent_message')]
        with self.assertRaisesRegex(RuntimeError, 'codex_answer_missing'):
            parse_codex_stream('\n'.join(map(json.dumps, events)), panel,
                               'buried_live_qualification', 'current', self.record_names())

        events = [json.loads(line) for line in stream.splitlines()]
        for event in events:
            item = event.get('item')
            if isinstance(item, dict) and item.get('type') == 'mcp_tool_call':
                item['tool'] = 'cairn_edit'
                break
        with self.assertRaisesRegex(RuntimeError, 'codex_unexpected_tool_call'):
            parse_codex_stream('\n'.join(map(json.dumps, events)), panel,
                               'buried_live_qualification', 'current', self.record_names())

    def test_direct_arm_rejects_every_tool_and_pull_must_match_exact_search_arguments(self):
        stream, panel = self.fixture_stream()
        with self.assertRaisesRegex(RuntimeError, 'codex_direct_tool_call'):
            parse_codex_stream(stream, panel, 'buried_live_qualification',
                               'direct', self.record_names())
        events = [json.loads(line) for line in stream.splitlines()]
        for event in events:
            item = event.get('item')
            if (isinstance(item, dict) and item.get('id') == 'item-pull'
                    and event.get('type') == 'item.started'):
                item['arguments'] = dict(item['arguments'], receipt_id='altered')
        with self.assertRaisesRegex(RuntimeError, 'codex_tool_identity_changed'):
            parse_codex_stream('\n'.join(map(json.dumps, events)), panel,
                               'buried_live_qualification', 'current', self.record_names())

    def test_retrieval_arm_may_answer_without_native_tool_calls(self):
        stream, panel = self.fixture_stream()
        events = [json.loads(line) for line in stream.splitlines()]
        events = [event for event in events if event.get('item', {}).get('type') != 'mcp_tool_call']
        parsed = parse_codex_stream('\n'.join(map(json.dumps, events)), panel,
                                    'buried_live_qualification', 'current', self.record_names())
        self.assertEqual(parsed['observed_tools'], [])
        self.assertEqual(parsed['source_validation'], 'no_source_delivery')
        self.assertFalse(parsed['whole_source_delivered'])

    def test_changed_duplicate_tool_id_and_server_error_are_rejected(self):
        stream, panel = self.fixture_stream()
        events = [json.loads(line) for line in stream.splitlines()]
        started = next(event for event in events if event.get('type') == 'item.started')
        events.insert(2, json.loads(json.dumps(started)))
        with self.assertRaisesRegex(RuntimeError, 'codex_tool_identity_duplicate'):
            parse_codex_stream('\n'.join(map(json.dumps, events)), panel,
                               'buried_live_qualification', 'current', self.record_names())

    def test_known_distractor_preview_outside_case_source_order_is_not_source_corruption(self):
        stream, panel = self.fixture_stream()
        events = [json.loads(line) for line in stream.splitlines()]
        search_event = next(event for event in events
                            if event.get('type') == 'item.completed'
                            and event.get('item', {}).get('id') == 'item-search')
        payload = json.loads(search_event['item']['result']['content'][0]['text'])
        distractor = source_body(panel['records']['trail_atlas'])
        payload['index'].append(dict(
            record_id='22345678-1234-4234-8234-123456789abc', version=1,
            body_sha256=hashlib.sha256(distractor.encode()).hexdigest(),
            summary='Trail motorcycle navigation distractor',
            pull_arguments=dict(handle='distractor-handle')))
        search_event['item']['result']['content'][0]['text'] = json.dumps(payload)
        names = dict(self.record_names(),
                     **{'22345678-1234-4234-8234-123456789abc': 'trail_atlas'})
        parsed = parse_codex_stream('\n'.join(map(json.dumps, events)), panel,
                                    'buried_live_qualification', 'current', names)
        previews = parsed['tool_results'][0]['source_delivery']['items']
        self.assertIn('preview', [item['extent'] for item in previews])
        self.assertEqual(parsed['source_validation'], 'match')
        self.assertEqual(parsed['whole_source_delivered'], True)
        events = [json.loads(line) for line in stream.splitlines()]
        for event in events:
            item = event.get('item')
            if isinstance(item, dict) and item.get('id') == 'item-pull' and event['type'] == 'item.completed':
                item['result']['is_error'] = True
        with self.assertRaisesRegex(RuntimeError, 'codex_tool_result_missing'):
            parse_codex_stream('\n'.join(map(json.dumps, events)), panel,
                               'buried_live_qualification', 'current', self.record_names())

    def test_expected_preturn_code_mode_guard_is_retained_but_late_error_fails(self):
        stream, panel = self.fixture_stream()
        events = [json.loads(line) for line in stream.splitlines()]
        guard = dict(type='item.completed',
                     item=dict(id='item_0', type='error', message=CODE_MODE_GUARD_ERROR))
        events.insert(1, guard)
        parsed = parse_codex_stream('\n'.join(map(json.dumps, events)), panel,
                                    'buried_live_qualification', 'current', self.record_names())
        self.assertEqual(parsed['guard_events'], ['code_mode_host_disabled_before_turn'])

        events = [json.loads(line) for line in stream.splitlines()]
        events.insert(2, guard)
        with self.assertRaisesRegex(RuntimeError, 'codex_unexpected_error_item'):
            parse_codex_stream('\n'.join(map(json.dumps, events)), panel,
                               'buried_live_qualification', 'current', self.record_names())

if __name__ == '__main__':
    unittest.main()
