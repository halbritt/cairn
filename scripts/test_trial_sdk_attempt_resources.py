"""Accounting ownership tests, not allocations, kernel identity or admission."""
import sys
import unittest

from trial_sdk_native_process_owner import NativeProcessOwner
from trial_sdk_attempt_resources import AttemptResources,ResourceError,RAW,WORKING,TRAFFIC


class ResourceTests(unittest.TestCase):
    def domain(self,resources):return resources.bind(NativeProcessOwner())

    def test_cli_freeze_keeps_both_live_original_wire_prefixes(self):
        from trial_sdk_command_service_broker import ServiceLink
        resources=AttemptResources()
        link=ServiceLink(resources,deadline_ns=100,standing_raw=2080,
                         standing_working=4096,clock=lambda:1)
        first=link.credit(64)
        second=link.credit(64,emergency=True)
        first.known(first.promise(32),32)
        second.known(second.promise(32),32)
        data=resources.grant(link.domain,'data',100)
        resources.quarantine(data,7)
        link._poison()
        state=resources.snapshot()
        self.assertEqual((state['traffic_spent'],state['traffic_reserved']),(71,157))
        self.assertEqual((first.snapshot()['known'],second.snapshot()['known']),(32,32))
        self.assertTrue(state['unknown'])

    def test_two_original_wire_credits_record_prefixes_in_either_poison_order(self):
        from trial_sdk_command_service_broker import WireCredit
        for reverse in (False,True):
            with self.subTest(reverse=reverse):
                resources=AttemptResources()
                first=WireCredit(resources,resources.local,64)
                second=WireCredit(resources,self.domain(resources),64)
                first.known(first.promise(32),32)
                second.known(second.promise(40),40)
                credits=(second,first) if reverse else (first,second)
                for credit in credits:credit._poison()
                state=resources.snapshot()
                self.assertEqual((state['traffic_spent'],state['traffic_reserved']),(72,56))
                self.assertTrue(state['unknown'])
                self.assertIsNone(first.snapshot()['quarantine_error'])
                self.assertIsNone(second.snapshot()['quarantine_error'])

    def test_terminal_prefixes_are_monotonic_idempotent_and_never_reopen_resources(self):
        resources=AttemptResources();domain=self.domain(resources)
        memory=resources.allocate(domain,'standing',10,20)
        settled=resources.grant(resources.local,'control',10)
        resources.settle(settled,7)
        grant=resources.grant(domain,'data',100)
        resources.freeze()
        for prefix in (0,0,23,23,70,70,100,100):
            resources.quarantine(grant,prefix)
            state=resources.snapshot()
            self.assertEqual((state['traffic_spent'],state['traffic_reserved']),
                             (7+prefix,100-prefix))
            self.assertEqual(state['traffic_available'],TRAFFIC-107)
            self.assertTrue(state['unknown'])
        before=resources.snapshot()
        for action in (lambda:resources.settle(grant,70),
                       lambda:resources.grant(resources.local,'control',1),
                       lambda:resources.release(memory),
                       lambda:resources.allocate(domain,'operation',1,1),
                       lambda:resources.retire(domain),lambda:resources.close()):
            with self.assertRaisesRegex(ResourceError,'^resource_unknown$'):action()
        self.assertEqual(resources.snapshot(),before)

    def test_terminal_invalid_and_stale_receipts_cannot_mutate_prefixes(self):
        resources=AttemptResources();domain=self.domain(resources)
        stale=resources.grant(resources.local,'control',10)
        resources.settle(stale,7)
        grant=resources.grant(domain,'data',100)
        other=AttemptResources();foreign=other.grant(other.local,'data',100)
        resources.quarantine(grant,23)
        before=resources.snapshot()
        for token,prefix,reason in ((grant,22,'resource_count_regression'),
                                    (grant,-1,'resource_count'),(grant,101,'resource_count'),
                                    (grant,True,'resource_count'),(grant,23.0,'resource_count'),
                                    (type(grant)(grant.ordinal),23,'resource_ticket'),
                                    (foreign,23,'resource_ticket'),(stale,7,'resource_ticket')):
            with self.subTest(reason=reason,prefix=prefix):
                with self.assertRaisesRegex(ResourceError,'^'+reason+'$'):
                    resources.quarantine(token,prefix)
                self.assertEqual(resources.snapshot(),before)

    def test_prefix_recording_refuses_reentrancy_and_wrong_owner_or_closed_ledger(self):
        from unittest.mock import patch
        import trial_sdk_attempt_resources as module
        resources=AttemptResources();grant=resources.grant(resources.local,'data',100)
        resources.freeze();resources._busy=True
        with self.assertRaisesRegex(ResourceError,'^resource_busy$'):
            resources.quarantine(grant,23)
        resources._busy=False
        with patch.object(module.os,'getpid',return_value=resources._creator+1):
            with self.assertRaisesRegex(ResourceError,'^resource_wrong_process$'):
                resources.quarantine(grant,23)
        self.assertEqual(resources.snapshot()['traffic_spent'],0)
        closed=AttemptResources();closed.close()
        with self.assertRaisesRegex(ResourceError,'^resource_closed$'):
            closed.quarantine(grant,23)

    def test_interrupted_settlement_cannot_be_counted_again_as_a_terminal_prefix(self):
        resources=AttemptResources();grant=resources.grant(resources.local,'data',100)
        hit=[]
        def trace(frame,event,arg):
            if (event=='line' and frame.f_code.co_name=='settle'
                    and frame.f_locals.get('self') is resources
                    and resources._spent==17 and grant in resources._traffic and not hit):
                hit.append(True);raise KeyboardInterrupt()
            return trace
        old=sys.gettrace()
        try:
            sys.settrace(trace)
            with self.assertRaises(KeyboardInterrupt):resources.settle(grant,17)
        finally:sys.settrace(old)
        self.assertEqual(hit,[True]);before=resources.snapshot()
        self.assertEqual((before['traffic_spent'],before['traffic_reserved']),(17,0))
        with self.assertRaisesRegex(ResourceError,'^resource_settlement_unknown$'):
            resources.quarantine(grant,17)
        self.assertEqual(resources.snapshot(),before)

    def test_each_terminal_update_line_preserves_prefix_and_idempotent_recovery(self):
        # Discover the actually executed source lines of one inert update, then
        # require a one-shot interruption at each, including finally and return.
        sample=AttemptResources();token=sample.grant(sample.local,'data',100)
        sample.quarantine(token,13);lines=set()
        def observe(frame,event,arg):
            if event=='line' and frame.f_code is AttemptResources.quarantine.__code__:
                lines.add(frame.f_lineno)
            return observe
        old=sys.gettrace()
        try:
            sys.settrace(observe);sample.quarantine(token,29)
        finally:sys.settrace(old)
        self.assertGreater(len(lines),10)
        for line in sorted(lines):
            with self.subTest(line=line):
                resources=AttemptResources();grant=resources.grant(resources.local,'data',100)
                resources.quarantine(grant,13);hit=[]
                def trace(frame,event,arg):
                    if (event=='line' and frame.f_code is AttemptResources.quarantine.__code__
                            and frame.f_locals.get('self') is resources
                            and frame.f_lineno==line and not hit):
                        hit.append(True);raise KeyboardInterrupt()
                    return trace
                old=sys.gettrace()
                try:
                    sys.settrace(trace)
                    with self.assertRaises(KeyboardInterrupt):resources.quarantine(grant,29)
                finally:sys.settrace(old)
                self.assertEqual(hit,[True]);state=resources.snapshot()
                self.assertIn(state['traffic_spent'],(13,29))
                self.assertEqual(state['traffic_spent']+state['traffic_reserved'],100)
                self.assertTrue(state['unknown'])
                resources.quarantine(grant,29);resources.quarantine(grant,29)
                self.assertEqual((resources.snapshot()['traffic_spent'],
                                  resources.snapshot()['traffic_reserved']),(29,71))

    def test_original_limits_are_shared_across_held_children(self):
        resources=AttemptResources();a=self.domain(resources);b=self.domain(resources)
        one=resources.allocate(a,'operation',100_000,500*1024*1024)
        self.assertIsNotNone(one)
        self.assertIsNone(resources.allocate(b,'operation',32_000,0))
        self.assertIsNone(resources.allocate(b,'operation',0,20*1024*1024))
        self.assertEqual(resources.snapshot()['raw_reserved'],100_000)
        resources.release(one)
        two=resources.allocate(b,'operation',30_000,20*1024*1024)
        self.assertIsNotNone(two)
        resources.release(two)
        self.assertEqual(resources.snapshot()['working_reserved'],0)
        self.assertEqual((RAW,WORKING,TRAFFIC),(128*1024,512*1024*1024,4*1024*1024))

    def test_raw_and_decoded_allocations_share_the_original_aggregate_limit(self):
        resources=AttemptResources();a=self.domain(resources);b=self.domain(resources)
        self.assertIsNone(resources.allocate(a,'operation',RAW,WORKING))
        one=resources.allocate(a,'operation',RAW,WORKING-RAW)
        self.assertIsNotNone(one)
        self.assertIsNone(resources.allocate(b,'operation',0,1))
        state=resources.snapshot()
        self.assertEqual(state['known_allocation_reserved'],WORKING)
        resources.release(one)
        two=resources.allocate(b,'operation',0,WORKING)
        self.assertIsNotNone(two)
        self.assertEqual(resources.snapshot()['known_allocation_peak'],WORKING)
        resources.release(two)

    def test_same_numbers_or_foreign_domains_do_not_replace_held_tickets(self):
        resources=AttemptResources();a=self.domain(resources)
        token=resources.allocate(a,'standing',100,200)
        forged=type(token)(token.ordinal)
        with self.assertRaisesRegex(ResourceError,'^resource_ticket$'):resources.release(forged)
        other=AttemptResources()
        with self.assertRaisesRegex(ResourceError,'^resource_domain$'):
            other.allocate(a,'operation',1,1)
        self.assertEqual(resources.snapshot()['raw_reserved'],100)
        resources.release(token)
        with self.assertRaisesRegex(ResourceError,'^resource_ticket$'):resources.release(token)

    def test_control_headroom_and_actual_traffic_settlement_never_refund_spent_bytes(self):
        resources=AttemptResources();a=self.domain(resources);b=self.domain(resources)
        control=resources.grant(a,'control',1000)
        data=resources.grant(a,'data',TRAFFIC-1000)
        self.assertEqual(resources.grant_info(data)['available_at_issue'],TRAFFIC-1000)
        self.assertIsNone(resources.grant(b,'data',1))
        resources.settle(data,TRAFFIC-1200)
        self.assertEqual(resources.snapshot()['traffic_available'],200)
        self.assertIsNone(resources.grant(b,'data',201))
        resources.settle(control,900)
        self.assertEqual(resources.snapshot()['traffic_available'],300)
        self.assertEqual(resources.snapshot()['traffic_spent'],TRAFFIC-300)
        with self.assertRaisesRegex(ResourceError,'^resource_ticket$'):resources.settle(data,0)

    def test_missing_grant_is_distinct_from_explicit_final_zero_allowance(self):
        resources=AttemptResources();a=self.domain(resources)
        with self.assertRaisesRegex(ResourceError,'^resource_zero_grant$'):
            resources.grant(a,'data',0)
        control=resources.grant(a,'control',TRAFFIC)
        zero=resources.grant(a,'data',0)
        self.assertEqual(resources.grant_info(zero),{'maximum':0,'available_at_issue':0})
        resources.settle(zero,0)
        resources.settle(control,TRAFFIC)
        self.assertEqual(resources.snapshot()['traffic_spent'],TRAFFIC)

    def test_uncertain_receipt_retains_the_whole_unsettled_allowance(self):
        resources=AttemptResources();a=self.domain(resources)
        grant=resources.grant(a,'data',1536)
        resources.quarantine(grant,23)
        state=resources.snapshot()
        self.assertTrue(state['unknown'])
        self.assertEqual((state['traffic_spent'],state['traffic_reserved']),(23,1513))
        with self.assertRaisesRegex(ResourceError,'^resource_unknown$'):resources.settle(grant,23)
        with self.assertRaisesRegex(ResourceError,'^resource_unknown$'):resources.grant(a,'control',1)

    def test_lifetimes_are_bounded_and_cleanup_cannot_retire_live_leases(self):
        resources=AttemptResources();owner=NativeProcessOwner();a=resources.bind(owner)
        with self.assertRaisesRegex(ResourceError,'^resource_owner_already_bound$'):resources.bind(owner)
        lease=resources.allocate(a,'standing',10,20)
        with self.assertRaisesRegex(ResourceError,'^resource_slot_busy$'):resources.allocate(a,'standing',1,1)
        with self.assertRaisesRegex(ResourceError,'^resource_domain_live$'):resources.retire(a)
        with self.assertRaisesRegex(ResourceError,'^resource_attempt_live$'):resources.close()
        resources.release(lease);resources.retire(a);resources.close()
        with self.assertRaisesRegex(ResourceError,'^resource_closed$'):resources.bind(NativeProcessOwner())

    def test_diagnostic_mutation_cannot_mint_credit(self):
        resources=AttemptResources();a=self.domain(resources)
        lease=resources.allocate(a,'operation',100,200)
        grant=resources.grant(a,'data',300)
        state=resources.snapshot();state['raw_reserved']=0;state['domains'].clear()
        info=resources.grant_info(grant);info['maximum']=TRAFFIC
        self.assertEqual(resources.snapshot()['raw_reserved'],100)
        self.assertEqual(resources.grant_info(grant)['maximum'],300)
        resources.settle(grant,300);resources.release(lease)

    def test_final_cleanup_interruption_retains_charge_and_prevents_replay(self):
        for fault in (KeyboardInterrupt(),BlockingIOError()):
            with self.subTest(fault=type(fault).__name__):
                resources=AttemptResources();a=self.domain(resources);fired=False
                def trace(frame,event,arg):
                    nonlocal fired
                    if (event=='line' and frame.f_code is resources.allocate.__func__.__code__
                            and frame.f_locals.get('answer') is not None
                            and not resources._busy and not resources._unknown and not fired):
                        fired=True
                        raise fault
                    return trace
                try:
                    sys.settrace(trace)
                    with self.assertRaises(type(fault)):resources.allocate(a,'operation',100,200)
                finally:sys.settrace(None)
                self.assertTrue(fired)
                state=resources.snapshot()
                self.assertTrue(state['unknown'])
                self.assertEqual((state['raw_reserved'],state['working_reserved']),(100,200))
                with self.assertRaisesRegex(ResourceError,'^resource_unknown$'):
                    resources.allocate(a,'operation',100,200)


if __name__=='__main__':unittest.main()
