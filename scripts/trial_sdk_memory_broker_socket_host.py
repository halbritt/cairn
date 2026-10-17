"""Explicit ordinary owned-child/kernel fixture. No API/native/SDK operations."""
import json
import errno
import os
from pathlib import Path
import select
import signal
import tempfile
import time

from trial_sdk_memory_broker_socket import BrokerListener, BrokerSocketError
from trial_sdk_memory_broker_socket_child import REQUEST, REPLY
from trial_sdk_native_process_owner import NativeProcessOwner
from trial_sdk_peer import Peer
from trial_sdk_ipc import Codec


def fd_set():
    found=set()
    for value in os.listdir('/proc/self/fd'):
        try: os.fstat(int(value))
        except OSError as error:
            if error.errno!=errno.EBADF:raise
            continue
        found.add(int(value))
    return found


def finish(owner,deadline):
    logs={key:bytearray() for key in ('stdout','stderr')}
    owner.close_stdin()
    slab=bytearray(512);view=memoryview(slab)
    try:
        while time.monotonic()<deadline:
            state=owner.snapshot()
            for name in logs:
                if not state[name+'_eof']:
                    count=owner.read_into(name,view)
                    if count>0:
                        logs[name].extend(view[:count])
                        if sum(map(len,logs.values()))>65536: raise ValueError('host_log_bound')
            waited=owner.observe_wait()
            state=owner.snapshot()
            if waited is not None and state['stdout_eof'] and state['stderr_eof']:
                return dict(wait=waited,release=owner.release(),
                            child=json.loads(logs['stdout']),stderr_bytes=len(logs['stderr']))
            time.sleep(.001)
        raise TimeoutError('host_child_deadline')
    finally: view.release()


def kernel_case(mode,work_end):
    before=fd_set();listener=BrokerListener();owners=[]
    result={'mode':mode,'ok':False,'errors':[],'api_dispatches_observed':None,
            'runtime_qualified':False}
    used=peak=0
    def reserve(n):
        nonlocal used,peak
        if used+n>131072:return False
        used+=n;peak=max(peak,used);return True
    def release(n):
        nonlocal used
        used-=n
    end=min(work_end,time.monotonic()+3)
    try:
        Peer.protect()
        with tempfile.TemporaryDirectory(prefix='cairn-mem-') as directory:
            deadline=time.monotonic_ns()+min(2_500_000_000,int(max(0,end-time.monotonic())*1e9))
            listener.start(directory,deadline,reserve,release)
            path=str(Path(directory)/'memory.sock')
            here=Path(__file__).parent
            def launch(which):
                owner=NativeProcessOwner();owners.append(owner)
                owner.start(('/usr/bin/python3','-B',str(here/'trial_sdk_memory_broker_socket_child.py'),which,path),
                            str(here),{'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'})
                return owner
            intended=launch('hold' if mode=='wrong_peer' else mode)
            actual=launch('normal') if mode=='wrong_peer' else intended
            endpoint=None
            while time.monotonic()<end:
                try: endpoint=listener.accept(intended)
                except BrokerSocketError:
                    if mode!='wrong_peer':raise
                    result['connection_peer_refused']=True;break
                if endpoint is not None:break
                select.select([listener.fileno()],[],[],.005)
            else: raise TimeoutError('host_accept_deadline')
            if mode=='wrong_peer':
                assert result.get('connection_peer_refused') is True
                assert listener.connection.snapshot()['primary']=='broker_connection_peer'
            else:
                backing=bytearray(b'x'*544);view=memoryview(backing)
                held=fd_set()
                try:
                    while time.monotonic()<end:
                        try: count=endpoint.receive_into(view)
                        except BrokerSocketError:
                            if mode not in ('rights','fork_sender'): raise
                            result['record_refused']=True;break
                        if count>=0:break
                        select.select([endpoint.fileno()],[],[],.005)
                    else:raise TimeoutError('host_record_deadline')
                    if mode=='normal':
                        assert bytes(view[:count])==REQUEST
                        codec=Codec('C2B',lambda n:True,lambda n:None,profile=3)
                        frame=codec.decode(view[:count]);body=frame.body;record=frame.record
                        assert frame.kind==33 and frame.operation==1 and bytes(body)==b'{}'
                        body.release();record.release();frame.release()
                        reply=memoryview(REPLY)
                        try:
                            while endpoint.send(reply)<0:
                                select.select([],[endpoint.fileno()],[],.005)
                        finally:reply.release()
                        result['request_exact']=True
                    else:
                        assert result.get('record_refused') is True
                        assert backing==bytes(544)
                        assert fd_set()==held
                        result['refusal_erased_and_no_fd_growth']=True
                finally:view.release()
            listener.close()
            result['listener']=listener.snapshot()
            result['owners']=[finish(owner,end+.3) for owner in owners]
            assert all(row['wait']['returncode']==0 and row['release']['released']
                       and row['stderr_bytes']==0 for row in result['owners'])
            for row in result['owners']:
                child=row['child']
                if 'endpoint' in child:
                    assert child['endpoint']['closed_known']
                    assert not child['endpoint']['cleanup']
                    assert child['auxiliary_used']==0
            assert listener.connection.snapshot()['closed_known']
            if mode=='normal':assert result['owners'][0]['child']['reply_matches']
            assert not listener.snapshot()['cleanup'] and not listener.snapshot()['has_handles']
            assert used==0
        result['ok']=True
    except BaseException as error:
        result['errors'].append(type(error).__name__+':'+str(error)[:80])
    finally:
        try:
            listener.close()
        except BaseException as error:
            result['errors'].append('listener_cleanup:'+type(error).__name__)
        finally:
            for owner in owners:
                try:
                    if owner.observe_wait() is None:owner.signal(1,int(signal.SIGKILL))
                    while owner.observe_wait() is None and time.monotonic()<end+.5:time.sleep(.001)
                    result.setdefault('cleanup',[]).append({'wait':owner.observe_wait(),
                                                           'release':owner.cleanup_after_wait()})
                except BaseException as error:result['errors'].append('cleanup:'+type(error).__name__)
        result['final_listener']=listener.snapshot()
        if listener.snapshot()['cleanup'] or listener.snapshot()['has_handles']:
            result['errors'].append('listener_cleanup_unknown')
    result['fd_restored']=fd_set()==before
    result['auxiliary_used']=used;result['auxiliary_peak']=peak
    result['ok']=result['ok'] and not result['errors'] and result['fd_restored'] and used==0
    return result


def main():
    end=time.monotonic()+8
    cases=[kernel_case(mode,end) for mode in ('normal','wrong_peer','rights','fork_sender')]
    print(json.dumps({'cases':cases,'scope':'ordinary credentialed sockets and owned children only; no API/native/SDK/provider/evaluation'}))
    return 0 if all(case['ok'] for case in cases) else 1


if __name__=='__main__':raise SystemExit(main())
