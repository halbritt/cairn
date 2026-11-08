"""Inactive F1 installation, verification and external-completion mechanisms.

Fixed Unix uid/gid 0/1000 and tool-route assumptions are retained from the
reviewed prototype. Source-owned selections remain unbound. There is no CLI
binding interface, installation grant or operational entry in this module.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import time

SELECTION=None
OWNER_HOME=None
INSTALL_ENACTED=False
ROOT_STAGE_ENACTED=False
OBSERVE_ENACTED=False
CAPTURE_ENACTED=False
EXECUTOR_LAYOUT=None
CAPTURE_KIND=None
ROOT_PROGRAM_TEXT=None
ROOT_PROGRAM_SHA256=None
# Future Root-selected external capture, embedded only after exact review.
# These constants are not caller authentication or an observation grant.
INSTALL_COMPLETION_CAPTURE_TEXT=None
INSTALL_COMPLETION_CAPTURE_SHA256=None
SELECTED_INSTALLER_SHA256=None
INSTALL_ENV={'PATH':'/usr/bin:/bin','LC_ALL':'C','PYTHONDONTWRITEBYTECODE':'1'}
# Root work, final publication, and startup each need their own allowance.
ROOT_CHILD_STARTUP_MARGIN_SECONDS=5
ROOT_CHILD_INTAKE_SECONDS=30+5+ROOT_CHILD_STARTUP_MARGIN_SECONDS
INSTALL_WRAPPER_ENVELOPE_SECONDS=30+ROOT_CHILD_INTAKE_SECONDS+30+5
OBSERVE_WRAPPER_ENVELOPE_SECONDS=100

class Refusal(Exception):pass

def need(value,code):
    if not value:raise Refusal(code)

def identity(info):
    return (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid,info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)

def owner_home():
    need(type(OWNER_HOME) is str and Path(OWNER_HOME).is_absolute(),'OWNER_HOME_UNBOUND')
    return OWNER_HOME

def expected_owner(path):
    home=owner_home()
    return 1000 if str(path)==home or str(path).startswith(home+'/') else 0

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()+b'\n'

def parse_json(raw):
    need(type(raw) is bytes and len(raw)<=65536,'JSON_BOUND')
    def pairs(items):
        out={}
        for key,value in items:
            need(key not in out,'JSON_DUPLICATE');out[key]=value
        return out
    try:return json.loads(raw.decode('utf-8','strict'),object_pairs_hook=pairs,parse_constant=lambda x:(_ for _ in ()).throw(Refusal('JSON_CONSTANT')))
    except (ValueError,UnicodeError,RecursionError):raise Refusal('JSON_SHAPE') from None

class Files:
    def uid(self):return os.geteuid()
    def gid(self):return os.getegid()
    def now(self):return time.monotonic()
    def env(self):return dict(os.environ)
    def owner_for(self,path):return expected_owner(path)

    def open_dir(self,path):
        path=Path(path);need(path.is_absolute() and '..' not in path.parts,'PATH_SHAPE')
        fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
        current=Path('/')
        try:
            info=os.fstat(fd);need(info.st_uid==self.owner_for(current) and not info.st_mode&0o022,'PARENT_OWNER_MODE')
            for component in path.parts[1:]:
                nxt=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
                os.close(fd);fd=nxt;current=current/component
                info=os.fstat(fd);need(stat.S_ISDIR(info.st_mode) and info.st_uid==self.owner_for(current) and not info.st_mode&0o022,'PARENT_OWNER_MODE')
            return fd
        except BaseException:os.close(fd);raise

    def check_parent(self,path):
        fd=self.open_dir(Path(path).parent)
        try:
            info=os.fstat(fd);need(not info.st_mode&stat.S_ISGID,'PARENT_SETGID')
            return identity(info)
        finally:os.close(fd)

    def absent(self,path):
        path=Path(path);fd=self.open_dir(path.parent)
        try:
            try:os.stat(path.name,dir_fd=fd,follow_symlinks=False)
            except FileNotFoundError:return
            raise Refusal('DESTINATION_EXISTS')
        finally:os.close(fd)

    def checked(self,path,uid,mode,limit,digest=None,exact_bytes=None,gid=None):
        path=Path(path);parent=self.open_dir(path.parent)
        fd=None
        try:
            fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=parent)
            before=os.fstat(fd)
            need(stat.S_ISREG(before.st_mode) and before.st_uid==uid and before.st_nlink==1 and not before.st_mode&0o022,'FILE_GUARD')
            need(mode is None or stat.S_IMODE(before.st_mode)==mode,'FILE_MODE')
            need(gid is None or before.st_gid==gid,'FILE_GROUP')
            need(before.st_size<=limit,'FILE_BOUND')
            raw=bytearray()
            while len(raw)<=limit:
                block=os.read(fd,min(65536,limit+1-len(raw)))
                if not block:break
                raw.extend(block)
            need(len(raw)<=limit,'FILE_BOUND')
            after=os.fstat(fd);named=os.stat(path.name,dir_fd=parent,follow_symlinks=False)
            need(identity(before)==identity(after)==identity(named),'FILE_RACED')
            need(exact_bytes is None or len(raw)==exact_bytes,'FILE_LENGTH')
            measured=hashlib.sha256(raw).hexdigest();need(digest is None or measured==digest,'FILE_HASH')
            return bytes(raw),{'dev':after.st_dev,'ino':after.st_ino,'uid':after.st_uid,'gid':after.st_gid,'mode':format(stat.S_IMODE(after.st_mode),'04o'),'bytes':len(raw),'sha256':measured}
        finally:
            if fd is not None:os.close(fd)
            os.close(parent)

    def create_dir(self,path,uid,gid):
        need(self.uid()==uid and self.gid()==gid,'KERNEL_INSTALL_IDENTITY')
        path=Path(path);parent=self.open_dir(path.parent);fd=None
        try:
            os.mkdir(path.name,0o700,dir_fd=parent)
            fd=os.open(path.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
            os.fchmod(fd,0o700)
            info=os.fstat(fd);need(info.st_uid==uid and info.st_gid==gid and stat.S_IMODE(info.st_mode)==0o700,'CREATED_DIRECTORY_IDENTITY')
            os.fsync(fd);os.fsync(parent)
        finally:
            if fd is not None:os.close(fd)
            os.close(parent)

        return {'dev':info.st_dev,'ino':info.st_ino,'uid':info.st_uid,'gid':info.st_gid,'mode':format(stat.S_IMODE(info.st_mode),'04o')}

    def write_new(self,path,raw,uid,gid,mode):
        need(self.uid()==uid and self.gid()==gid and type(raw) is bytes and len(raw)<=65536,'WRITE_INPUT')
        path=Path(path);parent=self.open_dir(path.parent);fd=None
        try:
            fd=os.open(path.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=parent)
            os.fchmod(fd,0o600);first=os.fstat(fd)
            need(stat.S_ISREG(first.st_mode) and first.st_uid==uid and first.st_gid==gid and first.st_nlink==1,'CREATED_FILE_IDENTITY')
            offset=0
            while offset<len(raw):
                count=os.write(fd,raw[offset:]);need(count>0,'WRITE_ZERO');offset+=count
            os.fchmod(fd,mode);os.fsync(fd)
            after=os.fstat(fd);named=os.stat(path.name,dir_fd=parent,follow_symlinks=False)
            need(identity(after)==identity(named) and after.st_size==len(raw) and after.st_uid==uid and after.st_gid==gid and after.st_nlink==1 and stat.S_IMODE(after.st_mode)==mode,'WRITTEN_FILE_IDENTITY')
            os.fsync(parent)
        finally:
            if fd is not None:os.close(fd)
            os.close(parent)
        return self.checked(path,uid,mode,len(raw),hashlib.sha256(raw).hexdigest(),len(raw),gid)[1]

    def finish_dir(self,path,uid,gid,mode):
        need(self.uid()==uid and self.gid()==gid,'KERNEL_INSTALL_IDENTITY')
        fd=self.open_dir(path);parent=self.open_dir(Path(path).parent)
        try:
            os.fchmod(fd,mode);info=os.fstat(fd)
            need(info.st_uid==uid and info.st_gid==gid and stat.S_IMODE(info.st_mode)==mode,'DIRECTORY_FINAL_IDENTITY')
            os.fsync(fd);os.fsync(parent)
        finally:os.close(fd);os.close(parent)

    def verify_dir(self,directory,rows,initial=False):
        fd=self.open_dir(directory['path'])
        try:
            first=os.fstat(fd);mode=int(directory['initial_mode'] if initial else directory['final_mode'],8)
            need(first.st_uid==directory['uid'] and first.st_gid==directory['gid'] and stat.S_IMODE(first.st_mode)==mode,'DIRECTORY_FINAL_IDENTITY')
            expected={Path(row['future_path']).name for row in rows};names=set()
            with os.scandir(fd) as entries:
                for item in entries:
                    need(len(names)<3 and item.name in expected,'CLOSURE_ENTRIES');names.add(item.name)
            need(names==expected and len(rows)==3,'CLOSURE_ENTRIES')
            result={}
            for row in rows:
                result[Path(row['future_path']).name]=self.checked(row['future_path'],row['future_uid'],int(row['future_mode'],8),row['bytes'],row['sha256'],row['bytes'],directory['gid'])[1]
            need(identity(os.fstat(fd))==identity(first),'DIRECTORY_RACED')
            fresh=self.open_dir(directory['path'])
            try:need(identity(os.fstat(fresh))==identity(first),'DIRECTORY_PATH_RACED')
            finally:os.close(fresh)
            return result
        finally:os.close(fd)

    def source(self,row):
        return self.checked(Path(SELECTION['source_root'])/row['source'],1000,0o600,row['bytes'],row['sha256'],row['bytes'])[0]

    def check_intent(self):
        fd=self.open_dir(SELECTION['intent'])
        try:
            info=os.fstat(fd)
            need(info.st_uid==1000 and info.st_gid==1000 and stat.S_IMODE(info.st_mode)==0o700,'INTENT_DIRECTORY_GUARD')
        finally:os.close(fd)

    def record(self,name,value,root=False):
        allowed={'root-start.json','root-final.json'} if root else {'started.json','root-launch.json','final.json','observe-launch.json','observe-final.json'}
        need(name in allowed,'RECORD_NAME');self.check_intent()
        return self.write_new(Path(SELECTION['intent'])/name,canonical(value),0 if root else 1000,0 if root else 1000,0o644 if root else 0o600)

    def read_record_bytes(self,name,root=False):
        self.check_intent()
        raw,_=self.checked(Path(SELECTION['intent'])/name,0 if root else 1000,0o644 if root else 0o600,65536)
        return raw

    def read_record(self,name,root=False):return parse_json(self.read_record_bytes(name,root))

    def alias(self,path,target):
        path=Path(path);parent=self.open_dir(path.parent)
        try:
            info=os.stat(path.name,dir_fd=parent,follow_symlinks=False)
            need(stat.S_ISLNK(info.st_mode) and info.st_uid==0,'ALIAS_GUARD')
            need(os.readlink(path.name,dir_fd=parent)==target,'ALIAS_CHANGED')
            return identity(info),identity(os.fstat(parent))
        finally:os.close(parent)

    def tools(self,install_only=False):
        home=owner_home();tools=SELECTION['tools'];sudo_links=(('/usr/bin/sudo','/etc/alternatives/sudo'),('/etc/alternatives/sudo','/usr/lib/cargo/bin/sudo'))
        before=[self.alias(a,z) for a,z in sudo_links]
        python=self.alias('/usr/bin/python3',tools['python_alias_target'])
        selected=[('/usr/lib/cargo/bin/sudo','sudo_endpoint',0),('/usr/bin/'+tools['python_alias_target'],'python',0)]
        if not install_only:selected += [('/usr/bin/systemctl','systemctl',0),('/usr/bin/ss','ss',0),('/usr/bin/tailscale','tailscale',0),(home+'/.local/bin/cairn','cairn',1000)]
        result={}
        for path,key,owner in selected:result[key]=self.checked(path,owner,None,67108864,tools[key])[1]
        need(before==[self.alias(a,z) for a,z in sudo_links] and python==self.alias('/usr/bin/python3',tools['python_alias_target']),'ALIAS_RACED')
        return result

def finite_seconds(value):
    return type(value) in (int,float) and value>=0 and math.isfinite(value)

class Child:
    """Sampled SIGCHLD guards require stable default disposition and no competing
    reaper throughout the child's life. They cannot attest either premise or
    detect an unobserved transient change. Intake timeout never cancels a child.
    """
    def __init__(self):self.receipt=None
    def run(self,argv,env,deadline):
        r={'class':'fixed-child','pid':None,'start_identity':None,'outcome':'NOT_LAUNCHED_CLOCK_INVALID','returncode':None,'eof':False,'stdout_bytes':0,'stderr_bytes':0,'elapsed_seconds':None,'sigchld_before_launch':'UNKNOWN','sigchld_last':'UNKNOWN'};self.receipt=r
        last=None;start=None
        def sample():
            nonlocal last
            value=time.monotonic()
            need(finite_seconds(value) and (last is None or value>=last),'CHILD_CLOCK_INVALID')
            last=value
            return value
        try:
            need(finite_seconds(deadline),'CHILD_DEADLINE_INVALID')
            start=sample()
            if start>=deadline:r['outcome']='NOT_LAUNCHED_DEADLINE';return None
        except (Refusal,OSError,OverflowError):return None
        if signal.getsignal(signal.SIGCHLD) is not signal.SIG_DFL:
            r['outcome']='NOT_LAUNCHED_SIGCHLD';return None
        r['sigchld_before_launch']='SIG_DFL_OBSERVED';r['sigchld_last']='SIG_DFL_OBSERVED'
        r['outcome']='LAUNCH_UNKNOWN'
        try:child=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,cwd='/',umask=0o077,close_fds=True,shell=False)
        except OSError:return None
        r['pid']=child.pid;out=bytearray();selector=None;streams=[child.stdout,child.stderr]
        try:
            selector=selectors.DefaultSelector()
            for stream in streams:os.set_blocking(stream.fileno(),False);selector.register(stream,selectors.EVENT_READ)
            while True:
                now=sample()
                if now>=deadline:r['outcome']='TIMEOUT_UNKNOWN';return None
                if signal.getsignal(signal.SIGCHLD) is not signal.SIG_DFL:
                    r['sigchld_last']='CHANGED';r['outcome']='SIGCHLD_CHANGED_UNKNOWN';return None
                status=child.poll();r['returncode']=status
                if signal.getsignal(signal.SIGCHLD) is not signal.SIG_DFL:
                    r['sigchld_last']='CHANGED';r['outcome']='SIGCHLD_CHANGED_UNKNOWN';return None
                if not selector.get_map() and status is not None:
                    # Validate clock/deadline again before accepting terminal intake.
                    if sample()>=deadline:r['outcome']='TIMEOUT_UNKNOWN';return None
                    if signal.getsignal(signal.SIGCHLD) is not signal.SIG_DFL:
                        r['sigchld_last']='CHANGED';r['outcome']='SIGCHLD_CHANGED_UNKNOWN';return None
                    r['eof']=True;r['outcome']='EXITED_ZERO_EOF' if type(status) is int and status==0 else 'EXITED_NONZERO_EOF'
                    return bytes(out) if type(status) is int and status==0 else None
                now=sample()
                if now>=deadline:r['outcome']='TIMEOUT_UNKNOWN';return None
                for key,_ in selector.select(min(0.05,deadline-now)):
                    try:block=os.read(key.fileobj.fileno(),8192)
                    except BlockingIOError:continue
                    if not block:selector.unregister(key.fileobj);continue
                    label='stdout_bytes' if key.fileobj is child.stdout else 'stderr_bytes';r[label]+=len(block)
                    if r[label]>65536:r['outcome']='OUTPUT_BOUND_UNKNOWN';return None
                    if key.fileobj is child.stdout:out.extend(block)
        except (Refusal,OverflowError):r['outcome']='CLOCK_INVALID_UNKNOWN';return None
        except OSError:r['outcome']='INTAKE_UNKNOWN';return None
        finally:
            if selector is not None:selector.close()
            for stream in streams:stream.close()
            # Last validated sample only: no unchecked final clock arithmetic.
            r['elapsed_seconds']=last-start
            r['elapsed_basis']='LAST_VALIDATED_SAMPLE_NOT_COMPLETION_TIME'
            # Closing intake is not cancellation or proof of completion; never wait/kill/retry.


def positive_child(receipt):
    return (type(receipt) is dict and receipt.get('outcome')=='EXITED_ZERO_EOF' and
            type(receipt.get('returncode')) is int and receipt['returncode']==0 and receipt.get('eof') is True and
            type(receipt.get('pid')) is int and receipt['pid']>0)


def sha256_text(value):
    return type(value) is str and len(value)==64 and all(c in '0123456789abcdef' for c in value)

def completion_capture(stdout,child_receipt,installer_sha256):
    """Pure projection of EXTERNALLY observed outcome, not an observer/authority.

    Root must select the exact capture and producer bytes after reviewing the
    actual external execution result. Never derive this from local final.json.
    """
    need(sha256_text(installer_sha256),'INSTALLER_CAPTURE_SOURCE')
    need(positive_child(child_receipt),'INSTALLER_CAPTURE_CHILD_NOT_POSITIVE')
    value=parse_json(stdout)
    need(type(value) is dict and value.get('schema')=='F1.install/1' and
         value.get('plan_id')==SELECTION['plan_id'] and value.get('status')=='INSTALLED_NOT_OBSERVED' and
         'reason' not in value and positive_child(value.get('child')),'INSTALLER_CAPTURE_NOT_COMPLETE')
    need(canonical(value)==stdout,'INSTALLER_CAPTURE_NONCANONICAL')
    return {'schema':'F1.externally-observed-install/1','plan_id':SELECTION['plan_id'],
            'intent':SELECTION['intent'],'installer_sha256':installer_sha256,
            'stdout_sha256':hashlib.sha256(stdout).hexdigest(),'final_sha256':hashlib.sha256(stdout).hexdigest(),'stdout':stdout.decode('utf-8'),
            'child':child_receipt}

def selected_completion(installed,final_bytes):
    need(type(INSTALL_COMPLETION_CAPTURE_TEXT) is str and sha256_text(INSTALL_COMPLETION_CAPTURE_SHA256) and
         sha256_text(SELECTED_INSTALLER_SHA256),'INSTALL_COMPLETION_UNBOUND')
    raw=INSTALL_COMPLETION_CAPTURE_TEXT.encode('utf-8')
    need(hashlib.sha256(raw).hexdigest()==INSTALL_COMPLETION_CAPTURE_SHA256,'INSTALL_COMPLETION_CAPTURE_PIN')
    capture=parse_json(raw)
    need(type(capture) is dict and type(capture.get('stdout')) is str,'INSTALL_COMPLETION_CAPTURE_SHAPE')
    expected=completion_capture(capture['stdout'].encode('utf-8'),capture.get('child'),SELECTED_INSTALLER_SHA256)
    need(capture==expected,'INSTALL_COMPLETION_CAPTURE_BINDING')
    need(canonical(installed)==final_bytes==capture['stdout'].encode('utf-8') and hashlib.sha256(final_bytes).hexdigest()==capture['final_sha256'],'INSTALL_COMPLETION_FINAL_DIFFERS')
    return INSTALL_COMPLETION_CAPTURE_SHA256


def role_rows(uid):return [row for row in SELECTION['files'] if row['future_uid']==uid]
def role_dir(uid):return next(row for row in SELECTION['directories'] if row['uid']==uid)
class Clock:
    def __init__(self,source):self.source=source;self.last=None
    def sample(self):
        value=self.source()
        need(finite_seconds(value) and (self.last is None or value>=self.last),'COOPERATIVE_CLOCK_INVALID')
        self.last=value;return value
    def deadline(self,seconds):
        need(finite_seconds(seconds) and seconds>0,'COOPERATIVE_BUDGET_INVALID')
        end=self.sample()+seconds;need(finite_seconds(end),'COOPERATIVE_DEADLINE_INVALID');return end
    def budget(self,seconds):return Budget(self,self.deadline(seconds))

class Budget:
    def __init__(self,clock,end):self.clock=clock;self.end=end
    def check(self):need(finite_seconds(self.end) and self.clock.sample()<self.end,'COOPERATIVE_DEADLINE')

def check_deadline(fs,deadline):deadline.check()


def install_role(fs,uid,stages,deadline):
    need(fs.uid()==uid and fs.gid()==uid,'KERNEL_INSTALL_IDENTITY')
    directory=role_dir(uid);rows=role_rows(uid)
    buffers={row['future_path']:fs.source(row) for row in rows}
    check_deadline(fs,deadline);fs.absent(directory['path'])
    stages.append({'stage':'directory-attempt','path':directory['path']})
    created=fs.create_dir(directory['path'],uid,uid);stages.append({'stage':'directory-created','path':directory['path'],'identity':created})
    for row in rows:
        check_deadline(fs,deadline);stages.append({'stage':'file-attempt','path':row['future_path']})
        proof=fs.write_new(row['future_path'],buffers[row['future_path']],uid,uid,int(row['future_mode'],8))
        stages.append({'stage':'file-verified','path':row['future_path'],'identity':proof})
    fs.verify_dir(directory,rows,initial=True)
    fs.finish_dir(directory['path'],uid,uid,int(directory['final_mode'],8))
    proof=fs.verify_dir(directory,rows);stages.append({'stage':'closure-verified','uid':uid})
    return proof


def root_stage(fs):
    stages=[];result={'schema':'F1.root-install/1','plan_id':SELECTION['plan_id'],'status':'UNKNOWN','stages':stages};started=False
    try:
        need(fs.uid()==0 and fs.gid()==0,'ROOT_KERNEL_IDENTITY');clock=Clock(fs.now);clock.sample()
        initial=fs.read_record('started.json');need(initial=={'schema':'F1.install-intent/1','plan_id':SELECTION['plan_id'],'state':'STARTED_NO_RETRY'},'INSTALL_INTENT')
        fs.record('root-start.json',{'plan_id':SELECTION['plan_id'],'state':'ROOT_STAGE_STARTED_NO_RETRY'},root=True);started=True
        fs.absent(role_dir(1000)['path'])
        result['proof']=install_role(fs,0,stages,clock.budget(30));result['status']='ROOT_CLOSURE_COMPLETE'
    except Refusal as exc:result['reason']=str(exc)
    except BaseException:result['reason']='ROOT_STAGE_EXCEPTION_UNKNOWN'
    if started:
        try:
            final_budget=clock.budget(5);fs.record('root-final.json',result,root=True);final_budget.check()
        except BaseException:result['status']='UNKNOWN';result['reason']='ROOT_FINAL_NOT_DURABLE'
    return result


def root_program():
    need(type(ROOT_PROGRAM_TEXT) is str and type(ROOT_PROGRAM_SHA256) is str and hashlib.sha256(ROOT_PROGRAM_TEXT.encode()).hexdigest()==ROOT_PROGRAM_SHA256,'ROOT_PROGRAM_UNBOUND')
    return ['/usr/bin/sudo','-n','/usr/bin/python3','-I','-B','-c',ROOT_PROGRAM_TEXT]


def install(fs,child):
    stages=[];result={'schema':'F1.install/1','plan_id':SELECTION['plan_id'],'status':'UNKNOWN','stages':stages,'child':None};intent=False
    try:
        need(fs.uid()==1000 and fs.gid()==1000,'COORDINATOR_KERNEL_IDENTITY');argv=root_program();clock=Clock(fs.now);deadline=clock.budget(30)
        for row in SELECTION['files']:fs.source(row)
        for directory in SELECTION['directories']:fs.check_parent(directory['path']);fs.absent(directory['path'])
        fs.absent(SELECTION['intent']);fs.tools(install_only=True);deadline.check()
        fs.create_dir(SELECTION['intent'],1000,1000);intent=True
        fs.record('started.json',{'schema':'F1.install-intent/1','plan_id':SELECTION['plan_id'],'state':'STARTED_NO_RETRY'})
        check_deadline(fs,deadline)
        fs.record('root-launch.json',{'plan_id':SELECTION['plan_id'],'state':'ONE_ROOT_LAUNCH_INTENT','argv_prefix':argv[:-1],'program_sha256':ROOT_PROGRAM_SHA256})
        stages.append({'stage':'root-launch-attempt'})
        fs.tools(install_only=True);deadline.check()
        result['budgets']={'preflight':30,'child_work':30,'child_final_publication':5,'child_startup_margin':ROOT_CHILD_STARTUP_MARGIN_SECONDS,'child_intake':ROOT_CHILD_INTAKE_SECONDS,'postflight_work':30,'final_publication':5,'wrapper_envelope':INSTALL_WRAPPER_ENVELOPE_SECONDS}
        raw=child.run(argv,INSTALL_ENV,clock.deadline(ROOT_CHILD_INTAKE_SECONDS));result['child']=child.receipt
        deadline=clock.budget(30)
        need(raw is not None and positive_child(child.receipt),'ROOT_CHILD_NOT_POSITIVE')
        reported=parse_json(raw)
        need(type(reported) is dict and reported.get('schema')=='F1.root-install/1' and reported.get('plan_id')==SELECTION['plan_id'] and reported.get('status')=='ROOT_CLOSURE_COMPLETE','ROOT_REPORT_NOT_COMPLETE')
        # Reported success is insufficient: check the actual final closure independently.
        fs.verify_dir(role_dir(0),role_rows(0));stages.append({'stage':'root-closure-independently-verified'})
        deadline.check();result['collector_proof']=install_role(fs,1000,stages,deadline)
        result['helper_proof']=fs.verify_dir(role_dir(0),role_rows(0));deadline.check();result['status']='INSTALLED_NOT_OBSERVED'
    except Refusal as exc:result['reason']=str(exc)
    except BaseException:result['reason']='INSTALL_EXCEPTION_UNKNOWN'
    result['child']=child.receipt
    if intent:
        try:
            final_budget=clock.budget(5);fs.record('final.json',result);final_budget.check()
        except BaseException:result['status']='UNKNOWN';result['reason']='INSTALL_FINAL_NOT_DURABLE'
    return result


def verify_then_observe(fs,child):
    result={'schema':'F1.external-invocation/1','plan_id':SELECTION['plan_id'],'status':'UNKNOWN','child':None,'acceptance':'NOT_ASSESSED'};marked=False
    try:
        need(fs.uid()==1000 and fs.gid()==1000,'VERIFIER_KERNEL_IDENTITY');clock=Clock(fs.now);preflight=clock.budget(30)
        need(fs.env()==SELECTION['observe_env'],'MINIMAL_ENVIRONMENT')
        need(INSTALL_COMPLETION_CAPTURE_TEXT is not None,'INSTALL_COMPLETION_UNBOUND')
        final_bytes=fs.read_record_bytes('final.json');installed=parse_json(final_bytes);need(type(installed) is dict and installed.get('plan_id')==SELECTION['plan_id'] and installed.get('status')=='INSTALLED_NOT_OBSERVED','INSTALL_NOT_COMPLETE')
        result['external_completion_capture_sha256']=selected_completion(installed,final_bytes)
        for directory in SELECTION['directories']:fs.verify_dir(directory,role_rows(directory['uid']))
        fs.tools();fs.absent(SELECTION['output'])
        # PROPOSED PLAN AMENDMENT: exclusive durable launch marker before any invocation.
        preflight.check()
        fs.record('observe-launch.json',{'plan_id':SELECTION['plan_id'],'state':'ONE_OBSERVATION_LAUNCH_INTENT','argv':SELECTION['observe_argv']});marked=True
        for directory in SELECTION['directories']:fs.verify_dir(directory,role_rows(directory['uid']))
        fs.tools();fs.absent(SELECTION['output'])
        preflight.check();result['budgets']={'preflight':30,'child_work':30,'child_start_final_margin':5,'child_intake':35,'postflight':30,'final_publication':5}
        raw=child.run(SELECTION['observe_argv'],SELECTION['observe_env'],clock.deadline(35));result['child']=child.receipt
        postflight=clock.budget(30)
        need(raw is not None and positive_child(child.receipt),'COLLECTOR_CHILD_NOT_POSITIVE')
        final,_=fs.checked(Path(SELECTION['output'])/'final.json',1000,0o600,65536)
        value=parse_json(final)
        need(type(value) is dict and value.get('schema')=='cairn.F1-observation/2' and value.get('f1_acceptance')=='NOT_ASSESSED' and value.get('readiness')=='NOT_ASSESSED','COLLECTOR_FINAL_SHAPE')
        need(value.get('status') in ('UNKNOWN','OBSERVED_COOPERATIVE_RELATIONSHIP_CANDIDATE'),'COLLECTOR_FINAL_STATUS')
        postflight.check();result['collector_status']=value['status'];result['status']='COLLECTOR_EXITED_WITH_FINAL' if value['status']!='UNKNOWN' else 'OBSERVATION_UNKNOWN'
    except Refusal as exc:result['reason']=str(exc)
    except BaseException:result['reason']='VERIFIER_EXCEPTION_UNKNOWN'
    result['child']=child.receipt
    if marked:
        try:
            final_budget=clock.budget(5);fs.record('observe-final.json',result);final_budget.check()
        except BaseException:result['status']='UNKNOWN';result['reason']='OBSERVATION_WRAPPER_FINAL_NOT_DURABLE'
    return result


def observation_capture(stdout,receipt,verifier_sha256):
    need(sha256_text(verifier_sha256) and positive_child(receipt),'VERIFIER_CAPTURE_NOT_POSITIVE')
    value=parse_json(stdout)
    need(type(value) is dict and value.get('schema')=='F1.external-invocation/1' and
         value.get('plan_id')==SELECTION['plan_id'] and value.get('status')=='COLLECTOR_EXITED_WITH_FINAL' and
         value.get('acceptance')=='NOT_ASSESSED' and 'reason' not in value and
         positive_child(value.get('child')),'VERIFIER_CAPTURE_NOT_COMPLETE')
    need(canonical(value)==stdout,'VERIFIER_CAPTURE_NONCANONICAL')
    return {'schema':'F1.externally-observed-verifier/1','plan_id':SELECTION['plan_id'],
            'intent':SELECTION['intent'],'verifier_sha256':verifier_sha256,
            'stdout_sha256':hashlib.sha256(stdout).hexdigest(),'final_sha256':hashlib.sha256(stdout).hexdigest(),
            'stdout':stdout.decode('utf-8'),'child':receipt,'acceptance':'NOT_ASSESSED'}

def executor_layout():
    # Future exact reviewed source constant, never CLI input or caller authority.
    need(type(EXECUTOR_LAYOUT) is dict and set(EXECUTOR_LAYOUT)=={'directory','files'},'EXECUTOR_LAYOUT_UNBOUND')
    d=EXECUTOR_LAYOUT['directory'];rows=EXECUTOR_LAYOUT['files']
    need(type(d) is dict and set(d)=={'path','uid','gid','initial_mode','final_mode'} and
         d.get('uid')==1000 and d.get('gid')==1000 and d.get('initial_mode')=='0700' and
         d.get('final_mode')=='0700' and type(d.get('path')) is str,'EXECUTOR_DIRECTORY_BINDING')
    need(type(rows) is list and len(rows)==3,'EXECUTOR_FILES_BINDING')
    expected={'installer.py','root_stage.py','verify_invoke.py'};names=set()
    for row in rows:
        need(type(row) is dict and set(row)=={'future_path','future_uid','future_mode','bytes','sha256'},'EXECUTOR_ROW_BINDING')
        path=Path(row['future_path']);need(str(path.parent)==d['path'] and path.name in expected and path.name not in names and
             row['future_uid']==1000 and row['future_mode']=='0600' and type(row['bytes']) is int and
             0<row['bytes'] and sha256_text(row['sha256']),'EXECUTOR_ROW_BINDING');names.add(path.name)
    need(names==expected,'EXECUTOR_FILES_BINDING')
    return d,rows

def capture_wrapper(fs,child):
    # No on-disk success marker. The exclusive launch marker is intent only.
    result={'schema':'F1.supervisor/1','plan_id':SELECTION['plan_id'],'status':'UNKNOWN','child':None,'acceptance':'NOT_ASSESSED'}
    try:
        need(fs.uid()==1000 and fs.gid()==1000,'SUPERVISOR_KERNEL_IDENTITY')
        need(CAPTURE_KIND in ('install','observe'),'CAPTURE_KIND_UNBOUND')
        directory,rows=executor_layout();clock=Clock(fs.now);preflight=clock.budget(30)
        env=INSTALL_ENV if CAPTURE_KIND=='install' else SELECTION['observe_env']
        need(fs.env()==env,'SUPERVISOR_ENVIRONMENT')
        name='installer.py' if CAPTURE_KIND=='install' else 'verify_invoke.py'
        selected=next(r for r in rows if Path(r['future_path']).name==name)
        marker=str(Path(SELECTION['intent']).parent/('root-F1-'+CAPTURE_KIND+'-supervisor-launch-001.json'))
        fs.verify_dir(directory,rows);fs.tools(install_only=CAPTURE_KIND=='install');fs.absent(marker)
        preflight.check()
        fs.write_new(marker,canonical({'schema':'F1.supervisor-intent/1','kind':CAPTURE_KIND,'plan_id':SELECTION['plan_id'],'source_sha256':selected['sha256'],'state':'ONE_LAUNCH_NO_RETRY'}),1000,1000,0o600)
        fs.verify_dir(directory,rows);fs.tools(install_only=CAPTURE_KIND=='install');preflight.check()
        argv=['/usr/bin/python3','-I','-B',selected['future_path']]
        wrapper_envelope=INSTALL_WRAPPER_ENVELOPE_SECONDS if CAPTURE_KIND=='install' else OBSERVE_WRAPPER_ENVELOPE_SECONDS
        child_intake=wrapper_envelope+5
        supervisor_envelope=30+child_intake+30+5
        result['budgets']={'preflight':30,'wrapper_envelope':wrapper_envelope,'startup_final_margin':5,'child_intake':child_intake,'postflight':30,'final_output_margin':5,'supervisor_envelope':supervisor_envelope,'external_observer_allowance':supervisor_envelope+5}
        raw=child.run(argv,env,clock.deadline(child_intake));result['child']=child.receipt
        postflight=clock.budget(30)
        need(raw is not None and positive_child(child.receipt),'SUPERVISED_CHILD_NOT_POSITIVE')
        final_name='final.json' if CAPTURE_KIND=='install' else 'observe-final.json'
        final_bytes=fs.read_record_bytes(final_name)
        need(final_bytes==raw,'SUPERVISED_FINAL_DIFFERS')
        capture=(completion_capture if CAPTURE_KIND=='install' else observation_capture)(raw,child.receipt,selected['sha256'])
        fs.verify_dir(directory,rows);postflight.check()
        result['capture']=capture;result['status']='CAPTURED_INSTALL_COMPLETION' if CAPTURE_KIND=='install' else 'CAPTURED_VERIFIER_COMPLETION'
        # Positive return is only observed by the external caller, never by a local success file.
        final_budget=clock.budget(5);need(len(canonical(result))<=65536,'SUPERVISOR_OUTPUT_BOUND');final_budget.check()
    except Refusal as exc:result['reason']=str(exc);result['status']='UNKNOWN';result.pop('capture',None)
    except BaseException:result['reason']='SUPERVISOR_EXCEPTION_UNKNOWN';result['status']='UNKNOWN';result.pop('capture',None)
    result['child']=child.receipt
    return result


def main():
    # Enabled role projections require separate exact-source review and grants.
    print('REFUSED_UNBOUND')
    return 1


if __name__=='__main__':
    raise SystemExit(main())
