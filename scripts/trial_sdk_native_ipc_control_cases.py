"""Actual fixed control-capacity host cases; no opaque SDK or native CLI."""
import array
import os
from pathlib import Path
import socket
import struct
import subprocess
import time
import test_trial_sdk_native_ipc as old
HERE=Path(__file__).resolve().parent
CASES=('exchange33','exchange32','exchange544','receive33','bounds','exchange-over33',
       'receive-over33','rights33','wrong-sender33','timeout33')
ENV={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'}


def case(mode,binary,suite_deadline=None):
    deadline=time.monotonic()+1.5
    if suite_deadline is not None:deadline=min(deadline,suite_deadline)
    parent,node=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
    for end in (parent,node):end.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
    parent.settimeout(.5)
    pidfd=os.pidfd_open(os.getpid());child=output=sender=None
    result={'case':mode,'ok':False}
    try:
        child=subprocess.Popen(['/usr/bin/node','--disable-proto=throw',str(HERE/'test_trial_sdk_native_ipc_control.cjs'),
            str(binary),mode,str(node.fileno()),str(pidfd),str(os.getpid()),str(os.getuid()),str(os.getgid())],
            cwd=HERE,env=ENV,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            pass_fds=(node.fileno(),pidfd),start_new_session=True)
        node.close();output=old.Output(child,deadline)
        if output.message()!={'phase':'ready','mode':mode,'pid':child.pid}:raise ValueError('node_not_ready')
        if mode in ('receive33','receive-over33'):parent.send(b'R'*(34 if mode=='receive-over33' else 33))
        elif mode=='rights33':
            fd=os.open('/dev/null',os.O_RDONLY|os.O_CLOEXEC)
            try:parent.sendmsg([b'R'*33],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[fd]))])
            finally:os.close(fd)
        elif mode=='wrong-sender33':
            # Exact fixed sender marker33; its actual PID must fail parent-PID binding.
            sender=subprocess.Popen(['/usr/bin/python3','-I','-B','-c',
                'import socket,sys;s=socket.socket(fileno=int(sys.argv[1]));s.send(b"R"*33);s.close()',str(parent.fileno())],
                env=ENV,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,pass_fds=(parent.fileno(),),start_new_session=True)
            result['sender_wait']=old.finish(sender,deadline)
            if result['sender_wait']['actual_wait']!=0 or result['sender_wait']['killed']:raise ValueError('sender_wait')
            sender=None
        child.stdin.write(b'x');child.stdin.close()
        if mode.startswith('exchange') or mode=='timeout33':
            wire,control,flags,_=parent.recvmsg(545,socket.CMSG_SPACE(struct.calcsize('3i')))
            if wire!=b'Q'*32 or flags:raise ValueError('request_wire')
            expected=(child.pid,os.getuid(),os.getgid())
            if [(level,kind,struct.unpack('3i',body)) for level,kind,body in control]!=[(socket.SOL_SOCKET,socket.SCM_CREDENTIALS,expected)]:raise ValueError('request_credentials')
            result['authenticated_request_bytes']=32
            if mode!='timeout33':
                length=32 if mode=='exchange32' else 544 if mode=='exchange544' else 34 if mode=='exchange-over33' else 33
                if parent.send(b'R'*length)!=length:raise ValueError('short_reply')
        observed=output.message();result['observed']=observed
        if observed.get('phase')!='result' or observed.get('ok') is not True or observed.get('mode')!=mode:raise ValueError('node_case_failed')
        if mode=='bounds':
            parent.setblocking(False)
            try:parent.recv(1)
            except BlockingIOError:result['pre_effect_refusal']=True
            else:raise ValueError('invalid_operation_sent')
        output.drain()
        if output.pending or output.logs['stderr']:raise ValueError('unexpected_output')
        result.update(old.finish(child,deadline))
        if result['actual_wait']!=0 or result['killed']:raise ValueError('node_wait')
        result['ok']=True
    except BaseException as error:
        result['failure']=str(error)[:80] if isinstance(error,(ValueError,TimeoutError)) else type(error).__name__
    finally:
        if child and 'actual_wait' not in result:result.update(old.finish(child,deadline))
        if output:
            try:output.drain()
            except BaseException as error:result['drain_failure']=type(error).__name__
            result['logs']={name:old.digest(body) for name,body in output.logs.items()}
            if not result['ok']:
                result['stdout_selected']=bytes(output.logs['stdout']).decode('utf-8',errors='replace')[:4096]
                result['stderr_selected']=bytes(output.logs['stderr']).decode('utf-8',errors='replace')[:4096]
            output.close()
        if child and child.stdin and not child.stdin.closed:child.stdin.close()
        if sender:result['sender_wait']=old.finish(sender,deadline)
        os.close(pidfd);parent.close();node.close()
    return result
