from dataclasses import replace
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path[:0]=['/work','/work/accepted','/work/tests']
from backend_binding import BackendUnknown,configured_binding,ssh_argv,systemctl_argv
from backend_files import PinVerifier
from backend_wire import decode
from backend_process import Completion,SSHProcess,SystemctlProcess
from backend_entry import main,serve_once
from root_launcher import FixedSSHLauncher
from remote_executor import RemoteExecutor,RemoteRun
from effects import Action,EffectRunner,ExchangeUnknown,Intent,SECOND,canonical
from transport import ChildTransport
from fixtures import Clock,binding,request,remote,Admission,SSHChannel


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.binding,self.old=binding(self.base)
    def root(self,mode='positive'):
        executor,observer,process,completion=remote(self.binding,self.old)
        clock=Clock();intent=Intent(self.base/'root'/'run',self.binding.run_id,self.binding.digest())
        admission=Admission(clock);channel=SSHChannel(executor);channel.mode=mode;self.addCleanup(channel.close_fixture)
        launcher=FixedSSHLauncher(self.binding,intent,PinVerifier(),admission,channel)
        engine=EffectRunner(intent,ChildTransport(clock.monotonic_ns,launcher),clock)
        return executor,observer,process,completion,clock,admission,channel,launcher,engine
    def test_end_to_end_fixed_actions_causal_outcomes_and_intents(self):
        executor,observer,process,completion,clock,admission,channel,launcher,engine=self.root()
        for action in (Action.SIGNAL_MAIN,Action.RELOAD_MANAGER,Action.START_SELECTED):
            out=engine.perform(action,signal_deadline_ns=clock.ns+30*SECOND,prelaunch=admission.ready)
            self.assertEqual(out['status'],'POSITIVE_OUTCOME');self.assertFalse(out['normal_join_inferred'])
            self.assertEqual(out['outcome']['binding_sha256'],self.binding.digest())
        self.assertEqual([x[2:] for x in process.calls],[('kill','--kill-whom=main','--signal=SIGTERM','cairn-presence.service'),('daemon-reload',),('start','cairn-presence.service')])
        self.assertEqual(len(channel.calls),3)
        again=engine.perform(Action.START_SELECTED)
        self.assertEqual(again['status'],'STOP_UNCERTAIN');self.assertEqual(len(channel.calls),3)
    def test_launcher_refuses_before_durable_requested_intent(self):
        clock=Clock();intent=Intent(self.base/'root'/'run',self.binding.run_id,self.binding.digest())
        channel=SSHChannel(None);launcher=FixedSSHLauncher(self.binding,intent,PinVerifier(),Admission(clock),channel)
        with self.assertRaises(ExchangeUnknown):launcher.launch(request(self.binding,Action.SIGNAL_MAIN))
        self.assertEqual(channel.calls,[])
    def test_default_binding_entry_and_ports_refuse_before_input(self):
        with self.assertRaises(BackendUnknown):configured_binding()
        with self.assertRaises(ExchangeUnknown):FixedSSHLauncher().launch({})
        self.assertEqual(RemoteExecutor().execute_wire(b'{}\n').returncode,2)
        class Unreadable:
            def read(self,n):raise AssertionError('disabled entry must not read wire')
        self.assertEqual(serve_once(None,Unreadable(),io.BytesIO(),io.BytesIO()),2)
        with patch('sys.stdout',new=io.StringIO()):self.assertEqual(main(),2)
    def test_stale_root_admission_launches_no_SSH_or_remote_command(self):
        executor,observer,process,completion,clock,admission,channel,launcher,engine=self.root()
        admission.delay_on_launcher=True
        out=engine.perform(Action.SIGNAL_MAIN,signal_deadline_ns=clock.ns+30*SECOND,prelaunch=admission.ready)
        self.assertEqual(out['status'],'STOP_UNCERTAIN');self.assertEqual(channel.calls,[]);self.assertEqual(process.calls,[])
    def test_remote_prepared_run_required_before_command(self):
        run=RemoteRun(self.binding)
        executor=RemoteExecutor(self.binding,run,PinVerifier(),object(),object(),object())
        out=executor.execute_wire(canonical(request(self.binding,Action.SIGNAL_MAIN))+b'\n')
        self.assertEqual(out.returncode,2);self.assertFalse(run.directory.exists())
    def test_bad_binding_pin_refuses_without_remote_effect(self):
        executor,observer,process,completion=remote(self.binding,self.old)
        Path(self.binding.remote_systemctl.path).write_bytes(b'changed fixture bytes')
        out=executor.execute_wire(canonical(request(self.binding,Action.SIGNAL_MAIN))+b'\n')
        self.assertEqual(out.returncode,2);self.assertEqual(process.calls,[])
    def test_existing_uncertain_action_blocks_new_executor_or_new_effect_id(self):
        executor,observer,process,completion=remote(self.binding,self.old)
        completion.error=BackendUnknown('fixture lost command observation')
        out=executor.execute_wire(canonical(request(self.binding,Action.SIGNAL_MAIN))+b'\n')
        self.assertEqual(out.returncode,2);self.assertEqual(len(process.calls),1);self.assertIn(Action.SIGNAL_MAIN,executor.children)
        fresh=RemoteExecutor(self.binding,RemoteRun(self.binding),executor.verifier,executor.proof,process,completion)
        second=fresh.execute_wire(canonical(request(self.binding,Action.RELOAD_MANAGER,2))+b'\n')
        self.assertEqual(second.returncode,2);self.assertEqual(len(process.calls),1)
    def test_fixed_subprocess_argv_uses_no_shell_or_privilege_escalation(self):
        with patch('backend_process.subprocess.Popen') as spawn:
            SSHProcess().spawn(self.binding,self.base/'root')
            args,kw=spawn.call_args;self.assertEqual(args[0],ssh_argv(self.binding));self.assertFalse(kw['shell'])
            self.assertIn('BatchMode=yes',args[0]);self.assertIn('StrictHostKeyChecking=yes',args[0])
            SystemctlProcess().spawn(self.binding,Action.SIGNAL_MAIN)
            args,kw=spawn.call_args;self.assertEqual(args[0],systemctl_argv(self.binding,Action.SIGNAL_MAIN));self.assertFalse(kw['shell'])


class FaultMatrixTests(unittest.TestCase):
    def test_root_transport_loss_partial_duplicate_and_mismatch_after_remote_launch(self):
        for mode in ('lost','partial','duplicate','mismatch'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                base=Path(tmp);b,old=binding(base);executor,observer,process,completion=remote(b,old)
                clock=Clock();intent=Intent(base/'root'/'run',b.run_id,b.digest());admission=Admission(clock)
                channel=SSHChannel(executor);channel.mode=mode
                engine=EffectRunner(intent,ChildTransport(clock.monotonic_ns,FixedSSHLauncher(b,intent,PinVerifier(),admission,channel)),clock)
                try:
                    result=engine.perform(Action.SIGNAL_MAIN,signal_deadline_ns=clock.ns+30*SECOND,prelaunch=admission.ready)
                    self.assertEqual(result['status'],'STOP_UNCERTAIN');self.assertEqual(len(process.calls),1)
                    engine.perform(Action.START_SELECTED);self.assertEqual(len(channel.calls),1)
                finally:channel.close_fixture()
    def test_remote_raced_restart_and_journal_proof_refuse_before_postjoin_command(self):
        for fault in ('raced','restart','journal'):
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as tmp:
                b,old=binding(Path(tmp));executor,observer,process,completion=remote(b,old)
                self.assertEqual(executor.execute_wire(canonical(request(b,Action.SIGNAL_MAIN))+b'\n').returncode,0)
                observer.fault=fault
                self.assertEqual(executor.execute_wire(canonical(request(b,Action.RELOAD_MANAGER,2))+b'\n').returncode,2)
                self.assertEqual(len(process.calls),1)
    def test_remote_nonzero_missing_reap_stderr_oversize_are_unknown_and_not_repeated(self):
        outcomes=[Completion(1,b'',b'',True),Completion(None,b'',b'',True),Completion(0,b'',b'',False),
                  Completion(0,b'',b'error',True),Completion(0,b'x'*8193,b'',True)]
        for value in outcomes:
            with self.subTest(value=value.returncode),tempfile.TemporaryDirectory() as tmp:
                b,old=binding(Path(tmp));executor,observer,process,completion=remote(b,old);completion.result=value
                data=canonical(request(b,Action.SIGNAL_MAIN))+b'\n'
                self.assertEqual(executor.execute_wire(data).returncode,2);self.assertEqual(executor.execute_wire(data).returncode,2)
                self.assertEqual(len(process.calls),1);self.assertIn(Action.SIGNAL_MAIN,executor.children)
    def test_malformed_or_command_bearing_wire_cannot_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            b,old=binding(Path(tmp));good=request(b,Action.SIGNAL_MAIN)
            wires=[b'',b'{}\n',b'x'*4097,canonical(good),canonical(good)+b'\n'+canonical(good),
                   canonical({**good,'command':'arbitrary'})+b'\n',canonical({**good,'run_id':'other'})+b'\n',
                   b'{"schema":0,"schema":0}\n',canonical({**good,'request_sha256':'f'*64})+b'\n']
            for wire in wires:
                with self.subTest(size=len(wire)):
                    with self.assertRaises(BackendUnknown):decode(wire,b)


if __name__=='__main__':unittest.main(verbosity=2)
