import base64
import copy
import json
import unittest

from ..frames import FrameError, Frames
from ..evidence import EvidenceError, Ledger, PULL, SEARCH
from .test_selected_evidence import search, partial, REQUEST, PULL_REQUEST, RECEIPT, HANDLE, BODY


SESSION = '70000000-0000-4000-8000-000000000001'
MODEL = 'test-model'
INIT = {'type': 'system', 'subtype': 'init', 'session_id': SESSION, 'model': MODEL,
        'claude_code_version': 'test-version', 'permissionMode': 'dontAsk',
        'tools': [SEARCH, PULL], 'mcp_servers': [{'name': 'cairn', 'status': 'connected', 'source': 'dynamic'}]}
FINAL = {'type': 'result', 'session_id': SESSION, 'subtype': 'success', 'is_error': False,
         'result': json.dumps({'action': 'preserve the consumed target', 'unknown': 'capacity'}),
         'modelUsage': {MODEL: {'provider': 'firstParty', 'inputTokens': 10, 'outputTokens': 5,
                              'cacheReadInputTokens': 0, 'cacheCreationInputTokens': 0}},
         'total_cost_usd': 0.001, 'num_turns': 2}


def wire(event):
    return (json.dumps(event, ensure_ascii=False) + '\n').encode()


def blocks(role, values):
    return {'type': role, 'session_id': SESSION, 'message': {'content': values}}


def call(name=SEARCH, args=None, key='s'):
    return blocks('assistant', [{'type': 'tool_use', 'id': key, 'name': name,
                'input': args if args is not None else {'request_id': REQUEST, 'query': 'actual lexical 😀 phrase'}}])


def reply(value=None, key='s'):
    return blocks('user', [{'type': 'tool_result', 'tool_use_id': key,
                           'content': [{'type': 'text', 'text': json.dumps(value if value is not None else search())}]}])


def reader():
    return Frames(SESSION, MODEL, 'test-version')


class FrameTest(unittest.TestCase):
    def test_selected_search_and_span_survive_arbitrary_utf8_chunk_boundaries(self):
        r = reader()
        args = {'request_id': PULL_REQUEST, 'receipt_id': RECEIPT, 'handle': HANDLE,
                'span': {'offset': 14, 'length': 12}}
        events = [INIT, call(), reply(), call(PULL, args, 'p'), reply(partial(), 'p'), FINAL]
        for byte in b''.join(wire(e) for e in events):
            r.feed(bytes([byte]))
        got = r.finish(0)
        self.assertEqual(got['init']['servers'][0]['source'], 'dynamic')
        evidence = got['evidence']
        self.assertEqual(evidence['calls']['s']['arguments']['query'], 'actual lexical 😀 phrase')
        self.assertEqual(base64.b64decode(evidence['responses']['p']['source']['span']['body_base64']), BODY.encode()[14:26])
        self.assertEqual(got['observed_frame_count'], 6)

    def test_plain_transcript_and_unknown_payloads_are_not_retained(self):
        r = reader()
        init = copy.deepcopy(INIT); init['unselected_private'] = 'RAW_TRANSCRIPT_SENTINEL'
        r.feed(wire(init))
        r.feed(wire(blocks('assistant', [{'type': 'text', 'text': 'RAW_TRANSCRIPT_SENTINEL'},
                    {'type': 'thinking', 'thinking': 'RAW_TRANSCRIPT_SENTINEL'}])))
        r.feed(wire(blocks('user', 'RAW_TRANSCRIPT_SENTINEL')))
        final = copy.deepcopy(FINAL); final['private_sdk_field'] = 'RAW_TRANSCRIPT_SENTINEL'
        final['modelUsage'][MODEL]['private_field'] = 'RAW_TRANSCRIPT_SENTINEL'
        r.feed(wire(final))
        self.assertNotIn('RAW_TRANSCRIPT_SENTINEL', json.dumps(r.finish(0)))

    def test_metadata_session_tools_server_model_and_source_fail_closed(self):
        changes = [('session_id', 'other'), ('model', 'other'), ('permissionMode', 'default'),
                   ('claude_code_version', 'other'), ('tools', [SEARCH, PULL, 'Bash']),
                   ('mcp_servers', [{'name': 'cairn', 'status': 'connected', 'source': 'unreviewed'}]),
                   ('mcp_servers', [{'name': 'cairn', 'status': 'connected', 'private_key': 'SENTINEL'}])]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                r = reader(); event = copy.deepcopy(INIT); event[key] = value
                with self.assertRaises(FrameError): r.feed(wire(event))
                self.assertIsNone(r.init)
                with self.assertRaisesRegex(FrameError, 'reader_unavailable'): r.finish(0)

    def test_final_and_exit_are_both_required_no_recovery_after_failure(self):
        for mode in ['no_final', 'partial_line', 'nonzero', 'bool_exit', 'before_init']:
            with self.subTest(mode=mode):
                r = reader()
                if mode == 'before_init':
                    with self.assertRaisesRegex(FrameError, 'event_before_init'): r.feed(wire(FINAL))
                    continue
                r.feed(wire(INIT))
                if mode != 'no_final': r.feed(wire(FINAL))
                if mode == 'partial_line': r.feed(b'{"type":')
                code = 1 if mode == 'nonzero' else False if mode == 'bool_exit' else 0
                with self.assertRaisesRegex(FrameError, 'incomplete_stream_or_exit'): r.finish(code)
                with self.assertRaisesRegex(FrameError, 'reader_unavailable'): r.feed(b'')

    def test_event_after_final_and_unanswered_calls_never_complete(self):
        r = reader(); r.feed(wire(INIT)); r.feed(wire(call()))
        with self.assertRaisesRegex(FrameError, 'unanswered_tool_call'): r.feed(wire(FINAL))
        r = reader(); r.feed(wire(INIT)); r.feed(wire(FINAL))
        with self.assertRaisesRegex(FrameError, 'event_after_final'): r.feed(wire(call()))
        with self.assertRaisesRegex(FrameError, 'reader_unavailable'): r.finish(0)

    def test_role_allowlist_and_event_transaction(self):
        for block in [{'type': 'tool_use', 'id': 'bad', 'name': ['Bash'], 'input': {}},
                      {'type': 'tool_result', 'tool_use_id': 'missing', 'content': ''},
                      {'type': 'unrecognized', 'payload': 'PRIVATE'}]:
            with self.subTest(block=block):
                r = reader(); r.feed(wire(INIT))
                event = call(); event['message']['content'].append(block)
                with self.assertRaises(FrameError): r.feed(wire(event))
                self.assertEqual(r.ledger.calls, {})

    def test_protocol_json_rejects_duplicate_keys_nonfinite_invalid_utf8_and_deep_nesting(self):
        cases = [b'{"type":"a","type":"b"}\n', b'{"number":NaN}\n', b'\xff\n',
                 b'{"x":' + b'['*40 + b'0' + b']'*40 + b'}\n']
        for data in cases:
            with self.subTest(data=data[:60]):
                r = reader()
                with self.assertRaises(FrameError): r.feed(data)
                self.assertEqual(r.ledger.calls, {})

    def test_frame_bounds_before_unbounded_buffer_or_call_growth(self):
        r = reader()
        with self.assertRaisesRegex(FrameError, 'chunk_bounds'): r.feed(b'x'*65537)
        r = reader()
        for _ in range(16): r.feed(b'x'*65536)
        with self.assertRaisesRegex(FrameError, 'line_bounds'): r.feed(b'x')

    def test_error_result_retains_no_private_error_message_and_no_unmatched_calls(self):
        r = reader(); r.feed(wire(INIT)); r.feed(wire(call()))
        event = reply(); event['message']['content'][0].update(is_error=True, content='PRIVATE_FAILURE_SENTINEL')
        r.feed(wire(event)); r.feed(wire(FINAL)); got = r.finish(0)
        self.assertNotIn('PRIVATE_FAILURE_SENTINEL', json.dumps(got))
        self.assertTrue(got['evidence']['responses']['s']['is_error'])
        r = reader(); r.feed(wire(INIT))
        with self.assertRaisesRegex(FrameError, 'response_identity'): r.feed(wire(reply()))

    def test_usage_and_final_text_are_typed_bounded_and_not_correctness(self):
        mutations = [('modelUsage', {MODEL: {'provider': 'other'}}),
                     ('is_error', True), ('result', 'plain prose'), ('result', json.dumps({'x': 'z'*8192})),
                     ('num_turns', True), ('total_cost_usd', -1)]
        for key, value in mutations:
            with self.subTest(key=key):
                r = reader(); r.feed(wire(INIT)); event = copy.deepcopy(FINAL); event[key] = value
                with self.assertRaises(FrameError): r.feed(wire(event))
                self.assertIsNone(r.final)

    def test_giant_cost_poison_is_terminal_before_any_later_success(self):
        r = reader(); r.feed(wire(INIT)); event = copy.deepcopy(FINAL)
        event['total_cost_usd'] = 10 ** 399
        with self.assertRaisesRegex(FrameError, 'usage_cost'): r.feed(wire(event))
        self.assertIsNone(r.final)
        with self.assertRaisesRegex(FrameError, 'reader_unavailable'): r.feed(wire(FINAL))
        with self.assertRaisesRegex(FrameError, 'reader_unavailable'): r.finish(0)

    def test_utf16_and_utf8_bom_are_not_jsonl_or_final_json(self):
        for data in [json.dumps(INIT).encode('utf-16-le') + b'\n', b'\xef\xbb\xbf' + wire(INIT)]:
            with self.subTest(encoding=data[:4]):
                r = reader()
                with self.assertRaisesRegex(FrameError, 'invalid_frame_json'): r.feed(data)
                self.assertIsNone(r.init)
                with self.assertRaisesRegex(FrameError, 'reader_unavailable'): r.finish(0)
        r = reader(); r.feed(wire(INIT)); event = copy.deepcopy(FINAL)
        event['result'] = '\ufeff' + event['result']
        with self.assertRaisesRegex(FrameError, 'invalid_frame_json'): r.feed(wire(event))
        self.assertIsNone(r.final)

    def test_unhashable_class_and_entity_kind_are_typed_refusals(self):
        for value in [[], {}]:
            with self.subTest(value=value):
                ledger = Ledger(); ledger.request('s', SEARCH, {'request_id': REQUEST})
                result = search(); result['index'][0]['class'] = value
                with self.assertRaisesRegex(EvidenceError, 'record_class'): ledger.response('s', json.dumps(result))
                self.assertEqual(ledger.responses, {})
                with self.assertRaisesRegex(EvidenceError, 'entities_shape'):
                    Ledger().request('s', SEARCH, {'entities': [{'kind': value, 'name': 'known'}]})


if __name__ == '__main__':
    unittest.main()
