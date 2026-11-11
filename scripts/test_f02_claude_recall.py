"""Actual Source and checked Go Claude recall with a disposable Unix API."""
import copy
import base64
import fcntl
import http.server
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import unittest
import uuid

import test_f02_checked_ordinary as ordinary
import test_f02_bound_memory as bound


class ClaudeUnboundRecallTests(unittest.TestCase):
    setUpClass = classmethod(ordinary.CheckedOrdinaryTests.setUpClass.__func__)
    tearDownClass = classmethod(ordinary.CheckedOrdinaryTests.tearDownClass.__func__)
    setUp = ordinary.CheckedOrdinaryTests.setUp
    close = ordinary.CheckedOrdinaryTests.close
    source = ordinary.CheckedOrdinaryTests.source
    go = ordinary.CheckedOrdinaryTests.go
    go_vector = ordinary.CheckedOrdinaryTests.go_vector
    selected_vector = ordinary.CheckedOrdinaryTests.selected_vector
    go_launcher = ordinary.CheckedOrdinaryTests.go_launcher
    state = ordinary.CheckedOrdinaryTests.state
    overwrite = ordinary.CheckedOrdinaryTests.overwrite
    repin = ordinary.CheckedOrdinaryTests.repin

    def claude(self, name, prompt):
        for config, path in ((self.f.memory, self.f.memory_path), (self.f.coord, self.f.coord_path)):
            config['harness'] = 'claude'
            self.overwrite(path, ordinary.encoded(config))
        self.repin()
        self.f.event.update(hook_event_name=name, session_id='11111111-1111-4111-8111-111111111111',
                            prompt=prompt, prompt_id='native-fixture-prompt')
        self.f.raw = ordinary.encoded(self.f.event)

    def pair(self):
        rows = []
        for launch in (self.source, self.go):
            shutil.rmtree(self.f.memory['state_dir'], ignore_errors=True)
            self.calls.clear()
            result = launch()
            calls = copy.deepcopy(self.calls)
            for operation, body, headers in calls:
                if operation != 'version':
                    identity=body.pop('request_id')
                    self.assertEqual(str(uuid.UUID(identity)),identity)
                self.assertEqual(headers['Authorization'],'Bearer checked-ordinary-synthetic-token')
                for key in ('elapsed_ms', 'search_ms', 'pulls_ms', 'selector_ms'):
                    if operation == 'recall-observation':
                        self.assertGreaterEqual(body.pop(key), 0)
            rows.append(dict(exit=result.returncode, stdout=result.stdout.decode(),
                             stderr=result.stderr.decode(), state=self.state(),
                             requests=[(op, body) for op, body, _ in calls]))
        print('CLAUDE_UNBOUND_PAIR='+json.dumps(dict(event=self.f.event['hook_event_name'],
              source=rows[0], go=rows[1]), sort_keys=True), flush=True)
        self.assertEqual(rows[1], rows[0])
        return rows[1]

    def test_start_and_prompt_reach_real_checked_recall_body(self):
        for name in ('SessionStart', 'UserPromptSubmit'):
            with self.subTest(event=name):
                self.claude(name, 'Inspect the current retention policy.')
                row = self.pair()
                self.assertEqual((row['exit'], row['stderr']), (0, ''))
                self.assertEqual([op for op, _ in row['requests']], ['version', 'index'])

    def test_start_and_prompt_query_refusal_keeps_exact_public_diagnostics(self):
        for name in ('SessionStart', 'UserPromptSubmit'):
            with self.subTest(event=name):
                self.claude(name, 'Inspect "alpha\x00beta" policy.')
                row = self.pair()
                self.assertEqual((row['exit'], row['stdout']), (1, ''))
                self.assertEqual(row['requests'], [])
                self.assertEqual(row['stderr'], 'Cairn lifecycle diagnostic: phase=handle error_class=ValueError\n'
                    'Cairn lifecycle: invalid lifecycle input or unavailable local file; use native tools or an explicit handoff.\n')


class ClaudeBoundCase(bound.BoundMemoryCase):
    def setUp(self):
        super().setUp()
        for config, path in ((self.f.memory, self.f.memory_path), (self.f.coord, self.f.coord_path)):
            config['harness'] = 'claude'
            self.overwrite(path, bound.encoded(config))
        self.repin_profiles()
        self.event('UserPromptSubmit', prompt_id='native-fixture-prompt', prompt='Inspect current retention policy.')

    def pair(self):
        directory = Path(self.f.memory['state_dir'])
        seed = {p.name:p.read_bytes() for p in directory.iterdir()} if directory.exists() else {}
        rows = []
        for label, launch in (('source', self.source), ('go', self.go)):
            self.reset_state(seed)
            self.calls.clear();self.report_locks.clear()
            self.before_run(label)
            result = launch()
            self.after_run()
            requests = []
            for op, body, headers in copy.deepcopy(self.calls):
                if op != 'version':
                    identity = body['request_id']
                    self.assertEqual(str(uuid.UUID(identity)), identity)
                    if not getattr(self,'controlled_ids',False):body.pop('request_id')
                if op in ('recall-observation', 'capture-observation'):
                    for key in ('duration_ms', 'elapsed_ms', 'search_ms', 'pulls_ms', 'selector_ms'):
                        if key in body:self.assertGreaterEqual(body.pop(key), 0)
                self.assertEqual(headers['Authorization'], 'Bearer bound-memory-synthetic-token')
                token = headers['Cairn-Client-Diagnostics']
                origin = json.loads(base64.urlsafe_b64decode(token+'='*(-len(token)%4)))['origin']
                self.assertEqual(origin['component'], 'lifecycle-memory')
                self.assertEqual(origin['basis'], 'reported')
                if label == 'go':self.assertEqual(origin['implementation_id'], self.f.receipt['binary']['sha256'])
                else:self.assertNotEqual(origin.get('implementation_id'), self.f.receipt['binary']['sha256'])
                requests.append((op, body))
            rows.append(dict(exit=result.returncode, stdout=result.stdout.decode(), stderr=result.stderr.decode(),
                             state=self.state(), requests=requests, locks=list(self.report_locks)))
        print('CLAUDE_BOUND_PAIR='+json.dumps(dict(event=self.f.event['hook_event_name'],
              source=rows[0], go=rows[1]), sort_keys=True), flush=True)
        self.assertEqual(rows[1], rows[0])
        self.assertTrue(all(all(lock.values()) for lock in rows[1]['locks']))
        return result

    def state(self):
        directory=Path(self.f.memory['state_dir']);result={}
        if not directory.exists():return result
        for path in directory.iterdir():
            if path.suffix=='.lock':continue
            if path.is_dir():result[path.name]=dict(filesystem_kind='directory');continue
            try:body=json.loads(path.read_bytes())
            except (ValueError,UnicodeError):result[path.name]=dict(raw_hex=path.read_bytes().hex());continue
            for label in ('last_recall','last_capture'):
                for key in ('at','duration_ms','elapsed_seconds'):
                    measured=body.get(label,{}).pop(key,None)
                    if measured is not None:self.assertGreaterEqual(measured,0)
            result[path.name]=body
        return result

    def required(self, body='Required current guard. 日本語 🛡.'):
        self.selected = [dict(record=dict(record_id='55555555-5555-4555-8555-555555555555',
                              version=1, body=body), mandatory=True, reason='required', evidence=[], authority=[])]


class ClaudeBoundRecallTests(ClaudeBoundCase):

    def test_bound_start_then_prompt_allocates_and_repeated_owner_does_not_refund(self):
        self.required()
        self.event('SessionStart', source='startup', prompt='')
        self.assertEqual(self.pair().returncode, 0)
        self.event('UserPromptSubmit', prompt_id='first-prompt', prompt='Inspect current retention policy.')
        self.assertEqual(self.pair().returncode, 0)
        before=self.state()[bound.SESSION+'.inbox-recall.json']
        grant=before['grants'][before['active']]
        self.assertGreater(grant['output_bytes'], 0)
        self.assertGreater(grant['native_allowance_bytes'], 0)
        refreshed=self.pair()
        self.assertNotIn(b'candidate_bodies', refreshed.stdout)
        after=self.state()[bound.SESSION+'.inbox-recall.json']
        self.assertEqual(len(after['grants']), 1)
        self.assertEqual(after['grants'][after['active']]['output_bytes'], grant['output_bytes'])
        self.assertEqual(after['grants'][after['active']]['native_allowance_bytes'], grant['native_allowance_bytes'])

    def test_claude_required_start_refuses_exit_one_and_prompt_exit_two(self):
        self.required('Required guard '+('context '*2000))
        for name, code in (('SessionStart', 1), ('UserPromptSubmit', 2)):
            with self.subTest(event=name):
                self.reset_state({})
                self.event(name, prompt_id='first-prompt', prompt='Inspect current retention policy.')
                result=self.pair()
                self.assertEqual((result.returncode, result.stdout), (code, b''))
                self.assertIn(b'RequiredContextRefused', result.stderr)
                state=self.state()[bound.SESSION+'.inbox-recall.json']
                grant=state['grants'][state['active']] if state.get('active') else state
                self.assertTrue(grant['required']['pending'])

    def test_launcher_task_reuses_one_allowance_across_distinct_prompt_owners(self):
        self.f.memory['recall_task_key']='12345678-1234-4234-8234-123456789abc'
        self.overwrite(self.f.memory_path,bound.encoded(self.f.memory));self.repin_profiles()
        self.required()
        self.assertEqual(self.pair().returncode,0)
        before=self.state()[bound.SESSION+'.inbox-recall.json']
        self.event('UserPromptSubmit',prompt_id='second-prompt',prompt='Continue current retention policy.')
        self.assertEqual(self.pair().returncode,0)
        after=self.state()[bound.SESSION+'.inbox-recall.json']
        self.assertEqual(len(after['grants']),1)
        self.assertEqual(after['grants'][after['active']]['output_bytes'],before['grants'][before['active']]['output_bytes'])

    def test_search_failure_keeps_required_pending_and_next_success_does_not_refund(self):
        self.required();self.assertEqual(self.pair().returncode,0)
        before=self.state()[bound.SESSION+'.inbox-recall.json']
        self.refusal='API_UNAVAILABLE'
        self.event('UserPromptSubmit',prompt_id='second-prompt',prompt='Inspect another current requirement.')
        self.assertEqual(self.pair().returncode,2)
        self.refusal=None
        self.assertEqual(self.pair().returncode,0)
        after=self.state()[bound.SESSION+'.inbox-recall.json']
        self.assertEqual(len(after['grants']),2)
        self.assertEqual(after['grants'][before['active']]['output_bytes'],before['grants'][before['active']]['output_bytes'])

    def test_initialized_marker_prevents_missing_ledger_refund(self):
        self.assertEqual(self.pair().returncode,0)
        directory=Path(self.f.memory['state_dir'])
        (directory/(bound.SESSION+'.inbox-recall.json')).unlink()
        result=self.pair()
        self.assertEqual((result.returncode,result.stdout),(1,b''))
        self.assertIn(b'ValueError',result.stderr)
        self.assertFalse((directory/(bound.SESSION+'.inbox-recall.json')).exists())

    def test_missing_prompt_owner_delivers_required_without_optional_allowance(self):
        self.required()
        self.event('UserPromptSubmit',prompt='Inspect current retention policy.')
        self.assertEqual(self.pair().returncode,0)
        state=self.state()[bound.SESSION+'.inbox-recall.json']
        self.assertEqual(state['grants'],{})
        self.assertGreater(state['pending_bytes'],0)

    def test_busy_session_preserves_read_only_required_grant(self):
        self.required();self.assertEqual(self.pair().returncode,0)
        before=self.state()[bound.SESSION+'.inbox-recall.json']
        def hold(side):
            path=Path(self.f.memory['state_dir'])/(bound.SESSION+'.lock')
            self.held=path.open('a');fcntl.flock(self.held,fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.prepare_run=hold
        self.after_run=lambda:self.held.close()
        self.assertEqual(self.pair().returncode,2)
        self.assertEqual(self.state()[bound.SESSION+'.inbox-recall.json'],before)
        self.assertEqual(self.calls,[])

    def test_final_state_replace_failure_keeps_required_pending(self):
        self.required();self.assertEqual(self.pair().returncode,0)
        def collide():
            path=Path(self.f.memory['state_dir'])/(bound.SESSION+'.json')
            path.unlink();path.mkdir()
        self.index_hook=collide
        result=self.pair()
        self.assertEqual(result.returncode,2)
        state=self.state()[bound.SESSION+'.inbox-recall.json']
        self.assertTrue(state['grants'][state['active']]['required']['pending'])

    def test_malformed_prompt_is_a_stable_safe_go_refusal(self):
        self.event('UserPromptSubmit',prompt_id='first-prompt',prompt='Inspect "alpha\ud800beta" policy.')
        source=self.source();source_calls=list(self.calls)
        self.reset_state({});self.calls.clear()
        go=self.go()
        print('CLAUDE_CLASS_B_PROMPT='+json.dumps(dict(source_exit=source.returncode,source_stderr=source.stderr.decode(),
              source_operations=[op for op,_,_ in source_calls],go_exit=go.returncode,go_stderr=go.stderr.decode())),flush=True)
        self.assertEqual((go.returncode,go.stdout),(1,b''))
        self.assertEqual(go.stderr,b'Cairn lifecycle diagnostic: phase=handle error_class=HookError\n'
                         b'Cairn lifecycle: invalid memory hook input; use native tools or an explicit handoff.\n')
        self.assertEqual(self.calls,[])

    def test_public_invocation_trace_matches_success_and_refusals(self):
        directory=self.f.memory_path.parent/'invocation-trace';directory.mkdir(mode=0o700)
        trace=directory/'trace.jsonl'
        for kind in ('success','optional_error','required_start','required_prompt'):
            with self.subTest(kind=kind):
                self.reset_state({});trace.unlink(missing_ok=True)
                self.selected=[];self.refusal='API_UNAVAILABLE' if kind=='optional_error' else None
                name='SessionStart' if kind=='required_start' else 'UserPromptSubmit'
                self.event(name,prompt_id='trace-prompt',prompt='Inspect current retention policy.')
                if kind.startswith('required_'):self.required('Required guard '+('context '*2000))
                self.pair()
                rows=[json.loads(line) for line in trace.read_text().splitlines()]
                self.assertEqual([row['kind'] for row in rows],['entry','parsed','exit']*2)
                def stable(row):
                    row=dict(row);identity=row.pop('inv')
                    self.assertEqual(len(identity),32);self.assertGreaterEqual(int(identity,16),0)
                    for key in ('at','duration_ms','pid','ppid'):
                        if key in row:self.assertGreaterEqual(row.pop(key),0)
                    return row
                self.assertEqual([stable(row) for row in rows[3:]],[stable(row) for row in rows[:3]])



class ClaudeOptionalRecallTests(ClaudeBoundCase):
    """Control only UUID entropy; the actual checked Go command stays intact.

    Optional context contains request IDs, so these pairs retain them literally.
    The independent Python public main uses the retained /2 Source closure;
    the ordinary/bound tests above separately qualify its installed frontend.
    """
    controlled_ids=True

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        root=Path(cls.build.name)
        init=root/'uuid.go'
        init.write_text('''package main
import "github.com/google/uuid"
type claudeFixtureSequence struct{ n byte }
func(s *claudeFixtureSequence) Read(p []byte)(int,error){for i:=range p{p[i]=s.n};s.n++;return len(p),nil}
func init(){uuid.SetRand(&claudeFixtureSequence{})}
''')
        overlay=root/'overlay.json'
        overlay.write_text(json.dumps(dict(Replace={str(bound.ROOT/'cmd/cairn-lifecycle/zz_claude_fixture_uuid.go'):str(init)})))
        subprocess.run(['go','build','-buildvcs=false','-overlay',str(overlay),'-o',str(cls.binary),'./cmd/cairn-lifecycle'],
                       cwd=bound.ROOT,check=True)
        cls.binary.chmod(0o500)

    def source(self, **kwargs):
        root=self.root/'retained-source'
        # Stage the unchanged complete Source bundle through its installed helper.
        from test_f04_activation import module
        loader=module('claude_optional_source_bundle',bound.ROOT/'integrations/lifecycle/lifecycle_bundle.py')
        loader.stage_runtime(bound.ROOT/'integrations/lifecycle',root)
        descriptor=loader.bundle_descriptor(root)
        old=self.f.binding.read_bytes();manifest=json.loads(old)
        manifest.update(schema='cairn.inbox-recall-binding/2')
        for key,name in (('bridge','inbox_recall.py'),('engine','memory.py')):
            manifest[key]=dict(path=str(root/name),sha256=bound.sha((root/name).read_bytes()),bundle=descriptor)
        self.overwrite(self.f.binding,bound.encoded(manifest))
        script='''import importlib.util,sys,uuid
from unittest.mock import patch
spec=importlib.util.spec_from_file_location("claude_source",sys.argv[1])
hook=importlib.util.module_from_spec(spec);spec.loader.exec_module(hook)
sys.argv=[sys.argv[1],"--config",sys.argv[2]]
counter=0
def next_uuid():
 global counter
 result=uuid.UUID(bytes=bytes([counter])*16,version=4);counter+=1;return result
original=hook.Memory.search
def search(self,*args,**kwargs):
 kwargs["request_id"]=str(next_uuid())
 result=original(self,*args,**kwargs)
 for entry in result.get("index",[]):entry["pull_arguments"]["request_id"]=str(next_uuid())
 return result
with patch.object(hook.Memory,"search",search),patch.object(hook.uuid,"uuid4",next_uuid):
 raise SystemExit(hook.main())
'''
        try:
            return subprocess.run([sys.executable,'-I','-B','-c',script,str(root/'memory.py'),str(self.f.memory_path)],
                                  input=self.f.raw,cwd=self.f.root,env=self.f.env(),capture_output=True,timeout=18)
        finally:self.overwrite(self.f.binding,old)

    def setUp(self):
        super().setUp()
        self.server.shutdown();self.server.server_close();self.thread.join()
        (self.root/'api.sock').unlink()
        self.optional=[];self.whole_refusal=None;self.transform=None;self.confirmed_expands=0
        self.search_status='READY';self.search_discovery=None
        owner=self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                op=self.path.removeprefix('/v1/')
                owner.calls.append((op,body,dict(self.headers)))
                if self.headers.get('Authorization')!='Bearer bound-memory-synthetic-token':self.send_error(401);return
                failure=None
                if op=='version':data=dict(preview_capabilities=dict(schema='cairn.preview-capabilities/1',entities_omitted=True))
                elif op=='index':
                    failure=owner.refusal
                    semantic=dict(schema='cairn.semantic/17',mode='index',status=owner.search_status,scope=body['scope'],
                                  purpose='context',destination=dict(name='hosted',allow_local=False),query='sha256:fixture-query',
                                  available_tokens=32000,selected=owner.selected,omitted={},index=[x['entry'] for x in owner.optional])
                    if owner.search_discovery is not None:semantic['discovery']=owner.search_discovery
                    data=dict(package=dict(receipt_id='11111111-1111-4111-8111-111111111111',nonce='',seal='blake3:'+'a'*64,semantic=semantic),
                              handles=[dict(record_id=x['entry']['record_id'],version=x['entry']['version'],handle='optional-'+str(i)) for i,x in enumerate(owner.optional)],
                              expires_at='2030-01-01T00:00:00Z',credits_remaining=4,bytes_remaining=24000)
                elif op=='expand':
                    failure=owner.whole_refusal if not body.get('span') else None
                    item=owner.optional[int(body['handle'].removeprefix('optional-'))];entry=item['entry']
                    if not failure:owner.confirmed_expands+=1
                    data=dict(selection=dict(record=dict(record_id=entry['record_id'],version=entry['version'],kind=entry['kind'],
                              **{'class':entry['class']},body=item['body']),mandatory=False,reason='candidate',evidence=[],authority=[],conflicts=entry.get('conflicts',[])),
                              credits_remaining=max(0,4-owner.confirmed_expands),bytes_remaining=20000)
                    if body.get('span'):
                        span=body['span'];raw=item['body'].encode();part=raw[span['offset']:span['offset']+span['length']]
                        data['selection']['record']['body']=''
                        data['span']=dict(offset=span['offset'],end=span['offset']+len(part),total_bytes=len(raw),sha256=bound.sha(part),
                                          source_sha256=bound.sha(raw),body=part.decode())
                    if owner.transform:owner.transform(body,data)
                elif op=='recall-observation':owner.observe_locks(op);data={}
                else:self.send_error(400);return
                self.send_response(422 if failure else 200);self.end_headers()
                response=dict(schema='cairn.response/1',ok=not failure,status=failure or 'OK')
                response.update(dict(message='fixture refusal') if failure else dict(data=data))
                self.wfile.write(bound.encoded(response))
        self.server=bound.Server(str(self.root/'api.sock'),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()

    def before_run(self,side):
        self.confirmed_expands=0
        super().before_run(side)

    def note(self,body='Current retention policy. 日本語 😀',offset=0,length=24):
        raw=body.encode();length=min(length,len(raw)-offset)
        entry=dict(record_id=str(uuid.UUID(int=len(self.optional)+5,version=4)),version=3,kind='note',
                   **{'class':'A'},summary=raw[offset:offset+length].decode(),body_sha256=bound.sha(raw),summary_span=dict(offset=offset,length=length))
        self.optional.append(dict(entry=entry,body=body))
        return entry

    def context(self,result):
        self.assertEqual(result.returncode,0,result.stderr)
        text=json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
        return json.loads(text[text.index('{"selected"'):])

    def test_current_whole_candidate_and_exact_request_ids(self):
        self.note()
        context=self.context(self.pair())
        self.assertEqual(context['candidate_inspection']['pull_calls'],1)
        self.assertEqual(context['candidate_bodies'][0]['response']['selection']['record']['body'],self.optional[0]['body'])

    def test_changed_source_bytes_are_not_delivered(self):
        self.note();self.transform=lambda request,response:response['selection']['record'].update(body='Changed bytes.')
        context=self.context(self.pair())
        self.assertEqual(context['candidate_bodies'],[])

    def test_whole_refusal_uses_checked_span_without_credit_refund(self):
        self.note('Owner opening.\n'+'preamble '*180+'Current retention source. '+'tail '*140,offset=1500,length=32)
        self.whole_refusal='BUDGET_REFUSED'
        context=self.context(self.pair())
        self.assertEqual(context['candidate_bodies'][0]['response']['source_extent'],'partial_span')
        self.assertGreaterEqual(context['candidate_inspection']['pull_calls'],2)

    def test_shared_receipt_never_spends_more_than_four_expansions(self):
        for i in range(5):self.note('Current source '+str(i)+' with bounded retention policy.')
        context=self.context(self.pair())
        self.assertLessEqual(context['candidate_inspection']['pull_calls'],4)
        self.assertLessEqual(sum(op=='expand' for op,_,_ in self.calls),4)

    def test_degraded_search_remains_success_and_optional_escape_is_failure(self):
        self.search_status='DEGRADED_NO_EMBEDDINGS'
        self.assertEqual(self.pair().returncode,0)
        self.reset_state({});self.refusal='API_UNAVAILABLE'
        result=self.pair()
        self.assertEqual((result.returncode,result.stdout),(1,b''))
        self.assertEqual(len(result.stderr.splitlines()),2)

    def test_competing_sources_are_delivered_whole_or_left_as_previews(self):
        first=self.note('First current source.\n',length=20)
        second=self.note('Second current source.\n',length=20)
        conflict=dict(id='77777777-7777-4777-8777-777777777777',version=1,
                      members=[dict(record_id=entry['record_id'],version=entry['version']) for entry in (first,second)])
        first['conflicts']=second['conflicts']=[conflict]
        incomplete=self.context(self.pair())
        self.assertEqual(incomplete['candidate_bodies'],[])
        self.assertEqual(len(incomplete['index']),2)
        self.reset_state({})
        def complete(request,response):
            response['competing']=[dict(record=dict(record_id=second['record_id'],version=3,kind='note',
                                        **{'class':'A'},body=self.optional[1]['body']),mandatory=False,reason='candidate',conflicts=[conflict])]
        self.transform=complete
        context=self.context(self.pair())
        self.assertEqual(context['candidate_inspection']['delivered_records'],2)
        self.assertEqual(context['candidate_inspection']['pull_calls'],1)
        self.assertEqual(context['index'],[])
