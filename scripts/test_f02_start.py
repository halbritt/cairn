"""Class A producer-shaped SessionStart processes against a private Unix API.

The oracle uses retained /2 activation and bypasses only the unused start bridge
loader; its entire memory body, real CLI, rendering and persistence run.
The candidate runs the actual receipt-selected public launcher and checked Go.
"""
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

from test_f04_activation import Fixture, ROOT, SESSION, encoded, oracle, scalar_tree, sha, write


class Server(socketserver.ThreadingMixIn,socketserver.UnixStreamServer):
    allow_reuse_address = True
    daemon_threads = True


class F02StartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.build.name)/'lifecycle'
        cls.cli = Path(cls.build.name)/'cairn'
        for target, package in ((cls.binary, './cmd/cairn-lifecycle'), (cls.cli, './cmd/cairn')):
            subprocess.run(['go','build','-buildvcs=false','-o',str(target),package],cwd=ROOT,check=True)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.f = Fixture(self.root/'project',self.binary)
        self.f.memory.update(cairn=str(self.cli),socket=str(self.root/'api.sock'),token_file=str(self.root/'token'))
        for key in ('cairn','socket','token_file'):
            self.f.coord[key] = self.f.memory[key]
        write(self.root/'token',b'f02-synthetic-token',0o600)
        self.f.event = dict(hook_event_name='SessionStart',source='startup',cwd=str(self.f.root),session_id=SESSION)
        self.f.raw = encoded(self.f.event)
        self.f.publish_configuration()
        self.selected = []
        self.status = 'READY'
        self.refusal = None
        self.checkpoint = None
        self.index_raw = None
        self.index_delay = 0
        self.calls = []
        owner = self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                operation = self.path.removeprefix('/v1/')
                owner.calls.append((operation,body,dict(self.headers)))
                if operation == 'version':
                    data = dict(preview_capabilities=dict(schema='cairn.preview-capabilities/1',entities_omitted=True))
                elif operation == 'index':
                    if owner.index_delay:
                        time.sleep(owner.index_delay)
                    if owner.index_raw is not None:
                        self.send_response(200)
                        self.end_headers()
                        self.wfile.write(owner.index_raw)
                        return
                    if owner.refusal:
                        self.send_response(422)
                        self.end_headers()
                        self.wfile.write(encoded(dict(schema='cairn.response/1',ok=False,status=owner.refusal,message='fixture refusal')))
                        return
                    data = dict(package=dict(receipt_id='11111111-1111-4111-8111-111111111111',nonce='',seal='blake3:'+'a'*64,
                                             semantic=dict(schema='cairn.semantic/17',mode='index',status=owner.status,
                                                           scope=dict(repo='fixture',task_id='codex/'+SESSION,run_id=SESSION),
                                                           purpose='context',destination=dict(name='hosted',allow_local=False),
                                                           query='sha256:fixture-query',available_tokens=32000,
                                                           selected=owner.selected,omitted={},index=[])),
                                handles=[],expires_at='2030-01-01T00:00:00Z',credits_remaining=4,bytes_remaining=24000)
                    if owner.checkpoint and body.get('query') == '"'+owner.checkpoint['title']+'"':
                        record = owner.checkpoint
                        semantic = data['package']['semantic']
                        semantic['page'] = dict(offset=0)
                        semantic['index'] = [dict(record_id=record['id'],version=3,kind='note',**{'class':'A'},
                                                  summary=record['body'],body_sha256=sha(record['body'].encode()))]
                        data['handles'] = [dict(record_id=record['id'],version=3,handle='checkpoint-handle')]
                        semantic['selected'] = []
                elif operation == 'expand':
                    record = owner.checkpoint
                    data = dict(selection=dict(record=dict(record_id=record['id'],version=3,body=record['body'])),
                                credits_remaining=3)
                elif operation == 'recall-observation':
                    data = {}
                else:
                    raise AssertionError(operation)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(encoded(dict(schema='cairn.response/1',ok=True,status='OK',data=data)))
        self.server = Server(str(self.root/'api.sock'),Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def source(self,prefix=(),*,controlled_ids=False):
        script = '''import importlib.util,sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('source_memory',sys.argv[1])
hook=importlib.util.module_from_spec(spec); spec.loader.exec_module(hook)
config=Path(sys.argv[2]); controlled=sys.argv[3]=='controlled'; sys.argv=[sys.argv[1],'--config',str(config)]
with patch.object(hook,'load_inbox_recall',return_value=None):
 if not controlled:
  sys.exit(hook.main())
 # Control only the randomness boundary. The actual CLI, memory body, API,
 # paired arguments and UUIDv5 derivation still execute.
 import uuid
 counter=0
 def next_uuid():
  global counter
  result=uuid.UUID(bytes=bytes([counter])*16,version=4); counter+=1; return result
 original=hook.Memory.search
 def search(self,*args,**kwargs):
  kwargs['request_id']=str(next_uuid())
  result=original(self,*args,**kwargs)
  for entry in result.get('index',[]): entry['pull_arguments']['request_id']=str(next_uuid())
  return result
 with patch.object(hook.Memory,'search',search),patch.object(hook.uuid,'uuid4',next_uuid):
  sys.exit(hook._main(SimpleNamespace(config=config)))
'''
        original = self.f.binding.read_bytes()
        manifest = json.loads(original)
        manifest['schema'] = 'cairn.inbox-recall-binding/2'
        write(self.f.binding,encoded(manifest))
        try:
            return subprocess.run([*prefix,sys.executable,'-B','-c',script,str(ROOT/'integrations/lifecycle/memory.py'),str(self.f.memory_path),'controlled' if controlled_ids else 'normal'],
                                  input=self.f.raw,capture_output=True,cwd=self.f.root,env=self.f.env(),timeout=18)
        finally:
            write(self.f.binding,original)

    def state(self):
        directory = Path(self.f.memory['state_dir'])
        result = {}
        if directory.exists():
            for path in directory.iterdir():
                if path.suffix == '.lock':
                    continue
                try:
                    body = json.loads(path.read_text())
                except (ValueError,UnicodeError):
                    # Corruption fixtures must retain their exact bytes; a
                    # refused read must not repair or refund this file.
                    result[path.name] = path.read_bytes()
                    continue
                if 'last_recall' in body:
                    # Only wall/elapsed measurements differ between the real processes.
                    for key in ('at','duration_ms','elapsed_seconds'):
                        body['last_recall'].pop(key,None)
                result[path.name] = body
        return result

    def pair(self,prefix=()):
        directory = Path(self.f.memory['state_dir'])
        seed = {p.name:(p.read_bytes(),p.stat().st_mode & 0o7777) for p in directory.iterdir()} if directory.exists() else {}
        source = self.source(prefix)
        source_state = self.state()
        source_calls = list(self.calls)
        shutil.rmtree(self.f.memory['state_dir'],ignore_errors=True)
        if seed:
            directory.mkdir(mode=0o700)
            for name,(raw,mode) in seed.items():
                write(directory/name,raw,mode)
        self.calls.clear()
        candidate = subprocess.run([*prefix,str(self.f.launcher('memory'))],input=self.f.raw,capture_output=True,
                                   cwd=self.f.root,env=self.f.env(),timeout=18)
        self.assertEqual((candidate.returncode,candidate.stdout,candidate.stderr),
                         (source.returncode,source.stdout,source.stderr))
        candidate_state = self.state()
        self.assertEqual(candidate_state.keys(),source_state.keys())
        for name in source_state:
            self.assertEqual(scalar_tree(candidate_state[name]),scalar_tree(source_state[name]),name)
        self.assertEqual([c[0] for c in self.calls],[c[0] for c in source_calls])
        for (operation,actual,headers),(_,expected,source_headers) in zip(self.calls,source_calls):
            actual,expected = dict(actual),dict(expected)
            # These are fresh process UUIDs, not persisted mutation identities.
            # Assert their shape and one initiation separately from deterministic fields.
            for request in (actual,expected):
                ident = request.pop('request_id',None)
                if operation != 'version':
                    self.assertEqual(str(uuid.UUID(ident)),ident)
                    self.assertNotEqual(uuid.UUID(ident).int,0)
                else:
                    self.assertIsNone(ident)
            self.assertEqual(actual,expected)
            self.assertEqual(headers['Authorization'],'Bearer f02-synthetic-token')
            declaration = headers.get('Cairn-Client-Diagnostics')
            self.assertIsNotNone(declaration)
            diagnostic = json.loads(base64.urlsafe_b64decode(declaration+'='*(-len(declaration)%4)))
            self.assertEqual(diagnostic['origin']['component'],'lifecycle-memory')
            self.assertEqual(diagnostic['origin']['implementation_id'],sha(self.f.binary.read_bytes()))
            source_declaration = source_headers['Cairn-Client-Diagnostics']
            source_diagnostic = json.loads(base64.urlsafe_b64decode(source_declaration+'='*(-len(source_declaration)%4)))
            self.assertEqual(source_diagnostic['origin']['implementation_id'],sha((ROOT/'integrations/lifecycle/memory.py').read_bytes()))
            self.assertEqual(diagnostic['transport_build'],source_diagnostic['transport_build'])
        return candidate

    def charged_grant(self, *, workstream=None, emitted=1200, reserved=1000):
        directory = Path(self.f.memory['state_dir'])
        directory.mkdir(mode=0o700)
        turn = '33333333-3333-4333-8333-333333333333'
        grant = dict(limit_bytes=9500,emitted_hook_bytes=emitted,reserved_native_bytes=reserved,granted=True,
                     serialized_hook_bytes=emitted,required=dict(count=0,selection_sha256='',pending=False))
        budget = dict(schema='cairn.codex-memory-grants/1',session_id=SESSION,active_turn_id=turn,
                      pending=dict(limit_bytes=9500,emitted_hook_bytes=0,reserved_native_bytes=0,granted=False),turns={turn:grant})
        write(directory/(SESSION+'.memory-budget.json'),encoded(budget))
        write(directory/(SESSION+'.memory-budget.initialized'),encoded(dict(schema=budget['schema'],session_id=SESSION)))
        if workstream:
            write(directory/(SESSION+'.json'),encoded(dict(workstream=workstream,seen={'old':7})))

    def test_empty_start(self):
        self.assertEqual(self.pair().stdout,b'{}\n')

    def test_degraded_empty_start(self):
        self.status = 'DEGRADED_NO_EMBEDDINGS'
        self.assertEqual(self.pair().returncode,0)

    def test_required_whole_start(self):
        self.selected = [dict(record=dict(record_id='22222222-2222-4222-8222-222222222222',version=1,body='Keep 日本語 & exact "qualifiers".'),
                              mandatory=True,reason='fixture',evidence=[],authority=[])]
        self.assertIn(b'hookSpecificOutput',self.pair().stdout)

    def test_required_conflict_stops_codex(self):
        self.refusal = 'OPEN_CONFLICT'
        result = self.pair()
        self.assertEqual(result.returncode,0)
        self.assertEqual(json.loads(result.stdout),{'continue':False,'stopReason':'Bound inbox recall cannot restore required context.'})

    def test_optional_refusal_keeps_both_diagnostics(self):
        self.refusal = 'NOT_FOUND'
        result = self.pair()
        self.assertEqual(result.returncode,1)
        self.assertEqual(len(result.stderr.splitlines()),2)

    def test_consumed_grant_required_refresh(self):
        self.charged_grant()
        self.selected = [dict(record=dict(record_id='22222222-2222-4222-8222-222222222222',version=1,body='Still required.'),
                              mandatory=True,reason='fixture',evidence=[],authority=[])]
        result = self.pair()
        self.assertIn(b'Still required.',result.stdout)

    def test_compact_resume_whole_checkpoint_refresh(self):
        for source in ('compact','resume'):
            with self.subTest(source=source):
                shutil.rmtree(self.f.memory['state_dir'],ignore_errors=True)
                self.calls.clear()
                title = 'Handoff: project / Session checkpoint'
                self.charged_grant(workstream=title)
                self.f.memory['context_lifecycle'] = 'compact/1'
                self.f.event['source'] = source
                self.f.raw = encoded(self.f.event)
                self.f.publish_configuration()
                self.checkpoint = dict(id='55555555-5555-4555-8555-555555555555',title=title,
                                       body=title+'\n\nStatus: unfinished\nContinue the saved task. 日本語')
                result = self.pair()
                self.assertIn(b'Current saved unfinished work',result.stdout)

    def test_malformed_and_empty_api_replies(self):
        for raw in (b'',b'{',b'[]',encoded(dict(schema='cairn.response/1',ok=True,status='OK',data=None))):
            with self.subTest(raw=raw):
                shutil.rmtree(self.f.memory['state_dir'],ignore_errors=True)
                self.calls.clear()
                self.index_raw = raw
                result = self.pair()
                self.assertEqual(result.returncode,1)
                self.assertEqual(result.stdout,b'')
                self.assertEqual(len(result.stderr.splitlines()),2)

    def test_disabled_binding_retains_unbound_accounting(self):
        self.f.publish_configuration(enabled=False)
        self.assertEqual(self.pair().stdout,b'{}\n')

    def test_privacy_marker_skips_without_api_or_state(self):
        (self.f.root/'.cairn-no-memory').touch()
        self.assertEqual(self.pair().stdout,b'{}\n')
        self.assertEqual(self.calls,[])
        self.assertFalse(Path(self.f.memory['state_dir']).exists())

    def test_contended_session_lock_is_a_labelled_failure(self):
        directory = Path(self.f.memory['state_dir'])
        directory.mkdir(mode=0o700)
        with (directory/(SESSION+'.lock')).open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            source = self.source()
            candidate = self.f.public('memory')
        self.assertEqual((candidate.returncode,candidate.stdout,candidate.stderr),
                         (source.returncode,source.stdout,source.stderr))
        self.assertEqual(candidate.returncode,1)
        self.assertEqual(self.calls,[])

    def test_exactly_one_checked_go_exec_and_no_cli_subprocess(self):
        log = self.f.root/'exec.trace'
        result = subprocess.run(['/usr/bin/strace','-f','-qq','-o',str(log),'-s','1024','-e','trace=execve,openat',str(self.f.launcher('memory'))],
                                input=self.f.raw,capture_output=True,cwd=self.f.root,env=self.f.env(),timeout=18)
        self.assertEqual((result.returncode,result.stdout,result.stderr),(0,b'{}\n',b''))
        rows = log.read_text().splitlines()
        executions = [row for row in rows if 'execve("/proc/self/fd/' in row]
        self.assertEqual(len(executions),1)
        self.assertIn('"memory", "recall"',executions[0])
        self.assertFalse(any('execve("'+str(self.cli)+'"' in row for row in rows))
        opens = [row for row in rows if 'openat(' in row and '"'+str(self.f.memory_path)+'"' in row]
        self.assertEqual(len(opens),2,'parent and checked Go each read current config once')

    def test_selected_cell_preserves_invocation_trace(self):
        directory = self.f.root/'invocation-trace'
        directory.mkdir(mode=0o700)
        result = self.f.public('memory')
        self.assertEqual(result.returncode,0)
        records = [json.loads(line) for line in (directory/'trace.jsonl').read_text().splitlines()]
        self.assertEqual([r['kind'] for r in records],['entry','parsed','exit'])
        final = records[-1]
        self.assertEqual({key:final[key] for key in ('phase','stage','outcome','bound','context_bytes','main_return_code')},
                         dict(phase='handle',stage='saved',outcome='engine_completed',bound=True,context_bytes=0,main_return_code=0))

    def test_owner_prompt_quotes_files_and_recent_hints(self):
        directory = Path(self.f.memory['state_dir'])
        directory.mkdir(mode=0o700)
        write(directory/(SESSION+'.json'),encoded(dict(hints=dict(files={'recent.go':10000000000.0},
                                                                 searches={'repair':10000000000.0},
                                                                 errors=['KNOWN_FAILURE'],error_at=10000000000.0))))
        self.f.event['prompt'] = 'Please resume `ignore "this quote"` "Current failure" src/config.go résumé Kelvin.'
        self.f.raw = encoded(self.f.event)
        self.pair()
        index = next(body for op,body,_ in self.calls if op == 'index')
        self.assertIn('"Current failure"',index['query'])
        self.assertNotIn('"this quote"',index['query'])
        self.assertEqual([e['name'] for e in index['entities']],['src/config.go','recent.go'])

    def test_recall_meter_reports_actual_operations_and_bytes(self):
        self.f.memory['recall_observations'] = True
        self.f.publish_configuration()
        source = self.source()
        source_meter = next(body for op,body,_ in self.calls if op == 'recall-observation')
        shutil.rmtree(self.f.memory['state_dir'])
        self.calls.clear()
        candidate = self.f.public('memory')
        candidate_meter = next(body for op,body,_ in self.calls if op == 'recall-observation')
        self.assertEqual((candidate.returncode,candidate.stdout,candidate.stderr),(source.returncode,source.stdout,source.stderr))
        for meter in (source_meter,candidate_meter):
            for field in ('elapsed_ms','search_ms','pulls_ms','selector_ms'):
                self.assertIs(type(meter[field]),int)
                self.assertGreaterEqual(meter.pop(field),0)
            ident = meter.pop('request_id')
            self.assertEqual(str(uuid.UUID(ident)),ident)
        self.assertEqual(candidate_meter,source_meter)
        self.assertEqual(candidate_meter['search_calls'],1)
        self.assertEqual(candidate_meter['pull_calls'],0)
        self.assertEqual(candidate_meter['injected_bytes'],0)

    def test_missing_socket_preserves_caught_failure(self):
        self.f.memory['socket'] = str(self.root/'missing.sock')
        self.f.coord['socket'] = self.f.memory['socket']
        self.f.publish_configuration()
        result = self.pair()
        self.assertEqual(result.returncode,1)
        self.assertIn(b'memory command exited 7; operation not confirmed',result.stderr)

    def test_marker_cannot_refund_missing_grant_ledger(self):
        self.charged_grant()
        (Path(self.f.memory['state_dir'])/(SESSION+'.memory-budget.json')).unlink()
        result = self.pair()
        self.assertEqual(result.returncode,1)
        self.assertEqual(result.stdout,b'')
        self.assertIn('initialized',next(name for name in self.state() if name.endswith('.initialized')))

    def test_search_timeout_preserves_both_fixed_diagnostics(self):
        self.index_delay = 6
        result = self.pair()
        self.assertEqual(result.returncode,1)
        self.assertIn(b'command timed out; memory operation not confirmed',result.stderr)

    def test_oversize_required_selection_stops_whole(self):
        self.selected = [dict(record=dict(record_id='22222222-2222-4222-8222-222222222222',version=1,body='x'*6000+'日本語'*1000),
                              mandatory=True,reason='fixture',evidence=[],authority=[])]
        result = self.pair()
        self.assertEqual(result.returncode,0)
        self.assertFalse(json.loads(result.stdout)['continue'])
        budget = self.state()[SESSION+'.memory-budget.json']
        self.assertTrue(budget['pending']['required']['pending'])
        self.assertEqual(budget['pending']['emitted_hook_bytes'],0)

    def test_grant_fsync_failure_never_delivers_success(self):
        result = self.pair(('/usr/bin/strace','-f','-qq','-e','trace=fsync','-e','inject=fsync:error=EIO:when=1','-o',str(self.root/'fault.trace')))
        self.assertEqual(result.returncode,1)
        self.assertEqual(result.stdout,b'')
        self.assertIn(b'error_class=OSError',result.stderr)
        self.assertFalse((Path(self.f.memory['state_dir'])/(SESSION+'.memory-budget.initialized')).exists())

    def test_go_python_go_roundtrip_keeps_native_reservation_and_opaque_state(self):
        initial = self.f.public('memory')
        self.assertEqual((initial.returncode,initial.stdout,initial.stderr),(0,b'{}\n',b''))
        path = Path(self.f.memory['state_dir'])/(SESSION+'.memory-budget.json')
        budget = json.loads(path.read_text())
        opaque = dict(integer=10**120,unicode='日本語',negative_zero=-0.0)
        budget['source_opaque'] = opaque
        oracle.save_codex_ledger(path,budget)
        self.f.event.update(hook_event_name='UserPromptSubmit',turn_id='33333333-3333-4333-8333-333333333333',
                            prompt='Continue the bounded author task.')
        self.f.raw = encoded(self.f.event)
        retained = self.source()
        self.assertEqual((retained.returncode,retained.stderr),(0,b''))
        before = json.loads(path.read_text())
        turn = before['active_turn_id']
        self.assertTrue(before['turns'][turn]['granted'])
        self.assertGreater(before['turns'][turn]['reserved_native_bytes'],0)
        self.f.event.update(hook_event_name='SessionStart',source='compact',prompt='')
        self.f.raw = encoded(self.f.event)
        restored = self.f.public('memory')
        self.assertEqual((restored.returncode,restored.stdout,restored.stderr),(0,b'{}\n',b''))
        after = json.loads(path.read_text())
        self.assertEqual(scalar_tree(after['source_opaque']),scalar_tree(opaque))
        self.assertEqual(after['active_turn_id'],turn)
        for key in ('granted','reserved_native_bytes','emitted_hook_bytes','serialized_hook_bytes'):
            self.assertEqual(after['turns'][turn][key],before['turns'][turn][key])

    def test_corrupt_source_marker_and_ledger_refuse_without_refund(self):
        for target in ('memory-budget.json','memory-budget.initialized'):
            with self.subTest(target=target):
                shutil.rmtree(self.f.memory['state_dir'],ignore_errors=True)
                self.calls.clear()
                self.charged_grant()
                write(Path(self.f.memory['state_dir'])/(SESSION+'.'+target),b'{')
                result = self.pair()
                self.assertEqual(result.returncode,1)
                self.assertIn(b'error_class=HookError',result.stderr)

    def test_privacy_trace_keeps_zero_context_without_saving_state(self):
        directory = self.f.root/'invocation-trace'
        directory.mkdir(mode=0o700)
        (self.f.root/'.cairn-no-memory').touch()
        result = self.pair()
        self.assertEqual(result.stdout,b'{}\n')
        exits = [json.loads(line) for line in (directory/'trace.jsonl').read_text().splitlines() if json.loads(line)['kind']=='exit']
        expected = {k:v for k,v in exits[0].items() if k not in ('inv','at','duration_ms')}
        actual = {k:v for k,v in exits[1].items() if k not in ('inv','at','duration_ms')}
        self.assertEqual(actual,expected)
        self.assertEqual(actual['context_bytes'],0)

    def test_malformed_prompt_or_context_refuses_before_api(self):
        # Class B: safe fixed refusal, without claiming Python exception parity.
        for field,value in (('prompt',{'private':'must not echo'}),('prompt','\ud800must not echo'),('context',None),('context',{'revision':17}),('context',{'revision':'\ud800'})):
            with self.subTest(field=field,value=value):
                self.calls.clear()
                self.f.event.pop('prompt',None)
                self.f.memory.pop('context',None)
                if field=='prompt':
                    self.f.event[field] = value
                else:
                    self.f.memory[field] = value
                self.f.raw = encoded(self.f.event)
                self.f.publish_configuration()
                result = self.f.public('memory')
                self.assertEqual(result.returncode,1)
                self.assertEqual(result.stdout,b'')
                self.assertNotIn(b'must not echo',result.stderr)
                self.assertEqual(self.calls,[])

    def test_custom_unicode_state_directory_is_used(self):
        self.f.memory['state_dir'] = str(self.root/'custom 記録 "quoted" $literal')
        self.f.publish_configuration()
        result = self.pair()
        self.assertEqual(result.stdout,b'{}\n')
        self.assertTrue((Path(self.f.memory['state_dir'])/(SESSION+'.memory-budget.json')).is_file())
        self.assertFalse((self.f.root/'memory-state').exists())

    def test_unset_home_uses_the_same_account_fallback(self):
        environment = self.f.env
        self.f.env = lambda:{k:v for k,v in environment().items() if k!='HOME'}
        self.assertEqual(self.pair().stdout,b'{}\n')

    def test_complete_rpc_request_ids_match_with_controlled_randomness(self):
        build = self.root/'uuid-build'
        build.mkdir()
        for name in ('go.mod','go.sum'):
            shutil.copyfile(ROOT/name,build/name)
        for name in ('internal','core','localapi'):
            shutil.copytree(ROOT/name,build/name)
        package = build/'cmd'/'f02-ids'
        package.mkdir(parents=True)
        (package/'main.go').write_text('''package main
import (
 "os"
 "github.com/google/uuid"
 "github.com/halbritt/cairn/internal/lifecycle/activationipc"
)
type sequence struct { n byte }
func (s *sequence) Read(p []byte) (int,error) {
 for i:=range p { p[i]=s.n }; s.n++; return len(p),nil
}
func main() { uuid.SetRand(&sequence{}); os.Exit(activationipc.Run(os.Args[1:],os.Stdin,os.Stdout,os.Stderr)) }
''')
        helper = build/'ids'
        subprocess.run(['go','build','-buildvcs=false','-o',str(helper),'./cmd/f02-ids'],cwd=build,check=True)
        self.f.binary.chmod(0o700)
        write(self.f.binary,helper.read_bytes(),0o500)
        manifest = json.loads(self.f.binding.read_text())
        manifest['binary']['sha256'] = sha(helper.read_bytes())
        write(self.f.binding,encoded(manifest))
        self.f.receipt['binary'] = manifest['binary']
        title = 'Handoff: project / Request identity checkpoint'
        self.charged_grant(workstream=title)
        self.f.memory.update(context_lifecycle='compact/1',recall_observations=True)
        self.f.event['source'] = 'compact'
        self.f.raw = encoded(self.f.event)
        self.f.publish_configuration()
        self.checkpoint = dict(id='55555555-5555-4555-8555-555555555555',title=title,
                               body=title+'\nStatus: unfinished\nContinue the selected work.')
        directory = Path(self.f.memory['state_dir'])
        seed = {p.name:(p.read_bytes(),p.stat().st_mode & 0o7777) for p in directory.iterdir()}
        source = self.source(controlled_ids=True)
        source_state,source_calls = self.state(),list(self.calls)
        shutil.rmtree(directory)
        directory.mkdir(mode=0o700)
        for name,(raw,mode) in seed.items():
            write(directory/name,raw,mode)
        self.calls.clear()
        candidate = self.f.public('memory')
        self.assertEqual((candidate.returncode,candidate.stdout,candidate.stderr),(source.returncode,source.stdout,source.stderr))
        candidate_state = self.state()
        self.assertEqual(candidate_state.keys(),source_state.keys())
        for name in source_state:
            self.assertEqual(scalar_tree(candidate_state[name]),scalar_tree(source_state[name]))
        for _,_,headers in self.calls:
            declaration = headers['Cairn-Client-Diagnostics']
            diagnostic = json.loads(base64.urlsafe_b64decode(declaration+'='*(-len(declaration)%4)))
            self.assertEqual(diagnostic['origin']['implementation_id'],sha(helper.read_bytes()))
        actual,expected = [],[]
        for calls,output in ((self.calls,actual),(source_calls,expected)):
            for operation,body,headers in calls:
                body = dict(body)
                if operation=='recall-observation':
                    for field in ('elapsed_ms','search_ms','pulls_ms','selector_ms'):
                        self.assertGreaterEqual(body.pop(field),0)
                output.append((operation,body))
        self.assertEqual(actual,expected)  # request_id is retained unchanged
        requests = [body['request_id'] for op,body in actual if 'request_id' in body]
        self.assertEqual(requests[0],'00000000-0000-4000-8000-000000000000')
        self.assertEqual(requests[1],'01010101-0101-4101-8101-010101010101')
        self.assertEqual(uuid.UUID(requests[2]).version,5)
        self.assertEqual(requests[3],'03030303-0303-4303-8303-030303030303')
        self.assertEqual(len(set(requests)),4)
