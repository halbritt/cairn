"""F04 real receipt-selected processes and source activation state, no live API."""
import hashlib
import http.server
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import shlex
import subprocess
import sys
import tempfile
import threading
import socketserver
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SESSION = '11111111-1111-4111-8111-111111111111'
TURN = '22222222-2222-4222-8222-222222222222'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


compat = module('f04_compatibility_source', ROOT / 'integrations/lifecycle/lifecycle_compatibility.py')
oracle = module('f04_activation_oracle', ROOT / 'integrations/lifecycle/grant_ledger.py')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write(path, raw, mode=0o600):
    path.write_bytes(raw)
    path.chmod(mode)


def overwrite_fixture(path, raw):
    """Inject changed bytes into an owned fixture, retaining its selected mode."""
    mode = path.stat().st_mode & 0o777
    path.chmod(mode | 0o200)
    try:
        path.write_bytes(raw)
    finally:
        path.chmod(mode)


def encoded(obj):
    return json.dumps(obj, separators=(',', ':'), ensure_ascii=True).encode()


class Fixture:
    def __init__(self, root, binary, *, enabled=True, harness='codex', compact=False, binary_name='activation'):
        self.root = root
        root.mkdir(mode=0o700)
        (root / '.git').mkdir()
        self.binary = root / 'bin' / binary_name
        self.binary.parent.mkdir(mode=0o700)
        shutil.copyfile(binary, self.binary)
        self.binary.chmod(0o500)
        self.binding = root / 'binding.json'
        self.memory_path, self.coord_path = root / 'memory.json', root / 'coordination.json'
        if compact:
            (root/'bindings').mkdir(mode=0o700)
            self.coord_path = root/'bindings'/'fixture.json'
        self.api = root / 'fixture-api'
        write(self.api, b'''#!/usr/bin/python3
import json,sys,pathlib,time
args=sys.argv[1:]; index=1
if args[0]=='agent':
 while args[index].startswith('--'): index+=2
 op=args[index]
else: op=args[0]
body=sys.stdin.read(); request=json.loads(body or '{}') if args[0]=='agent' else {}
with pathlib.Path(__file__).with_suffix('.calls').open('a') as log: log.write(op+'\\n')
with pathlib.Path(__file__).with_suffix('.jsonl').open('a') as log: log.write(json.dumps(dict(operation=op,argv=args,input=body))+'\\n')
selected=pathlib.Path(__file__).with_suffix('.replies')
replies=json.loads(selected.read_text()) if selected.exists() else {}
if op in replies:
 queue=replies[op]; reply=queue.pop(0) if len(queue)>1 else queue[0]
 if reply.pop('claim_identity',False):
  reply['data']['attempt'].update(attempt_id=request['request_id'],session=request['session'])
 if reply.pop('advance_identity',False) and reply['data']['attempt'] is not None:
  reply['data']['attempt'].update(attempt_id=request['request_id'],session=request['session'])
 time.sleep(reply.pop('delay',0))
 selected.write_text(json.dumps(replies)); print(json.dumps(dict(schema='cairn.response/1',**reply)))
 sys.exit(0 if reply['ok'] else reply.get('exit',4))
data={'status':'READY','destination':{'name':'hosted','allow_local':False},'selected':[],'index':[],'omitted':{}}
if op=='agent-register':
 data=dict(agent_id='33333333-3333-4333-8333-333333333333',execution_id='44444444-4444-4444-8444-444444444444',native_session_id=request['native_session_id'],display_name='agent-fixture',inbox='agent/fixture',context_revision=1,metadata=request['metadata'])
 pathlib.Path(__file__).with_suffix('.agent').write_text(json.dumps(data))
elif op in ('agent-context','agent-heartbeat'):
 data=json.loads(pathlib.Path(__file__).with_suffix('.agent').read_text())
 if op=='agent-context':
  data.update(metadata=request['metadata'],context_revision=request['expected_revision']+1)
  pathlib.Path(__file__).with_suffix('.agent').write_text(json.dumps(data))
elif op in ('event-next','session-inbox-claim'): data={'delivery':None}
elif op=='session-inbox-pending': data={'requests':0,'notices':0,'responses':0}
elif op!='search':
 print(json.dumps({'ok':False,'status':'UNEXPECTED_FIXTURE_OPERATION'}));sys.exit(7)
print(json.dumps({'ok':True,'status':'OK','data':data}))
''', 0o500)
        common = dict(harness=harness, cairn=str(self.api), socket=str(root/'unused.sock'),
                      token_file=str(root/'unread-token'), repo='fixture', inbox_recall_binding=str(self.binding))
        self.memory = dict(common, state_dir=str(root/'memory-state'), recall_mode='agent_tools', context_bytes=9500,
                           recall_observations=False)
        self.coord = dict(common, state_dir=str(root/'coordination-state'), native_delivery=True, binding='fixture')
        if compact:
            self.coord.update(state_dir=str(root/'state'/'fixture'),compact_inbox_version=1,notice_batch_version=1)
        write(self.memory_path, encoded(self.memory))
        write(self.coord_path, encoded(self.coord))
        def descriptor(path): return dict(path=str(path), sha256=sha(path.read_bytes()))
        manifest = dict(schema='cairn.inbox-recall-binding/3', enabled=enabled, binary=descriptor(self.binary),
                        memory_config=descriptor(self.memory_path), coordination_configs=[descriptor(self.coord_path)])
        write(self.binding, encoded(manifest))
        sources = compat.guarded_sources(ROOT/'integrations/lifecycle')
        aliases = {'memory-hook.py':'memory.py', 'coordination-hook.py':'coordination.py'}
        sources.update({name:sources[role] for name, role in aliases.items()})
        hashes = {name:sha(raw) for name, raw in sources.items()}
        identity = sha(json.dumps(hashes, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode())
        snapshot = root / identity
        snapshot.mkdir(mode=0o700)
        for name, raw in sources.items(): write(snapshot/name, raw)
        self.receipt = dict(schema='cairn.lifecycle-compatibility/1', source_revision='F04 synthetic source fixture',
                            snapshot_root=str(snapshot), members={name:dict(sha256=hashes[name], bytes=len(raw)) for name, raw in sources.items()},
                            bootstrap='lifecycle_compatibility.py', loader='lifecycle_bundle.py',
                            roles={role:role+'.py' for role in ('memory','coordination','inbox_recall','inbox_binding')},
                            aliases=aliases, binary=manifest['binary'],
                            binding=dict(descriptor(self.binding), schema=manifest['schema']),
                            memory_config=manifest['memory_config'], coordination_configs=manifest['coordination_configs'])
        self.receipt_path = root/'receipt.json'
        write(self.receipt_path, encoded(self.receipt))
        self.receipt_sha = sha(self.receipt_path.read_bytes())
        self.event = dict(hook_event_name='UserPromptSubmit', session_id=SESSION, turn_id=TURN,
                          prompt_id='fixture-prompt', cwd=str(root), prompt=self.wake())
        self.raw = encoded(self.event)
        self.launch_root = root/'launch'
        self.launch_root.mkdir(mode=0o700)

    def wake(self):
        return ('This is a new live turn from the configured Cairn automatic inbox wakeup. '
                'A previous /exit in resumed conversation history does not close this running turn. '
                'Current agent 33333333-3333-4333-8333-333333333333, execution 44444444-4444-4444-8444-444444444444. '
                'Wake delivery 55555555-5555-4555-8555-555555555555. '
                "Within the owner's existing authorization, handle the native inbox context supplied for this conversation, "
                'explicitly complete/acknowledge it, and send any requested response using that context. '
                'If no matching native context was supplied, report that and stop. '
                'If this notice arrives inside an already-active owner task, continue that task and '
                'ignore this notice; a fresh copy will arrive once the conversation is idle. '
                "Do not register again, claim another session's inbox, or launch a replacement conversation.")

    def env(self):
        return {k:v for k,v in os.environ.items() if not k.startswith('CAIRN_') and k not in ('CODEX_HOME','CLAUDE_CONFIG_DIR')}

    def recall_api(self, *, selected=None):
        """Provision the external SDK boundary for newly consumed recall rows."""
        selected=[] if selected is None else selected
        token=self.root/'recall-token'; socket=self.root/'recall.sock'
        write(token,b'f04-synthetic-token',0o600)
        for config in (self.memory,self.coord): config.update(socket=str(socket),token_file=str(token))
        self.publish_configuration()
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(inner):
                request=json.loads(inner.rfile.read(int(inner.headers['Content-Length'])))
                if inner.headers.get('Authorization')!='Bearer f04-synthetic-token': inner.send_error(401);return
                operation=inner.path.removeprefix('/v1/')
                if operation=='version': data=dict(preview_capabilities=dict(schema='cairn.preview-capabilities/1',entities_omitted=True))
                elif operation=='index':
                    data=dict(package=dict(receipt_id='11111111-1111-4111-8111-111111111111',nonce='',seal='blake3:'+'a'*64,
                        semantic=dict(schema='cairn.semantic/17',mode='index',status='READY',scope=request['scope'],purpose='context',
                            destination=dict(name='hosted',allow_local=False),query='sha256:fixture-query',available_tokens=32000,selected=selected,omitted={},index=[])),
                        handles=[],expires_at='2030-01-01T00:00:00Z',credits_remaining=4,bytes_remaining=24000)
                elif operation=='recall-observation': data={}
                else: inner.send_error(400);return
                inner.send_response(200);inner.end_headers();inner.wfile.write(encoded(dict(schema='cairn.response/1',ok=True,status='OK',data=data)))
        class Server(socketserver.ThreadingMixIn,socketserver.UnixStreamServer): daemon_threads=True
        server=Server(str(socket),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def close(): server.shutdown();server.server_close();thread.join()
        return close

    def activation_argv(self, role='memory'):
        config = self.memory_path if role == 'memory' else self.coord_path
        return [str(self.binary), 'lifecycle', 'binding', 'activation', '--entrypoint', role,
                '--config', str(config), '--config-sha256', sha(config.read_bytes()),
                '--binding-sha256', self.receipt['binding']['sha256'], '--receipt', str(self.receipt_path),
                '--receipt-sha256', self.receipt_sha, '--deadline', str(time.monotonic()+13)]

    def activation(self, role='memory', raw=None):
        return subprocess.run(self.activation_argv(role), input=self.raw if raw is None else raw,
                              capture_output=True, cwd=self.root, env=self.env(), timeout=15)

    def launcher(self, role, timeout=None):
        config = self.memory_path if role == 'memory' else self.coord_path
        vector = compat.launch_vector(sys.executable, self.receipt_path, self.receipt_sha, role,
                                      role+'-hook.py', config, timeout if timeout is not None else (13 if role=='memory' else 15), self.launch_root)
        raw = compat.launcher_bytes(vector)
        directory = self.launch_root/sha(raw)
        directory.mkdir(mode=0o700, exist_ok=True)
        launcher = directory/'inbox'
        if launcher.exists():
            assert launcher.read_bytes()==raw
        else:
            write(launcher, raw, 0o500)
        return launcher

    def public(self, role, timeout=None):
        return subprocess.run([str(self.launcher(role,timeout))], input=self.raw, capture_output=True, cwd=self.root, env=self.env(), timeout=18)

    def shell(self, argv, body=b'', env=None):
        text=shlex.join(argv)
        assert len(text.encode())<=512, text
        return subprocess.run(['/bin/sh','-c',text],input=body,capture_output=True,cwd=self.root,env=env or self.env(),timeout=35)

    def replies(self, values):
        self.api.with_suffix('.replies').write_text(json.dumps(values))

    def api_calls(self):
        path=self.api.with_suffix('.jsonl')
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def publish_configuration(self, *, enabled=None):
        """Stage a new test generation before issuing its independently pinned L."""
        write(self.memory_path,encoded(self.memory))
        write(self.coord_path,encoded(self.coord))
        manifest=json.loads(self.binding.read_text())
        if enabled is not None: manifest['enabled']=enabled
        manifest['memory_config']['sha256']=sha(self.memory_path.read_bytes())
        manifest['coordination_configs'][0]['sha256']=sha(self.coord_path.read_bytes())
        write(self.binding,encoded(manifest))
        for key in ('memory_config','coordination_configs'): self.receipt[key]=manifest[key]
        self.receipt['binding']['sha256']=sha(self.binding.read_bytes())
        write(self.receipt_path,encoded(self.receipt))
        self.receipt_sha=sha(self.receipt_path.read_bytes())


def scalar_tree(obj):
    if type(obj) is dict: return ('object', [(key,scalar_tree(v)) for key,v in obj.items()])
    if type(obj) is list: return ('array', [scalar_tree(v) for v in obj])
    if type(obj) is float: return ('float', 'nan' if math.isnan(obj) else obj.hex())
    return (type(obj).__name__, obj)


class F04ActivationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.build.name)/'activation'
        subprocess.run(['go','build','-buildvcs=false','-o',str(cls.binary),'./cmd/cairn-lifecycle'], cwd=ROOT, check=True)

    @classmethod
    def tearDownClass(cls): cls.build.cleanup()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def fixture(self, name='go', **kwargs): return Fixture(self.root/name, self.binary, **kwargs)

    def test_activation_matches_source_and_preserves_lossless_opaque_state(self):
        for harness in ('codex','claude'):
            with self.subTest(harness=harness):
                f = self.fixture(harness, harness=harness)
                source = self.root/(harness+'-oracle')
                source.mkdir(mode=0o700)
                name = SESSION + ('.inbox-recall.json' if harness=='claude' else '.memory-budget.json')
                state = (dict(schema='cairn.inbox-recall-grants/1', session=SESSION, grants={}) if harness=='claude'
                         else dict(schema='cairn.codex-memory-grants/1', session_id=SESSION, active_turn_id=None, turns={},
                                   pending=dict(limit_bytes=9500,emitted_hook_bytes=0,reserved_native_bytes=0,granted=False)))
                state['opaque'] = dict(big=9007199254740993123456789, negative_zero=-0.0, nan=float('nan'),
                                       unicode='日本語<>&\ud800', true=True)
                target = Path(f.memory['state_dir']); target.mkdir(mode=0o700)
                write(target/name, encoded(state)); write(source/name, encoded(state))
                turn = 'claude-channel:fixture-prompt' if harness=='claude' else TURN
                expected = oracle.activation_decision(dict(f.memory,state_dir=str(source)),harness,SESSION,turn,True)
                result = f.activation()
                self.assertEqual((result.returncode,result.stdout,result.stderr),(0,b'{"enabled":true}\n',b''))
                self.assertTrue(expected)
                self.assertEqual(scalar_tree(json.loads((target/name).read_text())),scalar_tree(json.loads((source/name).read_text())))
                # A second role reads the existing choice without changing state.
                before = (target/name).read_bytes()
                result = f.activation('coordination')
                self.assertEqual((result.returncode,result.stdout,result.stderr),(0,b'{"enabled":true}\n',b''))
                self.assertEqual((target/name).read_bytes(),before)

    def test_initialized_deletion_refuses_without_reset(self):
        f = self.fixture()
        self.assertEqual(f.activation().returncode,0)
        path = Path(f.memory['state_dir'])/(SESSION+'.memory-budget.json')
        path.unlink()
        result = f.activation('coordination')
        self.assertEqual(result.returncode,1)
        self.assertEqual(result.stdout,b'')
        self.assertEqual(json.loads(result.stderr)['error']['class'],'ValueError')
        self.assertFalse(path.exists())

    def test_actual_public_callers_preserve_source_byte_decoding_and_exact_claude_owner(self):
        for role in ('memory','coordination'):
            for codec in ('utf-8-sig','utf-16','utf-16-be','utf-32','utf-32-be','duplicates'):
                with self.subTest(role=role,codec=codec):
                    f=self.fixture(role+codec,harness='claude')
                    owner='日本語-owner'
                    f.event['prompt_id']=owner
                    # Coordination uses the required-only real retained wake.
                    f.event['host_pid']=os.getpid()
                    f.event['prompt']=(f'<channel source="cairn-events" native_session_id="{SESSION}" '
                        'agent_id="33333333-3333-4333-8333-333333333333" execution_id="44444444-4444-4444-8444-444444444444" '
                        'delivery_id="55555555-5555-4555-8555-555555555555">\n'+f.wake()+'\n</channel>')
                    f.raw=(json.dumps(f.event,ensure_ascii=False).encode(codec) if codec!='duplicates' else
                           encoded(f.event).replace(b'"hook_event_name":"UserPromptSubmit"',
                             b'"Hook_Event_Name":"Stop","hook_event_name":"Stop","hook_event_name":"UserPromptSubmit"'))
                    if role=='coordination': self.addCleanup(f.recall_api())
                    result=f.public(role)
                    self.assertEqual((result.returncode,result.stderr),(0,b''),result.stdout)
                    state=json.loads((Path(f.memory['state_dir'])/(SESSION+'.inbox-recall.json')).read_text())
                    self.assertEqual(state['inbox_activation'],{'claude-channel:'+owner:True})

    def test_lone_surrogate_claude_owners_keep_the_source_error_before_any_write(self):
        for role in ('memory','coordination'):
            for owner in ('\ud800','\udcff'):
                with self.subTest(role=role,owner=repr(owner)):
                    f=self.fixture(role+hex(ord(owner)),harness='claude')
                    f.event['prompt_id']=owner;f.raw=encoded(f.event)
                    with self.assertRaises(UnicodeEncodeError): oracle.claude_owner(f.event)
                    result=f.public(role)
                    self.assertEqual((result.returncode,result.stdout),(1 if role=='memory' else 2,b''))
                    self.assertIn(b'phase=activation error_class=ValueError' if role=='memory' else b'phase=activation error_class=OtherError',result.stderr)
                    self.assertFalse(Path(f.memory['state_dir']).exists())
                    self.assertEqual(f.api_calls(),[])

    def test_both_complete_public_frontends_reach_retained_handlers(self):
        for role in ('memory','coordination'):
            for enabled in (False,True):
                with self.subTest(role=role,enabled=enabled):
                    f = self.fixture(role+str(enabled),enabled=enabled)
                    if role=='memory' and not enabled: self.addCleanup(f.recall_api())
                    if role=='coordination':
                        f.event.update(hook_event_name='SessionStart',prompt='',host_pid=os.getpid())
                        f.raw=encoded(f.event)
                    result = f.public(role)
                    calls=f.api.with_suffix('.calls')
                    print('PUBLIC_F04',role,enabled,result.returncode,repr(result.stdout),repr(result.stderr),'api_calls='+repr(calls.read_text() if calls.exists() else ''),flush=True)
                    self.assertEqual((result.returncode,result.stderr),(0,b''))
                    if role=='memory':
                        self.assertEqual(json.loads(result.stdout),{})
                        path=Path(f.memory['state_dir'])/(SESSION+'.memory-budget.json')
                        self.assertEqual(json.loads(path.read_text())['inbox_activation'],{TURN:enabled})
                    else:
                        self.assertIn('agent-fixture',json.loads(result.stdout)['hookSpecificOutput']['additionalContext'])
                        self.assertIn('agent-register',f.api.with_suffix('.calls').read_text().splitlines())

    def test_invalid_code_refuses_before_ledger_even_for_excluded_event(self):
        f=self.fixture()
        member=Path(f.receipt['snapshot_root'])/'memory.py'
        member.chmod(0o600)  # actual installer output is sealed 0400
        write(member,b'raise RuntimeError("unchecked code must never execute")\n')
        f.raw=encoded(dict(f.event,hook_event_name='Stop'))
        result=f.public('memory')
        self.assertEqual(result.returncode,1)
        self.assertEqual(result.stdout,b'')
        self.assertEqual(result.stderr,b'Cairn lifecycle diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n')
        self.assertFalse(Path(f.memory['state_dir']).exists())

    def required_wake(self,name,harness,body):
        f=self.fixture(name,harness=harness)
        f.event['host_pid']=os.getpid()
        if harness=='claude':
            f.event['prompt']=(f'<channel source="cairn-events" native_session_id="{SESSION}" '
                'agent_id="33333333-3333-4333-8333-333333333333" execution_id="44444444-4444-4444-8444-444444444444" '
                'delivery_id="55555555-5555-4555-8555-555555555555">\n'+f.wake()+'\n</channel>')
        f.raw=encoded(f.event)
        result=dict(status='READY',destination=dict(name='hosted',allow_local=False),
                    selected=[dict(mandatory=True,record=dict(body=body))],index=[],omitted={})
        f.replies({'search':[dict(ok=True,status='OK',data=result)]})
        self.addCleanup(f.recall_api(selected=result['selected']))
        return f,f.public('coordination')

    def test_complete_reserved_wake_retains_whole_required_context_at_exact_wire_limit(self):
        for harness in ('codex','claude'):
            with self.subTest(harness=harness):
                # No delivery is admitted by the fixture store: this is the
                # retained required-only wake path, not a fabricated grant.
                probe,result=self.required_wake('probe-'+harness,harness,'R')
                self.assertEqual((result.returncode,result.stderr),(0,b''),result.stdout)
                overhead=len(result.stdout)-1
                allowance=re.search(rb'Automatic exact-source read allowance: (\d+)',result.stdout).group(1)
                maximum=9500-overhead+len(allowance)-1
                self.assertGreater(maximum,1000)
                exact,result=self.required_wake('exact-'+harness,harness,'R'*maximum)
                self.assertEqual((result.returncode,result.stderr),(0,b''),result.stdout)
                self.assertEqual(len(result.stdout),9500)
                self.assertIn('R'*maximum,json.loads(result.stdout)['hookSpecificOutput']['additionalContext'])
                suffix='.inbox-recall.json' if harness=='claude' else '.memory-budget.json'
                activation=json.loads((Path(exact.memory['state_dir'])/(SESSION+suffix)).read_text())
                owner='claude-channel:fixture-prompt' if harness=='claude' else TURN
                self.assertEqual(activation['inbox_activation'],{owner:True})
                grants=json.loads((Path(exact.memory['state_dir'])/(SESSION+'.inbox-recall.json')).read_text())
                self.assertEqual(len(grants['grants']),1)
                grant=next(iter(grants['grants'].values()))
                self.assertEqual(grant['identity']['boundary'],'unadmitted_wake')
                self.assertEqual(grant['output_bytes'],len(result.stdout))
                self.assertLessEqual(grant['output_bytes']+grant['native_allowance_bytes'],9500)
                over,result=self.required_wake('extra-'+harness,harness,'R'*(maximum+1))
                self.assertEqual((result.returncode,result.stdout),(2,b''))
                self.assertIn(b'NATIVE_PROMPT_REFUSED',result.stderr)
                self.assertTrue(all(row['operation'] not in ('ack','complete') for row in over.api_calls()))


if __name__=='__main__': unittest.main()
