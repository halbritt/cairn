from dataclasses import replace
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path[:0]=['/work','/work/tests']
from effects import Action, EffectRunner, Intent, SECOND
from sequence import Sequence, OldIdentity, EffectSafetyRefusal, UnitPoint
from transport import ChildTransport
from test_effects import Clock, PositiveTransport
from test_sequence import claim, structure, FixtureEvidence
from test_transport import FixedDummyLauncher


class PhysicalGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.count=0
    def system(self):
        self.count+=1;clock=Clock();transport=PositiveTransport(clock)
        effects=EffectRunner(Intent(Path(self.tmp.name)/str(self.count),'fixture','a'*64),transport,clock)
        seq=Sequence(effects,clock,OldIdentity(111,555,0,'fixture-boot','b'*64),FixtureEvidence())
        seq.signal(claim(clock));return clock,transport,effects,seq
    def test_postjoin_fresh_physical_changes_refuse_both_fixed_actions(self):
        for action in (Action.START_SELECTED,Action.RELOAD_MANAGER):
            for fault in ('restart','new_pid','old_ticks','identity_race','abnormal_exit','preservation','boot'):
                with self.subTest(action=action.value,fault=fault):
                    clock,transport,effects,seq=self.system()
                    seq.join(lambda:structure(clock),lambda:claim(clock),lambda _:None)
                    fresh=structure(clock)
                    if fault=='restart':fresh=replace(fresh,after=replace(fresh.after,restarts=1))
                    elif fault=='new_pid':fresh=replace(fresh,after=UnitPoint(222,777,0,'active','running',0,0,'success'))
                    elif fault=='old_ticks':fresh=replace(fresh,after=UnitPoint(111,999,0,'active','running',0,0,'success'))
                    elif fault=='identity_race':fresh=replace(structure(clock,'raced'),identity_stable=False)
                    elif fault=='abnormal_exit':fresh=replace(fresh,after=replace(fresh.after,exit_code=2,exit_status=9,result='signal'))
                    elif fault=='preservation':fresh=replace(fresh,preservation_digest='c'*64)
                    elif fault=='boot':fresh=replace(fresh,boot_id='other-boot')
                    with self.assertRaises(EffectSafetyRefusal):seq.postjoin_effect(action,fresh)
                    self.assertTrue(effects.stopped);self.assertEqual(len(transport.requests),1)
    def test_join_journal_contradiction_refuses_and_marks_operational_failure(self):
        for flag in ('hidden_kill','timeout','unexpected_restart'):
            with self.subTest(flag=flag):
                clock,transport,effects,seq=self.system()
                flagged=replace(structure(clock),journal_flags=(flag,))
                with self.assertRaises(EffectSafetyRefusal):seq.join(lambda:flagged,lambda:claim(clock),lambda _:None)
                self.assertTrue(seq.operational_failure);self.assertTrue(effects.stopped);self.assertFalse(seq.joined)
                with self.assertRaises(EffectSafetyRefusal):seq.postjoin_effect(Action.START_SELECTED,structure(clock))
                self.assertEqual(len(transport.requests),1)
    def test_postjoin_journal_contradiction_refuses_both_actions(self):
        for action in (Action.START_SELECTED,Action.RELOAD_MANAGER):
            with self.subTest(action=action.value):
                clock,transport,effects,seq=self.system()
                seq.join(lambda:structure(clock),lambda:claim(clock),lambda _:None)
                flagged=replace(structure(clock),journal_flags=('hidden_kill',))
                with self.assertRaises(EffectSafetyRefusal):seq.postjoin_effect(action,flagged)
                self.assertTrue(seq.operational_failure);self.assertTrue(effects.stopped)
                self.assertEqual(len(transport.requests),1)


class CustodyAndDurabilityTests(unittest.TestCase):
    def test_EOF_then_positive_reap_is_required_for_causal_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock=Clock();launcher=FixedDummyLauncher('eof-before-reap')
            transport=ChildTransport(time.monotonic_ns,launcher)
            engine=EffectRunner(Intent(Path(tmp)/'intent','fixture','a'*64),transport,clock)
            try:
                result=engine.perform(Action.SIGNAL_MAIN,signal_deadline_ns=clock.ns+30*SECOND,prelaunch=clock.receipt)
                self.assertEqual(result['status'],'POSITIVE_OUTCOME')
                child=next(iter(transport.children.values()))
                self.assertEqual(child.returncode,0);self.assertEqual(launcher.count,1)
                self.assertTrue(child.stdout.closed and child.stderr.closed)
                self.assertEqual(result['outcome']['run_id'],'fixture')
                self.assertEqual(result['outcome']['request_sha256'],transport_request_digest(engine))
                self.assertFalse(result['remote_descendant_terminal_guarantee'])
            finally:
                # Fixture-owned dummy only; even a mutant gets natural bounded cleanup.
                for child in transport.children.values():
                    if child.returncode is None:child.communicate(timeout=2)

    def test_intent_parent_fsync_follows_file_and_directory_before_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent=Path(tmp);parent_id=(parent.stat().st_dev,parent.stat().st_ino)
            events=[];real_fsync=os.fsync;clock=Clock();transport=PositiveTransport(clock)
            def traced(fd):
                s=os.fstat(fd)
                kind='parent' if (s.st_dev,s.st_ino)==parent_id else ('intent-dir' if stat.S_ISDIR(s.st_mode) else 'receipt-file')
                events.append(kind);return real_fsync(fd)
            exchange=transport.exchange
            def launched(request,budget):events.append('launch');return exchange(request,budget)
            transport.exchange=launched
            with patch('effects.os.fsync',side_effect=traced):
                intent=Intent(parent/'intent','fixture','a'*64)
                result=EffectRunner(intent,transport,clock).perform(Action.SIGNAL_MAIN,signal_deadline_ns=clock.ns+30*SECOND,prelaunch=clock.receipt)
            self.assertEqual(result['status'],'POSITIVE_OUTCOME')
            self.assertEqual(events[:3],['receipt-file','intent-dir','parent'])
            self.assertLess(events.index('parent'),events.index('launch'))

    def test_intent_parent_fsync_failure_prevents_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent=Path(tmp);parent_id=(parent.stat().st_dev,parent.stat().st_ino)
            real_fsync=os.fsync;clock=Clock();transport=PositiveTransport(clock);events=[]
            def failing(fd):
                s=os.fstat(fd)
                if (s.st_dev,s.st_ino)==parent_id:
                    events.append('parent-failure');raise OSError('fixture parent durability failure')
                return real_fsync(fd)
            with patch('effects.os.fsync',side_effect=failing):
                with self.assertRaises(OSError):
                    intent=Intent(parent/'intent','fixture','a'*64)
                    EffectRunner(intent,transport,clock).perform(Action.SIGNAL_MAIN,signal_deadline_ns=clock.ns+30*SECOND,prelaunch=clock.receipt)
            self.assertEqual(events,['parent-failure']);self.assertEqual(transport.requests,[])


def transport_request_digest(engine):
    import json
    requested=next(engine.intent.directory.glob('*-effect-requested.json'))
    return json.loads(requested.read_text())['request_sha256']


if __name__=='__main__':unittest.main(verbosity=2)
