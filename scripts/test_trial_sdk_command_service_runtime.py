"""Real transport/Codec/core paths with inert OS and socket boundaries only."""
import os
import hashlib
import json
import select
import socket
import sys
import unittest
from unittest.mock import patch

import trial_sdk_command_service_runtime as runtime
import trial_sdk_memory_broker_socket as transport
import trial_sdk_peer as peer
import trial_sdk_memory_command_job as jobs
from test_trial_sdk_command_service import Owner
from trial_sdk_command_policy import FixedCommandPolicy
from trial_sdk_ipc import Codec
from trial_sdk_parent_storage import Root, Channel
import trial_sdk_command_service_wire as wire


class Endpoint:
    def __init__(self):
        self.incoming=[];self.sent=[];self.again=False;self.connected=False;self.peer_closed=False
    def fileno(self):return 808
    def getsockopt(self,level,option,*args):
        return {socket.SO_TYPE:socket.SOCK_SEQPACKET,socket.SO_DOMAIN:socket.AF_UNIX,
                socket.SO_PASSCRED:1,socket.SO_PEERCRED:peer.CREDENTIAL.pack(123,os.getuid(),os.getgid())}[option]
    def setsockopt(self,*args):pass
    def settimeout(self,*args):pass
    def setblocking(self,*args):pass
    def connect(self,path):self.connected=True
    def detach(self):return 808
    def send(self,view,flags):
        self.sent.append(bytes(view))
        if self.again:self.again=False;raise BlockingIOError()
        return len(view)
    def recvmsg_into(self,views,control,flags):
        if not self.incoming and self.peer_closed:return 0,[],socket.MSG_CMSG_CLOEXEC,None
        if not self.incoming:raise BlockingIOError()
        value=self.incoming.pop(0)
        views[0][:len(value)]=value
        return len(value),[(socket.SOL_SOCKET,socket.SCM_CREDENTIALS,
                            peer.CREDENTIAL.pack(123,os.getuid(),os.getgid()))],0,None


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.endpoint=Endpoint();self.closed=[];self.now=1;self.polls=[]
        self.ready_pids=set()
        fixture=self
        class Poll:
            def __init__(self):self.registered={}
            def register(self,fd,events):self.registered[fd]=events
            def poll(self,timeout):
                if set(self.registered)=={909}:return []
                fixture.polls.append((dict(self.registered),timeout))
                events=[]
                if 808 in self.registered:
                    mask=self.registered[808]
                    ready=(select.POLLOUT if mask&select.POLLOUT else 0)
                    if fixture.endpoint.incoming and mask&select.POLLIN:ready|=select.POLLIN
                    if fixture.endpoint.peer_closed:ready|=select.POLLHUP|select.POLLIN
                    if ready:events.append((808,ready))
                events.extend((fd,select.POLLIN) for fd in fixture.ready_pids if fd in self.registered)
                return events
        patches=[patch.object(transport.os,'getppid',return_value=123),
                 patch.object(transport.os,'pidfd_open',return_value=909),
                 patch.object(transport.os,'get_inheritable',return_value=False),
                 patch.object(transport.os,'close',side_effect=self.closed.append),
                 patch.object(transport.socket,'socket',return_value=self.endpoint),
                 patch.object(transport,'_prctl',return_value=0),
                 patch.object(peer,'_prctl',return_value=0),
                 patch.object(transport.select,'poll',Poll),
                 patch.object(transport.time,'monotonic_ns',side_effect=lambda:self.now)]
        for item in patches:item.start();self.addCleanup(item.stop)
        self.binding=runtime.RuntimeBinding(
            FixedCommandPolicy('/fixed/cairn','/fixed/api','/fixed/token','repo','task','run'),
            '/fixed',{},'/fixed/record',10_000_000_000,
            runtime.RAW_STORAGE,1024*1024,40000,32*1024*1024)
        self.root=Root(1056)
        self.body=self.root.allocate('body',512)
        self.channel=Channel(Codec,self.root,self.root.allocate('record',544),'P2S',profile=4)

    def frame(self,kind,operation=0,*fields,data=None):
        size=len(data) if data is not None else wire.STRUCTS[kind].size
        hold=self.body.hold(size)
        try:
            if data is None:wire.pack_into(kind,hold.view,*fields)
            else:hold.view[:]=data
            token=self.channel.encode(kind,operation,hold)
            def collect(frame):
                with frame.record as record:return bytes(record)
            value=token.visit(collect)
            token.complete(token.generation,'known')
            return value
        finally:hold.close()

    def test_constructor_is_inert_and_start_claims_fixed_standing_before_connect(self):
        owner=runtime.CommandServiceRuntime(self.binding)
        self.assertFalse(self.endpoint.connected)
        self.assertFalse(owner.snapshot()['standing_claimed'])
        owner.start()
        self.assertTrue(self.endpoint.connected)
        self.assertTrue(owner.snapshot()['standing_claimed'])
        self.assertEqual(owner.snapshot()['backing_bytes'],2112)
        self.assertEqual(owner.snapshot()['sent_bytes'],0)
        owner.close()

    def test_eagain_keeps_identical_ready_and_no_replayed_core_transition(self):
        owner=runtime.CommandServiceRuntime(self.binding);owner.start()
        self.endpoint.again=True
        owner.step(0)
        self.assertEqual(owner.snapshot()['service']['transcript']['phase'],'boot')
        self.assertEqual(owner.snapshot()['sent_bytes'],0)
        owner.step(0)
        self.assertEqual(self.endpoint.sent[0],self.endpoint.sent[1])
        self.assertEqual(owner.snapshot()['service']['transcript']['phase'],'idle')
        self.assertEqual(owner.snapshot()['sent_bytes'],32)
        owner.close()

    def test_missing_parent_frame_parks_without_inventing_job_or_turn(self):
        owner=runtime.CommandServiceRuntime(self.binding);owner.start();owner.step(0)
        before=owner.snapshot()['received_bytes']
        for _ in range(3):owner.step(0)
        self.assertEqual(owner.snapshot()['received_bytes'],before)
        self.assertIsNone(owner.snapshot()['service']['job'])
        self.assertEqual(self.polls[-1][0],{808:select.POLLIN})
        owner.close()

    def test_shutdown_keeps_service_alive_after_known_bye_send(self):
        owner=runtime.CommandServiceRuntime(self.binding);owner.start();owner.step(0)
        self.endpoint.incoming.append(self.frame(wire.SHUTDOWN))
        owner.step(0);owner.step(0)
        state=owner.snapshot()
        self.assertTrue(state['bye_sent'])
        self.assertFalse(state['done'])
        self.assertFalse(state['closed'])
        owner.close()

    def test_positive_send_before_peer_deadline_failure_is_retained_without_replay(self):
        owner=runtime.CommandServiceRuntime(self.binding);owner.start()
        original=self.endpoint.send
        def send(view,flags):
            count=original(view,flags)
            self.now=self.binding.deadline_ns
            return count
        self.endpoint.send=send
        with self.assertRaises(ValueError):owner.step(0)
        state=owner.snapshot()
        self.assertEqual(state['sent_bytes'],32)
        self.assertTrue(state['send_pending'])
        self.assertTrue(state['unknown'])
        self.assertEqual(state['service']['transcript']['phase'],'boot')
        with self.assertRaises(ValueError):owner.step(0)
        self.assertEqual(len(self.endpoint.sent),1)
        owner.close()

    def test_bad_credential_receive_keeps_bytes_but_never_reaches_service(self):
        owner=runtime.CommandServiceRuntime(self.binding);owner.start();owner.step(0)
        self.endpoint.incoming.append(self.frame(wire.SHUTDOWN))
        original=self.endpoint.recvmsg_into
        def receive(*args):
            count,_,flags,address=original(*args)
            return count,[(socket.SOL_SOCKET,socket.SCM_CREDENTIALS,
                           peer.CREDENTIAL.pack(124,os.getuid(),os.getgid()))],flags,address
        self.endpoint.recvmsg_into=receive
        with self.assertRaises(ValueError):owner.step(0)
        state=owner.snapshot()
        self.assertEqual(state['received_bytes'],32)
        self.assertTrue(state['unknown'])
        self.assertEqual(state['service']['transcript']['phase'],'idle')
        owner.close()

    def test_premature_disconnect_cannot_be_normal_completion(self):
        owner=runtime.CommandServiceRuntime(self.binding);owner.start();owner.step(0)
        self.endpoint.incoming.append(b'')
        with self.assertRaises(ValueError):owner.step(0)
        self.assertFalse(owner.snapshot()['done'])
        self.assertTrue(owner.snapshot()['unknown'])
        owner.close()

    def test_expired_poll_does_not_send_ready_or_reset_deadline(self):
        owner=runtime.CommandServiceRuntime(self.binding);owner.start()
        self.now=self.binding.deadline_ns
        with self.assertRaises(ValueError):owner.step(0)
        self.assertEqual(self.endpoint.sent,[])
        self.assertFalse(owner.snapshot()['done'])
        owner.close()

    def test_short_send_is_unknown_and_never_calls_sent(self):
        owner=runtime.CommandServiceRuntime(self.binding);owner.start()
        self.endpoint.send=lambda view,flags:16
        with self.assertRaises(ValueError):owner.step(0)
        state=owner.snapshot()
        self.assertEqual(state['sent_bytes'],16)
        self.assertEqual(state['service']['transcript']['phase'],'boot')
        self.assertTrue(state['unknown'])
        owner.close()

    def pump_reply(self,owner):
        for _ in range(8):
            state=owner.snapshot()
            if not state['send_pending'] and state['service']['pending'] is None:return
            owner.step(0)
        self.fail('reply chain exceeded its fixed bound')

    def request(self,owner,job_owner,operation=1,lease=10):
        text=json.dumps({'operation':'search','args':['--repo','repo','--task','task',
                        '--run','run','--tokens','1024','--','q'],'body':None},
                        separators=(',',':')).encode()
        self.endpoint.incoming.append(self.frame(wire.PREPARE,operation,1,5000,lease,65536,
                                               64*1024*1024,len(text),hashlib.sha256(text).digest()))
        owner.step(0)
        self.endpoint.incoming.append(self.frame(wire.REQUEST,operation,data=text));owner.step(0)
        self.endpoint.incoming.append(self.frame(wire.REQUEST_END,operation))
        with patch.object(jobs,'NativeProcessOwner',return_value=job_owner):owner.step(0)
        self.pump_reply(owner)

    def test_cancelled_live_job_collects_modeled_wait_and_preserves_missing_eof(self):
        fixture=self
        class JobOwner(Owner):
            signals=0
            def signal(self,*args):
                self.signals+=1;super().signal(*args);fixture.ready_pids.add(707)
            def fileno(self,name):
                fixture.assertEqual(name,'pidfd')
                return 707
        owner=runtime.CommandServiceRuntime(self.binding);owner.start();owner.step(0)
        job_owner=JobOwner();self.request(owner,job_owner)
        self.endpoint.incoming.append(self.frame(wire.START,1,10));owner.step(0)
        self.assertEqual(job_owner.reads,0)
        for _ in range(2):owner.step(0)
        self.assertEqual(job_owner.reads,0)  # Missing TURN does not poll job IO.
        self.endpoint.incoming.append(self.frame(wire.CANCEL,1,1));owner.step(0)
        self.pump_reply(owner);owner.step(0)
        state=owner.snapshot()['service']['job']
        self.assertIsNotNone(state['wait'])
        self.assertFalse(state['release']['released'])
        self.assertEqual(job_owner.signals,1)
        self.assertFalse(state['streams']['stdout']['eof'])
        self.endpoint.incoming.append(self.frame(wire.RELEASE,1,10));owner.step(0)
        self.pump_reply(owner)
        self.assertTrue(owner.snapshot()['service']['transcript']['release_unknown'])
        owner.close()

    def test_unlaunched_cancel_releases_without_native_start_or_default_turn(self):
        owner=runtime.CommandServiceRuntime(self.binding);owner.start();owner.step(0)
        job_owner=Owner();self.request(owner,job_owner)
        self.endpoint.incoming.append(self.frame(wire.CANCEL,1,1));owner.step(0)
        self.pump_reply(owner)
        self.endpoint.incoming.append(self.frame(wire.RELEASE,1,10));owner.step(0)
        self.pump_reply(owner)
        state=owner.snapshot()['service']
        self.assertFalse(job_owner.launched)
        self.assertTrue(state['job']['release']['released'])
        self.assertEqual(state['budgets']['working_reserved'],0)
        owner.close()

    def test_cancelled_attempt_cannot_start_another_operation_after_release(self):
        owner=runtime.CommandServiceRuntime(self.binding);owner.start();owner.step(0)
        job_owner=Owner();self.request(owner,job_owner)
        self.endpoint.incoming.append(self.frame(wire.CANCEL,1,1));owner.step(0)
        self.pump_reply(owner)
        self.endpoint.incoming.append(self.frame(wire.RELEASE,1,10));owner.step(0)
        self.pump_reply(owner)
        state=owner.snapshot()['service']
        self.assertFalse(job_owner.launched)
        self.assertEqual(state['transcript']['phase'],'idle')
        self.assertTrue(state['job']['release']['released'])
        self.assertIsNone(state['budgets']['lease'])
        other=Owner()
        with self.assertRaisesRegex(wire.ServiceWireError,'^service_attempt_failed$'):
            self.request(owner,other,2,11)
        self.assertFalse(other.launched)
        self.assertTrue(owner.snapshot()['unknown'])
        owner.close()

    def test_counter_copy_interrupt_retains_socket_prefix_without_send_replay(self):
        owner=runtime.CommandServiceRuntime(self.binding);owner.start()
        hit=[]
        def trace(frame,event,arg):
            if (event=='line' and frame.f_code.co_name=='_capture_transfer'
                    and frame.f_locals.get('self') is owner
                    and frame.f_locals.get('count')==32
                    and frame.f_locals.get('direction')=='send' and not hit):
                hit.append(True);raise KeyboardInterrupt('counter copy')
            return trace
        old=sys.gettrace()
        try:
            sys.settrace(trace)
            with self.assertRaises(KeyboardInterrupt):owner.step(0)
        finally:sys.settrace(old)
        self.assertEqual(hit,[True])
        self.assertEqual(owner.snapshot()['sent_bytes'],32)
        self.assertTrue(owner.snapshot()['unknown'])
        self.assertEqual(owner.snapshot()['service']['transcript']['phase'],'boot')
        with self.assertRaises(ValueError):owner.step(0)
        self.assertEqual(len(self.endpoint.sent),1)
        owner.close()

    def test_only_actual_post_bye_eof_finishes_and_retires_local_standing(self):
        owner=runtime.CommandServiceRuntime(self.binding);owner.start();owner.step(0)
        self.endpoint.incoming.append(self.frame(wire.SHUTDOWN));owner.step(0);owner.step(0)
        self.assertTrue(owner.snapshot()['bye_sent'])
        self.endpoint.peer_closed=True
        owner.step(0)
        self.assertTrue(owner.snapshot()['done'])
        self.assertTrue(owner.snapshot()['socket']['terminal_eof'])
        owner.close()
        state=owner.snapshot()
        self.assertTrue(state['standing_released'])
        self.assertEqual(state['backing_bytes'],0)
        self.assertTrue(state['socket']['closed_known'])
        self.assertEqual(sorted(self.closed),[808,909])

    def test_post_bye_zero_credentialed_packet_cannot_finish(self):
        owner=runtime.CommandServiceRuntime(self.binding);owner.start();owner.step(0)
        self.endpoint.incoming.append(self.frame(wire.SHUTDOWN));owner.step(0);owner.step(0)
        self.endpoint.incoming.append(b'');self.endpoint.peer_closed=True
        with self.assertRaises(ValueError):owner.step(0)
        self.assertFalse(owner.snapshot()['done'])
        self.assertTrue(owner.snapshot()['unknown'])
        owner.close()

    def paced_job(self):
        class WaitingOwner(Owner):
            def read_into(self,name,view):self.reads+=1;return -1
            def fileno(self,name):return {'stdout':711,'stderr':712,'pidfd':707,'stdin':713}[name]
        owner=runtime.CommandServiceRuntime(self.binding);owner.start();owner.step(0)
        job_owner=WaitingOwner();self.request(owner,job_owner)
        self.endpoint.incoming.append(self.frame(wire.START,1,10));owner.step(0);self.pump_reply(owner)
        # Real stdin-close and subsequently observed wait surface immediately.
        for ordinal in (1,2):
            self.endpoint.incoming.append(self.frame(wire.TURN,1,20+ordinal,ordinal,1536,0))
            owner.step(0);self.pump_reply(owner)
        self.assertEqual(owner.snapshot()['service']['transcript']['lifecycle'],9)
        self.endpoint.incoming.append(self.frame(wire.TURN,1,23,3,1536,0));owner.step(0)
        return owner,job_owner

    def test_zero_progress_parks_on_actual_cli_interest_without_restep(self):
        owner,job_owner=self.paced_job()
        count=len(self.endpoint.sent);reads=job_owner.reads
        owner.step(0);owner.step(0)
        self.assertEqual(len(self.endpoint.sent),count)
        self.assertTrue(owner.snapshot()['paced'])
        self.assertNotIn(713,self.polls[-1][0])  # stdin closed already.
        self.assertNotIn(707,self.polls[-1][0])  # CLI wait already observed.
        self.assertEqual(self.polls[-1][0][711],select.POLLIN)
        self.assertEqual(self.polls[-1][0][808],select.POLLIN)
        self.ready_pids.add(711);owner.step(0);owner.step(0)
        self.assertEqual(len(self.endpoint.sent),count+1)
        self.assertEqual(job_owner.reads,reads)
        owner.close()

    def test_cancel_unparks_exact_zero_report_then_ack_without_restep(self):
        owner,job_owner=self.paced_job();owner.step(0)
        reads=job_owner.reads
        self.endpoint.incoming.append(self.frame(wire.CANCEL,1,1));owner.step(0)
        self.pump_reply(owner)
        self.assertEqual(job_owner.reads,reads)
        state=owner.snapshot()['service']['transcript']
        self.assertTrue(state['cancel_acknowledged'])
        self.assertIsNone(state['turn'])
        owner.close()

    def test_paced_report_uses_original_operation_deadline_not_service_lifetime(self):
        owner,job_owner=self.paced_job();owner.step(0)
        count=len(self.endpoint.sent);self.now=5000
        with self.assertRaises(ValueError):owner.step(0)
        self.assertEqual(len(self.endpoint.sent),count)
        self.assertTrue(owner.snapshot()['unknown'])
        owner.close()

    def test_serve_uses_real_core_loop_and_returns_only_after_local_known_close(self):
        shutdown=self.frame(wire.SHUTDOWN)
        original=self.endpoint.send
        def send(view,flags):
            count=original(view,flags)
            if len(self.endpoint.sent)==1:self.endpoint.incoming.append(shutdown)
            elif len(self.endpoint.sent)==2:self.endpoint.peer_closed=True
            return count
        self.endpoint.send=send
        result=runtime.serve(self.binding)
        self.assertTrue(result['done']);self.assertTrue(result['closed'])
        self.assertTrue(result['standing_released'])
        self.assertEqual((result['sent_bytes'],result['received_bytes']),(64,32))
        self.assertTrue(result['socket']['closed_known'])

    def test_final_diagnostics_do_not_alias_retained_service_state(self):
        owner=runtime.CommandServiceRuntime(self.binding);owner.start();owner.step(0)
        self.endpoint.incoming.append(self.frame(wire.SHUTDOWN));owner.step(0);owner.step(0)
        self.endpoint.peer_closed=True;owner.step(0);owner.close()
        first=owner.snapshot();first['service']['transcript']['phase']='counterfeit'
        self.assertEqual(owner.snapshot()['service']['transcript']['phase'],'closed')

    def test_close_interrupt_keeps_unknown_without_descriptor_retry(self):
        owner=runtime.CommandServiceRuntime(self.binding);owner.start();owner.step(0)
        self.endpoint.incoming.append(self.frame(wire.SHUTDOWN));owner.step(0);owner.step(0)
        self.endpoint.peer_closed=True;owner.step(0)
        hit=[]
        def trace(frame,event,arg):
            if (event=='line' and frame.f_code is runtime.CommandServiceRuntime.close.__code__
                    and self.closed and not hit):
                hit.append(True);raise KeyboardInterrupt('local close')
            return trace
        old=sys.gettrace()
        try:
            sys.settrace(trace)
            with self.assertRaises(KeyboardInterrupt):owner.close()
        finally:sys.settrace(old)
        self.assertEqual(hit,[True])
        self.assertTrue(owner.snapshot()['unknown'])
        self.assertFalse(owner.snapshot()['standing_released'])
        closed=list(self.closed);owner.close();self.assertEqual(self.closed,closed)

    def test_known_zero_byte_refusal_bypasses_pacing_and_sends_refuse(self):
        class WaitingOwner(Owner):
            def read_into(self,name,view):self.reads+=1;return -1
            def fileno(self,name):return {'stdout':711,'stderr':712,'pidfd':707,'stdin':713}[name]
        owner=runtime.CommandServiceRuntime(self.binding);owner.start();owner.step(0)
        job_owner=WaitingOwner(b'x',b'');self.request(owner,job_owner)
        self.endpoint.incoming.append(self.frame(wire.START,1,10));owner.step(0);self.pump_reply(owner)
        for ordinal in (1,2):
            self.endpoint.incoming.append(self.frame(wire.TURN,1,20+ordinal,ordinal,1536,0))
            owner.step(0);self.pump_reply(owner)
        # Actual real job zero-room probe reports queued data and raises the
        # known command_stream_exhausted refusal, with no new byte or lifecycle.
        self.endpoint.incoming.append(self.frame(wire.TURN,1,23,3,0,1));owner.step(0)
        owner.step(0)
        self.assertFalse(owner.snapshot()['paced'])
        self.pump_reply(owner)
        state=owner.snapshot()['service']
        self.assertEqual(state['job']['primary'],'command_stream_exhausted')
        self.assertEqual(state['transcript']['phase'],'refused')
        owner.close()


if __name__=='__main__':unittest.main()
