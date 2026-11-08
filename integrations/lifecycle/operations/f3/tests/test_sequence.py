from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
sys.path[:0] = ['/work', '/work/tests']
from claims import ClaimRead, OwnerCoverage, ClaimFailure, ExternalInputsUnbound, ObservedLeaseLapse, check_timing
from effects import Action, ClockClaimFault, EffectRunner, Intent, SECOND
from sequence import Sequence, OldIdentity, JoinRead, UnitPoint, EffectSafetyRefusal
from test_effects import Clock, PositiveTransport


class FixtureEvidence:
    def verify(self, read):
        if read.evidence_references != ('fixture-only-F1-F2-F4-F5',):
            raise ClaimFailure('fixture_external_evidence_missing')


def claim(clock):
    return ClaimRead(clock.ns-100_000_000, clock.ns, 0, (10_000_000,),
                     (OwnerCoverage(('agent','execution','native'), clock.ns-SECOND, clock.ns-31*SECOND,
                                    clock.ns+89*SECOND, clock.ns+89*SECOND, ('attempt','delivery'), False, 90),),
                     ('fixture-only-F1-F2-F4-F5',))


def structure(clock, kind='normal'):
    old = UnitPoint(111, 555, 0, 'active', 'running', 0, 0, 'success')
    absent = UnitPoint(0, None, 0, 'inactive', 'dead', 1, 0, 'success')
    return JoinRead(old if kind in ('waiting','raced') else absent,
                    old if kind == 'waiting' else absent, kind == 'normal',
                    (111,) if kind == 'waiting' else (), kind == 'normal',
                    'fixture-boot', 'b'*64, True, (), clock.receipt())


class SequenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='/tmp'); self.addCleanup(self.tmp.cleanup)
        self.clock = Clock(); self.transport = PositiveTransport(self.clock)
        self.effects = EffectRunner(Intent(Path(self.tmp.name)/'intent','fixture-run','a'*64),self.transport,self.clock)
        self.old = OldIdentity(111,555,0,'fixture-boot','b'*64)
        self.sequence = Sequence(self.effects,self.clock,self.old,FixtureEvidence())

    def signal(self):
        self.assertEqual(self.sequence.signal(claim(self.clock))['status'],'POSITIVE_OUTCOME')

    def test_nonraced_normal_join_after_deadline_retains_safe_continuation_only(self):
        self.signal()
        def observe():
            self.clock.ns = self.sequence.deadline_ns + 1
            return structure(self.clock)
        self.sequence.join(observe, lambda: claim(self.clock), lambda _: None)
        self.assertIn('postjoin_claim_budget_overrun',self.sequence.claims)
        result=self.sequence.postjoin_effect(Action.RELOAD_MANAGER,structure(self.clock))
        self.assertEqual(result['status'],'POSITIVE_OUTCOME')
        self.assertEqual(result['wait_budget_ns'],2*SECOND)
        self.assertFalse(self.sequence.first_readback_budget_open())

    def test_no_normal_join_at_deadline_is_start_blocking(self):
        self.signal()
        def observe():
            self.clock.ns=self.sequence.deadline_ns
            return structure(self.clock,'waiting')
        def unexpected_pause(_): self.fail('no-join deadline must refuse before another pause')
        with self.assertRaises(EffectSafetyRefusal): self.sequence.join(observe,lambda:claim(self.clock),unexpected_pause)
        with self.assertRaises(EffectSafetyRefusal): self.sequence.postjoin_effect(Action.START_SELECTED,structure(self.clock))
        self.assertEqual(len(self.transport.requests),1)

    def test_raced_proof_only_after_deadline_blocks_but_at_deadline_can_join(self):
        for offset in (0,1):
            with self.subTest(offset=offset):
                clock=Clock(); transport=PositiveTransport(clock)
                effects=EffectRunner(Intent(Path(self.tmp.name)/('case%d'%offset),'case%d'%offset,'a'*64),transport,clock)
                seq=Sequence(effects,clock,self.old,FixtureEvidence());seq.signal(claim(clock));calls=[]
                def observe():
                    calls.append(1)
                    clock.ns=seq.deadline_ns-SECOND if len(calls)==1 else seq.deadline_ns+offset
                    return structure(clock,'raced' if len(calls)==1 else 'normal')
                if offset:
                    with self.assertRaises(EffectSafetyRefusal): seq.join(observe,lambda:claim(clock),lambda _:None)
                    self.assertTrue(effects.stopped)
                else:
                    seq.join(observe,lambda:claim(clock),lambda _:None)
                    self.assertTrue(seq.joined); self.assertFalse(effects.stopped)

    def test_claim_read_error_and_overrun_do_not_manufacture_a_physical_veto(self):
        self.signal();self.clock.ns=self.sequence.deadline_ns+1
        self.assertIsNotNone(self.sequence.claim_read(lambda:claim(self.clock)))
        def unavailable(): raise FileNotFoundError('private exception text')
        self.assertIsNone(self.sequence.claim_read(unavailable))
        self.assertFalse(self.effects.stopped)
        self.assertNotIn('private exception text',str(self.sequence.claims))

    def test_timely_positive_signal_with_stale_postcheck_remains_claim_only(self):
        initial=claim(self.clock);initial=replace(initial,earliest_root_rt_ns=self.clock.ns-1_950_000_000)
        self.assertEqual(self.sequence.signal(initial)['status'],'POSITIVE_OUTCOME')
        self.assertIn('C3_stale_composite_read',self.sequence.claims)
        self.assertFalse(self.effects.stopped)
        self.sequence.join(lambda:structure(self.clock),lambda:claim(self.clock),lambda _:None)
        self.assertEqual(self.sequence.postjoin_effect(Action.START_SELECTED,structure(self.clock))['status'],'POSITIVE_OUTCOME')

    def test_S_is_positive_start_receipt_not_send_or_next_identity_read(self):
        self.signal();self.sequence.join(lambda:structure(self.clock),lambda:claim(self.clock),lambda _:None)
        before=self.clock.ns
        outcome=self.sequence.postjoin_effect(Action.START_SELECTED,structure(self.clock))
        receipt=outcome['after'];self.assertGreater(receipt['rt_ns'],before)
        self.clock.ns+=SECOND;self.sequence.bind_started_identity(('fixture-boot',222,777))
        self.assertEqual(self.sequence.S,receipt)

    def test_unbound_external_predicates_and_physical_unknown_refuse(self):
        seq=Sequence(self.effects,self.clock,self.old)
        with self.assertRaises(ExternalInputsUnbound):seq.signal(claim(self.clock))
        self.assertEqual(self.transport.requests,[])
        self.signal()
        bad=replace(structure(self.clock),journal_interval_qualified=False)
        with self.assertRaises(EffectSafetyRefusal):self.sequence.join(lambda:bad,lambda:claim(self.clock),lambda _:None)
        self.assertTrue(self.effects.stopped)

    def test_missing_old_ticks_with_explicit_preproof_race_requires_another_proof(self):
        self.signal(); calls=[]
        def observe():
            calls.append(1)
            if len(calls) > 1: return structure(self.clock)
            value=structure(self.clock,'raced')
            return replace(value,before=replace(value.before,start_ticks=None),identity_stable=False)
        self.sequence.join(observe,lambda:claim(self.clock),lambda _:None)
        self.assertEqual(len(calls),2)
        self.assertIn('preproof_structural_race',self.sequence.claims)
        self.assertTrue(self.sequence.joined)

    def test_explicit_lapse_remains_operational_failure_not_text_inference(self):
        self.signal()
        def explicit(): raise ObservedLeaseLapse('api',('agent','execution','attempt','delivery'))
        self.sequence.claim_read(explicit)
        self.assertTrue(self.sequence.operational_failure)
        self.assertEqual(self.sequence.lapse_observations[0]['source'],'api')
        self.assertFalse(self.effects.stopped)
        self.assertFalse(self.sequence.first_readback_budget_open())

    def test_pre_signal_burst_age_and_margin_use_whole_composite(self):
        baseline=claim(self.clock)
        stale=replace(baseline,earliest_root_rt_ns=self.clock.ns-2*SECOND-1)
        low=replace(baseline,owners=(replace(baseline.owners[0],expires_ns=self.clock.ns+75*SECOND),))
        second=replace(baseline.owners[0],identity=('other','execution','native'),last_seen_ns=self.clock.ns-5*SECOND)
        spread=replace(baseline,owners=baseline.owners+(second,),control_durations_ns=(10_000_000,10_000_000))
        for value in (stale,low,spread):
            with self.assertRaises(ClaimFailure): self.sequence.signal(value)
        self.assertEqual(self.transport.requests,[])
        boundary=replace(baseline,earliest_root_rt_ns=self.clock.ns-2*SECOND)
        self.assertEqual(check_timing(boundary,self.clock.receipt(),75,complete_pass=True),510_000_000)

    def test_claim_clock_fault_after_physical_join_does_not_veto_safe_effect(self):
        self.signal(); read=structure(self.clock); controls=claim(self.clock)
        def broken(): raise ClockClaimFault('fixture discontinuity')
        self.clock.receipt=broken
        self.sequence.join(lambda:read,lambda:controls,lambda _:None)
        self.assertTrue(self.sequence.joined); self.assertIsNone(self.sequence.E)
        result=self.sequence.postjoin_effect(Action.START_SELECTED,read)
        self.assertEqual(result['status'],'POSITIVE_OUTCOME')
        self.assertEqual(result['wait_budget_ns'],2*SECOND)
        self.assertIsNone(self.sequence.S)
        self.assertFalse(self.sequence.first_readback_budget_open())


if __name__=='__main__':unittest.main(verbosity=2)
