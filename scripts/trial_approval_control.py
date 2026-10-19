"""Trial-local, per-call root approval for the official Claude stdio protocol.

Decision files are owner-only operator controls, not proof of a distinct Unix
principal. Native processes never receive a mount of this directory. No policy
update, input rewriting, hook approval, extra user turn or automatic allow.
"""
from collections import deque
from pathlib import Path
import argparse,hashlib,json,os,re,stat,sys,tempfile,time,uuid
from functools import wraps

LIMIT=1024*1024
class ApprovalError(ValueError):pass
def digest(raw):return hashlib.sha256(raw).hexdigest()
def guarded(method):
    @wraps(method)
    def run(self,*args,**kwargs):
        if getattr(self,'closed',False) or getattr(self,'stopped',False) or self.failed:raise ApprovalError('controller_closed_or_failed')
        try:return method(self,*args,**kwargs)
        except (ApprovalError,OSError,ValueError,TypeError,KeyError,RecursionError) as error:
            self.failed=self.failed or type(error).__name__
            self.event('protocol_failure',reason=self.failed)
            self.stop(self.failed)
            raise ApprovalError(self.failed) from error
    return run
def encoded(value):return (json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n').encode('utf-8')
def canonical(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'),sort_keys=True,allow_nan=False).encode('utf-8')
def strict(raw):
    if len(raw)>LIMIT:raise ApprovalError('frame_limit')
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ApprovalError('duplicate_json_key')
            result[key]=value
        return result
    try:
        result=json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ApprovalError('nonfinite_json')))
        if type(result) is not dict:raise ApprovalError('object_required')
        encoded(result)  # Reject surrogate/encoding/size surprises before publication.
        return result
    except (ValueError,UnicodeError,RecursionError) as error:
        raise ApprovalError('invalid_json') from error

def private_dir(path):
    path=Path(path);path.mkdir(mode=0o700,parents=True,exist_ok=True)
    info=path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:raise ApprovalError('directory_not_private')
    return path

def read_private(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        # Atomic publication briefly has a private staging hardlink to the same
        # complete inode. Neither alias is writable by the native sandbox.
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:raise ApprovalError('file_not_private')
        raw=stream.read(LIMIT+1)
    return raw,strict(raw)

def publish(path,value):
    """A complete, fsynced object appears exclusively; readers never see partial JSON."""
    path=Path(path);private_dir(path.parent)
    staging=private_dir(path.parent/'.staging')
    fd,name=tempfile.mkstemp(prefix='publishing-',dir=staging)
    try:
        with os.fdopen(fd,'wb') as output:
            output.write(encoded(value));output.flush();os.fsync(output.fileno())
        os.link(name,path,follow_symlinks=False)
        descriptor=os.open(path.parent,os.O_DIRECTORY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)
    finally:os.unlink(name)

def decision_for(request,request_raw,behavior,root_execution_id,*,clause,review_note):
    if behavior not in ('allow','deny') or root_execution_id!=request['binding']['root_execution_id']:raise ApprovalError('root_decision_identity')
    clauses={'allow':{'A1','A2','A3'},'deny':{'D1','D2','D3'}}
    if type(clause) is not str or clause not in clauses[behavior] or type(review_note) is not str or not review_note.strip() or len(review_note.encode('utf-8'))>4096:raise ApprovalError('root_review_clause_or_note')
    return dict(schema='cairn.native-approval-decision/1',binding=request['binding'],
                session_id=request['session_id'],request_id=request['native_request']['request_id'],
                tool_use_id=request['native_request']['request']['tool_use_id'],input_sha256=request['input_sha256'],
                request_sha256=digest(request_raw),pending_nonce=request['pending_nonce'],
                root_execution_id=root_execution_id,behavior=behavior,clause=clause,review_note=review_note)

def decide(path,*,expected_sha256,behavior,root_execution_id,clause,review_note):
    path=Path(path);raw,request=read_private(path)
    if any(os.path.lexists(path.parent.parent/name) for name in ('summary.json','stopped.json')) or request.get('wall_deadline_monotonic_ns') is not None and time.monotonic_ns()>=request['wall_deadline_monotonic_ns']:raise ApprovalError('no_longer_pending')
    if digest(raw)!=expected_sha256 or request.get('schema')!='cairn.native-approval-request/1':raise ApprovalError('reviewed_request_mismatch')
    _,binding=read_private(path.parent.parent/'binding.json')
    if canonical(request['binding'])!=canonical(binding) or path.parent.name!='requests' or path.name!=digest(request['native_request']['request_id'].encode())+'.json':raise ApprovalError('request_run_mismatch')
    value=decision_for(request,raw,behavior,root_execution_id,clause=clause,review_note=review_note)
    destination=path.parent.parent/'decisions'/path.name
    publish(destination,value)
    return destination

class ApprovalControl:
    def __init__(self,directory,binding,user):
        if set(binding)!={'run_id','run_execution_id','freeze_sha256','root_execution_id','admission','approval_policy_sha256'}:raise ApprovalError('binding_shape')
        if any(type(binding[k]) is not str or not binding[k] for k in binding if k!='admission') or type(binding['admission']) is not int:raise ApprovalError('binding_identity')
        if not all(re.fullmatch('[0-9a-f]{64}',binding[k]) for k in ('freeze_sha256','approval_policy_sha256')) or not 3<=binding['admission']<=72:raise ApprovalError('binding_campaign_identity')
        message=strict(user)
        if message.get('type')!='user' or not user.endswith(b'\n'):raise ApprovalError('one_user_message_required')
        self.directory=private_dir(directory);self.requests=private_dir(self.directory/'requests');self.decisions=private_dir(self.directory/'decisions')
        self.binding=dict(binding);self.user=bytes(user);self.user_sent=0
        self.started_ns=time.monotonic_ns();self.wall_started_ns=None;self.deadline_ns=None;self.stopped=False;self.stopped_ns=None
        self.waits={};self.active_waits=set();self.active_since_ns=None;self.total_wait_ns=0
        publish(self.directory/'binding.json',self.binding)
        self.queue=deque();self.pending={};self.seen=set();self.tools=set();self.seen_decisions={}
        self.init_id='initialize-'+uuid.uuid4().hex;self.init_written=False;self.initialized=False
        self.session=None;self.terminal=False;self.failed=None;self.completed=0;self.control_written=0;self.denied=0
        self.journal=(self.directory/'events.jsonl').open('xb',buffering=0);os.chmod(self.directory/'events.jsonl',0o600)
        self.enqueue('initialize',dict(type='control_request',request_id=self.init_id,request=dict(subtype='initialize',hooks=None)))

    def event(self,kind,**values):
        now=time.monotonic_ns()
        self.journal.write(encoded(dict(kind=kind,binding=self.binding,monotonic_ns=now,elapsed_seconds=(now-self.started_ns)/1e9,**values)));os.fsync(self.journal.fileno())

    def set_deadline(self,deadline_ns,*,started_ns=None):
        start=self.started_ns if started_ns is None else started_ns
        if self.deadline_ns is not None or type(start) is not int or type(deadline_ns) is not int or deadline_ns<=start:raise ApprovalError('deadline_binding')
        self.wall_started_ns=start;self.deadline_ns=deadline_ns
        self.event('wall_deadline',wall_deadline_monotonic_ns=deadline_ns)

    def end_wait(self,ident,reason,at_ns=None):
        if ident not in self.active_waits:return
        now=time.monotonic_ns() if at_ns is None else at_ns
        row=self.waits[ident]
        row.update(ended_monotonic_ns=now,end_reason=reason,wait_seconds=(now-row['pending_monotonic_ns'])/1e9)
        if reason=='response_complete':row['response_complete_monotonic_ns']=now
        self.active_waits.remove(ident)
        if not self.active_waits:
            self.total_wait_ns+=now-self.active_since_ns;self.active_since_ns=None
        self.event('approval_wait_ended',request_id=ident,**row)

    def stop(self,status):
        if self.stopped:return
        self.stopped=True
        now=time.monotonic_ns()
        if status=='timeout' and self.deadline_ns is not None:now=min(now,self.deadline_ns)
        self.stopped_ns=now
        for ident in list(self.active_waits):self.end_wait(ident,status,now)
        publish(self.directory/'stopped.json',dict(status=status,binding=self.binding,ended_monotonic_ns=now))
        self.event('stopped',status=status,ended_monotonic_ns=now)

    def need(self,condition,reason):
        if not condition:
            self.failed=reason;self.event('refused',reason=reason);raise ApprovalError(reason)

    def enqueue(self,kind,frame,request_id=None):
        raw=frame if isinstance(frame,bytes) else encoded(frame)
        self.queue.append(dict(kind=kind,raw=raw,offset=0,request_id=request_id))
        self.event('queued',channel=kind,bytes=len(raw),sha256=digest(raw),
                   control_raw_utf8=raw.decode('utf-8') if kind!='user' else None)

    def suffix(self):return self.queue[0]['raw'][self.queue[0]['offset']:] if self.queue else b''

    @guarded
    def wrote(self,count):
        completed_ns=time.monotonic_ns()
        self.need(bool(self.queue) and type(count) is int and 0<count<=len(self.suffix()),'write_progress')
        frame=self.queue[0];frame['offset']+=count
        if frame['kind']=='user':self.user_sent+=count
        else:self.control_written+=count
        if frame['offset']==len(frame['raw']):
            self.queue.popleft()
            if frame['kind']=='initialize':self.init_written=True
            if frame['kind']=='response':
                self.end_wait(frame['request_id'],'response_complete',completed_ns);self.pending.pop(frame['request_id']);self.completed+=1
            self.event('write_completed',channel=frame['kind'],bytes=len(frame['raw']),sha256=digest(frame['raw']),write_complete_monotonic_ns=completed_ns)

    @guarded
    def receive(self,raw):
        received_ns=time.monotonic_ns()
        frame=strict(raw);kind=frame.get('type')
        self.need(kind in {'control_request','control_response','control_cancel_request','system','assistant','user','result','stream_event','rate_limit_event'},'unknown_native_frame')
        if kind not in ('control_request','control_response','control_cancel_request'):
            if kind=='system' and frame.get('subtype')=='init':
                session=frame.get('session_id')
                # Startup metadata may precede the user write; it grants no
                # permission. Requests below still require the complete input.
                self.need(not self.terminal and self.session is None and type(session) is str and 0<len(session)<=256,'native_session_order')
                self.need(frame.get('model')=='claude-opus-5-5','native_model_mismatch');self.session=session
            if kind=='result':
                self.need(self.initialized and self.user_sent==len(self.user) and not self.pending and not self.queue and not self.terminal,'terminal_with_pending_control')
                self.terminal=True
            return False
        self.event('native_control',raw_utf8=raw.decode('utf-8'),raw_sha256=digest(raw))
        self.need(not self.failed and not self.terminal,'control_after_terminal_or_failure')
        if kind=='control_response':
            self.need(set(frame)=={'type','response'} and type(frame['response']) is dict,'initialize_shape')
            response=frame['response'];keys=set(response)
            self.need(keys in ({'subtype','request_id','response'},{'subtype','request_id','response','pending_permission_requests','pending_user_dialog_requests'}),'initialize_shape')
            self.need(self.init_written and not self.initialized and response.get('subtype')=='success' and response.get('request_id')==self.init_id and type(response.get('response')) is dict,'initialize_mismatch')
            if 'pending_permission_requests' in response:
                self.need(response['pending_permission_requests']==[] and type(response['pending_permission_requests']) is list and response['pending_user_dialog_requests']==[] and type(response['pending_user_dialog_requests']) is list,'initialize_pending')
            self.initialized=True;self.enqueue('user',self.user);return True
        self.need(self.initialized and self.user_sent==len(self.user) and self.session is not None,'permission_before_complete_input')
        if kind=='control_cancel_request':
            self.need(set(frame)=={'type','request_id'} and frame['request_id'] in self.pending,'unknown_cancel')
            self.end_wait(frame['request_id'],'native_permission_cancelled',received_ns)
            self.need(False,'native_permission_cancelled')
        self.need(set(frame)=={'type','request_id','request'} and type(frame['request']) is dict,'permission_shape')
        ident=frame['request_id'];request=frame['request']
        self.need(type(ident) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,200}',ident) is not None and ident not in self.seen,'request_identity_or_replay')
        self.need(set(request)>={'subtype','tool_name','input','tool_use_id'} and set(request)<={'subtype','tool_name','input','tool_use_id','permission_suggestions','blocked_path','decision_reason','title','display_name','description','agent_id'},'permission_fields')
        self.need(request['subtype']=='can_use_tool' and type(request['input']) is dict,'unknown_control_request')
        suggestions=request.get('permission_suggestions')
        self.need(suggestions is None or type(suggestions) is list and all(type(item) is dict for item in suggestions),'permission_suggestions_shape')
        self.need(all(request.get(key) is None or type(request[key]) is str for key in ('blocked_path','decision_reason','title','display_name','description','agent_id')),'permission_metadata_shape')
        from trial_task_permissions import ALLOW,DENY
        supported={'Bash','Read','Edit','Write','Glob','Grep'}|{x for x in ALLOW if x.startswith('mcp__')}
        self.need(request['tool_name'] in supported and request['tool_name'] not in DENY,'tool_not_in_trial_scope')
        tool=request['tool_use_id'];self.need(type(tool) is str and re.fullmatch(r'toolu_[A-Za-z0-9_-]{1,128}',tool) is not None and tool not in self.tools,'tool_identity_or_replay')
        self.need(request.get('agent_id') in (None,''),'subagent_not_authorized')
        self.need(len(self.seen)<128 and len(self.pending)<16,'permission_request_limit')
        self.seen.add(ident);self.tools.add(tool)
        now=received_ns
        self.waits[ident]=dict(pending_monotonic_ns=now,decision_observed_monotonic_ns=None,response_complete_monotonic_ns=None,ended_monotonic_ns=None,end_reason=None,wait_seconds=None)
        if not self.active_waits:self.active_since_ns=now
        self.active_waits.add(ident)
        value=dict(schema='cairn.native-approval-request/1',binding=self.binding,session_id=self.session,pending_nonce=uuid.uuid4().hex,
                   pending_monotonic_ns=now,wall_deadline_monotonic_ns=self.deadline_ns,input_sha256=digest(encoded(request['input'])),native_request=frame,native_raw_utf8=raw.decode('utf-8'))
        name=digest(ident.encode())+'.json';path=self.requests/name
        publish(path,value);stored=path.read_bytes()
        self.pending[ident]=dict(value=value,raw=stored,name=name,queued=False)
        self.event('pending_root_review',request_id=ident,request_path=str(path),request_sha256=digest(stored))
        print(json.dumps(dict(type='approval_pending',request_path=str(path),request_sha256=digest(stored),binding=self.binding)),flush=True)
        return True

    @guarded
    def poll(self):
        self.need(not self.failed,'controller_failed')
        expected={r['name']:ident for ident,r in self.pending.items()}
        for path in self.decisions.iterdir():
            if path.name=='.staging':private_dir(path);continue
            raw,value=read_private(path)
            if path.name in self.seen_decisions:
                self.need(digest(raw)==self.seen_decisions[path.name],'decision_changed_after_consumption');continue
            self.need(path.name in expected,'unknown_or_replayed_decision')
            ident=expected[path.name];pending=self.pending[ident]
            self.need(not pending['queued'],'decision_replayed')
            wanted=decision_for(pending['value'],pending['raw'],value.get('behavior'),self.binding['root_execution_id'],clause=value.get('clause'),review_note=value.get('review_note'))
            self.need(canonical(value)==canonical(wanted),'decision_binding_mismatch')
            self.seen_decisions[path.name]=digest(raw)
            if value['behavior']=='deny':self.denied+=1
            response=dict(behavior='allow',updatedInput=pending['value']['native_request']['request']['input']) if value['behavior']=='allow' else dict(behavior='deny',message='Root denied this exact pending request.',interrupt=True)
            pending['queued']=True
            self.waits[ident]['decision_observed_monotonic_ns']=time.monotonic_ns()
            self.event('root_decision',request_id=ident,decision=value)
            self.enqueue('response',dict(type='control_response',response=dict(subtype='success',request_id=ident,response=response)),ident)

    def close(self,status):
        self.stop(status)
        self.event('closed',status=status,pending_requests=len(self.pending),responses_completed=self.completed)
        publish(self.directory/'summary.json',dict(schema='cairn.native-approval-summary/1',binding=self.binding,status=status,
                failed=self.failed,pending_requests=len(self.pending),responses_completed=self.completed,
                requests=len(self.seen),denied_decisions=self.denied,task_input_bytes_written=self.user_sent,control_bytes_written=self.control_written,
                terminal_observed=self.terminal,controller_started_monotonic_ns=self.started_ns,wall_started_monotonic_ns=self.wall_started_ns,stopped_monotonic_ns=self.stopped_ns,wall_deadline_monotonic_ns=self.deadline_ns,
                total_wait_seconds=self.total_wait_ns/1e9,total_request_wait_seconds=sum(row['wait_seconds'] for row in self.waits.values()),
                wait_scope='complete pending request through final response byte or cancellation/stop; total_wait is union of concurrent intervals; teardown excluded',
                request_waits=[dict(request_id=ident,**row) for ident,row in self.waits.items()]))
        self.journal.close()
        self.closed=True

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('mode',choices=['show','decide']);parser.add_argument('--request',type=Path,required=True)
    parser.add_argument('--clause',choices=['A1','A2','A3','D1','D2','D3']);parser.add_argument('--review-note')
    parser.add_argument('--expected-sha256');parser.add_argument('--behavior',choices=['allow','deny']);parser.add_argument('--root-execution-id')
    args=parser.parse_args()
    if args.mode=='show':
        raw,value=read_private(args.request);print(json.dumps(dict(request_sha256=digest(raw),request=value),indent=2,ensure_ascii=True));return
    if not all((args.expected_sha256,args.behavior,args.root_execution_id,args.clause,args.review_note)):parser.error('exact reviewed hash, per-call behavior and root execution, policy clause and host-only review note required')
    print(decide(args.request,expected_sha256=args.expected_sha256,behavior=args.behavior,root_execution_id=args.root_execution_id,clause=args.clause,review_note=args.review_note))

if __name__=='__main__':main()
