"""Private fixture files, pipes and fake host boundaries; never SSH or systemctl."""
from dataclasses import asdict,replace
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from backend_binding import Binding,FilePin,BackendUnknown,SOURCE_NAMES,ssh_argv,systemctl_argv
from backend_files import PinVerifier
from backend_process import Completion
from backend_proof import ProofGate
from remote_executor import RemoteExecutor,RemoteRun
from effects import Action,Intent,SECOND,canonical,digest,SCHEMA
from sequence import OldIdentity,JoinRead,UnitPoint
from claims import ClaimRead,OwnerCoverage,check_timing


class Clock:
    def __init__(self):self.ns=100*SECOND;self.calls=0
    def mono(self):self.calls+=1;return self.ns
    def monotonic_ns(self):return self.mono()
    def receipt(self):return {'mono_ns':self.ns,'rt_ns':self.ns,'epoch':0}


def binding(base,run_id='fixture-run'):
    for name in ('pins','remote','root','source'):(base/name).mkdir(mode=0o700)
    def file(name,mode=0o700,folder='pins'):
        p=base/folder/name;data=('fixture-only:'+name).encode();p.write_bytes(data);p.chmod(mode)
        return FilePin(str(p),hashlib.sha256(data).hexdigest(),os.getuid(),mode,1024)
    closure=tuple(file(name,0o600,'source') for name in sorted(SOURCE_NAMES))
    entry=next(p for p in closure if Path(p.path).name=='backend_entry.py')
    old=OldIdentity(111,555,0,'fixture-boot','b'*64)
    value=Binding(run_id,os.getuid(),file('ssh'),file('config',0o600),file('known_hosts',0o600),
                  'fixture-user@fixture-host',file('python'),entry,file('systemctl'),closure,
                  str(base/'remote'),(('PATH','/fixture/bin'),),(('PATH','/fixture/bin'),),
                  digest(canonical(asdict(old))),'d'*64)
    return value,old


def request(binding,action,index=1):
    body={'schema':SCHEMA,'run_id':binding.run_id,'effect_id':binding.run_id+'-'+str(index),
          'action':action.value,'binding_sha256':binding.digest()}
    return {**body,'request_sha256':digest(canonical(body))}


class Selection:
    def check(self,action,expected):
        if expected!='d'*64:raise BackendUnknown('fixture selection mismatch')


class Signal:
    def check(self,binding,old,request):return None


class Observer:
    def __init__(self,old):self.old=old;self.fault=None
    def read_structure(self):
        absent=UnitPoint(0,None,0,'inactive','dead',1,0,'success')
        read=JoinRead(absent,absent,True,(),True,self.old.boot_id,self.old.preservation_digest,True,(),{})
        if self.fault=='raced':return replace(read,before=UnitPoint(111,555,0,'active','running',0,0,'success'),identity_stable=False)
        if self.fault=='restart':return replace(read,after=replace(absent,restarts=1))
        if self.fault=='journal':return replace(read,journal_flags=('timeout',))
        return read


class CommandProcess:
    def __init__(self):self.calls=[]
    def spawn(self,binding,action):
        self.calls.append(systemctl_argv(binding,action));return SimpleNamespace(pid=777)


class CompletionPort:
    def __init__(self):self.result=Completion(0,b'',b'',True);self.error=None
    def collect(self,child,budget):
        if self.error:raise self.error
        return self.result


def remote(binding,old):
    run=RemoteRun.prepare(binding);observer=Observer(old);proof=ProofGate(binding,old,observer,Selection(),Signal())
    process=CommandProcess();completion=CompletionPort()
    executor=RemoteExecutor(binding,run,PinVerifier(),proof,process,completion)
    return executor,observer,process,completion


class Admission:
    def __init__(self,clock):
        self.clock=clock;self.calls=0;self.delay_on_launcher=False
        self.read=ClaimRead(clock.ns-100_000_000,clock.ns,0,(10_000_000,),
                            (OwnerCoverage(('project','agent','execution'),clock.ns-SECOND,clock.ns-31*SECOND,clock.ns+89*SECOND),),('fixture evidence',))
    def check(self,value):
        self.calls+=1
        if value is not None and self.delay_on_launcher:self.clock.ns+=2*SECOND
        check_timing(self.read,self.clock.receipt(),75,complete_pass=True)
    def ready(self):self.check(None);return self.clock.receipt()


class PipeChild:
    def __init__(self,stdout=b'',stderr=b'',returncode=0):
        ri,wi=os.pipe();ro,wo=os.pipe();re,we=os.pipe()
        self.keep=ri;self.pid=888;self.returncode=returncode
        self.stdin=os.fdopen(wi,'wb',buffering=0);self.stdout=os.fdopen(ro,'rb',buffering=0);self.stderr=os.fdopen(re,'rb',buffering=0)
        assert len(stdout)<4096 and len(stderr)<4096
        os.write(wo,stdout);os.close(wo);os.write(we,stderr);os.close(we)
    def poll(self):return self.returncode
    def close_fixture(self):
        for s in (self.stdin,self.stdout,self.stderr):
            if not s.closed:s.close()
        os.close(self.keep)


class SSHChannel:
    def __init__(self,executor):self.executor=executor;self.calls=[];self.children=[];self.mode='positive'
    def spawn(self,binding,intent_directory):
        self.calls.append(ssh_argv(binding))
        path=sorted(Path(intent_directory).glob('*-effect-requested.json'))[-1]
        reply=self.executor.execute_wire(path.read_bytes())
        output=reply.stdout;code=reply.returncode;error=reply.stderr
        if self.mode=='lost':output=b'';code=255;error=b''
        elif self.mode=='partial':output=output[:20]
        elif self.mode=='duplicate':output=output+output
        elif self.mode=='mismatch' and output:
            body=json.loads(output);body['effect_id']='other';output=canonical(body)+b'\n'
        child=PipeChild(output,error,code);self.children.append(child);return child
    def close_fixture(self):
        for child in self.children:child.close_fixture()
