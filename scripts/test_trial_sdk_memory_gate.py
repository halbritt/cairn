"""Attempt budget behavior; no process, service or provider calls."""
import time
import sys
import trial_sdk_memory_gate as gate_module
import unittest
from unittest.mock import patch

from trial_sdk_memory_gate import MemoryDispatchGate, MemoryGateError


class MemoryGateTests(unittest.TestCase):
    def test_pending_start_debits_before_forward_and_retires_exact_value_once(self):
        seen=[]; prepared=object()
        def forward(request,deadline):
            seen.append(gate.snapshot())
            return prepared
        gate=MemoryDispatchGate(forward,forward,time.monotonic_ns()+30_000_000_000)
        channel=gate.hook(time.monotonic_ns()+5_000_000_000)
        handle=channel.start('pull',body='{}')
        self.assertEqual(seen[0]['expansions_debited'],1)
        self.assertEqual(seen[0]['pending']['phase'],'forwarding')
        state=gate.snapshot()
        self.assertEqual((state['dispatches'],state['returned']),(1,1))
        self.assertEqual(state['pending']['phase'],'pending')
        self.assertIs(gate.complete_pending(handle),prepared)
        state=gate.snapshot()
        self.assertIsNone(state['pending'])
        self.assertEqual(state['last_pending']['phase'],'retired')
        self.assertFalse(state['completion_unknown'])
        with self.assertRaisesRegex(MemoryGateError,'^memory_pending_handle$'):
            gate.complete_pending(handle)
        self.assertEqual(len(seen),1)
        self.assertEqual(gate.snapshot()['expansions_debited'],1)

    def test_later_failure_can_close_after_expiry_without_erasing_forward_facts(self):
        prepared=object();calls=[]
        def forward(*args):calls.append(True);return prepared
        gate=MemoryDispatchGate(forward,forward,time.monotonic_ns()+30_000_000_000)
        handle=gate.native_start('mcp__cairn__cairn_pull',{})
        with patch('trial_sdk_memory_gate.time.monotonic_ns',side_effect=AssertionError('no new clock grant')):
            self.assertIsNone(gate.fail_pending(handle))
        state=gate.snapshot()
        self.assertEqual((state['dispatches'],state['returned'],state['expansions_debited']),(1,1,1))
        self.assertEqual(state['last']['outcome'],'returned')
        self.assertTrue(state['completion_unknown'])
        self.assertIsNone(state['pending'])
        self.assertEqual(state['last_pending']['phase'],'unknown')
        self.assertEqual(state['failure'],'memory_pending_failed')
        with self.assertRaisesRegex(MemoryGateError,'^memory_pending_failed$'):
            gate.native('mcp__cairn__cairn_pull',{})
        with self.assertRaisesRegex(MemoryGateError,'^memory_pending_failed$'):
            gate.complete_pending(handle)
        self.assertEqual(calls,[True])

    def test_late_or_clock_unknown_completion_cannot_deliver_or_reopen_gate(self):
        for clock,error,label in ((10,MemoryGateError,'memory_deadline'),
                                  (OSError('clock'),OSError,'memory_clock_unknown'),
                                  (1,MemoryGateError,'memory_clock_unknown')):
            with self.subTest(clock=type(clock).__name__):
                calls=[]
                gate=MemoryDispatchGate(lambda *a:calls.append(True),lambda *a:None,10)
                with patch('trial_sdk_memory_gate.time.monotonic_ns',side_effect=[0,1,2]):
                    handle=gate.native_start('mcp__cairn__cairn_pull',{})
                with patch('trial_sdk_memory_gate.time.monotonic_ns',side_effect=[clock]):
                    with self.assertRaises(error):gate.complete_pending(handle)
                state=gate.snapshot()
                self.assertEqual(state['failure'],label)
                self.assertTrue(state['completion_unknown'])
                self.assertIsNone(state['pending'])
                self.assertEqual(state['last_pending']['phase'],'unknown')
                self.assertEqual(state['last']['outcome'],'returned')
                self.assertEqual((state['returned'],state['expansions_debited']),(1,1))
                with self.assertRaises(MemoryGateError):gate.native('mcp__cairn__cairn_pull',{})
                self.assertEqual(calls,[True])

    def test_interrupted_pending_reservation_preserves_original_fault_and_no_dispatch(self):
        calls=[];fired=False
        gate=MemoryDispatchGate(lambda *a:calls.append(True),lambda *a:None,
                                time.monotonic_ns()+30_000_000_000)
        def trace(frame,event,arg):
            nonlocal fired
            if frame.f_code is gate._dispatch.__func__.__code__ and event=='line':
                state=gate.snapshot()
                if not fired and state['pending'] is not None and state['dispatches']==0:
                    fired=True
                    raise KeyboardInterrupt('pending reservation')
            return trace
        previous=sys.gettrace()
        try:
            sys.settrace(trace)
            with self.assertRaises(KeyboardInterrupt):
                gate.native_start('mcp__cairn__cairn_pull',{})
        finally:sys.settrace(previous)
        self.assertTrue(fired)
        self.assertEqual(calls,[])
        state=gate.snapshot()
        self.assertEqual((state['expansions_debited'],state['dispatches']),(1,0))
        self.assertEqual(state['failure'],'memory_gate_unknown')
        self.assertIsNone(state['pending'])
        self.assertTrue(state['completion_unknown'])
        with self.assertRaisesRegex(MemoryGateError,'^memory_gate_unknown$'):
            gate.native('mcp__cairn__cairn_pull',{})

    def test_pending_blocks_both_origins_and_both_entry_modes_before_new_debit(self):
        for pending_native in (True,False):
            for deferred in (True,False):
                with self.subTest(pending_native=pending_native,deferred=deferred):
                    calls=[]
                    gate=MemoryDispatchGate(lambda *a:calls.append(True),lambda *a:calls.append(True),
                                            time.monotonic_ns()+30_000_000_000)
                    channel=gate.hook(time.monotonic_ns()+5_000_000_000)
                    handle=(gate.native_start('mcp__cairn__cairn_pull',{}) if pending_native
                            else channel.start('pull',body='{}'))
                    with self.assertRaisesRegex(MemoryGateError,'^memory_dispatch_pending$'):
                        if pending_native:
                            (channel.start if deferred else channel.call)('search')
                        else:
                            (gate.native_start if deferred else gate.native)('mcp__cairn__cairn_search',{})
                    state=gate.snapshot()
                    self.assertEqual((state['dispatches'],state['returned'],state['expansions_debited']),(1,1,1))
                    self.assertEqual((state['hook_searches'],state['native_searches_debited']),(0,0))
                    self.assertTrue(state['completion_unknown'])
                    self.assertIsNotNone(state['pending'])
                    with self.assertRaisesRegex(MemoryGateError,'^memory_dispatch_pending$'):
                        gate.complete_pending(handle)
                    gate.fail_pending(handle)
                    self.assertIsNone(gate.snapshot()['pending'])
                    self.assertEqual(gate.snapshot()['failure'],'memory_dispatch_pending')
                    self.assertEqual(calls,[True])

    def test_stale_and_forged_handles_cannot_clear_a_new_pending_operation(self):
        class EqualLooking:
            def __eq__(self,other):raise AssertionError('never use equality as custody')
        for fake in (False,True):
            with self.subTest(fake=fake):
                gate=MemoryDispatchGate(lambda *a:None,lambda *a:None,time.monotonic_ns()+30_000_000_000)
                old=gate.native_start('mcp__cairn__cairn_pull',{})
                gate.complete_pending(old)
                current=gate.native_start('mcp__cairn__cairn_pull',{})
                sequence=gate.snapshot()['pending']['sequence']
                with self.assertRaisesRegex(MemoryGateError,'^memory_pending_handle$'):
                    gate.complete_pending(EqualLooking() if fake else old)
                self.assertEqual(gate.snapshot()['pending']['sequence'],sequence)
                self.assertEqual(gate.snapshot()['expansions_debited'],2)
                gate.fail_pending(current)
                self.assertEqual(gate.snapshot()['failure'],'memory_pending_handle')
                self.assertIsNone(gate.snapshot()['pending'])

    def test_foreign_handle_does_not_consume_its_original_gate_or_certify_a_refusal(self):
        refused={'ok':False,'status':'CONFIRMED_REFUSAL'}
        a=MemoryDispatchGate(lambda *x:refused,lambda *x:refused,time.monotonic_ns()+30_000_000_000)
        b=MemoryDispatchGate(lambda *x:None,lambda *x:None,time.monotonic_ns()+30_000_000_000)
        ah=a.native_start('mcp__cairn__cairn_search',{})
        bh=b.native_start('mcp__cairn__cairn_pull',{})
        with self.assertRaisesRegex(MemoryGateError,'^memory_pending_handle$'):b.complete_pending(ah)
        self.assertIs(a.complete_pending(ah),refused)
        self.assertIsNone(a.snapshot()['failure'])
        self.assertEqual(a.snapshot()['native_searches_debited'],1)
        b.fail_pending(bh)
        self.assertEqual(b.snapshot()['failure'],'memory_pending_handle')

    def test_hook_pending_completion_keeps_once_tightened_deadline_through_retirement(self):
        deadlines=[]
        gate=MemoryDispatchGate(lambda *a:None,lambda r,d:deadlines.append(d),1000)
        hook=gate.hook(100)
        with patch('trial_sdk_memory_gate.time.monotonic_ns',side_effect=[10,11,12,13]):
            handle=hook.start('pull',body='{}',timeout=.000000010)
        self.assertEqual(deadlines,[20])
        self.assertEqual(gate.snapshot()['pending']['deadline_ns'],20)
        with patch('trial_sdk_memory_gate.time.monotonic_ns',side_effect=[19,20]):
            with self.assertRaisesRegex(MemoryGateError,'^memory_deadline$'):gate.complete_pending(handle)
        state=gate.snapshot()
        self.assertIsNone(state['pending'])
        self.assertTrue(state['completion_unknown'])
        self.assertEqual(state['last_pending']['phase'],'unknown')
        self.assertEqual(state['last_pending']['deadline_ns'],20)
        self.assertEqual((state['dispatches'],state['returned'],state['expansions_debited']),(1,1,1))

    def test_pending_forward_failure_keeps_debit_and_separate_unknown_facts(self):
        def forward(*unused):raise InterruptedError('not retained')
        gate=MemoryDispatchGate(forward,forward,time.monotonic_ns()+30_000_000_000)
        with self.assertRaises(InterruptedError):gate.native_start('mcp__cairn__cairn_pull',{})
        state=gate.snapshot()
        self.assertEqual((state['dispatches'],state['returned'],state['expansions_debited']),(1,0,1))
        self.assertTrue(state['dispatch_unknown'])
        self.assertTrue(state['completion_unknown'])
        self.assertIsNone(state['pending'])
        self.assertEqual(state['last_pending']['phase'],'unknown')
        with self.assertRaisesRegex(MemoryGateError,'^memory_dispatch_unknown$'):
            gate.native('mcp__cairn__cairn_pull',{})

    def test_pending_diagnostics_are_copied_and_do_not_retain_request(self):
        import weakref
        references=[];prepared={'label':'response marker must stay private'}
        def forward(request,deadline):references.append(weakref.ref(request));return prepared
        gate=MemoryDispatchGate(forward,forward,time.monotonic_ns()+30_000_000_000)
        handle=gate.hook(time.monotonic_ns()+5_000_000_000).start('search',body='request marker must stay private')
        self.assertIsNone(references[0]())
        state=gate.snapshot()
        self.assertNotIn('marker',str(state))
        state['pending']['deadline_ns']=0;state['last_pending']['phase']='forged'
        self.assertNotEqual(gate.snapshot()['pending']['deadline_ns'],0)
        self.assertEqual(gate.snapshot()['last_pending']['phase'],'pending')
        self.assertIs(gate.complete_pending(handle),prepared)

    def test_final_dispatch_cleanup_interruption_cannot_reopen_sync_or_pending_gate(self):
        for deferred in (False,True):
            for fault in (KeyboardInterrupt(),BlockingIOError()):
                with self.subTest(deferred=deferred,fault=type(fault).__name__):
                    calls=[];fired=False
                    gate=MemoryDispatchGate(lambda *a:calls.append(True),lambda *a:None,
                                            time.monotonic_ns()+30_000_000_000)
                    def trace(frame,event,arg):
                        nonlocal fired
                        if (event=='line' and frame.f_code is gate._dispatch.__func__.__code__
                                and 'answer' in frame.f_locals and not gate.snapshot()['in_flight']
                                and not fired):
                            fired=True;raise fault
                        return trace
                    previous=sys.gettrace()
                    try:
                        sys.settrace(trace)
                        with self.assertRaises(type(fault)):
                            (gate.native_start if deferred else gate.native)('mcp__cairn__cairn_pull',{})
                    finally:sys.settrace(previous)
                    self.assertTrue(fired)
                    state=gate.snapshot()
                    self.assertEqual((state['returned'],state['expansions_debited']),(1,1))
                    self.assertEqual(state['last']['outcome'],'returned')
                    self.assertEqual(state['failure'],'memory_gate_unknown')
                    self.assertTrue(state['accounting_unknown'])
                    self.assertEqual(state['completion_unknown'],deferred)
                    self.assertIsNone(state['pending'])
                    with self.assertRaisesRegex(MemoryGateError,'^memory_gate_unknown$'):
                        gate.native('mcp__cairn__cairn_pull',{})
                    self.assertEqual(calls,[True])

    def test_final_pending_retirement_interruption_consumes_delivery_without_replay(self):
        for failed in (False,True):
            for fault in (KeyboardInterrupt(),BlockingIOError()):
                with self.subTest(failed=failed,fault=type(fault).__name__):
                    fired=False;calls=[]
                    gate=MemoryDispatchGate(lambda *a:calls.append(True),lambda *a:None,
                                            time.monotonic_ns()+30_000_000_000)
                    handle=gate.native_start('mcp__cairn__cairn_pull',{})
                    def trace(frame,event,arg):
                        nonlocal fired
                        if (event=='line' and frame.f_code is gate._settle_pending.__func__.__code__
                                and 'answer' in frame.f_locals and not gate.snapshot()['in_flight']
                                and not fired):
                            fired=True;raise fault
                        return trace
                    previous=sys.gettrace()
                    try:
                        sys.settrace(trace)
                        with self.assertRaises(type(fault)):
                            (gate.fail_pending if failed else gate.complete_pending)(handle)
                    finally:sys.settrace(previous)
                    self.assertTrue(fired)
                    state=gate.snapshot()
                    self.assertIsNone(state['pending'])
                    self.assertTrue(state['completion_unknown'])
                    self.assertEqual(state['last_pending']['phase'],'unknown')
                    self.assertEqual(state['failure'],'memory_pending_failed' if failed else 'memory_gate_unknown')
                    self.assertEqual((state['dispatches'],state['returned'],state['expansions_debited']),(1,1,1))
                    with self.assertRaises(MemoryGateError):gate.complete_pending(handle)
                    self.assertEqual(calls,[True])

    def test_frontend_clock_error_marks_an_existing_pending_lifecycle_unknown(self):
        calls=[]
        gate=MemoryDispatchGate(lambda *a:calls.append(True),lambda *a:calls.append(True),
                                time.monotonic_ns()+30_000_000_000)
        handle=gate.native_start('mcp__cairn__cairn_pull',{})
        channel=gate.hook(time.monotonic_ns()+5_000_000_000)
        with patch('trial_sdk_memory_gate.time.monotonic_ns',side_effect=OSError('clock')):
            with self.assertRaises(OSError):channel.call('search')
        state=gate.snapshot()
        self.assertEqual(state['failure'],'memory_clock_unknown')
        self.assertTrue(state['completion_unknown'])
        self.assertEqual(state['pending']['phase'],'unknown')
        self.assertEqual(state['last']['outcome'],'returned')
        self.assertEqual((state['dispatches'],state['returned'],state['expansions_debited']),(1,1,1))
        gate.fail_pending(handle)
        self.assertEqual(gate.snapshot()['failure'],'memory_clock_unknown')
        self.assertEqual(calls,[True])

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

    def test_postforward_clock_error_keeps_return_fact_and_poisoned_budget(self):
        calls=[]
        def forward(*args): calls.append(args); return {'ok':True,'data':{}}
        gate=MemoryDispatchGate(forward,forward,10)
        with patch('trial_sdk_memory_gate.time.monotonic_ns',side_effect=[0,1,OSError('clock')]):
            with self.assertRaises(OSError): gate.native('mcp__cairn__cairn_pull',{})
        state=gate.snapshot()
        self.assertEqual(state['returned'],1)
        self.assertEqual(state['last']['outcome'],'returned')
        self.assertEqual(state['failure'],'memory_clock_unknown')
        with self.assertRaisesRegex(MemoryGateError,'^memory_clock_unknown$'):
            gate.native('mcp__cairn__cairn_pull',{})
        self.assertEqual(len(calls),1)
        self.assertEqual(gate.snapshot()['expansions_debited'],1)

    def test_interruption_between_debit_and_forward_cannot_resume_attempt(self):
        calls=[]
        gate=MemoryDispatchGate(lambda *a:calls.append(a),lambda *a:calls.append(a),
                                time.monotonic_ns()+30_000_000_000)
        injected=[]
        def trace(frame,event,arg):
            if frame.f_code.co_filename==gate_module.__file__ and event=='line':
                state=gate.snapshot()
                if state['expansions_debited']==1 and state['dispatches']==0:
                    sys.settrace(None);injected.append(True)
                    raise KeyboardInterrupt('debit boundary')
            return trace
        previous=sys.gettrace()
        try:
            sys.settrace(trace)
            with self.assertRaises(KeyboardInterrupt): gate.native('mcp__cairn__cairn_pull',{})
        finally: sys.settrace(previous)
        self.assertEqual(injected,[True])
        self.assertEqual(calls,[])
        self.assertTrue(gate.snapshot()['accounting_unknown'])
        with self.assertRaisesRegex(MemoryGateError,'^memory_gate_unknown$'):
            gate.native('mcp__cairn__cairn_pull',{})
        self.assertEqual(gate.snapshot()['expansions_debited'],1)


if __name__ == '__main__':
    unittest.main()
