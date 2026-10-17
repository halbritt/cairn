"""One fixed real ordinary join; not SDK/native/provider or isolation admission."""
import codecs
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import time

HERE=Path(__file__).resolve().parent
from trial_sdk_owned_reactor import OwnedEngine
from trial_sdk_ipc import Codec
from trial_sdk_input_receiver import InputReceiver
from trial_sdk_native_process_owner import NativeProcessOwner
from trial_sdk_peer import Peer

ENV={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'}
TARGET=HERE/'trial_sdk_node_owner_child.py'
TUPLE={'command':'/usr/bin/python3','args':['-I','-B',str(TARGET)],'env':{**ENV,'CAIRN_FIXTURE':'q"\\雪'*200},'cwd':str(HERE)}


class PeerSlot:
    def __init__(self):self.peer=None;self.attempted=False;self.closed=False
    def receive_into(self,view):return self.peer.receive_into(view)
    def send(self,view):return self.peer.send(view)
    def close(self):
        if self.attempted:raise ValueError('peer_close_repeated')
        self.attempted=True
        if self.peer is not None:self.peer.close()
        self.closed=True
    def fileno(self):
        if self.closed:return None
        # Public borrowed descriptor; transient tickets are discarded after any action.
        return self.peer.fileno()


class NativePorts:
    def __init__(self,owner):self.owner=owner;self.operation=0;self.released=False
    def write(self,body):return self.owner.write(body)
    def close(self):return self.owner.close_stdin()
    def read_into(self,name,view):return self.owner.read_into(name,view)
    def signal(self,wire_operation,name):
        if self.owner.observe_wait() is not None:return 'rejected'
        if name not in ('SIGTERM','SIGKILL'):raise ValueError('signal_name')
        self.operation+=1
        return 'accepted' if self.owner.signal(self.operation,int(getattr(signal,name))) else 'rejected'
    def request_stop(self,reason):
        if self.owner.observe_wait() is not None:return 'already_exited'
        self.operation+=1
        if self.owner.signal(self.operation,int(signal.SIGTERM)):return 'accepted'
        if self.owner.observe_wait() is not None:return 'already_exited'
        raise ValueError('stop_not_accepted')
    def wait_ready(self):
        value=self.owner.observe_wait()
        return None if value is None else value['returncode']
    def release(self):
        value=self.owner.release()
        if value.get('released') is not True or value.get('handles_detached') is not True or value.get('cleanup'):
            raise ValueError('native_release_unknown')
        self.released=True


class Streams:
    def __init__(self,owner):self.owner=owner
    def read_into(self,name,view):return self.owner.read_into(name,view)
    def close(self):
        snap=self.owner.snapshot()
        if not snap['stdout_eof'] or not snap['stderr_eof'] or snap['cleanup'] or snap['primary']:
            raise ValueError('stream_close_unknown')


class Launch:
    def __init__(self,owner):self.owner=owner;self.total=None;self.seen=0;self.parts=[];self.decoder=None;self.committed=False;self.fragments=0
    def begin(self,total,operation):
        if self.total is not None or not 0<total<=8500:raise ValueError('launch_begin')
        self.total=total;self.decoder=codecs.getincrementaldecoder('utf8')('strict')
    def collect(self,body):
        self.seen+=len(body);self.fragments+=1
        if self.total is None or self.seen>self.total:raise ValueError('launch_collect')
        self.parts.append(self.decoder.decode(body))
    def commit(self):
        if self.seen!=self.total or self.committed:raise ValueError('launch_commit')
        tail=self.decoder.decode(b'',final=True)
        value=json.loads(''.join(self.parts)+tail)
        if type(value) is not dict or value!=TUPLE:raise ValueError('launch_tuple')
        self.parts=None;self.decoder=None
        self.owner.start((value['command'],*value['args']),value['cwd'],dict(value['env']))
        self.committed=True
    def cancel(self):
        if self.committed:raise ValueError('launch_cancel_order')
        value=self.owner.cleanup_unlaunched()
        if value.get('released') is not True:raise ValueError('launch_cancel_unknown')
        self.parts=None;self.decoder=None
    def release(self):self.parts=None;self.decoder=None


class Validator:
    def validate(self,value,metadata):
        if type(value) is str:value=json.loads(value)
        if type(value) is not dict or value!={'type':'fixture','value':'雪'}:raise ValueError('fixture_input')
        return True


class Actor:
    def __init__(self):
        self.process=None;self.pidfd=None;self.wait=None;self.streams={};self.logs={'stdout':bytearray(),'stderr':bytearray()}
        self.released=False;self.stdin=None;self.cleanup=[];self.wait_unknown=False
    def start(self,command,passed):
        held=[]
        try:
            for name in ('stdin','stdout','stderr'):
                parent,child=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM|socket.SOCK_CLOEXEC)
                self.streams[name]=parent;held.append(child)
            self.process=subprocess.Popen(command,stdin=held[0],stdout=held[1],stderr=held[2],
                                          env=ENV,cwd=HERE,close_fds=True,pass_fds=tuple(passed))
            self.stdin=self.streams.pop('stdin')
            for stream in self.streams.values():stream.setblocking(False)
            self.pidfd=os.pidfd_open(self.process.pid)
        finally:
            # Detach each held endpoint before one close; do not mask an active error.
            close_failed=False
            while held:
                child=held.pop(0)
                try:child.close()
                except BaseException:
                    close_failed=True;self.cleanup.append('actor_child_close_unknown')
            if close_failed and sys.exc_info()[0] is None:raise ValueError('actor_child_close_unknown')
    def drain(self):
        for name,stream in list(self.streams.items()):
            while True:
                try:body=stream.recv(4096)
                except BlockingIOError:break
                if not body:
                    del self.streams[name]
                    try:stream.close()
                    except BaseException:
                        self.cleanup.append('actor_'+name+'_close_unknown');raise
                    break
                available=65536-sum(map(len,self.logs.values()))
                self.logs[name].extend(body[:available])
                if len(body)>available:raise ValueError('actor_output_bound')
    def prepared(self):
        self.drain()
        if b'\n' not in self.logs['stdout']:return False
        value=json.loads(self.logs['stdout'].split(b'\n',1)[0])
        if value!={'phase':'prepared','pid':self.process.pid,'claimed':1290}:raise ValueError('actor_prepared')
        return True
    def observe_wait(self):
        if self.wait_unknown:raise ValueError('actor_wait_unknown')
        if self.wait is None and self.process is not None:
            try:pid,status=os.waitpid(self.process.pid,os.WNOHANG)
            except BaseException:
                self.wait_unknown=True;raise
            if pid:
                if pid!=self.process.pid:
                    self.wait_unknown=True;raise ValueError('actor_wait_identity')
                self.process.returncode=os.waitstatus_to_exitcode(status)
                self.wait={'pid':pid,'raw_status':status,'returncode':self.process.returncode}
        return self.wait
    def wait_ready(self):
        self.drain()
        value=self.observe_wait()
        # The actual raw wait is stored independently; release also needs observed status EOF.
        return value['returncode'] if value is not None and not self.streams else None
    def release(self):
        if self.wait is None or self.streams or self.stdin is not None or self.released or self.cleanup:raise ValueError('actor_release_order')
        fd,self.pidfd=self.pidfd,None
        try:os.close(fd)
        except BaseException:
            self.cleanup.append('actor_pidfd_close_unknown');raise
        self.released=True
    def go(self):
        if self.stdin.send(b'G')!=1:raise ValueError('bootstrap_send')
        stream,self.stdin=self.stdin,None
        try:stream.close()
        except BaseException:
            self.cleanup.append('actor_stdin_close_unknown');raise


def cleanup_owned(native,native_ports,actor,data,control,endpoints,parent_pidfd,result,deadline):
    """Independent once-attempt cleanup; actual waits survive unrelated errors.

    Missing actor pidfd does not authorize numeric-PID signaling. Close bootstrap
    input, observe its actual owned child, then leave unresolved work to the outer
    fixed process-group owner. No cleanup fact changes the Engine's success.
    """
    errors=result['errors']
    def attempt(label,operation):
        try:return operation()
        except BaseException:
            errors.append(label);return None
    def observe(label,owner):
        try:return True,owner.observe_wait()
        except BaseException:
            errors.append(label);return False,None
    if actor.stdin is not None:
        stream,actor.stdin=actor.stdin,None
        attempt('actor_stdin_close_unknown',stream.close)
    # Partial acquisition can leave a parent stdin before Popen itself returns.
    stream=actor.streams.pop('stdin',None)
    if stream is not None:attempt('actor_partial_stdin_close_unknown',stream.close)
    native_snapshot=attempt('native_snapshot_unknown',native.snapshot)
    native_wait_usable,native_wait=observe('native_wait_unknown',native)
    if native_snapshot is not None and native_snapshot['pid'] is not None and native_wait is None:
        native_ports.operation+=1
        attempt('native_stop_unknown',lambda:native.signal(native_ports.operation,int(signal.SIGKILL)))
    actor_wait_usable,actor_wait=observe('actor_wait_unknown',actor)
    if actor.process is not None and actor_wait is None and actor.pidfd is not None:
        attempt('actor_stop_unknown',lambda:signal.pidfd_send_signal(actor.pidfd,signal.SIGKILL,None,0))
    # Record raw waits independently, even if the other owner cannot be queried.
    while time.monotonic()<deadline:
        if native_wait_usable and native_snapshot is not None and native_snapshot['pid'] is not None and native_wait is None:
            native_wait_usable,native_wait=observe('native_wait_unknown',native)
        if actor_wait_usable and actor.process is not None and actor_wait is None:
            actor_wait_usable,actor_wait=observe('actor_wait_unknown',actor)
        native_done=native_snapshot is not None and (native_snapshot['pid'] is None or native_wait is not None or not native_wait_usable)
        actor_done=actor.process is None or actor_wait is not None or not actor_wait_usable
        if native_done and actor_done:break
        time.sleep(.005)
    result['native_final_wait']=native_wait;result['actor_final_wait']=actor_wait
    result['unresolved_children']={
        'native':native_snapshot is None or (native_snapshot['pid'] is not None and native_wait is None),
        'actor':actor.process is not None and actor_wait is None}
    if native_wait is not None:result['native_cleanup']=attempt('native_cleanup_unknown',native.cleanup_after_wait)
    elif native_snapshot is not None and native_snapshot['pid'] is None:
        result['native_cleanup']=attempt('native_unlaunched_cleanup_unknown',native.cleanup_unlaunched)
    cleaned=result.get('native_cleanup')
    if cleaned is not None and (type(cleaned) is not dict or cleaned.get('released') is not True or cleaned.get('handles_detached') is not True or cleaned.get('cleanup')):
        errors.append('native_cleanup_unresolved')
    for name,slot in (('data',data),('control',control)):
        if not slot.attempted:attempt(name+'_close_unknown',slot.close)
    while endpoints:
        pair=endpoints.pop(0)
        for index,endpoint in enumerate(pair):
            if endpoint.fileno()!=-1:attempt('endpoint_'+str(index)+'_close_unknown',endpoint.close)
    for name in tuple(actor.streams):
        stream=actor.streams.pop(name)
        attempt('actor_'+name+'_close_unknown',stream.close)
    if actor.pidfd is not None:
        fd,actor.pidfd=actor.pidfd,None
        attempt('actor_pidfd_close_unknown',lambda:os.close(fd))
    if parent_pidfd is not None:
        attempt('parent_pidfd_close_unknown',lambda:os.close(parent_pidfd))
    result['native_final']=attempt('native_final_snapshot_unknown',native.snapshot)
    result['actor_cleanup']=list(actor.cleanup)
    if any(result['unresolved_children'].values()):errors.append('owned_child_wait_unresolved')


def main():
    if len(sys.argv)!=1:raise ValueError('fixed_arguments')
    command='KILL'
    expected_wait=-9
    expected_primary=None
    deadline=time.monotonic()+8
    native=NativeProcessOwner();native_ports=NativePorts(native);actor=Actor()
    data,control=PeerSlot(),PeerSlot()
    ports={'data':data,'control':control,'process':native_ports,'native':native_ports,
           'streams':Streams(native),'launch':Launch(native),'validator':Validator(),'actor':actor}
    engine=OwnedEngine(codec=Codec,receiver=InputReceiver,ports=ports,clock=time.monotonic,
                       work_deadline=deadline,stop_seconds=1)
    result={'ok':False,'scope':'ordinary real output pressure mechanism only','command':command,'errors':[],'states':[],'milestones':[]}
    endpoints=[];parent_pidfd=None
    try:
        Peer.protect()
        for _ in range(2):
            left,right=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET|socket.SOCK_CLOEXEC)
            endpoints.append((left,right))
            for endpoint in (left,right):endpoint.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
        parent_pidfd=os.pidfd_open(os.getpid())
        ns=int(deadline*1_000_000_000)
        actor.start(['/usr/bin/node','--disable-proto=throw',str(HERE/'trial_sdk_node_owner_actor.cjs'),
                     str(endpoints[0][1].fileno()),str(endpoints[1][1].fileno()),str(parent_pidfd),
                     str(os.getpid()),str(os.getuid()),str(os.getgid()),str(ns)],
                    [endpoints[0][1].fileno(),endpoints[1][1].fileno(),parent_pidfd])
        fd,parent_pidfd=parent_pidfd,None
        os.close(fd)
        for slot,(left,right) in zip((data,control),endpoints):
            right.close()
            held=engine.regions['data'].hold(512)
            try:slot.peer=Peer(left,actor.pidfd,actor.process.pid,os.getuid(),os.getgid(),ns,held.view,
                               engine.root.reserve_external,engine.root.release_external)
            finally:held.close()
            left.close()
        while not actor.prepared():
            if time.monotonic()>=deadline:raise TimeoutError('bootstrap_deadline')
            select.select(list(actor.streams.values()),[],[],.02)
        actor.go()
        while engine.state not in ('RELEASED','UNRESOLVED'):
            actor.drain()
            if not result['states'] or result['states'][-1]!=engine.state:
                result['states'].append(engine.state)
                result['milestones'].append(engine.snapshot())
            interests=engine.interests();poller=select.poll();by_fd={}
            for key,ticket in interests.items():
                fd=None;mask=select.POLLIN
                if key in ('data_rx','data_tx','control_rx','control_tx'):
                    fd=(data if key.startswith('data') else control).fileno()
                    if key.endswith('tx'):mask=select.POLLOUT
                elif key=='actor_wait':
                    if not actor.streams:fd=actor.pidfd
                elif key in ('stdin','stdout','stderr','wait'):
                    try:fd=native.fileno('pidfd' if key=='wait' else key)
                    except ValueError:continue
                    if key=='stdin':mask=select.POLLOUT
                if fd is not None:by_fd.setdefault(fd,[]).append((key,ticket,mask))
            for fd,rows in by_fd.items():poller.register(fd,sum(set(mask for _,_,mask in rows)))
            for stream in actor.streams.values():
                fd=stream.fileno()
                if fd not in by_fd:poller.register(fd,select.POLLIN)
            # Internal actions run first; poll only after quiescence, with held readiness tickets.
            action=engine.step()
            if action=='idle':
                for fd,flags in poller.poll(20):
                    for key,ticket,mask in by_fd.get(fd,[]):
                        if flags&(mask|select.POLLHUP|select.POLLERR):engine.notify(key,ticket)
            if time.monotonic()>deadline+1.1:raise TimeoutError('composition_deadline')
        actor.drain()
        result['milestones'].append(engine.snapshot())
        result.update(launch_fragments=ports['launch'].fragments,engine=engine.snapshot(),native=native.snapshot(),actor_wait=actor.observe_wait(),
                      actor_released=actor.released,actor_stderr_bytes=len(actor.logs['stderr']))
        rows=[json.loads(line) for line in actor.logs['stdout'].splitlines()]
        result['actor_result']=rows[-1] if rows else None
        result['ok']=(engine.state=='RELEASED' and engine.primary==expected_primary and native.snapshot()['wait']['returncode']==expected_wait
                      and actor.wait['returncode']==0 and actor.released and not actor.logs['stderr']
                      and len(rows)==2 and rows[-1].get('ok') is True and engine.root.used==0
                      and native.snapshot()['native_input']['known_written']==33
                      and ports['launch'].fragments>=2
                      and rows[-1]['owner']['nativeWritten']==33
                      and rows[-1]['owner']['custodied']==33
                      and rows[-1]['owner']['releaseAck'] is True
                      and any(s['state']=='WAIT_RELEASE' for s in result['milestones'])
                      and any(s['state']=='ENGINE_LOCAL_RELEASED' and s['local_reserved']==0 and s['counterparty_reserved']==22386 and s['backing_reserved']==22386 for s in result['milestones'])
                      and result['milestones'][-1]['state']=='RELEASED')
    except BaseException as error:
        result['errors'].append(type(error).__name__+':'+str(error)[:160])
        result['engine']=engine.snapshot()
    finally:
        # Detach the last raw parent handle before passing it to once-only cleanup.
        fd,parent_pidfd=parent_pidfd,None
        cleanup_owned(native,native_ports,actor,data,control,endpoints,fd,result,min(deadline+1.6,time.monotonic()+.5))
    result['ok']=result['ok'] and not result['errors'] and not actor.cleanup
    print(json.dumps(result,ensure_ascii=True),flush=True)
    return 0 if result['ok'] and not result['errors'] else 1


if __name__=='__main__':raise SystemExit(main())
