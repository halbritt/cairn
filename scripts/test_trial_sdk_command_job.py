"""Deterministic job I/O models, not OS waits or shared-runtime admission."""
import hashlib
import unittest
from unittest.mock import patch

import trial_sdk_memory_command_job as module
from trial_sdk_native_input import NativeInputRejected


class Budget:
    def __init__(self,queue_limit=128*1024):
        self.queue_limit=queue_limit;self.queue=self.working=self.stream=0
    def reserve(self,queue,working):
        if self.queue+queue>self.queue_limit or self.working+working>512*1024*1024:return False
        self.queue+=queue;self.working+=working
        return True
    def release(self,queue,working):
        self.queue-=queue;self.working-=working
    def stream_room(self):return module.STREAM_LIMIT-self.stream
    def charge_stream(self,count):self.stream+=count


class OwnerModel:
    def __init__(self,stdout=b'',stderr=b'',code=0,*,early=False,epipe=False,width=512):
        self.data=dict(stdout=stdout,stderr=stderr)
        self.offset=dict(stdout=0,stderr=0);self.eof=dict(stdout=False,stderr=False)
        self.code,self.early,self.epipe,self.width=code,early,epipe,width
        self.launched=self.closed=self.rejected=self.released=False
        self.input=bytearray();self.writes=0
    def start(self,argv,cwd,env):self.launched=True
    def write(self,view):
        self.writes+=1
        if self.epipe and self.writes>1:
            self.rejected=True
            raise NativeInputRejected('native_input_rejected')
        count=min(len(view),2 if self.epipe else self.width)
        self.input.extend(view[:count])
        return count
    def close_stdin(self):self.closed=True
    def read_into(self,name,view):
        offset=self.offset[name]
        data=self.data[name][offset:offset+min(len(view),self.width)]
        view[:len(data)]=data;self.offset[name]+=len(data)
        if not data:self.eof[name]=True
        return len(data)
    def observe_wait(self):
        if self.launched and (self.early or self.closed):
            return {'pid':123,'raw_status':self.code<<8,'returncode':self.code}
        return None
    def release(self):
        assert self.closed and all(self.eof.values()) and self.observe_wait() is not None
        self.released=True
        return {'released':True,'handles_detached':True,'cleanup':()}
    def cleanup_unlaunched(self):
        assert not self.launched
        return {'released':True,'handles_detached':True,'cleanup':()}
    def snapshot(self):
        return {'launch_attempted':self.launched,
                'native_input':{'write_unknown':False,'input_rejected':self.rejected,
                                'known_written':len(self.input)}}


class JobTests(unittest.TestCase):
    def job(self,owner,body=None,budget=None,clock=None):
        budget=Budget() if budget is None else budget
        clock=(lambda:1) if clock is None else clock
        with patch.object(module,'NativeProcessOwner',return_value=owner):
            value=module.CommandJob(budget,10_000,clock=clock)
        value.prepare(body)
        value.start(('/unavailable/fixed-command',),'/unavailable',{})
        return value,budget

    def finish(self,job):
        for _ in range(50_000):
            job.step()
            if job.snapshot()['release'] is not None:return job.take_outcome()
        self.fail('model failed to finish within bounded steps')

    def test_exact_input_and_concurrent_outputs_preserve_nonzero_wait(self):
        owner=OwnerModel(b'{"status":"BUDGET_REFUSED"}',b'err\xff\xf0\x9f\x92\xa1',17,width=1)
        job,budget=self.job(owner,'\u00e9\U0001f4a1')
        outcome=self.finish(job)
        self.assertEqual(bytes(owner.input),'\u00e9\U0001f4a1'.encode())
        self.assertEqual((outcome.returncode,outcome.stdout,outcome.stderr),
                         (17,'{"status":"BUDGET_REFUSED"}','err\ufffd\U0001f4a1'))
        self.assertEqual((outcome.stdout_bytes,outcome.stderr_bytes),
                         (len(owner.data['stdout']),len(owner.data['stderr'])))
        self.assertEqual(budget.stream,len(owner.input)+outcome.stdout_bytes+outcome.stderr_bytes)
        self.assertTrue(owner.released)
        self.assertGreater(budget.working,0)  # Delivery does not refund live text.
        del outcome
        job.retire()
        self.assertEqual((budget.queue,budget.working),(0,0))

    def test_early_wait_does_not_discard_unread_tails_or_invent_input(self):
        owner=OwnerModel(b'x'*2049,b'e'*1025,23,early=True,width=7)
        job,budget=self.job(owner,'unread body')
        outcome=self.finish(job)
        self.assertEqual(owner.input,bytearray())
        self.assertEqual((outcome.returncode,outcome.stdout,outcome.stderr),(23,'x'*2049,'e'*1025))
        self.assertEqual(job.snapshot()['stdin_terminal'],'peer_ended')

    def test_known_epipe_keeps_prefix_and_actual_nonzero_outcome(self):
        owner=OwnerModel(b'{"status":"BUDGET_REFUSED"}',b'',17,epipe=True)
        job,budget=self.job(owner,'abcdef')
        outcome=self.finish(job)
        self.assertEqual(bytes(owner.input),b'ab')
        self.assertEqual(job.snapshot()['input_written'],2)
        self.assertEqual(job.snapshot()['stdin_terminal'],'known_epipe')
        self.assertEqual(outcome.returncode,17)
        self.assertEqual(owner.writes,2)

    def test_full_engine_stream_limits_with_bounded_raw_reservation(self):
        owner=OwnerModel(b'a'*module.OUTPUT_LIMIT,b'\xff'*module.OUTPUT_LIMIT)
        job,budget=self.job(owner)
        outcome=self.finish(job)
        self.assertEqual(outcome.stdout,'a'*module.OUTPUT_LIMIT)
        self.assertEqual(outcome.stderr,'\ufffd'*module.OUTPUT_LIMIT)
        self.assertEqual(job.snapshot()['queue_reservation'],module.RAW_RESERVATION)
        self.assertLess(budget.queue,128*1024)
        self.assertEqual(job.snapshot()['streams']['stdout']['raw_sha256'],
                         hashlib.sha256(owner.data['stdout']).hexdigest())

    def test_malformed_stdout_is_deferred_until_wait_and_stream_completion(self):
        owner=OwnerModel(b'\xf4\x90\x80\x80',b'tail',3,width=1)
        job,budget=self.job(owner)
        with self.assertRaisesRegex(UnicodeError,'^command_stdout_utf8$'):
            self.finish(job)
        self.assertTrue(owner.released)
        self.assertEqual(job.snapshot()['wait']['returncode'],3)
        self.assertTrue(all(row['eof'] for row in job.snapshot()['streams'].values()))

    def test_output_limit_precedes_latched_utf8_error(self):
        owner=OwnerModel(b'\xff'+b'a'*module.OUTPUT_LIMIT,b'')
        job,budget=self.job(owner)
        with self.assertRaisesRegex(module.CommandJobError,'^command_output_limit$'):
            self.finish(job)
        self.assertEqual(job.snapshot()['streams']['stdout']['raw_bytes'],module.OUTPUT_LIMIT+1)
        with self.assertRaisesRegex(module.CommandJobError,'^command_output_limit$'):job.step()

    def test_late_positive_write_keeps_actual_progress_and_rejects_result(self):
        now=[1]
        owner=OwnerModel()
        original=owner.write
        def late(view):
            count=original(view);now[0]=10_000;return count
        owner.write=late
        job,budget=self.job(owner,'body',clock=lambda:now[0])
        with self.assertRaisesRegex(module.CommandJobError,'^command_deadline$'):job.step()
        state=job.snapshot()
        self.assertEqual(state['input_written'],4)
        self.assertEqual(state['input_sha256'],hashlib.sha256(b'body').hexdigest())
        self.assertEqual(budget.stream,4)
        with self.assertRaisesRegex(module.CommandJobError,'^command_deadline$'):job.take_outcome()

    def test_shared_queue_reservation_refuses_before_launch_without_extra_pool(self):
        budget=Budget(module.RAW_RESERVATION)
        first=OwnerModel();second=OwnerModel()
        with patch.object(module,'NativeProcessOwner',side_effect=[first,second]):
            one=module.CommandJob(budget,10_000,clock=lambda:1)
            two=module.CommandJob(budget,10_000,clock=lambda:1)
        one.prepare(None)
        with self.assertRaisesRegex(module.CommandJobError,'^command_credit_refused$'):two.prepare(None)
        self.assertFalse(first.launched or second.launched)
        self.assertEqual(budget.queue,module.RAW_RESERVATION)
        one.cleanup_step();one.retire()
        self.assertEqual((budget.queue,budget.working),(0,0))


if __name__=='__main__':unittest.main()
