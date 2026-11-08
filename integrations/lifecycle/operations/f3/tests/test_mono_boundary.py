"""M1: separately returned monotonic values at the actual sequencing boundaries."""
from pathlib import Path
import sys
import tempfile
import unittest
sys.path[:0]=['/work','/work/tests']
from effects import Action, EffectRunner, Intent, SECOND
from sequence import Sequence, OldIdentity, EffectSafetyRefusal
from test_effects import Clock, PositiveTransport
from test_sequence import claim, structure, FixtureEvidence
from test_transport import FaultTransport


class OnceBadClock(Clock):
    def __init__(self):super().__init__();self.pending=[];self.bad_samples=0
    def monotonic_ns(self):
        if self.pending:
            self.bad_samples+=1;value=self.pending.pop(0)
            if isinstance(value,Exception):raise value
            return value
        return self.ns


def malformed():
    return [float('nan'),float('inf'),float('-inf'),.5,True,False,-1,'123',None,[],OSError('fixture clock failed')]


class MonoBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.counter=0
    def system(self):
        self.counter+=1;clock=OnceBadClock();transport=PositiveTransport(clock)
        effects=EffectRunner(Intent(Path(self.tmp.name)/str(self.counter),'fixture','a'*64),transport,clock)
        seq=Sequence(effects,clock,OldIdentity(111,555,0,'fixture-boot','b'*64),FixtureEvidence())
        self.assertEqual(seq.signal(claim(clock))['status'],'POSITIVE_OUTCOME')
        return clock,transport,effects,seq
    def blocked(self,clock,transport,effects,seq,observe):
        with self.assertRaises(EffectSafetyRefusal):seq.join(observe,lambda:claim(clock),lambda _:None)
        self.assertTrue(effects.stopped);self.assertFalse(seq.joined)
        with self.assertRaises(EffectSafetyRefusal):seq.postjoin_effect(Action.START_SELECTED,structure(clock))
        self.assertEqual(len(transport.requests),1);self.assertEqual(clock.bad_samples,1)

    def test_raced_late_proof_malformed_or_throwing_sample_never_joins_or_starts(self):
        for value in malformed():
            with self.subTest(value=repr(value)):
                clock,transport,effects,seq=self.system();calls=[]
                def observe():
                    calls.append(1)
                    if len(calls)==1:
                        clock.ns=seq.deadline_ns-SECOND;return structure(clock,'raced')
                    clock.ns=seq.deadline_ns+1;clock.pending=[value];return structure(clock)
                self.blocked(clock,transport,effects,seq,observe)
                self.assertEqual(len(calls),2)

    def test_no_join_malformed_or_throwing_budget_refuses_without_another_poll(self):
        for value in malformed():
            with self.subTest(value=repr(value)):
                clock,transport,effects,seq=self.system();calls=[]
                def observe():
                    calls.append(1)
                    if len(calls)>1:raise AssertionError('invalid budget must not keep polling')
                    clock.pending=[value];return structure(clock,'waiting')
                self.blocked(clock,transport,effects,seq,observe)
                self.assertEqual(len(calls),1)

    def test_nonraced_positive_join_keeps_physical_proof_and_C6_continuation(self):
        for value in malformed():
            with self.subTest(value=repr(value)):
                clock,transport,effects,seq=self.system()
                def observe():clock.pending=[value];return structure(clock)
                seq.join(observe,lambda:claim(clock),lambda _:None)
                self.assertTrue(seq.joined);self.assertFalse(effects.stopped)
                self.assertTrue(seq.claims);self.assertIsNotNone(seq.E)
                self.assertFalse(seq.first_readback_budget_open())
                self.assertEqual(seq.postjoin_effect(Action.START_SELECTED,structure(clock))['status'],'POSITIVE_OUTCOME')
                self.assertEqual(len(transport.requests),2)

    def test_claim_read_malformed_or_throwing_sample_is_claim_unknown_only(self):
        for value in malformed():
            with self.subTest(value=repr(value)):
                clock,transport,effects,seq=self.system();clock.pending=[value]
                self.assertIsNone(seq.claim_read(lambda:claim(clock)))
                self.assertTrue(seq.claims);self.assertFalse(effects.stopped)
                self.assertEqual(len(transport.requests),1)

    def test_first_readback_malformed_or_throwing_sample_never_qualifies(self):
        for value in malformed():
            with self.subTest(value=repr(value)):
                clock,transport,effects,seq=self.system();clock.pending=[value]
                self.assertFalse(seq.first_readback_budget_open())
                self.assertTrue(seq.claims);self.assertFalse(effects.stopped)
                self.assertFalse(seq.first_readback_budget_open())

    def test_first_readback_valid_integer_at_and_after_deadline_stays_closed(self):
        for offset,expected in ((-1,True),(0,False),(1,False)):
            clock,transport,effects,seq=self.system();clock.ns=seq.deadline_ns+offset
            self.assertEqual(seq.first_readback_budget_open(),expected)

    def test_raced_proof_already_in_budget_keeps_later_claim_fault_separate(self):
        for value in malformed():
            with self.subTest(value=repr(value)):
                clock,transport,effects,seq=self.system();calls=[]
                def observe():
                    calls.append(1)
                    if len(calls)==1:
                        clock.ns=seq.deadline_ns-2*SECOND;return structure(clock,'raced')
                    clock.ns=seq.deadline_ns-1;clock.pending=[clock.ns,value];return structure(clock)
                seq.join(observe,lambda:claim(clock),lambda _:None)
                self.assertTrue(seq.joined);self.assertFalse(effects.stopped)
                self.assertTrue(any(s.startswith('postjoin_claim_clock_unknown:') for s in seq.claims))
                self.assertEqual(seq.postjoin_effect(Action.START_SELECTED,structure(clock))['status'],'POSITIVE_OUTCOME')

    def test_postjoin_effect_timeout_still_blocks_after_claim_only_clock_failure(self):
        clock,transport,effects,seq=self.system()
        def observe():clock.pending=[float('nan')];return structure(clock)
        seq.join(observe,lambda:claim(clock),lambda _:None)
        self.assertTrue(seq.joined);self.assertFalse(effects.stopped)
        uncertain=FaultTransport(clock,'late-after-actuation');effects.transport=uncertain
        result=seq.postjoin_effect(Action.START_SELECTED,structure(clock))
        self.assertEqual(result['status'],'STOP_UNCERTAIN');self.assertTrue(effects.stopped)
        with self.assertRaises(EffectSafetyRefusal):seq.postjoin_effect(Action.RELOAD_MANAGER,structure(clock))
        self.assertEqual(len(transport.requests),1);self.assertEqual(len(uncertain.requests),1)


if __name__=='__main__':unittest.main(verbosity=2)
