"""Fresh ordinary owned-child contrasts; not original-budget/native admission.

Run only through the selected bounded outer supervisor after a retained intent
and immutable source snapshot. The budget port here is a diagnostic ledger,
not the actual campaign's shared-owner binding or physical-memory measurement.
"""
import json
from pathlib import Path
import selectors
import sys
import time

from trial_sdk_memory_command_job import CommandJob,CommandJobError,STREAM_LIMIT


class Budget:
    def __init__(self):self.queue=self.working=self.stream=0
    def reserve(self,queue,working):
        if self.queue+queue>128*1024 or self.working+working>512*1024*1024:return False
        self.queue+=queue;self.working+=working
        return True
    def release(self,queue,working):self.queue-=queue;self.working-=working
    def stream_room(self):return STREAM_LIMIT-self.stream
    def charge_stream(self,count):
        self.stream+=count
        if self.stream>STREAM_LIMIT:raise ValueError('fixture_stream_overflow')


def require(condition,code):
    if not condition:raise ValueError(code)


def ready(job,deadline,*,stdout_only=False):
    with selectors.DefaultSelector() as selector:
        for name,fd in job.interests().items():
            if stdout_only and name!='stdout':continue
            selector.register(fd,selectors.EVENT_WRITE if name=='stdin' else selectors.EVENT_READ)
        remaining=(deadline-time.monotonic_ns())/1e9
        require(remaining>0,'fixture_readiness_deadline')
        require(selector.select(min(remaining,0.1)) or time.monotonic_ns()<deadline,
                'fixture_readiness_deadline')


def run(mode):
    budget=Budget()
    deadline=time.monotonic_ns()+5_000_000_000
    job=CommandJob(budget,deadline)
    completed=False
    primary=None
    cleanup_errors=[]
    try:
        body='body' if mode=='reject' else 'B'*(2*1024*1024)
        job.prepare(body)
        del body  # Transfer the sole fixture body alias to the prepared job.
        child=Path(__file__).with_name('trial_sdk_memory_command_job_host_child.py')
        job.start((sys.executable,'-B',str(child),mode),str(child.parent),{})
        if mode=='reject':
            with selectors.DefaultSelector() as selector:
                selector.register(job.interests()['stdout'],selectors.EVENT_READ)
                require(bool(selector.select(1)),'fixture_rejection_startup_timeout')
        failure=None
        while True:
            try:job.step()
            except CommandJobError as error:
                failure=str(error)
                break
            state=job.snapshot()
            if state['release'] is not None:break
            ready(job,deadline)
        if mode=='overflow':
            require(failure=='command_stream_exhausted','fixture_overflow_missing')
            require(budget.stream==STREAM_LIMIT,'fixture_overflow_count')
            require(not job.snapshot()['outcome_unknown'],'fixture_overflow_outcome_unknown')
        else:
            require(failure is None,'fixture_unexpected_failure')
            outcome=job.take_outcome()
            require(outcome.returncode==17,'fixture_actual_returncode')
            if mode=='reject':
                require(outcome.stdout=='{"status":"BUDGET_REFUSED"}' and
                        outcome.stderr=='err\ufffd\ufffd','fixture_rejection_text')
                require(job.snapshot()['stdin_terminal']=='known_epipe','fixture_epipe_unobserved')
                require(job.snapshot()['input_written']==0,'fixture_rejection_prefix')
            else:
                require(outcome.stdout=='a'*(1024*1024) and outcome.stderr=='\ufffd'*(1024*1024),
                        'fixture_exact_output')
                require(budget.stream==STREAM_LIMIT,'fixture_exact_count')
                require(job.snapshot()['input_written']==2*1024*1024,'fixture_exact_input')
            del outcome
        # Actual wait and release remain mandatory even after overflow refusal.
        while job.owner.observe_wait() is None:
            require(time.monotonic_ns()<deadline,'fixture_cleanup_wait_deadline')
            # Readiness-only wait; cleanup_step would signal a still-live child.
            # The actual owner retains the wait before job cleanup collects it.
            ready(job,deadline)
        job.cleanup_step()
        state=job.snapshot()
        require(state['wait']['returncode']==17,'fixture_wait_code')
        require(not state['owner']['has_handles'],'fixture_owner_handles')
        if mode=='overflow':
            require(state['release']['released'] is False,'fixture_overflow_release')
            require(state['streams']['stderr']['eof'] is False,'fixture_overflow_eof')
            require('stderr_eof_unobserved' in state['release']['cleanup'],
                    'fixture_overflow_eof_diagnostic')
            require(all(row['raw_bytes']==1024*1024 for row in state['streams'].values()),
                    'fixture_overflow_stream_counts')
        else:
            job.retire()
            require((budget.queue,budget.working)==(0,0),'fixture_credit_return')
        completed=True
    except BaseException as error:
        primary={'type':type(error).__name__,'code':str(error)[:128]}
    finally:
        if not completed:
            try:job.abort()
            except BaseException as error:cleanup_errors.append('abort:'+type(error).__name__)
            # Independently guarded best effort; the outer owner retains final
            # containment and actual waits even when a partial launch failed.
            try:job.cleanup_step()
            except BaseException as error:cleanup_errors.append('cleanup:'+type(error).__name__)
    result={'case':mode,'status':'PASS' if completed else 'FAIL','failure':primary,
            'cleanup_errors':cleanup_errors,'traffic':budget.stream,
            'qualification':'ordinary_fixture_only'}
    try:
        state=job.snapshot()
        result.update(wait=state['wait'],release=state['release'],primary=state['primary'],
                      outcome_unknown=state['outcome_unknown'],stdin_terminal=state['stdin_terminal'],
                      input_written=state['input_written'],streams=state['streams'],
                      owner_wait=state['owner']['wait'],owner_pid=state['owner']['pid'],
                      input_rejected=state['owner']['input_rejected'],
                      has_handles=state['owner']['has_handles'])
    except BaseException as error:
        result['status']='FAIL'
        cleanup_errors.append('snapshot:'+type(error).__name__)
    return result


def main():
    observations=[]
    for mode in ('exact','reject','overflow'):
        row=run(mode)
        observations.append(row)
        # Emit each completed case, preserving earlier actual facts on later
        # failure or deadline. The observer retains every line, even partial runs.
        print(json.dumps({'schema':'cairn.command-job-host/1','cases':observations},sort_keys=True),flush=True)
        if row['status']!='PASS':return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
