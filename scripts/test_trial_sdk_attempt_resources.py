"""Accounting ownership tests, not allocations, kernel identity or admission."""
import sys
import unittest

from trial_sdk_native_process_owner import NativeProcessOwner
from trial_sdk_attempt_resources import AttemptResources,ResourceError,RAW,WORKING,TRAFFIC


class ResourceTests(unittest.TestCase):
    def domain(self,resources):return resources.bind(NativeProcessOwner())

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
