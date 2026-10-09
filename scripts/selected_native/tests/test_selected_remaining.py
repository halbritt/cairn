import copy
import json
import unittest
from unittest.mock import patch

from .. import frames
from ..evidence import EvidenceError, Ledger, PULL, SEARCH
from ..frames import FrameError
from .test_selected_evidence import offered, partial, search, selection, REQUEST, PULL_REQUEST, RECEIPT, HANDLE
from .test_selected_frames import INIT, FINAL, reader, wire, blocks, call, reply


class RemainingBoundaryTest(unittest.TestCase):
    def test_unexpected_event_failure_poison_and_public_error_have_no_private_chain(self):
        r = reader(); r.feed(wire(INIT))
        with patch.object(frames.Frames, '_event', side_effect=RuntimeError('PRIVATE_EXCEPTION_SENTINEL')):
            with self.assertRaisesRegex(FrameError, '^internal_refusal$') as caught:
                r.feed(wire(blocks('assistant', 'RAW_TRANSCRIPT_SENTINEL')))
        self.assertIsNone(caught.exception.__context__)
        self.assertIsNone(caught.exception.__cause__)
        self.assertNotIn('PRIVATE_EXCEPTION_SENTINEL', str(caught.exception))
        self.assertEqual(r.ledger.calls, {})
        with self.assertRaisesRegex(FrameError, '^reader_unavailable$'): r.feed(wire(FINAL))
        with self.assertRaisesRegex(FrameError, '^reader_unavailable$'): r.finish(0)

    def test_invalid_unicode_chain_and_raw_parse_locals_do_not_escape_frame_boundary(self):
        r = reader()
        try:
            r.feed(b'\xffRAW_TRANSCRIPT_SENTINEL\n')
        except FrameError as exc:
            self.assertEqual(str(exc), 'invalid_frame_json')
            self.assertIsNone(exc.__context__)
            self.assertIsNone(exc.__cause__)
            tb = exc.__traceback__
            while tb and tb.tb_frame.f_code.co_name != 'feed': tb = tb.tb_next
            self.assertIsNotNone(tb)
            local = tb.tb_frame.f_locals
            self.assertEqual(local['chunk'], b'')
            self.assertIsNone(local['raw'])
            self.assertIsNone(local['event'])
            self.assertIsNone(local['staged'])
            self.assertEqual(r.buffer, b'')
        else: self.fail('malformed stream was accepted')

    def test_interrupt_preserves_interrupt_and_poison_never_resumes(self):
        r = reader(); r.feed(wire(INIT))
        with patch.object(frames.Frames, '_event', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): r.feed(wire(call()))
        self.assertEqual(r.failure, 'observer_interrupted')
        with self.assertRaisesRegex(FrameError, '^reader_unavailable$'): r.feed(wire(FINAL))
        with self.assertRaisesRegex(FrameError, '^reader_unavailable$'): r.finish(0)

    def test_finish_copy_failure_is_typed_closed_and_has_no_private_chain(self):
        r = reader(); r.feed(wire(INIT)); r.feed(wire(FINAL))
        with patch.object(frames.copy, 'deepcopy', side_effect=RuntimeError('PRIVATE_COPY_SENTINEL')):
            with self.assertRaisesRegex(FrameError, '^internal_refusal$') as caught: r.finish(0)
        self.assertIsNone(caught.exception.__context__)
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(r.closed)
        with self.assertRaisesRegex(FrameError, '^reader_unavailable$'): r.finish(0)

    def test_retried_offer_nonce_is_linked_and_failed_event_rolls_back_history(self):
        r = reader(); r.feed(wire(INIT)); r.feed(wire(call())); r.feed(wire(reply()))
        second = copy.deepcopy(search())
        second['request_id'] = '40000000-0000-4000-8000-000000000004'
        second['index'][0]['pull_arguments']['request_id'] = '40000000-0000-4000-8000-000000000005'
        r.feed(wire(call(SEARCH, {'request_id': second['request_id']}, 'retry')))
        before = copy.deepcopy(r.ledger.offers)
        event = reply(second, 'retry'); event['message']['content'].append({'type': 'unknown'})
        with self.assertRaisesRegex(FrameError, '^unknown_content_block$'): r.feed(wire(event))
        self.assertEqual(r.ledger.offers, before)
        ledger = offered(); ledger.request('retry', SEARCH, {'request_id': second['request_id']})
        ledger.response('retry', json.dumps(second))
        args = copy.deepcopy(second['index'][0]['pull_arguments'])
        ledger.request('p', PULL, args)
        ledger.response('p', json.dumps({'selection': selection(), 'credits_remaining': 3, 'bytes_remaining': 3000}))
        out = ledger.selected()['responses']['p']
        self.assertTrue(out['request_id_matches_offered'])
        self.assertEqual(out['matching_offered_requests'], [{'search_call_id': 'retry', 'arguments': args}])
        self.assertEqual(out['offered_arguments']['request_id'], PULL_REQUEST)

    def test_fresh_range_nonce_is_permitted_and_labelled_not_matching_any_offer(self):
        ledger = offered()
        args = {'request_id': '40000000-0000-4000-8000-000000000006', 'receipt_id': RECEIPT,
                'handle': HANDLE, 'span': {'offset': 14, 'length': 12}}
        ledger.request('p', PULL, args); ledger.response('p', json.dumps(partial()))
        out = ledger.selected()['responses']['p']
        self.assertFalse(out['request_id_matches_offered'])
        self.assertEqual(out['matching_offered_requests'], [])
        self.assertEqual(out['request_id'], args['request_id'])
        self.assertEqual(out['response_ids_present'], [])

    def test_present_response_ids_must_match_actual_call(self):
        for key in ['request_id', 'receipt_id']:
            with self.subTest(key=key):
                ledger = offered(); ledger.request('p', PULL, {'request_id': PULL_REQUEST,
                    'receipt_id': RECEIPT, 'handle': HANDLE})
                value = {'selection': selection(), 'credits_remaining': 3, 'bytes_remaining': 3000,
                         key: '40000000-0000-4000-8000-000000000007'}
                with self.assertRaisesRegex(EvidenceError, '^pull_response_id_mismatch$'):
                    ledger.response('p', json.dumps(value))
                self.assertNotIn('p', ledger.responses)

    def test_explicit_opening_size_is_required_and_zero_offset_has_no_opening(self):
        for opening in [0, 4, 8]:
            with self.subTest(opening=opening):
                ledger = offered(); args = {'request_id': PULL_REQUEST, 'receipt_id': RECEIPT,
                    'handle': HANDLE, 'span': {'offset': 14, 'length': 12}, 'opening_bytes': 8}
                ledger.request('p', PULL, args)
                if opening == 8:
                    ledger.response('p', json.dumps(partial(opening=8)))
                    self.assertEqual(ledger.responses['p']['source']['source_opening']['end'], 8)
                else:
                    with self.assertRaisesRegex(EvidenceError, '^opening_not_requested$'):
                        ledger.response('p', json.dumps(partial(opening=opening)))
                    self.assertNotIn('p', ledger.responses)
        ledger = offered(); ledger.request('p', PULL, {'request_id': PULL_REQUEST, 'receipt_id': RECEIPT,
            'handle': HANDLE, 'span': {'offset': 0, 'length': 12}, 'opening_bytes': 8})
        ledger.response('p', json.dumps(partial(offset=0)))
        self.assertNotIn('source_opening', ledger.responses['p']['source'])

    def test_opening_without_span_and_standalone_unhashable_inputs_are_typed(self):
        with self.assertRaisesRegex(EvidenceError, '^opening_requires_span$'):
            offered().request('p', PULL, {'request_id': PULL_REQUEST, 'receipt_id': RECEIPT,
                                        'handle': HANDLE, 'opening_bytes': 8})
        for value in [[], {}]:
            with self.subTest(value=value):
                with self.assertRaisesRegex(EvidenceError, '^call_shape$'): Ledger().request('s', value, {})
                with self.assertRaisesRegex(EvidenceError, '^response_identity$'): Ledger().response(value, '')


if __name__ == '__main__': unittest.main()
