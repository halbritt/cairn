"""Fixed ordinary-host readiness checks. No SDK/native CLI/provider/namespace effects."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import test_trial_sdk_native_ipc as old

HERE=Path(__file__).resolve().parent
CASES=('readable','writable','cancel-rearm','close-armed','timeout','deadline',
       'capacity','dual','peer-exit','gc-retention','teardown','aggregate')
ENV={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'}


def case(mode,binary,suite_deadline=None):
    deadline=time.monotonic()+1.8
    if suite_deadline is not None:deadline=min(deadline,suite_deadline)
    parent,node=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
    for end in (parent,node):end.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
    parent.setblocking(False)
    extra_parent=extra_node=holder=child=output=None
    pidfd=None
    result={'case':mode,'ok':False}
    try:
        if mode=='peer-exit':
            holder=subprocess.Popen(['/usr/bin/python3','-I','-B','-c','import os;os.read(0,1)'],
                stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=ENV,
                start_new_session=True)
        peer_pid=holder.pid if holder else os.getpid()
        pidfd=os.pidfd_open(peer_pid)
        passed=[node.fileno(),pidfd]
        extra='-1'
        if mode=='dual':
            extra_parent,extra_node=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
            for end in (extra_parent,extra_node):end.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
            extra_parent.settimeout(.5)
            passed.append(extra_node.fileno());extra=str(extra_node.fileno())
        script='test_trial_sdk_native_ipc_aggregate.cjs' if mode=='aggregate' else ('test_trial_sdk_native_ipc_teardown.cjs' if mode=='teardown' else 'test_trial_sdk_native_ipc_readiness.cjs')
        child=subprocess.Popen(['/usr/bin/node','--expose-gc','--disable-proto=throw',str(HERE/script),
            str(binary),mode,str(node.fileno()),str(pidfd),str(peer_pid),str(os.getuid()),str(os.getgid()),extra],
            cwd=HERE,env=ENV,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            pass_fds=tuple(passed),start_new_session=True)
        node.close()
        if extra_node:extra_node.close()
        output=old.Output(child,deadline)
        first=output.message()
        if mode in ('readable','writable','cancel-rearm','dual','peer-exit','gc-retention'):
            if first.get('phase')!='armed':raise ValueError('not_armed')
            result['armed']=first
            tick=output.message()
            if tick!={'phase':'tick'}:raise ValueError('event_loop_did_not_progress')
            result['js_event_loop_progress_before_peer_io']=True
            if mode=='writable':
                count=0
                while True:
                    try:wire=parent.recv(545)
                    except BlockingIOError:break
                    if wire!=b'D'*544:raise ValueError('fill_mismatch')
                    count+=1
                    if count>1024:raise ValueError('fill_count')
                if count!=first['count']:raise ValueError('fill_count_mismatch')
                result['actual_fill_records_drained']=count
                if output.message()!={'phase':'written'}:raise ValueError('not_written')
                if parent.recv(545)!=b'Q'*32:raise ValueError('write_after_ready')
            else:
                if mode=='dual' and extra_parent.recv(545)!=b'Q'*32:raise ValueError('independent_peer_write')
                if holder:
                    holder.stdin.close()
                    result['helper_wait']=old.finish(holder,deadline)
                    if result['helper_wait']['actual_wait']!=0:raise ValueError('helper_wait')
                    holder=None
                if parent.send(b'b'*32)!=32:raise ValueError('short_selected_record')
            observed=output.message()
        else:observed=first
        if observed.get('phase')!='result' or observed.get('mode')!=mode or observed.get('ok') is not True:
            result['observed']=observed
            raise ValueError('node_case_failed')
        result['observed']=observed
        output.drain()
        if output.pending or output.logs['stderr']:raise ValueError('unexpected_output')
        result.update(old.finish(child,deadline))
        if result['actual_wait']!=0 or result['killed']:raise ValueError('owned_wait_failed')
        result['ok']=True
    except BaseException as error:
        result['failure']=str(error)[:80] if isinstance(error,(ValueError,TimeoutError)) else type(error).__name__
    finally:
        if child and 'actual_wait' not in result:
            result.update(old.finish(child,deadline))
        if output:
            try:output.drain()
            except BaseException as error:result['drain_failure']=type(error).__name__
            result['logs']={name:old.digest(body) for name,body in output.logs.items()}
            if not result['ok']:
                result['stdout_selected']=bytes(output.logs['stdout']).decode('utf-8',errors='replace')[:4096]
                result['stderr_selected']=bytes(output.logs['stderr']).decode('utf-8',errors='replace')[:4096]
            output.close()
        if holder:
            holder.stdin.close();result['helper_wait']=old.finish(holder,deadline)
        if pidfd is not None:os.close(pidfd)
        for end in (node,parent,extra_node,extra_parent):
            if end:end.close()
    return result
