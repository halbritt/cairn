"""Exercise the actual file/pipe/clock adapters with private fixture resources."""
import io
from dataclasses import replace
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path[:0]=['/work','/work/accepted','/work/tests']
from backend_binding import BackendUnknown
from backend_entry import serve_once
from backend_files import PinVerifier
from backend_process import BoundedCompletion
from effects import Action,EffectRunner,Intent,SECOND,canonical
from root_launcher import FixedSSHLauncher
from transport import ChildTransport
from sequence import Sequence,EffectSafetyRefusal,UnitPoint
from claims import ClaimFailure
from fixtures import binding,remote,request,PipeChild,Clock,Admission,SSHChannel


class Samples:
    def __init__(self,values):self.values=iter(values)
    def __call__(self):
        value=next(self.values)
        if isinstance(value,Exception):raise value
        return value


class CollectorTests(unittest.TestCase):
    def child(self,**kwargs):
        child=PipeChild(**kwargs);self.addCleanup(child.close_fixture);return child
    def test_EOF_before_reap_requires_later_positive_reap(self):
        child=self.child(stdout=b'fixed fixture output');polls=iter((None,None,0));child.poll=lambda:next(polls)
        result=BoundedCompletion(Samples((0,1,2,3,4))).collect(child,2*SECOND)
        self.assertEqual(result.returncode,0);self.assertTrue(result.eof)
        self.assertEqual(result.stdout,b'fixed fixture output')
    def test_EOF_without_reap_expires_unknown(self):
        child=self.child(returncode=None)
        with self.assertRaises(BackendUnknown):
            BoundedCompletion(Samples((0,1,SECOND,2*SECOND))).collect(child,2*SECOND)
        self.assertIsNone(child.poll())
    def test_actual_clock_return_faults_never_produce_completion(self):
        faults=(float('nan'),float('-inf'),float('inf'),True,-1,'1',None,RuntimeError('fixture clock error'))
        for fault in faults:
            for after_initial in (False,True):
                with self.subTest(fault=repr(fault),after_initial=after_initial):
                    child=self.child()
                    samples=(0,fault) if after_initial else (fault,)
                    with self.assertRaises(BackendUnknown):BoundedCompletion(Samples(samples)).collect(child,2*SECOND)
        child=self.child()
        with self.assertRaises(BackendUnknown):BoundedCompletion(Samples((100,99))).collect(child,2*SECOND)
    def test_output_limit_is_enforced_while_reading_pipe(self):
        # Real pipe bytes exceed the real2048B stderr cap; no mocked collector result.
        child=self.child(stderr=b'x'*2049)
        with self.assertRaises(BackendUnknown):BoundedCompletion(Samples((0,1,2,3))).collect(child,2*SECOND)


class CompositionBoundaryTests(unittest.TestCase):
    def test_changed_missing_extra_or_linked_source_member_blocks_command(self):
        for fault in ('changed','missing','extra','symlink','mode'):
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as tmp:
                b,old=binding(Path(tmp));executor,observer,process,completion=remote(b,old)
                member=Path(b.remote_source_files[0].path)
                if fault=='changed':member.write_bytes(b'changed')
                elif fault=='missing':member.unlink()
                elif fault=='extra':(member.parent/'unexpected.py').write_bytes(b'extra')
                elif fault=='symlink':member.unlink();member.symlink_to(b.remote_systemctl.path)
                else:member.chmod(0o664)
                self.assertEqual(executor.execute_wire(canonical(request(b,Action.SIGNAL_MAIN))+b'\n').returncode,2)
                self.assertEqual(process.calls,[])
    def test_remote_parent_fsync_failure_blocks_effect_with_partial_intent_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            b,old=binding(Path(tmp));executor,observer,process,completion=remote(b,old)
            original=os.fsync;parent=executor.run.directory.stat();seen=[]
            def fsync(fd):
                st=os.fstat(fd);seen.append((st.st_dev,st.st_ino))
                if (st.st_dev,st.st_ino)==(parent.st_dev,parent.st_ino):raise OSError('fixture parent fsync failed')
                return original(fd)
            with patch('os.fsync',side_effect=fsync):
                result=executor.execute_wire(canonical(request(b,Action.SIGNAL_MAIN))+b'\n')
            self.assertIn((parent.st_dev,parent.st_ino),seen)
            self.assertEqual(result.returncode,2);self.assertEqual(process.calls,[])
            self.assertTrue((executor.run.directory/Action.SIGNAL_MAIN.value).exists())
    def test_out_of_order_or_duplicate_action_new_identity_is_not_relaunched(self):
        for mode in ('out_of_order','new_identity'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                b,old=binding(Path(tmp));executor,observer,process,completion=remote(b,old)
                if mode=='new_identity':
                    self.assertEqual(executor.execute_wire(canonical(request(b,Action.SIGNAL_MAIN))+b'\n').returncode,0)
                    action=Action.SIGNAL_MAIN;expected=1
                else:action=Action.START_SELECTED;expected=0
                self.assertEqual(executor.execute_wire(canonical(request(b,action,99))+b'\n').returncode,2)
                self.assertEqual(len(process.calls),expected)
    def test_wire_read_is_bounded_and_cannot_invoke_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            b,old=binding(Path(tmp));executor,observer,process,completion=remote(b,old)
            source=io.BytesIO(b'x'*100_000);out=io.BytesIO();err=io.BytesIO()
            self.assertEqual(serve_once(executor,source,out,err),2)
            self.assertEqual(source.tell(),4097);self.assertEqual(process.calls,[]);self.assertEqual(out.getvalue(),b'')
    def test_SSH_launch_failure_is_unknown_and_consumed_without_remote_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);b,old=binding(base);clock=Clock();admission=Admission(clock)
            intent=Intent(base/'root'/'run',b.run_id,b.digest())
            class FailedSSH:
                calls=0
                def spawn(self,*args):self.calls+=1;raise OSError('fixture SSH spawn failure')
            process=FailedSSH();launcher=FixedSSHLauncher(b,intent,PinVerifier(),admission,process)
            engine=EffectRunner(intent,ChildTransport(clock.monotonic_ns,launcher),clock)
            result=engine.perform(Action.SIGNAL_MAIN,signal_deadline_ns=clock.ns+30*SECOND,prelaunch=admission.ready)
            self.assertEqual(result['status'],'STOP_UNCERTAIN');self.assertTrue(result['signalled'])
            engine.perform(Action.SIGNAL_MAIN,signal_deadline_ns=clock.ns+30*SECOND,prelaunch=admission.ready)
            self.assertEqual(process.calls,1)


class SequenceCompositionTests(unittest.TestCase):
    def setup_sequence(self,base):
        b,old=binding(base);executor,observer,process,completion=remote(b,old)
        clock=Clock();admission=Admission(clock);intent=Intent(base/'root'/'run',b.run_id,b.digest())
        channel=SSHChannel(executor);self.addCleanup(channel.close_fixture)
        engine=EffectRunner(intent,ChildTransport(clock.monotonic_ns,FixedSSHLauncher(b,intent,PinVerifier(),admission,channel)),clock)
        class Evidence:
            def verify(self,read):
                if read.evidence_references!=('fixture evidence',):raise ClaimFailure('fixture_evidence_missing')
        seq=Sequence(engine,clock,old,Evidence())
        self.assertEqual(seq.signal(admission.read)['status'],'POSITIVE_OUTCOME')
        return seq,clock,admission,observer,channel,process
    def test_nonraced_join_claim_only_failure_allows_C6_sequence_without_adoption(self):
        for fault in ('stale_coverage','clock_receipt'):
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as tmp:
                seq,clock,admission,observer,channel,process=self.setup_sequence(Path(tmp))
                clock.ns=seq.deadline_ns+1
                if fault=='clock_receipt':
                    def broken():raise RuntimeError('fixture claim clock unavailable')
                    clock.receipt=broken
                seq.join(observer.read_structure,lambda:admission.read,lambda _:self.fail('normal join needs no pause'))
                self.assertTrue(seq.joined);self.assertTrue(seq.claims);self.assertFalse(seq.effects.stopped)
                for action in (Action.RELOAD_MANAGER,Action.START_SELECTED):
                    result=seq.postjoin_effect(action,observer.read_structure())
                    self.assertEqual(result['status'],'POSITIVE_OUTCOME');self.assertEqual(result['wait_budget_ns'],2*SECOND)
                self.assertEqual(len(channel.calls),3);self.assertEqual(len(process.calls),3)
                self.assertFalse(seq.first_readback_budget_open())
    def test_missing_uncertain_or_raced_late_physical_join_blocks_next_command(self):
        for fault in ('missing','uncertain','raced_late'):
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as tmp:
                seq,clock,admission,observer,channel,process=self.setup_sequence(Path(tmp));calls=[]
                def structure():
                    calls.append(1);normal=observer.read_structure()
                    if fault=='uncertain':raise FileNotFoundError('fixture structure unavailable')
                    if fault=='missing':
                        clock.ns=seq.deadline_ns
                        old=UnitPoint(111,555,0,'active','running',0,0,'success')
                        return replace(normal,before=old,after=old,old_absent=False,cgroup_members=(111,),lock_released=False)
                    if len(calls)==1:
                        clock.ns=seq.deadline_ns-SECOND
                        return replace(normal,before=UnitPoint(111,555,0,'active','running',0,0,'success'),identity_stable=False)
                    clock.ns=seq.deadline_ns+1;return normal
                with self.assertRaises(EffectSafetyRefusal):seq.join(structure,lambda:admission.read,lambda _:None)
                self.assertFalse(seq.joined);self.assertTrue(seq.effects.stopped)
                with self.assertRaises(EffectSafetyRefusal):seq.postjoin_effect(Action.RELOAD_MANAGER,observer.read_structure())
                self.assertEqual(len(channel.calls),1);self.assertEqual(len(process.calls),1)


if __name__=='__main__':unittest.main(verbosity=2)
