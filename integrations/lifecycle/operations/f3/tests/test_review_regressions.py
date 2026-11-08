from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
sys.path[:0] = ['/work', '/work/tests']
from claims import ClaimFailure, check_timing
from effects import Action, EffectRunner, Intent, SECOND
from sequence import Sequence, OldIdentity
from test_effects import Clock, PositiveTransport
from test_sequence import claim, structure, FixtureEvidence


class EpochClock(Clock):
    def __init__(self): super().__init__(); self.epoch=0; self.bad_receipt=False
    def receipt(self):
        return {'rt_ns':float('nan') if self.bad_receipt else self.ns,
                'mono_ns':self.ns,'epoch':self.epoch}


class NumericDomainTests(unittest.TestCase):
    def test_all_used_time_domains_reject_malformed_values_before_arithmetic(self):
        clock=EpochClock(); initial=claim(clock); receipt=clock.receipt()
        malformed=[float('nan'),float('inf'),float('-inf'),.5,True,False,'123',None,[], -1]
        for value in malformed:
            cases=[]
            for field in ('last_seen_ns','prior_last_seen_ns','expires_ns','lease_deadline_ns','native_ttl_seconds'):
                cases.append((replace(initial,owners=(replace(initial.owners[0],**{field:value}),)),receipt,75,field))
            for field in ('earliest_root_rt_ns','finished_root_rt_ns','epoch'):
                cases.append((replace(initial,**{field:value}),receipt,75,field))
            cases.append((replace(initial,control_durations_ns=(value,)),receipt,75,'control duration'))
            for field in ('rt_ns','mono_ns','epoch'):
                cases.append((initial,{**receipt,field:value},75,'receipt '+field))
            cases.append((initial,receipt,value,'minimum seconds'))
            for read,point,minimum,field in cases:
                with self.subTest(field=field,value=repr(value)):
                    with self.assertRaises(ClaimFailure):check_timing(read,point,minimum,complete_pass=True)

    def test_valid_integer_nanosecond_boundaries_and_none_unleased_deadline(self):
        clock=EpochClock(); read=claim(clock)
        owner=replace(read.owners[0],expires_ns=clock.ns+75*SECOND+510_000_000,
                      lease_deadline_ns=None,attempt_identity=None,native_ttl_seconds=None)
        read=replace(read,earliest_root_rt_ns=clock.ns-2*SECOND,owners=(owner,))
        self.assertEqual(check_timing(read,clock.receipt(),75,complete_pass=True),510_000_000)
        with self.assertRaises(ClaimFailure):
            check_timing(replace(read,owners=(replace(owner,expires_ns=owner.expires_ns-1),)),clock.receipt(),75)

    def test_large_integer_nanoseconds_keep_one_nanosecond_boundary(self):
        clock=EpochClock();clock.ns=2**60;read=claim(clock)
        owner=replace(read.owners[0],expires_ns=clock.ns+75*SECOND+510_000_000)
        read=replace(read,owners=(owner,))
        self.assertEqual(check_timing(read,clock.receipt(),75,complete_pass=True),510_000_000)
        with self.assertRaises(ClaimFailure):
            check_timing(replace(read,owners=(replace(owner,expires_ns=owner.expires_ns-1),)),clock.receipt(),75)


class PreSendTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.clock=EpochClock();self.transport=PositiveTransport(self.clock)
        self.intent=Intent(Path(self.tmp.name)/'intent','fixture-run','a'*64)
        self.effects=EffectRunner(self.intent,self.transport,self.clock)
        self.evidence=FixtureEvidence()
        self.seq=Sequence(self.effects,self.clock,OldIdentity(111,555,0,'fixture-boot','b'*64),self.evidence)

    def after_requested(self,change):
        write=self.intent.write
        def changed(kind,value):
            write(kind,value)
            if kind=='effect-requested':change()
        self.intent.write=changed

    def assert_no_launch(self,read):
        result=self.seq.signal(read)
        self.assertEqual(self.transport.requests,[])
        self.assertFalse(self.effects.signalled)
        self.assertTrue(self.effects.stopped)
        self.assertEqual(result['status'],'STOP_UNCERTAIN')
        self.assertFalse(self.seq.joined)

    def test_exact_root_1point9_plus_point2_receipt_delay_never_launches(self):
        read=replace(claim(self.clock),earliest_root_rt_ns=self.clock.ns-1_900_000_000)
        self.after_requested(lambda:setattr(self.clock,'ns',self.clock.ns+200_000_000))
        self.assert_no_launch(read)

    def test_epoch_change_during_preparation_never_launches(self):
        read=claim(self.clock)
        self.after_requested(lambda:setattr(self.clock,'epoch',1))
        self.assert_no_launch(read)

    def test_malformed_clock_after_preparation_never_launches(self):
        read=claim(self.clock)
        self.after_requested(lambda:setattr(self.clock,'bad_receipt',True))
        self.assert_no_launch(read)

    def test_revoked_external_evidence_after_receipt_never_launches(self):
        read=claim(self.clock)
        def revoke():
            def unavailable(_):raise ClaimFailure('fixture evidence revoked')
            self.evidence.verify=unavailable
        self.after_requested(revoke)
        self.assert_no_launch(read)

    def test_evidence_recheck_duration_counts_toward_freshness(self):
        read=replace(claim(self.clock),earliest_root_rt_ns=self.clock.ns-1_800_000_000)
        verify=self.evidence.verify;calls=[]
        def slow(value):
            verify(value);calls.append(1)
            if len(calls)==2:self.clock.ns+=300_000_000
        self.evidence.verify=slow
        self.assert_no_launch(read)
        self.assertEqual(len(calls),2)

    def test_remaining_wait_deducts_receipt_and_final_evidence_cost(self):
        read=claim(self.clock); verify=self.evidence.verify;calls=[];budgets=[]
        self.after_requested(lambda:setattr(self.clock,'ns',self.clock.ns+100_000_000))
        def slow(value):
            verify(value);calls.append(1)
            if len(calls)==2:self.clock.ns+=150_000_000
        self.evidence.verify=slow
        exchange=self.transport.exchange
        def observe_budget(request,budget):
            budgets.append(budget);return exchange(request,budget)
        self.transport.exchange=observe_budget
        result=self.seq.signal(read)
        self.assertEqual(result['status'],'POSITIVE_OUTCOME')
        self.assertEqual(budgets,[1_750_000_000])
        self.assertEqual(result['observed_duration_ns'],350_000_000)
        self.assertEqual(result['prelaunch_receipt']['epoch'],read.epoch)

    def test_missing_source_admission_port_never_launches(self):
        result=self.effects.perform(Action.SIGNAL_MAIN,signal_deadline_ns=self.clock.ns+30*SECOND)
        self.assertEqual(result['status'],'STOP_UNCERTAIN')
        self.assertFalse(self.effects.signalled);self.assertEqual(self.transport.requests,[])

    def test_admission_work_exhausting_wait_budget_never_launches(self):
        def expensive():
            self.clock.ns+=2*SECOND;return self.clock.receipt()
        result=self.effects.perform(Action.SIGNAL_MAIN,signal_deadline_ns=self.clock.ns+30*SECOND,prelaunch=expensive)
        self.assertEqual(result['status'],'STOP_UNCERTAIN')
        self.assertFalse(self.effects.signalled);self.assertEqual(self.transport.requests,[])

    def test_invalid_clock_only_after_timely_positive_send_stays_claim_only(self):
        read=claim(self.clock);exchange=self.transport.exchange
        def change_after(request,budget):
            result=exchange(request,budget);self.clock.bad_receipt=True;return result
        self.transport.exchange=change_after
        result=self.seq.signal(read)
        self.assertEqual(result['status'],'POSITIVE_OUTCOME')
        self.assertTrue(self.effects.signalled);self.assertFalse(self.effects.stopped)
        self.assertIn('clock_receipt_unknown',self.seq.claims)
        self.seq.join(lambda:structure(self.clock),lambda:claim(self.clock),lambda _:None)
        follow=self.seq.postjoin_effect(Action.START_SELECTED,structure(self.clock))
        self.assertEqual(follow['status'],'POSITIVE_OUTCOME')
        self.assertFalse(self.seq.first_readback_budget_open())


if __name__=='__main__':unittest.main(verbosity=2)
