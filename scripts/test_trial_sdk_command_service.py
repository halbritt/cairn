"""Pure real service/job/Codec checks with an inert native-owner boundary."""
import hashlib
import json
import sys
import unittest
from unittest.mock import patch

import trial_sdk_command_service as module
import trial_sdk_memory_command_job as jobs
import trial_sdk_command_service_wire as w
from trial_sdk_command_policy import FixedCommandPolicy
from trial_sdk_ipc import Codec
from trial_sdk_parent_storage import Root, Channel
from test_trial_sdk_command_job import OwnerModel


class Owner(OwnerModel):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.reads=0;self.starts=[]
    def start(self,argv,cwd,env):
        self.starts.append((argv,cwd,dict(env)))
        super().start(argv,cwd,env)
    def read_into(self,name,view):
        self.reads+=1
        return super().read_into(name,view)
    def snapshot(self):
        value=super().snapshot()
        value.update(stdin_closed=self.closed,stdout_eof=self.eof['stdout'],
                     stderr_eof=self.eof['stderr'],wait_unknown=False,
                     signal_unknown=False,cleanup=())
        return value
    def signal(self,*args):self.early=True
    def cleanup_after_wait(self):
        self.released=True
        return {'released':self.closed and all(self.eof.values()),
                'handles_detached':True,
                'cleanup':() if all(self.eof.values()) else ('eof_unobserved',)}


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.clock=[1]
        self.operation=1
        self.budgets=module.ChildSublease()
        self.policy=FixedCommandPolicy('/fixed/cairn','/fixed/socket','/fixed/token',
                                       'repo','task','run')
        self.storage=memoryview(bytearray(512))
        self.service=module.CommandService(self.policy,'/fixed',{},self.budgets,
                                           40000,32*1024*1024,self.storage,
                                           clock=lambda:self.clock[0])
        self.root=Root(4096)
        self.body=self.root.allocate('body',512)
        self.channels={d:Channel(Codec,self.root,self.root.allocate(d,544),d,profile=4)
                       for d in ('P2S','S2P')}
        self.reports=[]
        self.flush()

    def send(self,kind,*fields,data=None):
        size=len(data) if data is not None else w.STRUCTS[kind].size
        hold=self.body.hold(size)
        try:
            if data is None:w.pack_into(kind,hold.view,*fields)
            else:hold.view[:]=data
            token=self.channels['P2S'].encode(kind,0 if kind==w.SHUTDOWN else self.operation,hold)
            try:token.visit(self.service.accept)
            finally:token.complete(token.generation,'known')
        finally:hold.close()

    def flush(self):
        for _ in range(8):
            pending=self.service.pending()
            if pending is None:return
            kind,operation,size=pending
            hold=self.body.hold(size)
            try:
                self.service.copy_reply(hold.view)
                self.reports.append((kind,bytes(hold.view)))
                token=self.channels['S2P'].encode(kind,operation,hold)
                try:token.visit(self.service.sent)
                finally:token.complete(token.generation,'known')
            finally:hold.close()
        self.fail('unbounded automatic reply chain')

    def prepare(self,owner,*,raw=65536,working=64*1024*1024,args=None,lease=10):
        if args is None:
            args=['--repo','repo','--task','task','--run','run','--tokens','1024','--','q']
        text=json.dumps({'operation':'search','args':args,'body':None},
                        separators=(',',':')).encode()
        self.send(w.PREPARE,1,10000,lease,raw,working,len(text),hashlib.sha256(text).digest())
        if self.service.pending() is not None:
            self.flush()
            return
        for offset in range(0,len(text),512):
            self.send(w.REQUEST,data=text[offset:offset+512])
        with patch.object(jobs,'NativeProcessOwner',return_value=owner):
            self.send(w.REQUEST_END)
        self.flush()

    def finish_job(self):
        for turn in range(1,20):
            self.send(w.TURN,20+turn,turn,1536,0);self.flush()
            if any(kind==w.OUTPUT for kind,_ in self.reports):return
        self.fail('model did not finish')

    def deliver(self):
        body=next(body for kind,body in self.reports if kind==w.OUTPUT)
        fields=w.STRUCTS[w.OUTPUT].unpack(body)
        grant=100
        for stream,size in enumerate(fields[2:4],1):
            while size:
                amount=min(size,512)
                self.send(w.DELIVER,grant,stream,amount);self.flush()
                size-=amount;grant+=1

    def test_real_job_fixed_route_progress_text_and_release(self):
        owner=Owner(b'{"status":"BUDGET_REFUSED"}',b'bad\xff',17)
        self.prepare(owner)
        self.assertFalse(owner.launched)
        self.assertEqual(self.reports[-1][0],w.PREPARED)
        self.send(w.START,10)
        self.assertEqual(owner.starts[0][0][:6],
                         ('/fixed/cairn','agent','--socket','/fixed/socket','--token-file','/fixed/token'))
        reads=owner.reads
        for _ in range(10):self.assertIsNone(self.service.pending())
        self.assertEqual(owner.reads,reads)  # No default job step.
        self.finish_job();self.deliver()
        status=w.STRUCTS[w.STATUS].unpack(self.reports[-1][1])
        self.assertEqual(status,(17<<8,17))
        texts={kind:b''.join(body for k,body in self.reports if k==kind)
               for kind in (w.STDOUT,w.STDERR)}
        self.assertEqual(texts[w.STDERR],b'bad\xef\xbf\xbd')
        self.assertGreater(self.budgets.snapshot()['working_reserved'],0)
        self.send(w.RELEASE,10);self.flush()
        self.assertEqual(w.STRUCTS[w.RELEASED].unpack(self.reports[-1][1]),(10,0))
        self.assertEqual(self.budgets.snapshot()['working_reserved'],0)
        self.assertTrue(owner.released)

    def test_preparation_sublease_refuses_before_job_launch(self):
        owner=Owner()
        self.prepare(owner,raw=10,working=100)
        self.assertFalse(owner.launched)
        self.assertEqual(self.reports[-1][0],w.REFUSE)
        self.assertEqual(self.budgets.snapshot()['raw_reserved'],0)

    def test_missing_grant_and_duplicate_start_do_not_repeat_job_effect(self):
        owner=Owner();self.prepare(owner);self.send(w.START,10)
        with self.assertRaises(w.ServiceWireError):self.send(w.START,10)
        self.assertEqual(len(owner.starts),1)
        self.assertEqual(owner.reads,0)

    def test_sublease_never_has_implicit_pool_or_refunds_unknown(self):
        budget=module.ChildSublease()
        with self.assertRaises(module.ServiceError):budget.reserve(1,1)
        budget.open(10,100,200)
        self.assertTrue(budget.reserve(50,100))
        self.assertFalse(budget.reserve(51,0))
        with self.assertRaises(module.ServiceError):budget.release(51,100)
        self.assertEqual(budget.snapshot()['raw_reserved'],50)

    def test_text_frame_remains_exact_until_known_send(self):
        owner=Owner(b'ok',b'');self.prepare(owner);self.send(w.START,10)
        self.finish_job()
        self.send(w.DELIVER,100,1,2)
        pending=self.service.pending()
        one=memoryview(bytearray(2));two=memoryview(bytearray(2))
        try:
            self.service.copy_reply(one);self.service.copy_reply(two)
            self.assertEqual(bytes(one),b'ok');self.assertEqual(bytes(two),b'ok')
            self.assertEqual(self.service.pending(),pending)
            self.assertFalse(any(k==w.STATUS for k,_ in self.reports))
        finally:one.release();two.release()
        self.flush()
        self.assertEqual(self.reports[-1][0],w.STATUS)

    def test_early_refusal_drains_declared_request_before_report(self):
        text=b'x'*1100
        self.send(w.PREPARE,1,10000,10,10,100,len(text),hashlib.sha256(text).digest())
        self.assertIsNone(self.service.pending())
        for offset in range(0,len(text),512):
            self.send(w.REQUEST,data=text[offset:offset+512])
            self.assertIsNone(self.service.pending())
        self.send(w.REQUEST_END);self.flush()
        self.assertEqual(w.STRUCTS[w.REFUSE].unpack(self.reports[-1][1]),(7,))
        self.assertIsNone(self.service.snapshot()['job'])
        self.send(w.RELEASE,10);self.flush()
        self.assertEqual(w.STRUCTS[w.RELEASED].unpack(self.reports[-1][1]),(10,0))

    def test_expired_preparation_drains_without_parsing_or_start(self):
        text=b'not-json'
        self.clock[0]=10000
        self.send(w.PREPARE,1,10000,10,65536,64*1024*1024,len(text),hashlib.sha256(text).digest())
        self.assertIsNone(self.service.pending())
        self.send(w.REQUEST,data=text);self.send(w.REQUEST_END);self.flush()
        self.assertEqual(w.STRUCTS[w.REFUSE].unpack(self.reports[-1][1]),(1,))
        self.assertEqual(self.budgets.snapshot()['working_reserved'],0)

    def test_cancel_preserves_pending_prepared_then_ack_and_unlaunched_cleanup(self):
        owner=Owner()
        # Hold the real PREPARED report instead of pretending its send completed.
        original=self.flush
        self.flush=lambda:None
        self.prepare(owner)
        self.flush=original
        self.assertEqual(self.service.pending()[0],w.PREPARED)
        self.send(w.CANCEL,1);self.flush()
        self.assertEqual([kind for kind,_ in self.reports[-2:]],[w.PREPARED,w.REFUSE])
        self.assertEqual(w.STRUCTS[w.REFUSE].unpack(self.reports[-1][1]),(6,))
        self.assertFalse(owner.launched)
        self.send(w.RELEASE,10)
        self.assertIsNone(self.service.pending())
        self.service.cleanup_step();self.flush()
        self.assertEqual(w.STRUCTS[w.RELEASED].unpack(self.reports[-1][1]),(10,0))

    def test_cancel_preserves_pending_text_without_status_then_ack(self):
        owner=Owner(b'ok',b'');self.prepare(owner);self.send(w.START,10)
        self.finish_job();self.send(w.DELIVER,100,1,2)
        self.send(w.CANCEL,1);self.flush()
        self.assertEqual([k for k,_ in self.reports[-2:]],[w.STDOUT,w.REFUSE])
        self.assertFalse(any(k==w.STATUS for k,_ in self.reports))
        self.send(w.RELEASE,10);self.flush()
        self.assertEqual(w.STRUCTS[w.RELEASED].unpack(self.reports[-1][1]),(10,0))

    def test_zero_final_grant_observes_eof_without_reading_payload(self):
        owner=Owner();self.prepare(owner);self.send(w.START,10)
        self.send(w.TURN,20,1,0,1);self.flush()
        progress=w.STRUCTS[w.PROGRESS].unpack(next(b for k,b in self.reports if k==w.PROGRESS))
        self.assertEqual(progress[2:5],(0,0,0))
        self.assertEqual(owner.reads,0)
        if not any(k==w.OUTPUT for k,_ in self.reports):
            self.send(w.TURN,21,2,0,1);self.flush()
        self.assertTrue(any(k==w.STATUS for k,_ in self.reports))

    def test_unknown_read_retains_turn_and_prefix_without_replay(self):
        class BrokenOwner(Owner):
            def read_into(self,name,view):
                if name=='stderr':raise OSError('boundary fault')
                return super().read_into(name,view)
        owner=BrokenOwner(b'prefix',b'');self.prepare(owner);self.send(w.START,10)
        with self.assertRaises(OSError):self.send(w.TURN,20,1,1536,0)
        state=self.service.snapshot()
        self.assertTrue(state['fatal'])
        self.assertEqual(state['job']['streams']['stdout']['raw_bytes'],6)
        self.assertEqual(state['budgets']['turn'],(20,1536,6))
        with self.assertRaises(module.ServiceError):self.send(w.TURN,21,2,1536,0)
        self.assertEqual(owner.reads,1)

    def test_split_utf8_delivery_has_exact_digest_and_original_raw_counts(self):
        owner=Owner(('💡'*130).encode(),b'\xff\xfe');self.prepare(owner);self.send(w.START,10)
        self.finish_job()
        fields=w.STRUCTS[w.OUTPUT].unpack(next(b for k,b in self.reports if k==w.OUTPUT))
        self.assertEqual(fields[:4],(520,2,520,6))
        grant=100
        for stream,size in enumerate(fields[2:4],1):
            while size:
                amount=min(size,3)
                self.send(w.DELIVER,grant,stream,amount);self.flush()
                size-=amount;grant+=1
        self.assertEqual(self.reports[-1][0],w.STATUS)
        output=b''.join(b for k,b in self.reports if k==w.STDOUT)
        self.assertEqual(output,owner.data['stdout'])
        self.assertEqual(hashlib.sha256(output).digest(),fields[7])

    def test_sublease_return_cleanup_interrupt_keeps_reservation_unknown(self):
        budget=module.ChildSublease();budget.open(10,100,200)
        fired=[]
        def trace(frame,event,arg):
            if (event=='line' and frame.f_code.co_name=='guarded'
                    and frame.f_locals.get('self') is budget
                    and frame.f_locals.get('answer') is True and not budget._busy):
                fired.append(True);raise KeyboardInterrupt('return cleanup')
            return trace
        try:
            sys.settrace(trace)
            with self.assertRaises(KeyboardInterrupt):budget.reserve(50,100)
        finally:sys.settrace(None)
        self.assertEqual(fired,[True])
        self.assertEqual(budget.snapshot()['raw_reserved'],50)
        self.assertTrue(budget.snapshot()['unknown'])
        with self.assertRaises(module.ServiceError):budget.release(50,100)

    def test_retirement_interrupt_is_not_swallowed_or_reported_known(self):
        owner=Owner();self.prepare(owner);self.send(w.START,10)
        self.finish_job()
        fired=[]
        def trace(frame,event,arg):
            if (event=='line' and frame.f_code.co_name=='release'
                    and frame.f_locals.get('self') is self.budgets):
                fired.append(True);raise KeyboardInterrupt('retire boundary')
            return trace
        try:
            sys.settrace(trace)
            with self.assertRaises(KeyboardInterrupt):self.send(w.RELEASE,10)
        finally:sys.settrace(None)
        self.assertEqual(fired,[True])
        state=self.service.snapshot()
        self.assertTrue(state['fatal'])
        self.assertTrue(state['preparation_unknown'])
        self.assertTrue(state['release_consumed'])
        self.assertIsNone(state['pending'])
        self.assertGreater(state['budgets']['working_reserved'],0)

    def test_cleanup_return_interrupt_poison_is_sticky(self):
        owner=Owner();self.prepare(owner);self.send(w.CANCEL,1);self.flush()
        fired=[]
        def trace(frame,event,arg):
            if (event=='line' and frame.f_code.co_name=='cleanup_step'
                    and frame.f_locals.get('self') is self.service
                    and self.service.snapshot()['job']['release'] is not None):
                fired.append(True);raise KeyboardInterrupt('cleanup reporting')
            return trace
        try:
            sys.settrace(trace)
            with self.assertRaises(KeyboardInterrupt):self.service.cleanup_step()
        finally:sys.settrace(None)
        self.assertEqual(fired,[True])
        self.assertTrue(self.service.snapshot()['fatal'])
        self.assertGreater(self.budgets.snapshot()['working_reserved'],0)

    def test_invalid_utf8_request_drains_remaining_frames_before_refusal(self):
        text=b'\xff'+b'x'*700
        self.send(w.PREPARE,1,10000,10,65536,64*1024*1024,len(text),hashlib.sha256(text).digest())
        self.send(w.REQUEST,data=text[:512])
        self.assertIsNone(self.service.pending())
        self.send(w.REQUEST,data=text[512:]);self.send(w.REQUEST_END);self.flush()
        self.assertEqual(w.STRUCTS[w.REFUSE].unpack(self.reports[-1][1]),(3,))
        self.assertIsNone(self.service.snapshot()['job'])

    def test_clock_failure_progress_is_unknown_and_cancel_ack_retains_turn(self):
        owner=Owner();self.prepare(owner);self.send(w.START,10)
        # The real job's clock validator turns this boundary failure into its
        # fixed unknown outcome; the service reports and retains that grant.
        self.clock[0]=False
        self.send(w.TURN,20,1,1536,0)
        self.send(w.CANCEL,1);self.flush()
        progress=w.STRUCTS[w.PROGRESS].unpack(next(b for k,b in self.reports if k==w.PROGRESS))
        self.assertEqual(progress[-1],1)
        self.assertEqual(w.STRUCTS[w.REFUSE].unpack(self.reports[-1][1]),(6,))
        self.assertEqual(self.budgets.snapshot()['turn'],(20,1536,0))
        self.assertTrue(self.budgets.snapshot()['unknown'])
        self.send(w.RELEASE,10)
        self.service.cleanup_step();self.service.cleanup_step();self.flush()
        self.assertEqual(w.STRUCTS[w.RELEASED].unpack(self.reports[-1][1]),(10,1))
        self.assertGreater(self.budgets.snapshot()['working_reserved'],0)

    def test_final_unused_allowance_can_be_regranted_without_revival(self):
        class WaitingOwner(Owner):
            waiting=True
            def read_into(self,name,view):
                if self.waiting:return -1
                return super().read_into(name,view)
        owner=WaitingOwner(b'abc',b'');self.prepare(owner);self.send(w.START,10)
        self.send(w.TURN,20,1,3,1);self.flush()
        self.assertEqual(self.service.snapshot()['transcript']['final_remaining'],3)
        owner.waiting=False
        self.send(w.TURN,21,2,3,1);self.flush()
        self.assertEqual(self.service.snapshot()['transcript']['final_remaining'],0)
        self.send(w.TURN,22,3,0,1);self.flush()
        self.assertTrue(any(k==w.OUTPUT for k,_ in self.reports))

    def test_expired_start_does_not_launch_or_reset_deadline(self):
        owner=Owner();self.prepare(owner)
        self.clock[0]=10000;self.send(w.START,10);self.flush()
        self.assertFalse(owner.launched)
        self.assertEqual(w.STRUCTS[w.REFUSE].unpack(self.reports[-1][1]),(1,))

    def test_stdin_close_unknown_never_sets_lifecycle_closed(self):
        class CloseFailure(Owner):
            def close_stdin(self):raise OSError('close boundary')
        owner=CloseFailure();self.prepare(owner);self.send(w.START,10)
        with self.assertRaises(OSError):self.send(w.TURN,20,1,1536,0)
        state=self.service.snapshot()
        self.assertEqual(state['job']['stdin_terminal'],'all_written')
        self.assertFalse(state['job']['owner']['stdin_closed'])
        self.assertEqual(module.CommandService._lifecycle(state['job'])&1,0)

    def test_successful_release_allows_fresh_serialized_operation_then_shutdown(self):
        first=Owner();self.prepare(first);self.send(w.START,10);self.finish_job()
        self.send(w.RELEASE,10);self.flush()
        self.operation=2
        second=Owner();self.prepare(second,lease=11);self.send(w.START,11)
        self.send(w.TURN,100,1,1536,0);self.flush()
        if self.reports[-1][0]!=w.STATUS:
            self.send(w.TURN,101,2,1536,0);self.flush()
        self.assertEqual(self.reports[-1][0],w.STATUS)
        self.send(w.RELEASE,11);self.flush();self.send(w.SHUTDOWN);self.flush()
        self.assertEqual(self.reports[-1][0],w.BYE)
        self.assertEqual(len(first.starts),1);self.assertEqual(len(second.starts),1)
        self.assertEqual(self.budgets.snapshot()['working_reserved'],0)


if __name__=='__main__':unittest.main()

