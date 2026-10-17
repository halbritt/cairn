"""Attempt budget behavior; no process, service or provider calls."""
import time
import unittest
from unittest.mock import patch

from trial_sdk_memory_gate import MemoryDispatchGate, MemoryGateError


class MemoryGateTests(unittest.TestCase):
    def test_shared_expansions_across_native_and_independent_hooks(self):
        calls = []
        def forward(request, deadline_ns):
            calls.append(request)
            return {'ok': True, 'data': {}}
        gate = MemoryDispatchGate(forward, forward, time.monotonic_ns() + 30_000_000_000)
        gate.native('mcp__cairn__cairn_pull', {})
        first = gate.hook(time.monotonic_ns() + 5_000_000_000)
        first.call('pull', (), '{}', 5)
        second = gate.hook(time.monotonic_ns() + 5_000_000_000)
        second.call('pull-evidence', (), '{}', 5)
        second.call('history', (), '{"version":0}', 5)
        with self.assertRaisesRegex(MemoryGateError, '^memory_expansion_limit$'):
            second.call('pull', (), '{"receipt_id":"another"}', 5)
        self.assertEqual(len(calls), 4)
        state = gate.snapshot()
        self.assertEqual(state['expansions_debited'], 4)
        self.assertEqual(state['requests'], 5)
        self.assertEqual(state['dispatches'], 4)
        self.assertEqual(state['refusals'], 1)

    def test_invalid_large_timeout_is_fixed_refusal_without_dispatch(self):
        calls = []
        gate = MemoryDispatchGate(lambda *a: calls.append(a), lambda *a: calls.append(a),
                                  time.monotonic_ns() + 30_000_000_000)
        with self.assertRaisesRegex(MemoryGateError, '^memory_hook_timeout$'):
            gate.hook(time.monotonic_ns() + 5_000_000_000).call('search', timeout=10**10000)
        self.assertEqual(calls, [])
        self.assertEqual(gate.snapshot()['dispatches'], 0)

    def test_hook_searches_do_not_spend_native_searches(self):
        calls = []
        def forward(request, deadline):
            calls.append(request)
            return {'ok': False, 'status': 'CONFIRMED_REFUSAL'}
        gate = MemoryDispatchGate(forward, forward, time.monotonic_ns()+30_000_000_000)
        hook = gate.hook(time.monotonic_ns()+5_000_000_000)
        for _ in range(17):
            hook.call('search')
        for _ in range(2):
            self.assertFalse(gate.native('mcp__cairn__cairn_search', {})['ok'])
        with self.assertRaisesRegex(MemoryGateError, '^memory_native_search_limit$'):
            gate.native('mcp__cairn__cairn_search', {})
        state = gate.snapshot()
        self.assertEqual((state['hook_searches'],state['native_searches_debited']), (17,2))
        self.assertEqual((state['dispatches'],state['returned'],len(calls)), (19,19,19))

    def test_debit_precedes_forward_and_uncertainty_never_refunds(self):
        seen = []
        def forward(request, deadline):
            seen.append(gate.snapshot())
            raise InterruptedError('not retained')
        gate = MemoryDispatchGate(forward, forward, time.monotonic_ns()+30_000_000_000)
        with self.assertRaises(InterruptedError):
            gate.native('mcp__cairn__cairn_pull', {})
        self.assertEqual(seen[0]['expansions_debited'], 1)
        self.assertTrue(seen[0]['in_flight'])
        self.assertEqual(gate.snapshot()['last']['outcome'], 'unknown')
        self.assertTrue(gate.snapshot()['dispatch_unknown'])
        with self.assertRaisesRegex(MemoryGateError, '^memory_dispatch_unknown$'):
            gate.hook(time.monotonic_ns()+5_000_000_000).call('pull')
        self.assertEqual(len(seen),1)
        self.assertEqual(gate.snapshot()['expansions_debited'],1)

    def test_current_native_read_permission_is_not_widened_by_legacy_classifier(self):
        for tool in ('mcp__cairn__cairn_history','mcp__cairn__cairn_pull_evidence',
                     'mcp__cairn__cairn_remember',None,[]):
            calls=[]
            gate=MemoryDispatchGate(lambda *a:calls.append(a),lambda *a:calls.append(a),
                                    time.monotonic_ns()+30_000_000_000)
            with self.assertRaisesRegex(MemoryGateError,'^memory_native_operation$'):
                gate.native(tool,{'version':1})
            self.assertEqual(calls,[])
            self.assertEqual(gate.snapshot()['dispatches'],0)

    def test_caught_reentrant_refusal_still_poisoned_outer_dispatch(self):
        def forward(request, deadline):
            try:
                gate.native('mcp__cairn__cairn_pull',{})
            except MemoryGateError:
                pass
            return {'ok':True,'data':{}}
        gate=MemoryDispatchGate(forward,forward,time.monotonic_ns()+30_000_000_000)
        with self.assertRaisesRegex(MemoryGateError,'^memory_dispatch_reentrant$'):
            gate.native('mcp__cairn__cairn_search',{})
        self.assertEqual(gate.snapshot()['dispatches'],1)
        self.assertEqual(gate.snapshot()['expansions_debited'],0)

    def test_expired_or_closed_hook_never_dispatches_and_no_payload_is_retained(self):
        for closed in (True,False):
            calls=[]
            gate=MemoryDispatchGate(lambda *a:calls.append(a),lambda *a:calls.append(a),
                                    time.monotonic_ns()+30_000_000_000)
            channel=gate.hook(time.monotonic_ns()+(5_000_000_000 if closed else -1))
            if closed: channel.close()
            with self.assertRaisesRegex(MemoryGateError,
                                        '^memory_hook_closed$' if closed else '^memory_deadline$'):
                channel.call('pull',body='private request marker')
            self.assertEqual(calls,[])
            self.assertNotIn('private request marker',str(gate.snapshot()))

    def test_request_validation_cannot_carry_dispatch_past_deadline(self):
        calls=[]
        gate=MemoryDispatchGate(lambda *a:calls.append(a),lambda *a:calls.append(a),1)
        with patch('trial_sdk_memory_gate.time.monotonic_ns',side_effect=[0,2]):
            with self.assertRaisesRegex(MemoryGateError,'^memory_deadline$'):
                gate.native('mcp__cairn__cairn_pull',{})
        self.assertEqual(calls,[])
        self.assertEqual(gate.snapshot()['expansions_debited'],0)


if __name__ == '__main__':
    unittest.main()
