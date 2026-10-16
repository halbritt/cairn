"""Pure controlled port testimony. No socket, process, SDK or signal effects."""
import unittest
import json
from trial_sdk_ipc import Codec
from trial_sdk_input_receiver import InputReceiver
from trial_sdk_parent_storage import Root,Channel
from trial_sdk_parent_reactor import Engine,ReactorError

class WireFixture:
    def __init__(self,direction):
        self.root=Root(2048)
        self.dest=self.root.allocate('record',544)
        self.body=self.root.allocate('body',512)
        self.codec=Channel(Codec,self.root,self.dest,direction)
    def encode(self,kind,op,body=b''):
        h=self.body.hold(len(body));h.view[:]=body
        t=self.codec.encode(kind,op,h);h.close()
        # Explicit simulated wire copy, outside candidate root (test fixture).
        result=t.visit(lambda f:bytes(f.record));t.complete(t.generation,'known')
        return result

class ControlledPeer:
    def __init__(self,direction):
        self.queue=[];self.sent=[];self.writable=True;self.send_outcome=None
        self.send_calls=0;self.close_calls=0
        self.root=Root(544);self.region=self.root.allocate('test_receive',544)
        self.decoder=Channel(Codec,self.root,self.region,direction)
    def receive_into(self,view):
        if not self.queue:return -1
        b=self.queue.pop(0);view[:len(b)]=b;return len(b)
    def send(self,view):
        self.send_calls+=1
        if self.send_outcome is not None:return self.send_outcome
        if not self.writable:return -1
        h=self.region.hold(len(view));h.view[:]=view
        t=self.decoder.decode(h)
        self.sent.append(t.visit(lambda f:(f.kind,f.operation,bytes(f.body))))
        t.complete(t.generation,'known')
        assert len(self.sent)<=2048
        return len(view)
    def close(self):self.close_calls+=1;assert self.close_calls==1

class ControlledLaunch:
    def __init__(self):self.data=bytearray();self.commits=0;self.cancels=0;self.releases=0;self.refuse=False
    def begin(self,total,op):assert total==2 and op==1
    def collect(self,body):self.data.extend(body);assert len(self.data)<=2
    def commit(self):
        if self.refuse or self.data!=b'{}':raise ReactorError('fixture_launch_refused')
        self.commits+=1
    def cancel(self):self.cancels+=1
    def release(self):self.releases+=1;assert self.releases==1

class ControlledProcess:
    def __init__(self):self.available=False;self.code=0;self.waits=0;self.stops=0;self.signals=0;self.releases=0;self.stop_result='accepted'
    def request_stop(self,reason):self.stops+=1;return self.stop_result
    def signal(self,operation,name):self.signals+=1;return 'accepted'
    def wait_ready(self):self.waits+=1;self.available=False;return self.code
    def release(self):self.releases+=1;assert self.releases==1

class ControlledNative:
    def __init__(self):self.ready=True;self.blocked=False;self.max_write=512;self.written=bytearray();self.calls=0;self.closes=0
    def write(self,body):
        self.calls+=1
        if self.blocked:self.ready=False;return 0
        n=min(self.max_write,len(body));self.written.extend(body[:n]);return n
    def close(self):self.closes+=1;assert self.closes==1

class ControlledStreams:
    def __init__(self):self.queue={'stdout':[],'stderr':[]};self.closes=0;self.calls=[];self.widths=[]
    def read_into(self,which,view):
        self.calls.append(which);self.widths.append((which,len(view)))
        if not self.queue[which]:return -1
        b=self.queue[which][0]
        if b is None:self.queue[which].pop(0);return 0
        if b == -1:self.queue[which].pop(0);return -1
        n=min(len(b),len(view));view[:n]=b[:n]
        if n==len(b):self.queue[which].pop(0)
        else:self.queue[which][0]=b[n:]
        return n
    def close(self):self.closes+=1;assert self.closes==1

class FixtureValidator:
    def validate(self,value,first_byte_ns):
        if isinstance(value,str):value=json.loads(value)
        if not isinstance(value,dict) or value.get('type')!='fixture':raise ReactorError('fixture_document_invalid')
        return True

class Rig:
    def __init__(self):
        self.now=1
        self.ports={'data':ControlledPeer('P2N_DATA'),'control':ControlledPeer('P2N_CONTROL'),
                    'launch':ControlledLaunch(),'process':ControlledProcess(),'native':ControlledNative(),
                    'streams':ControlledStreams(),'validator':FixtureValidator()}
        self.e=Engine(codec=Codec,receiver=InputReceiver,ports=self.ports,clock=lambda:self.now,work_deadline=100,stop_seconds=2)
        self.wire=WireFixture('N2P_DATA');self.control=WireFixture('N2P_CONTROL')
    def data(self,kind,op,body=b''):self.ports['data'].queue.append(self.wire.encode(kind,op,body))
    def credit(self,n=124):self.data(6,0,n.to_bytes(2,'big'));self.pump()
    def ctrl(self,command,op=1):self.ports['control'].queue.append(self.control.encode(201,op,bytes([command])))
    def tick(self):
        for key,ticket in list(self.e.interests().items()):
            ready=False
            if key in ('data_rx','control_rx'):ready=bool(self.ports[key.split('_')[0]].queue)
            elif key in ('data_tx','control_tx'):ready=self.ports[key.split('_')[0]].writable
            elif key=='stdin':ready=self.ports['native'].ready
            elif key in ('stdout','stderr'):ready=bool(self.ports['streams'].queue[key])
            elif key=='wait':ready=self.ports['process'].available
            if ready:self.e.notify(key,ticket)
        return self.e.step()
    def pump(self,limit=5000):
        for _ in range(limit):
            action=self.tick()
            if action in ('idle','terminal'):return
        raise AssertionError('model failed to quiesce '+str(self.e.snapshot()))
    def launch(self):
        self.data(1,1,b'\0\0\0\2\0\0\0\2');self.data(2,1,b'{}');self.data(3,1)
        self.pump()
        assert self.e.state=='RUNNING',self.e.snapshot()
    def input(self,body=b'{"type":"fixture"}\n',op=2):self.data(9,op,body);self.pump()
    def end(self,op=3):self.data(5,op);self.pump()
    def finish(self,wait_first):
        if wait_first:
            self.ports['process'].available=True;self.pump()
            assert self.e.state!='RELEASED'
        if self.e.stdout_credit==0:self.credit(124)
        self.ports['streams'].queue={'stdout':[None],'stderr':[None]};self.pump()
        if not wait_first:
            assert self.e.state!='RELEASED'
            self.ports['process'].available=True;self.pump()
        assert self.e.state=='RELEASED',self.e.snapshot()
        assert self.e.root.used==0 and self.e.root.leased==0
        assert self.ports['native'].closes==1
        assert all(self.ports[k].close_calls==1 for k in ('data','control'))
        assert self.ports['process'].releases==1


def test_normal_actual_composition_both_eof_wait_orders():
    peaks=[]
    for first in (True,False):
        r=Rig();r.launch();r.input();r.end();r.finish(first)
        peaks.append((r.e.root.peak,r.e.root.external_peak))
        assert bytes(r.ports['native'].written)==b'{"type":"fixture"}\n',repr(r.ports['native'].written)
        assert any(k==140 for k,_,_ in r.ports['data'].sent),r.ports['data'].sent
        assert any(k==133 and len(b)==5 for k,_,b in r.ports['data'].sent),r.ports['data'].sent
        assert any(k==137 for k,_,_ in r.ports['data'].sent),r.ports['data'].sent
        before=r.e.actions;r.pump();assert r.e.actions==before
    return peaks

def test_blocked_stdin_output_and_independent_control():
    r=Rig();r.launch();r.ports['native'].blocked=True;r.input()
    assert not r.ports['native'].written
    r.credit(124)
    r.ports['streams'].queue={'stdout':[b'out'],'stderr':[b'err']}
    r.ctrl(2);r.pump()
    assert r.ports['process'].signals==1
    assert (202,1,b'\1') in r.ports['control'].sent
    assert (134,0,b'out') in r.ports['data'].sent and (136,0,b'err') in r.ports['data'].sent
    r.ports['native'].blocked=False;r.ports['native'].ready=True;r.ports['native'].max_write=2;r.pump()
    assert len(r.ports['native'].written)==len(b'{"type":"fixture"}\n'), len(r.ports['native'].written)
    r.end();r.finish(False)

def test_parked_data_keeps_exact_frame_and_services_cancel():
    r=Rig();r.launch();r.credit(124);r.ports['data'].writable=False;r.input()
    assert r.e.box['data'] is not None
    token,identity,*_=r.e.box['data']
    # One explicit writable observation followed by known EAGAIN.
    ticket=r.e.interests()['data_tx'];r.e.notify('data_tx',ticket);r.pump()
    calls=r.ports['data'].send_calls
    r.ports['streams'].queue['stdout']=[b'pending'];r.ctrl(1);r.pump()
    assert r.e.box['data'][0] is token and r.e.box['data'][1]==identity
    assert r.ports['data'].send_calls==calls
    assert r.ports['process'].stops==1
    assert (202,1,b'\1') in r.ports['control'].sent
    r.now=3;r.e.step();assert r.e.state=='UNRESOLVED'
    assert r.e.root.used>0 and r.e.root.leased>0

def test_launch_cancel_and_refused_launch_do_not_signal_process():
    r=Rig();r.ctrl(1);r.pump()
    assert r.e.state=='RELEASED',r.e.snapshot()
    assert r.ports['launch'].cancels==1 and r.ports['launch'].commits==0
    assert r.ports['process'].stops==r.ports['process'].signals==r.ports['native'].closes==0
    r=Rig();r.ports['launch'].refuse=True
    r.data(1,1,b'\0\0\0\2\0\0\0\2');r.data(2,1,b'{}');r.data(3,1);r.pump()
    assert not r.e.started and r.ports['launch'].commits==0
    assert r.e.primary=='port_refused'

def test_malformed_semantics_precede_effects_and_missing_ports_refuse():
    r=Rig();r.ctrl(7);r.pump()
    assert r.ports['process'].signals==r.ports['launch'].commits==0
    assert r.e.primary=='control_semantics_invalid'
    r=Rig();bad=dict(r.ports);bad.pop('native')
    try:Engine(codec=Codec,receiver=InputReceiver,ports=bad,clock=lambda:0,work_deadline=100,stop_seconds=2)
    except ReactorError:pass
    else:raise AssertionError('missing port accepted')
    r=Rig();r.data(1,1,b'\0\0\0\2\0\0\0\3');r.pump()
    assert r.e.primary=='launch_header_invalid' and r.ports['launch'].commits==0

def test_stale_readiness_reply_and_unknown_send_quarantine():
    for mode in ('readiness','reply','send'):
        r=Rig();r.launch();r.credit(124);r.ports['data'].writable=False;r.input()
        assert r.e.box['data'] is not None
        if mode=='readiness':r.e.notify('data_tx',(r.e.generation,999))
        elif mode=='reply':r.e._finish_send('data',('stale',))
        else:
            r.ports['data'].send_outcome=1;r.ports['data'].writable=True;r.pump()
        assert r.e.unknown and r.e.root.used>0 and r.e.root.leased>0
        before=r.ports['data'].send_calls;r.now=3;r.e.step();r.pump()
        assert r.e.state=='UNRESOLVED' and r.ports['data'].send_calls==before

def test_maximum_ascii_and_utf8_lines_preserve_caps():
    observations=[]
    for unit in (b'a',bytes([240,159,152,128])):
        r=Rig();r.launch()
        prefix=b'{"type":"fixture","pad":"';suffix=b'"}\n'
        available=131072-len(prefix)-len(suffix)
        line=prefix+unit*(available//len(unit))+b'a'*(available%len(unit))+suffix
        for i in range(0,len(line),512):r.input(line[i:i+512],op=2+i//512)
        assert r.e.primary is None and not r.e.unknown
        assert bytes(r.ports['native'].written)==line
        assert r.e.root.denied_external_n is None and r.e.root.peak<=24576
        observations.append({'peak':r.e.root.peak,'external_peak':r.e.root.external_peak,'written':len(line)})
        r.end(op=258);r.finish(False)
    return observations

def test_wait_release_uncertainty_and_deadline_are_terminal():
    r=Rig();r.launch();r.ports['process'].available=True;r.ports['process'].code=True;r.pump()
    assert r.e.unknown and r.e.wait_code is None
    r.now=3;r.e.step();assert r.e.state=='UNRESOLVED'
    r=Rig();r.launch();r.input();r.end()
    def close_unknown():raise OSError('controlled close uncertainty')
    r.ports['streams'].close=close_unknown
    r.credit(124)
    r.ports['streams'].queue={'stdout':[None],'stderr':[None]};r.ports['process'].available=True;r.pump()
    assert r.e.unknown and 'streams' in r.e.release_attempted and 'streams' not in r.e.released
    assert r.e.root.used>0
    r=Rig();r.launch();r.now=100;r.e.step();assert r.e.primary=='work_deadline'
    r.pump();assert r.ports['process'].stops==1
    r.now=102;r.e.step();assert r.e.state=='UNRESOLVED'

def test_known_eagain_resume_and_invalid_input_never_writes():
    r=Rig();r.launch();r.credit(124);r.ports['data'].writable=False;r.input()
    t,identity,_,_=r.e.box['data']
    r.e.notify('data_tx',r.e.interests()['data_tx']);r.pump()
    assert r.e.box['data'][0] is t
    calls=r.ports['data'].send_calls
    for _ in range(20):assert r.e.step()=='idle'
    assert r.ports['data'].send_calls==calls
    r.ports['data'].writable=True;r.pump()
    assert r.e.completed['data']==identity and r.e.root.leased==0
    assert len([x for x in r.ports['data'].sent if x[0]==140])==1
    r.end();r.finish(False)
    r=Rig();r.launch();r.input(b'{"type":"invalid"}\n')
    assert not r.ports['native'].written and r.e.primary is not None

def test_known_running_cancel_can_release_without_erasing_reason():
    r=Rig();r.launch();r.input();r.end();r.ctrl(1);r.pump()
    assert r.ports['process'].stops==1 and r.e.primary=='cancelled'
    r.finish(False);assert r.e.primary=='cancelled'
    assert r.ports['native'].closes==1

def test_no_stdout_credit_never_reads_or_forwards_root_counterexample():
    r=Rig();r.launch();r.ports['streams'].queue['stdout']=[b'0123456789'];r.pump()
    assert 'stdout' not in r.ports['streams'].calls
    assert not any(k==134 for k,_,_ in r.ports['data'].sent)
    assert 'stdout' not in r.e.interests()
    r.ports['streams'].queue['stderr']=[b'err'];r.ctrl(2);r.pump()
    assert (136,0,b'err') in r.ports['data'].sent
    assert (202,1,b'\1') in r.ports['control'].sent
    assert 'stdout' not in r.ports['streams'].calls


def test_exact_positive_124_and_512_grants_and_partial_resume():
    for amount in (124,512):
        r=Rig();r.launch();r.credit(amount)
        assert r.e.primary is None and r.e.stdout_credit==amount
        r.ports['streams'].queue['stdout']=[b'abc'];r.pump()
        assert r.e.stdout_credit==amount-3
        assert r.ports['streams'].widths[-1]==('stdout',amount)
        assert (134,0,b'abc') in r.ports['data'].sent
        r.ports['streams'].queue['stdout']=[-1];r.pump()
        assert r.e.stdout_credit==amount-3
        calls=len(r.ports['streams'].calls)
        for _ in range(10):assert r.e.step()=='idle'
        assert len(r.ports['streams'].calls)==calls
        r.ports['streams'].queue['stdout']=[b'x'*(amount-3)+b'pending'];r.pump()
        assert r.e.stdout_credit==0 and r.ports['streams'].queue['stdout']==[b'pending']
        assert r.ports['streams'].widths[-1]==('stdout',amount-3)
        r.credit(7);r.pump();assert r.e.stdout_credit==0
        assert r.ports['streams'].queue['stdout']==[]


def test_invalid_zero_excess_overlap_grants_refuse_before_read_or_signal():
    for bad,existing in ((0,0),(513,0),(124,124)):
        r=Rig();r.launch()
        if existing:r.credit(existing)
        r.data(6,0,bad.to_bytes(2,'big'))
        r.e.notify('data_rx',r.e.interests()['data_rx'])
        assert r.e.step()=='data_rx'
        assert r.e.primary=='stdout_credit_invalid'
        assert r.ports['streams'].calls==[] and r.ports['process'].signals==r.ports['process'].stops==0
        assert r.e.stdout_credit==existing


def test_eof_after_exhaustion_needs_new_grant_and_wait_does_not_prove_eof():
    r=Rig();r.launch();r.input();r.end();r.credit(3)
    r.ports['streams'].queue={'stdout':[b'abc',None],'stderr':[None]};r.pump()
    assert r.e.stdout_credit==0 and not r.e.eof['stdout'] and r.e.eof['stderr']
    r.ports['process'].available=True;r.pump()
    assert r.e.wait_code==0 and not r.e.eof['stdout'] and r.e.state!='RELEASED'
    calls=len(r.ports['streams'].calls)
    for _ in range(10):r.e.step()
    assert len(r.ports['streams'].calls)==calls
    r.credit(1);r.pump()
    assert r.e.eof['stdout'] and r.e.stdout_credit==1 and r.e.state=='RELEASED'


def test_stdout_send_unknown_does_not_refund_debited_grant():
    r=Rig();r.launch();r.credit(124);r.ports['data'].writable=False
    r.ports['streams'].queue['stdout']=[b'0123456789'];r.pump()
    assert r.e.stdout_credit==114 and r.e.box['data'] is not None
    r.ports['data'].send_outcome=1;r.ports['data'].writable=True;r.pump()
    assert r.e.unknown and r.e.stdout_credit==114 and r.e.root.leased>0
    r.now=3;r.e.step();assert r.e.state=='UNRESOLVED' and r.e.stdout_credit==114


def load_tests(loader, tests, pattern):
    cases = (
        test_normal_actual_composition_both_eof_wait_orders,
        test_blocked_stdin_output_and_independent_control,
        test_parked_data_keeps_exact_frame_and_services_cancel,
        test_launch_cancel_and_refused_launch_do_not_signal_process,
        test_malformed_semantics_precede_effects_and_missing_ports_refuse,
        test_stale_readiness_reply_and_unknown_send_quarantine,
        test_maximum_ascii_and_utf8_lines_preserve_caps,
        test_wait_release_uncertainty_and_deadline_are_terminal,
        test_known_eagain_resume_and_invalid_input_never_writes,
        test_known_running_cancel_can_release_without_erasing_reason,
        test_no_stdout_credit_never_reads_or_forwards_root_counterexample,
        test_exact_positive_124_and_512_grants_and_partial_resume,
        test_invalid_zero_excess_overlap_grants_refuse_before_read_or_signal,
        test_eof_after_exhaustion_needs_new_grant_and_wait_does_not_prove_eof,
        test_stdout_send_unknown_does_not_refund_debited_grant,
    )
    return unittest.TestSuite(unittest.FunctionTestCase(case) for case in cases)


if __name__ == '__main__':
    unittest.main()
