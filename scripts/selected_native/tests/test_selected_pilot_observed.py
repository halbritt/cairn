"""Regression fixtures for the native-interface closure (pure, constructed input only).

Run in a directory that holds evidence.py, frames.py, observe.py and the reviewed
test_evidence.py builders. The same file runs against the ORIGINAL modules (fix tests
are expected RED) and against the candidate (all GREEN). No native child, provider,
socket, database or credential is touched; fixture values that were observed in the
two consumed pilot arms (claude 2.1.295) are marked OBSERVED, everything else is
CONSTRUCTED and says so.
"""
import json
import unittest

from .. import evidence
from .. import frames

from ..evidence import PULL, SEARCH, Ledger
from ..frames import FrameError, Frames
from .test_selected_evidence import BODY, HANDLE, PULL_REQUEST, RECEIPT, partial, search

# OBSERVED (RESULT.json of consumed arm A; scalar init fields retained by observe.py).
SESSION = 'dcf1d094-e993-40b1-8f65-8b64b3ed07a5'
MODEL = 'claude-opus-5-5'
VERSION = '2.1.295'
OBSERVED_INIT = {'type': 'system', 'subtype': 'init', 'session_id': SESSION, 'model': MODEL,
                 'claude_code_version': VERSION, 'permissionMode': 'dontAsk', 'apiKeySource': 'none',
                 'tools': ['mcp__cairn__cairn_pull', 'mcp__cairn__cairn_search'],
                 'mcp_servers': [{'name': 'cairn', 'status': 'connected', 'source': 'dynamic'}]}
# OBSERVED label counts in arm A: ("system","thinking_tokens") x18, ("system","ui_invalidate") x1,
# ("rate_limit_event", None) x1. Payload keys of those events were never retained: CONSTRUCTED here.
HOUSEKEEPING = [{'type': 'system', 'subtype': 'thinking_tokens', 'session_id': SESSION, 'payload': 'SENTINEL'},
                {'type': 'system', 'subtype': 'ui_invalidate', 'payload': 'SENTINEL'},
                {'type': 'rate_limit_event', 'payload': 'SENTINEL'}]
# OBSERVED modelUsage/usage key set of the same CLI (single model, firstParty); values CONSTRUCTED.
OBSERVED_FINAL = {'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': SESSION,
                  'num_turns': 5, 'total_cost_usd': 0.17405120000000002,
                  'result': json.dumps({'facts': [], 'next_action': 'x', 'avoid': [], 'unknowns': [], 'sources': []}),
                  'usage': {'input_tokens': 6, 'service_tier': 'standard', 'speed': 'standard'},
                  'modelUsage': {MODEL: {'inputTokens': 6, 'outputTokens': 4690, 'cacheReadInputTokens': 11096,
                                         'cacheCreationInputTokens': 9751, 'webSearchRequests': 0,
                                         'costUSD': 0.17405120000000002, 'contextWindow': 1000000,
                                         'maxOutputTokens': 128000, 'thinkingTokens': 1949,
                                         'canonicalModel': MODEL, 'provider': 'firstParty', 'costBasis': 'list'}}}
REFUSAL = 'BUDGET_REFUSED: expansion credits or remaining context bytes exhausted'  # OBSERVED length 70


def wire(event):
    return (json.dumps(event, ensure_ascii=False) + '\n').encode()


def feed_all(reader, events):
    for event in events:
        reader.feed(wire(event))


def message(role, blocks):
    return {'type': role, 'session_id': SESSION, 'message': {'content': blocks}}


def search_call(key='s'):
    return message('assistant', [{'type': 'tool_use', 'id': key, 'name': SEARCH,
                                  'input': {'request_id': '40000000-0000-4000-8000-000000000001', 'query': 'q',
                                            'semantic': True, 'memory_budget_bytes': 9500, 'min_pull_bytes': 5000}}])


def search_reply(key='s', content=None):
    return message('user', [{'type': 'tool_result', 'tool_use_id': key,
                             'content': content if content is not None else [{'type': 'text', 'text': json.dumps(search())}]}])


class FrameEvents(unittest.TestCase):
    def reader(self):
        return Frames(SESSION, MODEL, VERSION)

    def test_guard_observed_init_and_final_key_sets_are_accepted(self):
        reader = self.reader()
        feed_all(reader, [OBSERVED_INIT, OBSERVED_FINAL])
        done = reader.finish(0)
        self.assertEqual(done['init']['servers'][0]['source'], 'dynamic')
        self.assertEqual(done['final']['reported_turns'], 5)

    def test_fix_observed_housekeeping_events_are_counted_by_label_only(self):
        reader = self.reader()
        feed_all(reader, [OBSERVED_INIT, HOUSEKEEPING[0], search_call(), HOUSEKEEPING[0], HOUSEKEEPING[1],
                          search_reply(), HOUSEKEEPING[2], OBSERVED_FINAL])
        done = reader.finish(0)
        self.assertEqual(done['event_labels'], {'["system", "thinking_tokens"]': 2, '["system", "ui_invalidate"]': 1,
                                                '["rate_limit_event", "None"]': 1})
        self.assertNotIn('SENTINEL', json.dumps(done))
        self.assertEqual(done['observed_frame_count'], 8)

    def test_fix_housekeeping_before_init_and_after_final_are_labels_not_failures(self):
        reader = self.reader()
        feed_all(reader, [HOUSEKEEPING[1], OBSERVED_INIT, OBSERVED_FINAL, HOUSEKEEPING[2]])
        self.assertEqual(sum(reader.finish(0)['event_labels'].values()), 2)

    def test_guard_housekeeping_with_foreign_session_still_fails_closed(self):
        reader = self.reader()
        feed_all(reader, [OBSERVED_INIT])
        with self.assertRaises(FrameError) as caught:
            reader.feed(wire(dict(HOUSEKEEPING[0], session_id='70000000-0000-4000-8000-000000000009')))
        self.assertEqual(str(caught.exception), 'session_mismatch')

    def test_fix_label_count_and_text_are_bounded(self):
        reader = self.reader()
        feed_all(reader, [OBSERVED_INIT])
        for i in range(32):
            reader.feed(wire({'type': 'system', 'subtype': 'x%d' % i}))
        with self.assertRaises(FrameError) as caught:
            reader.feed(wire({'type': 'system', 'subtype': 'one-too-many'}))
        self.assertEqual(str(caught.exception), 'event_label_bounds')
        longer = self.reader()
        feed_all(longer, [OBSERVED_INIT, {'type': 't' * 500, 'subtype': 's' * 500}, OBSERVED_FINAL])
        (label,) = longer.finish(0)['event_labels']
        self.assertLessEqual(len(label), 64 * 2 + 8)

    def test_guard_conversation_events_after_final_remain_refused(self):
        reader = self.reader()
        feed_all(reader, [OBSERVED_INIT, OBSERVED_FINAL])
        with self.assertRaises(FrameError) as caught:
            reader.feed(wire(message('assistant', [{'type': 'text', 'text': 'late'}])))
        self.assertEqual(str(caught.exception), 'event_after_final')

    def test_fix_failed_event_after_label_poisons_reader(self):
        reader = self.reader()
        feed_all(reader, [OBSERVED_INIT, HOUSEKEEPING[0]])
        with self.assertRaises(FrameError):
            reader.feed(wire(message('assistant', [{'type': 'tool_use', 'id': 'x', 'name': 'Bash', 'input': {}}])))
        with self.assertRaises(FrameError) as caught:
            reader.feed(wire(HOUSEKEEPING[1]))
        self.assertEqual(str(caught.exception), 'reader_unavailable')

    def test_guard_unreviewed_tool_and_init_tool_set_stay_strict(self):
        reader = self.reader()
        init = dict(OBSERVED_INIT, tools=OBSERVED_INIT['tools'] + ['EndConversation'])
        with self.assertRaises(FrameError) as caught:
            reader.feed(wire(init))
        self.assertEqual(str(caught.exception), 'tool_allowlist')


class ToolResultShapes(unittest.TestCase):
    def ledger(self):
        ledger = Ledger()
        ledger.request('s', SEARCH, {'request_id': '40000000-0000-4000-8000-000000000001', 'query': 'q'})
        return ledger

    def test_guard_success_content_as_one_text_block_or_plain_string(self):
        for content in ([{'type': 'text', 'text': json.dumps(search())}], json.dumps(search())):
            with self.subTest(shape=type(content).__name__):
                ledger = self.ledger()
                ledger.response('s', content)
                self.assertIn('source_schema', ledger.selected()['responses']['s'])

    def test_guard_error_text_is_never_retained(self):
        for content in (REFUSAL + ' SENTINEL', [{'type': 'text', 'text': REFUSAL + ' SENTINEL'}]):
            ledger = self.ledger()
            ledger.response('s', content, True)
            self.assertNotIn('SENTINEL', json.dumps(ledger.selected()))

    def test_guard_original_evidence_keys_do_not_change_for_existing_consumers(self):
        ledger = self.ledger()
        ledger.response('s', [{'type': 'text', 'text': json.dumps(search())}])
        stored = ledger.selected()['responses']['s']
        for key in ('request_id', 'receipt_id', 'source_schema', 'bytes_remaining', 'credits_remaining',
                    'selected_sources', 'index', 'metadata_basis'):
            self.assertIn(key, stored)


    def test_known_boundary_ledger_numeric_caps_follow_the_117_contract(self):
        # Documentation of a compatibility edge, not a fix: current HEAD can issue larger rooms
        # (first-fitting-whole/2, memory-budget/4), the reviewed Ledger caps stay at the 117 values.
        ledger = self.ledger()
        too_large = search()
        too_large['bytes_remaining'] = 24001
        with self.assertRaises(evidence.EvidenceError):
            ledger.response('s', json.dumps(too_large))
        ledger = Ledger()
        with self.assertRaises(evidence.EvidenceError):
            ledger.request('s', SEARCH, {'query': 'q', 'inspection_policy': 'first-fitting-whole/2'})


class ChargeIdentity(unittest.TestCase):
    # OBSERVED retained balances/visible bytes of the four successful pulls in the consumed arms.
    # Charge = server balance delta; visible = observe.py visible_content_bytes.
    PULLS = [('A', 5144, 2854, 3, 2111), ('A', 2854, 563, 2, 2111), ('B', 5012, 2519, 3, 2314), ('B', 2519, 873, 2, 1466)]

    def test_guard_pull_charge_minus_visible_equals_224_minus_facade_extras(self):
        # Source: core.expansionMemoryCost = len(json.Marshal(string(json.Marshal(payload)))) + 256 and
        # mcpapi.toolResult text = payload + ,"credits_remaining":N,"bytes_remaining":M; observe.py counts
        # len(json.dumps([{"type":"text","text":T}])) = len(T) + 30 + escapes(T) with 4 extra escaped quotes.
        for arm, before, after, credits, visible in self.PULLS:
            extras = len(',"credits_remaining":%d' % credits) + len(',"bytes_remaining":%d' % after)
            with self.subTest(arm=arm, after=after):
                self.assertEqual((before - after) - visible, 224 - extras)


if __name__ == '__main__':
    unittest.main()
