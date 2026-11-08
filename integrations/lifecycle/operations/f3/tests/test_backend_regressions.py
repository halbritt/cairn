"""Regression boundaries for inherited wait policy and invalid file intake."""
import os
import json
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path[:0]=['/work','/work/accepted','/work/tests']
from backend_binding import BackendUnknown,ssh_argv
from backend_process import SSHProcess,SystemctlProcess,BoundedCompletion,WaitOwnedChild,Completion
from backend_files import regular_bytes,PinVerifier
from root_launcher import FixedSSHLauncher
from transport import ChildTransport
from effects import Action,canonical,SECOND,Intent,EffectRunner,ExchangeUnknown
from fixtures import binding,remote,request,PipeChild,Clock,Admission,SSHChannel
from remote_executor import RemoteExecutor,RemoteRun


class SignalCustodyTests(unittest.TestCase):
    def test_incompatible_SIGCHLD_refuses_before_concrete_process_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);b,old=binding(base)
            for disposition in (signal.SIG_IGN,lambda *_:None):
                with self.subTest(disposition=str(disposition)),patch('signal.getsignal',return_value=disposition),patch('backend_process.subprocess.Popen') as spawn:
                    with self.assertRaises(BackendUnknown):SSHProcess().spawn(b,base/'root')
                    with self.assertRaises(BackendUnknown):SystemctlProcess().spawn(b,Action.SIGNAL_MAIN)
                    self.assertEqual(spawn.call_count,0)

    def test_changed_SIGCHLD_blocks_positive_poll_and_keeps_owned_child(self):
        for samples in ((signal.SIG_IGN,),(signal.SIG_DFL,signal.SIG_IGN)):
            with self.subTest(samples=samples):
                child=PipeChild();self.addCleanup(child.close_fixture);owned=WaitOwnedChild(child)
                with patch('signal.getsignal',side_effect=samples):
                    with self.assertRaises(BackendUnknown):owned.poll()
                self.assertIs(owned.child,child)

    def test_changed_policy_during_collection_or_before_reply_refuses_positive(self):
        state=[signal.SIG_DFL];child=PipeChild();self.addCleanup(child.close_fixture)
        def poll():state[0]=signal.SIG_IGN;return 0
        child.poll=poll
        with patch('signal.getsignal',side_effect=lambda _:state[0]):
            with self.assertRaises(BackendUnknown):BoundedCompletion(lambda:0).collect(child,2*SECOND)
        state[0]=signal.SIG_DFL
        with tempfile.TemporaryDirectory() as tmp:
            b,old=binding(Path(tmp));executor,observer,process,completion=remote(b,old)
            def collect(child,budget):state[0]=signal.SIG_IGN;return Completion(0,b'',b'',True)
            completion.collect=collect
            with patch('signal.getsignal',side_effect=lambda _:state[0]):
                result=executor.execute_wire(canonical(request(b,Action.SIGNAL_MAIN))+b'\n')
            self.assertEqual(result.returncode,2);self.assertEqual(result.stdout,b'')
            self.assertEqual(len(process.calls),1);self.assertTrue(executor.stopped)
            self.assertIn(Action.SIGNAL_MAIN,executor.children)

    def test_ignored_SIGCHLD_real_exit3_child_cannot_become_APPLIED(self):
        # Root-authorized ONE harmless fixed program in the enclosing bwrap only.
        # The fixture changes/restores its own handler; backend code never does.
        class FixedExitThree:
            calls=0;child=None
            def spawn(self,binding,action):
                self.calls+=1
                self.child=subprocess.Popen([sys.executable,'-I','-B','-c','raise SystemExit(3)'],
                    stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                    shell=False,close_fds=True)
                return self.child
        prior=signal.getsignal(signal.SIGCHLD);fixed=FixedExitThree()
        try:
            signal.signal(signal.SIGCHLD,signal.SIG_IGN)
            with tempfile.TemporaryDirectory() as tmp:
                b,old=binding(Path(tmp));executor,observer,process,completion=remote(b,old)
                executor.process=fixed;executor.completion=BoundedCompletion(lambda:0)
                wire=canonical(request(b,Action.SIGNAL_MAIN))+b'\n'
                reply=executor.execute_wire(wire)
                self.assertEqual(reply.returncode,2);self.assertEqual(reply.stdout,b'')
                self.assertTrue(executor.stopped);self.assertEqual(fixed.calls,1)
                self.assertIs(executor.children[Action.SIGNAL_MAIN],fixed.child)
                self.assertEqual(executor.execute_wire(wire).returncode,2);self.assertEqual(fixed.calls,1)
        finally:
            try:
                if fixed.child is not None:
                    # Cleanup only for this known fixed harmless child; no kill.
                    # Under SIG_IGN this status is unqualified and is not an exit0 claim.
                    fixed.child.wait(timeout=2)
                    fixed.child.stdout.close();fixed.child.stderr.close()
            finally:signal.signal(signal.SIGCHLD,prior)
        self.assertIs(signal.getsignal(signal.SIGCHLD),prior)


class FileIntakeTests(unittest.TestCase):
    def test_FIFO_intake_is_nonblocking_and_refused_before_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            fifo=Path(tmp)/'fixture-fifo';os.mkfifo(fifo,0o600);original=os.open;opened=[]
            def guarded(path,flags,*args,**kwargs):
                if Path(path)==fifo:
                    # Assertion stops old code before any potentially blocking syscall.
                    self.assertTrue(flags&os.O_NONBLOCK,'FIFO opening must be nonblocking')
                    self.assertTrue(flags&os.O_NOFOLLOW)
                    fd=original(path,flags,*args,**kwargs);opened.append(fd);return fd
                return original(path,flags,*args,**kwargs)
            with patch('backend_files.os.open',side_effect=guarded),patch('backend_files.os.read',side_effect=AssertionError('invalid type must not be read')):
                with self.assertRaises(BackendUnknown):regular_bytes(fifo,os.getuid(),0o600,1024)
            self.assertEqual(len(opened),1)
            with self.assertRaises(OSError):os.fstat(opened[0])


class PersistenceCompositionTests(unittest.TestCase):
    def test_visible_remote_complete_after_fsync_error_stays_fenced_by_Root(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);b,old=binding(base);executor,observer,process,completion=remote(b,old)
            clock=Clock();admission=Admission(clock);intent=Intent(base/'root'/'run',b.run_id,b.digest())
            channel=SSHChannel(executor);self.addCleanup(channel.close_fixture)
            launcher=FixedSSHLauncher(b,intent,PinVerifier(),admission,channel)
            engine=EffectRunner(intent,ChildTransport(clock.monotonic_ns,launcher),clock)
            complete=executor.run.directory/Action.SIGNAL_MAIN.value/'0003-remote-complete.json'
            original=os.fsync;seen=[]
            def fsync(fd):
                st=os.fstat(fd)
                if complete.exists() and stat.S_ISREG(st.st_mode):
                    selected=complete.stat()
                    if (st.st_dev,st.st_ino)==(selected.st_dev,selected.st_ino):
                        seen.append(1);raise OSError('fixture complete-file fsync failure')
                return original(fd)
            with patch('os.fsync',side_effect=fsync):
                result=engine.perform(Action.SIGNAL_MAIN,signal_deadline_ns=clock.ns+30*SECOND,prelaunch=admission.ready)
            self.assertTrue(seen);self.assertTrue(complete.exists())
            self.assertEqual(result['status'],'STOP_UNCERTAIN')
            self.assertEqual(engine.perform(Action.RELOAD_MANAGER)['status'],'STOP_UNCERTAIN')
            fresh=FixedSSHLauncher(b,intent,PinVerifier(),admission,channel)
            with self.assertRaises(ExchangeUnknown):fresh.launch(request(b,Action.RELOAD_MANAGER,2))
            self.assertEqual(len(channel.calls),1);self.assertEqual(len(process.calls),1)


class OpusSurvivorTests(unittest.TestCase):
    def test_pin_refusal_consumes_remote_action_before_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            b,old=binding(Path(tmp));executor,observer,process,completion=remote(b,old)
            selected=Path(b.remote_systemctl.path);original=selected.read_bytes();selected.write_bytes(b'changed fixture')
            wire=canonical(request(b,Action.SIGNAL_MAIN))+b'\n'
            self.assertEqual(executor.execute_wire(wire).returncode,2);self.assertEqual(process.calls,[])
            self.assertEqual((executor.run.directory/Action.SIGNAL_MAIN.value/'0002-remote-requested.json').read_bytes(),wire)
            selected.write_bytes(original)
            fresh=RemoteExecutor(b,RemoteRun(b),executor.verifier,executor.proof,process,completion)
            self.assertEqual(fresh.execute_wire(canonical(request(b,Action.SIGNAL_MAIN,99))+b'\n').returncode,2)
            self.assertEqual(process.calls,[])
    def test_valid_noncanonical_JSON_is_refused_before_reservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            b,old=binding(Path(tmp));executor,observer,process,completion=remote(b,old)
            wire=json.dumps(request(b,Action.SIGNAL_MAIN),indent=2).encode()+b'\n'
            self.assertLess(len(wire),4096)
            self.assertEqual(executor.execute_wire(wire).returncode,2);self.assertEqual(process.calls,[])
            self.assertEqual({p.name for p in executor.run.directory.iterdir()},{'0001-intent.json'})
    def test_backward_clock_cannot_extend_wait_to_positive_completion(self):
        child=PipeChild();self.addCleanup(child.close_fixture);calls=[]
        def backwards_then_stable():
            calls.append(1);return 100 if len(calls)==1 else 99
        # With the order check removed, stable99 permits real EOF+positive poll.
        # No exhausted iterator can accidentally satisfy this expected refusal.
        with self.assertRaises(BackendUnknown):BoundedCompletion(backwards_then_stable).collect(child,2*SECOND)
    def test_launcher_session_is_consumed_independent_of_effect_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);b,old=binding(base);executor,observer,process,completion=remote(b,old)
            clock=Clock();admission=Admission(clock);intent=Intent(base/'root'/'run',b.run_id,b.digest())
            wire=request(b,Action.SIGNAL_MAIN);intent.write('effect-requested',wire)
            channel=SSHChannel(executor);self.addCleanup(channel.close_fixture)
            launcher=FixedSSHLauncher(b,intent,PinVerifier(),admission,channel)
            first=launcher.launch(wire)
            with self.assertRaises(ExchangeUnknown):launcher.launch(wire)
            self.assertIs(launcher.children[(wire['run_id'],wire['effect_id'])],first)
            self.assertEqual(len(channel.calls),1);self.assertEqual(len(process.calls),1)
    def test_fixed_SSH_options_close_helper_trust_and_forwarding_surfaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            b,old=binding(Path(tmp));argv=ssh_argv(b)
            options=dict(argv[index+1].split('=',1) for index,arg in enumerate(argv[:-1]) if arg=='-o')
            expected={'ProxyCommand':'none','ProxyJump':'none','PermitLocalCommand':'no',
                'KnownHostsCommand':'none','GlobalKnownHostsFile':'/dev/null',
                'VerifyHostKeyDNS':'no','UpdateHostKeys':'no','ForwardAgent':'no',
                'ForwardX11':'no','ClearAllForwardings':'yes','RequestTTY':'no','EscapeChar':'none'}
            self.assertEqual({key:options.get(key) for key in expected},expected)


if __name__=='__main__':unittest.main(verbosity=2)
