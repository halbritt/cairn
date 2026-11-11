"""Real claimed-inbox processes, exact retained Source, and disposable Unix API."""
import base64
import fcntl
import http.server
import json
import os
from pathlib import Path
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

from test_f04_activation import Fixture, ROOT, SESSION, TURN, encoded, module, scalar_tree, sha, write
from test_f02_inbox_identity import AGENT, EXECUTION, DELIVERY, ATTEMPT

RECORD = '77777777-7777-4777-8777-777777777777'

SOURCE = '''import importlib.util,json,sys,time
from pathlib import Path
spec=importlib.util.spec_from_file_location('source_inbox',sys.argv[1])
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
spec=importlib.util.spec_from_file_location('source_coordination',str(Path(sys.argv[1]).with_name('coordination.py')))
coordination=importlib.util.module_from_spec(spec);spec.loader.exec_module(coordination)
config=json.loads(Path(sys.argv[2]).read_text());frame=json.load(sys.stdin);reports=[]
def renew(attempt):
 delivery=attempt['delivery']; session=attempt['session']
 payload=dict(delivery_id=delivery['delivery_id'],lease_id=delivery['lease_id'],lease_seconds=90)
 data=coordination.call(config,'event-renew',payload,timeout=2,session=session)
 if any(data[k]!=delivery[k] for k in ('delivery_id','lease_id','event')) or data['state']!='leased':
  raise coordination.CoordinationError('STALE_LEASE','inbox recall delivery changed')
try:
 result=bridge.output(config,frame['event'],frame['observation'],frame['state'],frame['control'],
                      revalidate=renew,outer_deadline=time.monotonic()+12,reports=reports)
 response=dict(ok=True,message=result)
except (ValueError,KeyError,TypeError,OSError,coordination.CoordinationError) as exc:
 error=dict(**{'class':type(exc).__name__},message=str(exc),cairn_inbox_refusal=getattr(exc,'cairn_inbox_refusal',{}))
 if isinstance(exc,coordination.CoordinationError):error['code']=exc.code
 if hasattr(exc,'cairn_recall_failure'):error['cairn_recall_failure']=exc.cairn_recall_failure
 response=dict(ok=False,error=error)
print(json.dumps(response,ensure_ascii=False,separators=(',',':')))
'''


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class InboxDeliveryProcesses(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.binary, cls.cli = (Path(cls.build.name)/name for name in ('lifecycle', 'cairn'))
        for target, package in ((cls.binary, './cmd/cairn-lifecycle'), (cls.cli, './cmd/cairn')):
            subprocess.run(['go','build','-buildvcs=false','-o',str(target),package],cwd=ROOT,check=True)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.calls = []
        self.body = 'Implement the selected inbox consumer in internal/lifecycle/inboxipc/run.go. 日本語 😀'
        self.selected = []
        self.optional = []
        self.destination = dict(name='hosted',allow_local=False)
        self.index_refusals = []
        self.history_refusal = None
        self.history_change = None
        self.renew_change = None
        self.expand_refusal = None
        self.expands = 0
        self.meter_refusal = None
        owner = self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(inner):
                request=json.loads(inner.rfile.read(int(inner.headers['Content-Length'])))
                operation=inner.path.removeprefix('/v1/')
                owner.calls.append((operation,request,dict(inner.headers)))
                refusal=None
                if inner.headers.get('Authorization')!='Bearer inbox-fixture-token': inner.send_error(401);return
                if operation=='version':data=dict(preview_capabilities=dict(schema='cairn.preview-capabilities/1',entities_omitted=True))
                elif operation=='index':
                    if owner.index_refusals:refusal=owner.index_refusals.pop(0)
                    optional=[] if request.get('kinds') else owner.optional
                    data=dict(package=dict(receipt_id=SESSION,nonce='',seal='blake3:'+'a'*64,
                        semantic=dict(schema='cairn.semantic/17',mode='index',status='READY',scope=request['scope'],purpose='context',
                        destination=owner.destination,query='sha256:fixture-query',available_tokens=32000,
                        selected=owner.selected,omitted={},index=[n['entry'] for n in optional])),
                        handles=[dict(record_id=n['entry']['record_id'],version=n['entry']['version'],handle='optional-'+str(i)) for i,n in enumerate(optional)],
                        expires_at='2030-01-01T00:00:00Z',credits_remaining=4,bytes_remaining=24000)
                elif operation=='history':
                    refusal=owner.history_refusal
                    data=dict(historical=True,record_id=RECORD,versions=[dict(version=1,payload_available=True,body=owner.body,
                        body_bytes=len(owner.body.encode()),body_sha256=sha(owner.body.encode()))])
                    if owner.history_change:owner.history_change(data)
                elif operation=='event-renew':
                    data=dict(owner.attempt['delivery'],state='leased')
                    if owner.renew_change:owner.renew_change(data)
                elif operation=='expand':
                    if owner.expand_refusal and not request.get('span'):refusal=owner.expand_refusal
                    note=owner.optional[int(request['handle'].removeprefix('optional-'))]
                    owner.expands+=1
                    data=dict(selection=dict(record=dict(record_id=note['entry']['record_id'],version=note['entry']['version'],
                        kind='note',**{'class':'A'},body=note['body']),mandatory=False,reason='candidate',evidence=[],authority=[],conflicts=[]),
                        credits_remaining=max(0,4-owner.expands),bytes_remaining=20000)
                    if request.get('span'):
                        span=request['span'];raw=note['body'].encode();part=raw[span['offset']:span['offset']+span['length']]
                        data['selection']['record']['body']=''
                        data['span']=dict(offset=span['offset'],end=span['offset']+len(part),total_bytes=len(raw),sha256=sha(part),source_sha256=sha(raw))
                        try:data['span']['body']=part.decode()
                        except UnicodeDecodeError:data['span']['body_base64']=base64.b64encode(part).decode()
                elif operation=='recall-observation':data={};refusal=owner.meter_refusal
                else:inner.send_error(400);return
                inner.send_response(422 if refusal else 200);inner.end_headers()
                inner.wfile.write(encoded(dict(schema='cairn.response/1',ok=not bool(refusal),status=refusal or 'OK',
                    **({'message':'fixture refusal'} if refusal else {'data':data}))))
        self.server=Server(str(self.root/'api.sock'),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close_server)
        self.configure()

    def close_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

    def configure(self,harness='codex',kind='notice',trace=False,meter=False,compact=False):
        if hasattr(self,'f'):shutil.rmtree(self.f.root)
        self.f=Fixture(self.root/'project',self.binary,harness=harness,compact=compact)
        for config in (self.f.memory,self.f.coord):config.update(cairn=str(self.cli),socket=str(self.root/'api.sock'),token_file=str(self.root/'token'))
        self.f.memory.update(inbox_recall_trace=trace,recall_observations=meter)
        if compact:self.f.memory['context_lifecycle']='compact/1'
        write(self.root/'token',b'inbox-fixture-token')
        self.f.publish_configuration()
        native=TURN if harness=='codex' else 'claude-channel:fixture-prompt'
        if harness=='claude':self.f.event['prompt']='<channel source="cairn-events" native_session_id="'+SESSION+'" agent_id="'+AGENT+'" execution_id="'+EXECUTION+'" delivery_id="'+DELIVERY+'">\n'+self.f.wake()+'\n</channel>'
        self.f.raw=encoded(self.f.event)
        activated=self.f.activation('coordination')
        self.assertEqual((activated.returncode,activated.stdout,activated.stderr),(0,b'{"enabled":true}\n',b''))
        session=dict(agent_id=AGENT,execution_id=EXECUTION)
        self.publication=dict(event_id=TURN,kind=kind,ref=dict(record_id=RECORD,version=1),**{'from':'agent/'+AGENT})
        self.attempt=dict(attempt_id=ATTEMPT,session=session,native_turn_id=native,
            delivery=dict(delivery_id=DELIVERY,lease_id=ATTEMPT,event=self.publication))
        self.frame=dict(event=self.f.event,observation=dict(event='UserPromptSubmit',native_turn_id=native),
            state=dict(agent=session,workspace=str(self.f.root),inbox_attempt=self.attempt),
            control='Cairn fixture control',started_monotonic=time.monotonic())
        directory=Path(self.f.coord['state_dir'])/'inbox';directory.mkdir(parents=True,mode=0o700)
        self.command=directory/(ATTEMPT+'.json')
        write(self.command,encoded(dict(delivery_id=DELIVERY,event_id=TURN,source=self.publication['ref'],
            completion=['exact','complete'],response=['exact','response'],finish_reply=['exact','finish-reply'],
            compact_fallback_control='Cairn compact fixture control')))
        self.calls.clear();self.expands=0

    def candidate(self,operation='recall',frame=None,deadline=None):
        argv=self.f.activation_argv('coordination');argv[1:4]=['lifecycle','inbox',operation]
        if deadline is not None:argv[-1]=str(deadline)
        return subprocess.run(argv,input=encoded(self.frame if frame is None else frame),capture_output=True,
                              cwd=self.f.root,env=self.f.env(),timeout=18)

    def source(self):
        bundle=module('inbox_source_bundle',ROOT/'integrations/lifecycle/lifecycle_bundle.py')
        runtime=self.f.root/'legacy-runtime';bundle.stage_runtime(ROOT/'integrations/lifecycle',runtime)
        original=self.f.binding.read_bytes();legacy=json.loads(original);legacy['schema']='cairn.inbox-recall-binding/2'
        for key,name in (('engine','memory.py'),('bridge','inbox_recall.py')):
            path=runtime/name;legacy[key]=dict(path=str(path),sha256=sha(path.read_bytes()),bundle=bundle.bundle_descriptor(runtime))
        write(self.f.binding,encoded(legacy))
        try:return subprocess.run([sys.executable,'-B','-c',SOURCE,str(ROOT/'integrations/lifecycle/inbox_recall.py'),str(self.f.coord_path)],
            input=encoded(self.frame),capture_output=True,cwd=self.f.root,env=self.f.env(),timeout=18)
        finally:write(self.f.binding,original)

    def state(self):
        result={}
        for path in Path(self.f.memory['state_dir']).iterdir():
            if path.suffix=='.lock' or path.is_dir():continue
            obj=json.loads(path.read_text())
            for grant in obj.get('grants',{}).values():grant.pop('at',None)
            result[path.name]=obj
        return result

    def pair(self):
        directory=Path(self.f.memory['state_dir']);seed={p.name:(p.read_bytes(),p.stat().st_mode&0o777) for p in directory.iterdir()}
        index_refusals=list(self.index_refusals)
        source=self.source();expected=self.state();source_calls=list(self.calls)
        shutil.rmtree(directory);directory.mkdir(mode=0o700)
        for name,(raw,mode) in seed.items():write(directory/name,raw,mode)
        self.calls.clear();self.expands=0;self.index_refusals=index_refusals
        actual=self.candidate()
        self.assertEqual((actual.returncode,actual.stderr),(source.returncode,source.stderr))
        a,b=json.loads(actual.stdout),json.loads(source.stdout)
        for response in (a,b):
            if response.get('error',{}).get('cairn_recall_failure'):
                self.assertGreaterEqual(response['error']['cairn_recall_failure'].pop('deadline_remaining_ms'),0)
        self.assertEqual(a,b)
        self.assertEqual(self.state().keys(),expected.keys())
        for name,body in expected.items():self.assertEqual(scalar_tree(self.state()[name]),scalar_tree(body),name)
        self.assertEqual([c[0] for c in self.calls],[c[0] for c in source_calls])
        for (op,request,headers),(_,want,_) in zip(self.calls,source_calls):
            if op=='index' and not request.get('kinds'):
                self.assertEqual(str(uuid.UUID(request['request_id'])),request.pop('request_id'));self.assertEqual(str(uuid.UUID(want['request_id'])),want.pop('request_id'))
            self.assertEqual(request,want)
            diagnostic=json.loads(base64.urlsafe_b64decode(headers['Cairn-Client-Diagnostics']+'='*(-len(headers['Cairn-Client-Diagnostics'])%4)))
            if op=='event-renew':
                self.assertNotIn('origin',diagnostic)
                self.assertEqual(headers['Cairn-Agent-Id'],AGENT)
                self.assertEqual(headers['Cairn-Execution-Id'],EXECUTION)
            else:
                self.assertIn('origin',diagnostic,(op,diagnostic,a))
                self.assertEqual(diagnostic['origin']['implementation_id'],sha(self.f.binary.read_bytes()))
        return a

    def test_notice_required_only_and_exact_repeat_refusal(self):
        for harness in ('codex','claude'):
            with self.subTest(harness=harness):
                self.configure(harness)
                self.assertTrue(self.pair()['ok'])
                before=self.state();self.calls.clear()
                result=json.loads(self.candidate().stdout)
                self.assertFalse(result['ok']);self.assertEqual(result['error']['cairn_inbox_refusal']['refusal_reason'],'consumed')
                self.assertEqual(self.state(),before);self.assertEqual(self.calls,[])

    def test_checked_request_source_and_empty_optional_recall(self):
        for harness in ('codex','claude'):
            with self.subTest(harness=harness):
                self.configure(harness,'request')
                self.assertIn(self.body,self.pair()['message'])
                self.assertEqual([op for op,_,_ in self.calls],['version','index','history','version','index','event-renew'])

    def test_whole_required_selection_and_compact_finish_command(self):
        self.selected=[dict(record=dict(record_id=RECORD,version=1,body='Mandatory full context.'),mandatory=True,reason='fixture',evidence=[],authority=[])]
        self.configure(kind='request',compact=True)
        result=self.pair();self.assertIn('Mandatory full context.',result['message']);self.assertIn('finish_reply',result['message'])
        self.assertNotIn('"completion"',result['message'])

    def test_oversized_whole_source_is_not_queried_or_truncated(self):
        self.body='Exact source full body. '*1500
        self.configure(kind='request')
        result=self.pair();self.assertTrue(result['ok']);self.assertNotIn(self.body[:100],result['message'])
        self.assertIn('source_exceeds_delivery_budget_read_exact_source',result['message'])
        self.assertEqual([op for op,_,_ in self.calls],['version','index','history','event-renew'])

    def test_changed_source_and_command_identity_refuse_before_grant(self):
        for fault in ('hash','span','context'):
            with self.subTest(fault=fault):
                self.configure(kind='request')
                self.history_change=(lambda d:d['versions'][0].update(body_sha256='0'*64)) if fault=='hash' else (lambda d:d['versions'][0].update(span=dict(offset=0))) if fault=='span' else None
                if fault=='context':v=json.loads(self.command.read_text());v['source']['version']=2;write(self.command,encoded(v))
                result=self.pair();self.assertFalse(result['ok']);self.assertFalse(self.state()[SESSION+'.inbox-recall.json']['grants'])
                self.assertFalse(any(op=='event-renew' for op,_,_ in self.calls))

    def test_materialized_reports_run_only_when_explicitly_flushed_after_locks(self):
        self.configure(kind='request',trace=True,meter=True)
        result=json.loads(self.candidate().stdout);self.assertTrue(result['ok'],result)
        self.assertFalse(any(op=='recall-observation' for op,_,_ in self.calls))
        self.assertEqual(result['meter_report']['search_calls'],2)
        self.assertEqual(result['meter_report']['pull_calls'],0)
        self.assertEqual(result['trace_report']['task_source']['body_sha256'],sha(self.body.encode()))
        report=dict(trace_report=result['trace_report'],meter_report=result['meter_report'],started_monotonic=self.frame['started_monotonic'])
        before=self.state()
        # Reporting has no dependency on either session lock or grant state.
        with (Path(self.f.memory['state_dir'])/(SESSION+'.lock')).open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            flushed=self.candidate('report',report)
        self.assertEqual(json.loads(flushed.stdout),dict(ok=True,trace_status='written',meter_status='recorded'))
        self.assertEqual(self.state(),before)
        traces=list((Path(self.f.memory['state_dir'])/'inbox-recall-traces').glob('*.json'))
        self.assertEqual(len(traces),1);self.assertEqual(traces[0].stat().st_mode&0o777,0o600)
        self.assertNotIn(self.body,traces[0].read_text())
        body=next(body for op,body,_ in self.calls if op=='recall-observation')
        self.assertEqual(body['injected_bytes'],len(result['message'].encode()))
        self.assertEqual(body['method'],'cairn-lifecycle/inbox-recall-meter/1')

    def test_required_search_retries_once_with_same_intent_then_grants(self):
        self.index_refusals=['VERSION_CONFLICT',None]
        result=self.pair();self.assertTrue(result['ok'])
        searches=[request for op,request,_ in self.calls if op=='index']
        self.assertEqual(len(searches),2);self.assertEqual(searches[0],searches[1])
        self.assertEqual(sum(op=='event-renew' for op,_,_ in self.calls),1)

    def test_permanent_required_refusal_does_not_retry_or_grant(self):
        self.index_refusals=['AUTHORITY_DENIED']
        result=self.pair();self.assertFalse(result['ok'])
        self.assertEqual(result['error']['cairn_recall_failure']['recall_attempts'],1)
        self.assertEqual([op for op,_,_ in self.calls],['version','index'])
        self.assertFalse(self.state()[SESSION+'.inbox-recall.json']['grants'])

    def test_history_budget_refusal_keeps_exact_source_workflow(self):
        self.configure(kind='request');self.history_refusal='BUDGET_REFUSED'
        result=self.pair();self.assertTrue(result['ok']);self.assertIn('source_read_budget_refused_read_exact_source',result['message'])
        self.assertEqual(sum(op=='index' for op,_,_ in self.calls),1)

    def test_optional_timeout_keeps_required_output_without_retry(self):
        self.configure(kind='request');self.index_refusals=[None,'TIMEOUT']
        result=self.pair();self.assertTrue(result['ok']);self.assertIn('optional_recall_unavailable',result['message'])
        self.assertEqual(sum(op=='index' for op,_,_ in self.calls),2)

    def test_lease_identity_failure_leaves_no_new_grant(self):
        self.renew_change=lambda d:d.update(lease_id=RECORD)
        result=self.pair();self.assertFalse(result['ok']);self.assertFalse(self.state()[SESSION+'.inbox-recall.json']['grants'])

    def test_new_privacy_marker_returns_control_without_lookup_or_grant(self):
        (self.f.root/'.cairn-no-memory').touch()
        before=self.state();result=self.pair()
        self.assertEqual(result,dict(ok=True,message=self.frame['control']))
        self.assertEqual(self.state(),before);self.assertEqual(self.calls,[])

    def test_unadmitted_wake_is_required_only_without_renewal(self):
        self.frame['state']['inbox_attempt']=None
        result=self.pair();self.assertTrue(result['ok']);self.assertIn('required_only',result['message'])
        self.assertEqual([op for op,_,_ in self.calls],['version','index'])

    def test_required_context_is_whole_or_refused(self):
        self.selected=[dict(record=dict(record_id=RECORD,version=1,body='Required full source. '*600),mandatory=True,reason='fixture',evidence=[],authority=[])]
        result=self.pair();self.assertFalse(result['ok'])
        self.assertEqual(result['error']['cairn_inbox_refusal']['refusal_reason'],'required_context')
        self.assertFalse(self.state()[SESSION+'.inbox-recall.json']['grants'])

    def test_paid_whole_and_span_optional_sources_use_real_receipts(self):
        for span in (False,True):
            with self.subTest(span=span):
                self.configure(kind='request',trace=True,meter=True)
                body='Owner task opening.\n'+'preamble '*180+'Matching source. '+'tail '*140 if span else 'Current small source 日本語 😀'
                offset=1500 if span else 0;length=min(32,len(body.encode())-offset) if span else len(body.encode())
                self.optional=[dict(body=body,entry=dict(record_id=RECORD,version=1,kind='note',**{'class':'A'},
                    summary=body.encode()[offset:offset+length].decode(),body_sha256=sha(body.encode()),summary_span=dict(offset=offset,length=length)))]
                self.expand_refusal='BUDGET_REFUSED' if span else None
                result=json.loads(self.candidate().stdout);self.assertTrue(result['ok'],result)
                rendered=json.loads(result['message'][result['message'].index('{"selected":'):])
                self.assertEqual(rendered['candidate_inspection']['pull_calls'],2 if span else 1)
                self.assertEqual(sum(op=='expand' for op,_,_ in self.calls),2 if span else 1)
                supplied=rendered['candidate_bodies'][0]['response']
                if span:self.assertEqual(supplied['source_extent'],'partial_span')
                else:
                    self.assertNotIn('span',supplied)
                    self.assertEqual(supplied['selection']['record']['body'],body)
                self.assertEqual(result['meter_report']['pull_calls'],2 if span else 1)
                self.assertEqual(len(result['trace_report']['final']['bodies']),1)
                before=self.state();self.calls.clear()
                self.assertFalse(json.loads(self.candidate().stdout)['ok']);self.assertEqual(self.state(),before);self.assertEqual(self.calls,[])

    def test_failed_or_expired_reporting_does_not_change_delivery_or_grants(self):
        self.configure(kind='request',trace=True,meter=True)
        result=json.loads(self.candidate().stdout);self.assertTrue(result['ok'],result)
        before=self.state();self.calls.clear()
        report=dict(trace_report=result['trace_report'],meter_report=result['meter_report'],started_monotonic=time.monotonic()-20)
        flushed=json.loads(self.candidate('report',report).stdout)
        self.assertEqual(flushed,dict(ok=True,trace_status='written',meter_status='unknown'))
        self.assertEqual(self.calls,[]);self.assertEqual(self.state(),before)
        report['started_monotonic']=time.monotonic();self.meter_refusal='API_UNAVAILABLE'
        flushed=json.loads(self.candidate('report',report).stdout)
        self.assertEqual(flushed['meter_status'],'unknown');self.assertEqual(self.state(),before)

    def test_legacy_wire_charge_and_retired_turn_remain_source_equivalent(self):
        for retired in (False,True):
            with self.subTest(retired=retired):
                self.configure()
                path=Path(self.f.memory['state_dir'])/(SESSION+'.memory-budget.json')
                state=json.loads(path.read_text())
                if retired:
                    state['turns']={TURN:dict(limit_bytes=9500,emitted_hook_bytes=0,reserved_native_bytes=0,granted=False),
                                    AGENT:dict(limit_bytes=9500,emitted_hook_bytes=0,reserved_native_bytes=0,granted=False)}
                    state['active_turn_id']=AGENT
                else:state['pending']['emitted_hook_bytes']=1200
                write(path,encoded(state))
                result=self.pair()
                if retired:
                    self.assertFalse(result['ok']);self.assertEqual(result['error']['cairn_inbox_refusal']['refusal_reason'],'retired_turn')
                else:
                    self.assertTrue(result['ok'])
                    grant=self.state()[SESSION+'.memory-budget.json']['turns'][TURN]
                    self.assertGreater(grant['emitted_hook_bytes'],2400)
                    self.assertEqual(grant['emitted_hook_bytes'],grant['serialized_hook_bytes'])

    def test_compaction_retains_active_pending_and_unknown_fields(self):
        path=Path(self.f.memory['state_dir'])/(SESSION+'.inbox-recall.json')
        grants={}
        for i in range(80):
            grants[sha(('old-key-'+str(i)).encode())]=dict(identity=dict(native_owner='old-owner-'+str(i)),
                wake_sha256=sha(('old-wake-'+str(i)).encode()),limit_bytes=0,output_bytes=0,native_allowance_bytes=0,
                opaque=dict(large=10**100,unicode='日本語 😀'))
        keep=next(iter(grants));grants[keep]['required']=dict(count=0,selection_sha256='',pending=True)
        write(path,encoded(dict(schema='cairn.inbox-recall-grants/1',session=SESSION,grants=grants,opaque='retained')))
        result=self.pair();self.assertTrue(result['ok'])
        state=self.state()[path.name]
        self.assertEqual(state['opaque'],'retained');self.assertIn(keep,state['grants'])
        self.assertGreater(state['folded']['count'],0);self.assertTrue(state['folded']['tokens'])
        self.assertEqual(state['grants'][keep]['opaque']['large'],10**100)

    def test_folded_identity_and_missing_initialized_ledger_refuse_without_lookup(self):
        for missing in (False,True):
            with self.subTest(missing=missing):
                self.configure()
                directory=Path(self.f.memory['state_dir']);path=directory/(SESSION+'.inbox-recall.json')
                write(directory/(SESSION+'.inbox-recall.initialized'),encoded(dict(schema='cairn.inbox-recall-initialized/1',session=SESSION)))
                if not missing:
                    token=sha(('cairn.inbox-recall-fold/1\0wake\0'+sha(self.f.event['prompt'].encode())).encode())[:16]
                    write(path,encoded(dict(schema='cairn.inbox-recall-grants/1',session=SESSION,grants={},folded=dict(count=1,tokens=[token]))))
                result=self.pair();self.assertFalse(result['ok'])
                self.assertEqual(result['error']['cairn_inbox_refusal']['refusal_reason'],'missing' if missing else 'consumed')
                self.assertEqual(self.calls,[])

    def test_compact_notice_fallback_preserves_required_whole_output(self):
        self.configure(compact=True)
        self.frame['control']='Long original control. '*600
        result=self.pair();self.assertTrue(result['ok'])
        self.assertTrue(result['message'].startswith('Cairn compact fixture control'))
        self.assertLess(len(result['message'].encode()),9500)

    def test_report_rejects_changed_pins_and_text_bearing_trace(self):
        self.configure(trace=True,meter=True)
        result=json.loads(self.candidate().stdout);self.assertTrue(result['ok'],result)
        report=dict(trace_report=dict(result['trace_report'],body='not selected metadata'),meter_report=None,started_monotonic=time.monotonic())
        before=self.state();self.calls.clear()
        flushed=json.loads(self.candidate('report',report).stdout)
        self.assertEqual(flushed,dict(ok=True,trace_status='unknown',meter_status='not_requested'))
        self.assertFalse((Path(self.f.memory['state_dir'])/'inbox-recall-traces').exists())
        self.assertEqual(self.calls,[]);self.assertEqual(self.state(),before)
        # Reuse the old independent receipt/argv selection while changing data.
        argv=self.f.activation_argv('coordination');argv[1:4]=['lifecycle','inbox','report']
        write(self.f.memory_path,encoded(dict(self.f.memory,recall_observations=False)))
        failed=subprocess.run(argv,input=encoded(report),capture_output=True,cwd=self.f.root,env=self.f.env(),timeout=18)
        self.assertFalse(json.loads(failed.stdout)['ok']);self.assertEqual(self.calls,[]);self.assertEqual(self.state(),before)

    def test_memory_lock_contention_is_bounded_and_does_not_lookup(self):
        before=self.state();started=time.monotonic()
        with (Path(self.f.memory['state_dir'])/(SESSION+'.lock')).open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            result=json.loads(self.candidate().stdout)
        self.assertFalse(result['ok']);self.assertEqual(result['error']['class'],'BlockingIOError')
        self.assertEqual(result['error']['errno'],11)
        self.assertLess(time.monotonic()-started,6)
        self.assertEqual(self.state(),before);self.assertEqual(self.calls,[])

    def test_inbox_filesystem_failure_before_lookup_cannot_create_a_grant(self):
        path=Path(self.f.memory['state_dir'])/(SESSION+'.inbox-recall.json');path.mkdir()
        result=json.loads(self.candidate().stdout)
        self.assertFalse(result['ok']);self.assertEqual(result['error']['class'],'OSError')
        self.assertEqual(result['error']['errno'],21)
        self.assertEqual(self.calls,[]);self.assertTrue(path.is_dir())

    def test_original_expired_deadline_refuses_before_state_or_api(self):
        before=self.state()
        result=json.loads(self.candidate(deadline=time.monotonic()-1).stdout)
        self.assertFalse(result['ok']);self.assertEqual(self.calls,[]);self.assertEqual(self.state(),before)


if __name__=='__main__':unittest.main()
