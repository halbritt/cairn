"""Actual receipt-checked Go and selected committed R02 Python processes."""
import base64
import http.server
import json
import os
from pathlib import Path
import shlex
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
import time
import uuid
import copy

ROOT=Path(__file__).resolve().parents[1]
PRODUCER=Path(os.environ.get('CHECKED_ORDINARY_PRODUCER','/opt/checked-producer' if Path('/opt/checked-producer').exists() else str(ROOT)))
# Selected producer fixtures retain their own module globals; do not replace
# other suites' ROOT or Source modules during ordinary unittest discovery.
_saved_path=list(sys.path);_saved_modules={k:v for k,v in sys.modules.items() if k.startswith('test_')}
try:
    for _key in _saved_modules:sys.modules.pop(_key,None)
    sys.path.insert(0,str(PRODUCER/'scripts'))
    try:
        from test_r04_host_wiring import UnboundFixture
        from test_f04_activation import encoded,write,sha,scalar_tree,compat
    except ImportError as error:
        raise unittest.SkipTest('selected committed R04 producer6fe25e21 required for checked consumer corpus') from error
finally:
    for _key in list(sys.modules):
        if _key.startswith('test_'):sys.modules.pop(_key,None)
    sys.modules.update(_saved_modules);sys.path[:]=_saved_path

class Server(socketserver.ThreadingMixIn,socketserver.UnixStreamServer):daemon_threads=True
class CheckedOrdinaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build=tempfile.TemporaryDirectory();root=Path(cls.build.name)
        cls.binary=root/'ordinary';cls.stager=root/'stager';cls.cli=root/'cairn'
        for path,source,target in ((cls.binary,ROOT,'./cmd/cairn-lifecycle'),(cls.stager,PRODUCER,'./cmd/cairn-stage-lifecycle'),(cls.cli,ROOT,'./cmd/cairn')):
            subprocess.run(['go','build','-buildvcs=false','-o',str(path),target],cwd=source,check=True);path.chmod(0o500)
    @classmethod
    def tearDownClass(cls):cls.build.cleanup()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.f=UnboundFixture(self.root/'project',self.binary,stager=self.stager,harness='opencode',enabled=False)
        self.token=self.root/'token';write(self.token,b'checked-ordinary-synthetic-token',0o600)
        for config in (self.f.memory,self.f.coord):config.update(cairn=str(self.cli),socket=str(self.root/'api.sock'),token_file=str(self.token))
        self.f.publish_configuration(enabled=False)
        self.f.event=dict(hook_event_name='SessionStart',session_id='ses_CheckedOrdinary1',cwd=str(self.f.root),prompt='repair checked ordinary recall')
        self.f.raw=encoded(self.f.event);self.calls=[]
        owner=self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));op=self.path.removeprefix('/v1/')
                owner.calls.append((op,body,dict(self.headers)))
                if self.headers.get('Authorization')!='Bearer checked-ordinary-synthetic-token':self.send_error(401);return
                if op=='version':data=dict(preview_capabilities=dict(schema='cairn.preview-capabilities/1',entities_omitted=True))
                elif op=='index':data=dict(package=dict(receipt_id='11111111-1111-4111-8111-111111111111',nonce='',seal='blake3:'+'a'*64,semantic=dict(schema='cairn.semantic/17',mode='index',status='READY',scope=body['scope'],purpose='context',destination=dict(name='hosted',allow_local=False),query='sha256:fixture-query',available_tokens=32000,selected=[],omitted={},index=[])),handles=[],expires_at='2030-01-01T00:00:00Z',credits_remaining=4,bytes_remaining=24000)
                elif op=='recall-observation':data={}
                else:self.send_error(400);return
                self.send_response(200);self.end_headers();self.wfile.write(encoded(dict(schema='cairn.response/1',ok=True,status='OK',data=data)))
        self.server=Server(str(self.root/'api.sock'),Handler);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close)
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join()
    def go_vector(self):
        vector=[str(self.f.binary),'memory','--receipt',str(self.f.receipt_path),'--receipt-sha256',self.f.receipt_sha,'--role','memory','--entrypoint','memory-hook.py','--config',str(self.f.memory_path),'--hook-timeout','13','--launch-root',str(self.f.launch_root),'--shell','/bin/sh']
        for key,value in getattr(self,'selection_overrides',{}).items():vector[vector.index('--'+key)+1]=value
        return vector
    def go_launcher(self):
        vector=self.go_vector();raw=('#!/bin/sh\nexec '+shlex.join(vector)+' -- "$@"\n').encode()
        directory=self.f.launch_root/sha(raw);directory.mkdir(mode=0o700,exist_ok=True)
        launcher=directory/'inbox'
        if launcher.exists():self.assertEqual(launcher.read_bytes(),raw)
        else:write(launcher,raw,0o500)
        return launcher
    def go(self):
        return subprocess.run([str(self.go_launcher())],input=self.f.raw,cwd=self.f.root,env=self.f.env(),capture_output=True,timeout=18)
    def source(self):
        if not getattr(self,'source_repin',False) and not getattr(self,'selection_overrides',{}):return self.f.public('memory')
        vector=compat.launch_vector(sys.executable,self.f.receipt_path,self.f.receipt_sha,'memory','memory-hook.py',self.f.memory_path,13,self.f.launch_root)
        for key,value in getattr(self,'selection_overrides',{}).items():vector[vector.index('--'+key)+1]=value
        raw=compat.launcher_bytes(vector);directory=self.f.launch_root/sha(raw);directory.mkdir(mode=0o700,exist_ok=True)
        launcher=directory/'inbox'
        if not launcher.exists():write(launcher,raw,0o500)
        return subprocess.run([str(launcher)],input=self.f.raw,cwd=self.f.root,env=self.f.env(),capture_output=True,timeout=18)
    def state(self):
        directory=Path(self.f.memory['state_dir']);result={}
        if directory.exists():
            for path in directory.iterdir():
                if path.suffix=='.lock':continue
                body=json.loads(path.read_text())
                for k in ('at','duration_ms','elapsed_seconds'):body.get('last_recall',{}).pop(k,None)
                result[path.name]=body
        return result
    def pair(self):
        source=self.source();expected=self.state();calls=list(self.calls)
        shutil.rmtree(self.f.memory['state_dir'],ignore_errors=True);self.calls.clear()
        go=self.go()
        self.assertEqual((go.returncode,go.stdout,go.stderr),(source.returncode,source.stdout,source.stderr))
        self.assertEqual(scalar_tree(self.state()),scalar_tree(expected))
        self.assertEqual([c[0] for c in self.calls],[c[0] for c in calls])
        def normalize(body):
            body=copy.deepcopy(body)
            if 'request_id' in body:
                identifier=body.pop('request_id');self.assertEqual(str(uuid.UUID(identifier)),identifier)
            return body
        for actual,expected in zip(self.calls,calls):
            self.assertEqual(normalize(actual[1]),normalize(expected[1]))
        for rows,expected_image in ((calls,self.f.receipt['members']['memory.py']['sha256']),(self.calls,self.f.receipt['binary']['sha256'])):
            for _,_,headers in rows:
                token=headers['Cairn-Client-Diagnostics'];diagnostic=json.loads(base64.urlsafe_b64decode(token+'='*(-len(token)%4)))
                self.assertEqual(diagnostic['origin']['component'],'lifecycle-memory')
                if rows is calls:self.assertEqual(diagnostic['origin'],dict(component='lifecycle-memory',basis='reported'))
                else:self.assertEqual(diagnostic['origin'],dict(component='lifecycle-memory',basis='reported',implementation_id=expected_image))
        return go
    def test_real_installed_unbound_start_and_prompt_reach_same_body(self):
        print('CHECKED_ORDINARY_IMAGES='+json.dumps(dict(producer_commit='6fe25e2130aac774aecf02845466c426c5da74fd',receipt_sha256=sha(self.f.receipt_path.read_bytes()),source_members={name:member['sha256'] for name,member in self.f.receipt['members'].items()},go_image_sha256=sha(self.f.binary.read_bytes())),sort_keys=True),flush=True)
        for event in ('SessionStart','UserPromptSubmit'):
            with self.subTest(event=event):
                shutil.rmtree(self.f.memory['state_dir'],ignore_errors=True);self.calls.clear()
                self.f.event['hook_event_name']=event;self.f.raw=encoded(self.f.event)
                result=self.pair();self.assertEqual(result.returncode,0);self.assertTrue(self.calls)
    def test_changed_selected_artifacts_refuse_before_api_or_state(self):
        for name,path in (('closure',Path(self.f.receipt['snapshot_root'])/'memory.py'),
                          ('config',self.f.memory_path),('coordination-profile',self.f.coord_path),
                          ('manifest',self.f.binding),('image',self.f.binary)):
            with self.subTest(artifact=name):
                old=path.read_bytes();mode=path.stat().st_mode&0o777
                self.overwrite(path,old+b' ')
                try:
                    self.calls.clear();result=self.pair()
                    self.assertEqual(result.returncode,1);self.assertEqual(result.stdout,b'')
                    self.assertEqual(self.calls,[]);self.assertEqual(self.state(),{})
                finally:self.overwrite(path,old)

    def test_missing_selected_config_refuses_without_api_or_state(self):
        path=self.f.memory_path;parked=path.with_name('selected-away.json');path.rename(parked)
        try:
            result=self.pair();self.assertEqual(result.returncode,1);self.assertEqual(self.calls,[]);self.assertEqual(self.state(),{})
        finally:parked.rename(path)

    def overwrite(self,path,raw):
        mode=path.stat().st_mode&0o777;replacement=path.with_name('.replacement-'+path.name);write(replacement,raw,mode);replacement.replace(path)
    def repin(self):
        manifest=json.loads(self.f.binding.read_bytes())
        for key,paths in (('memory_config',[self.f.memory_path]),('coordination_configs',[self.f.coord_path])):
            descriptors=[dict(path=str(path),sha256=sha(path.read_bytes())) for path in paths]
            manifest[key]=descriptors[0] if key=='memory_config' else descriptors
            self.f.receipt[key]=manifest[key]
        self.overwrite(self.f.binding,encoded(manifest));self.f.receipt['binding']['sha256']=sha(self.f.binding.read_bytes())
        self.overwrite(self.f.receipt_path,encoded(self.f.receipt));self.f.receipt_sha=sha(self.f.receipt_path.read_bytes());self.source_repin=True
    def test_changed_selection_flags_match_actual_python_verifier(self):
        for overrides in ({'receipt-sha256':'0'*64},{'config':str(self.f.coord_path)},
                          {'entrypoint':'missing.py'},{'role':'coordination'},
                          {'hook-timeout':'0'},{'hook-timeout':'150'}):
            with self.subTest(overrides=overrides):
                self.selection_overrides=overrides;self.calls.clear()
                result=self.pair();self.assertEqual(result.returncode,1);self.assertEqual(self.calls,[]);self.assertEqual(self.state(),{})
        self.selection_overrides={}
    def test_repinned_mixed_profile_and_enabled_manifest_never_downgrade(self):
        for case in ('mixed-profile','enabled'):
            with self.subTest(case=case):
                if case=='mixed-profile':
                    path=self.f.coord_path;cfg=json.loads(path.read_bytes());cfg['inbox_recall_binding']=str(self.f.binding);self.overwrite(path,encoded(cfg));self.repin()
                else:
                    path=self.f.binding;manifest=json.loads(path.read_bytes());manifest['enabled']=True;self.overwrite(path,encoded(manifest));self.f.receipt['binding']['sha256']=sha(path.read_bytes());self.overwrite(self.f.receipt_path,encoded(self.f.receipt));self.f.receipt_sha=sha(self.f.receipt_path.read_bytes())
                self.calls.clear();result=self.pair();self.assertEqual(result.returncode,1);self.assertEqual(self.calls,[]);self.assertEqual(self.state(),{})
    def test_mutated_actual_go_launcher_refuses_before_configuration(self):
        launcher=self.go_launcher();raw=launcher.read_bytes();self.overwrite(launcher,raw+b'# changed\n')
        result=subprocess.run(self.go_vector()+['--'],input=self.f.raw,cwd=self.f.root,env=self.f.env(),capture_output=True,timeout=18)
        self.assertEqual((result.returncode,result.stdout),(1,b''));self.assertIn(b'compatibility_preflight',result.stderr);self.assertEqual(self.calls,[]);self.assertEqual(self.state(),{})
    def test_other_harness_account_variable_is_not_reinterpreted(self):
        original=self.f.env;self.f.env=lambda:dict(original(),CLAUDE_CONFIG_DIR=str(self.root/'another-account'))
        result=self.pair();self.assertEqual(result.returncode,0);self.assertTrue(self.calls)

    def test_missing_manifest_and_coordinator_preserve_source_failure_phase(self):
        for path in (self.f.binding,self.f.coord_path):
            with self.subTest(path=path.name):
                parked=path.with_name('temporarily-away.json');path.rename(parked)
                try:
                    self.calls.clear();result=self.pair();self.assertEqual(result.returncode,1);self.assertEqual(self.calls,[]);self.assertEqual(self.state(),{})
                finally:parked.rename(path)
    def test_malformed_selected_configuration_remains_refused_without_commit(self):
        old=self.f.memory_path.read_bytes();self.overwrite(self.f.memory_path,b'{')
        try:
            result=self.pair();self.assertEqual(result.returncode,1);self.assertEqual(self.calls,[]);self.assertEqual(self.state(),{})
        finally:self.overwrite(self.f.memory_path,old)
    def test_disabled_and_privacy_guards_still_require_valid_code(self):
        original=self.f.env
        self.f.env=lambda:dict(original(),CAIRN_LIFECYCLE_DISABLED='1')
        result=self.pair();self.assertEqual((result.returncode,result.stdout),(0,b'{}\n'));self.assertEqual(self.calls,[]);self.assertEqual(self.state(),{})
        self.overwrite(self.f.receipt_path,self.f.receipt_path.read_bytes()+b' ')
        result=self.pair();self.assertEqual(result.returncode,1);self.assertIn(b'compatibility_preflight',result.stderr);self.assertEqual(self.calls,[])
    def test_invalid_native_session_refuses_without_sdk_or_commit(self):
        self.f.event['session_id']='wrong';self.f.raw=encoded(self.f.event)
        result=self.pair();self.assertEqual(result.returncode,1);self.assertEqual(self.calls,[]);self.assertEqual(self.state(),{})

    def test_direct_entry_deadline_covers_waiting_for_original_stdin(self):
        launcher=self.go_launcher();started=time.monotonic()
        process=subprocess.Popen([str(launcher)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=self.f.root,env=self.f.env())
        try:
            process.wait(timeout=16)
            elapsed=time.monotonic()-started
            stdout=process.stdout.read();stderr=process.stderr.read()
            self.assertEqual((process.returncode,stdout),(1,b''));self.assertIn(b'compatibility_preflight',stderr)
            self.assertGreaterEqual(elapsed,12);self.assertLess(elapsed,16)
            self.assertEqual(self.calls,[]);self.assertEqual(self.state(),{})
        finally:
            if process.poll() is None:process.kill();process.wait()
            process.stdin.close();process.stdout.close();process.stderr.close()

    def test_symlinked_selected_artifacts_refuse_before_effects(self):
        for path in (self.f.receipt_path,Path(self.f.receipt['snapshot_root'])/'memory.py',self.f.binary):
            with self.subTest(path=path.name):
                parked=path.with_name('parked-'+path.name);path.rename(parked);path.symlink_to(parked)
                try:
                    self.calls.clear();result=self.pair();self.assertEqual(result.returncode,1);self.assertEqual(self.calls,[]);self.assertEqual(self.state(),{})
                finally:path.unlink();parked.rename(path)
    def test_forged_argv_cannot_substitute_same_bytes_executing_image(self):
        launcher=self.go_launcher()
        other=self.f.binary.with_name('other-image');shutil.copyfile(self.f.binary,other);other.chmod(0o500)
        self.assertEqual(sha(other.read_bytes()),self.f.receipt['binary']['sha256'])
        result=subprocess.run(self.go_vector()+['--'],executable=str(other),input=self.f.raw,cwd=self.f.root,env=self.f.env(),capture_output=True,timeout=18)
        self.assertEqual((result.returncode,result.stdout),(1,b''));self.assertIn(b'compatibility_preflight',result.stderr);self.assertEqual(self.calls,[]);self.assertEqual(self.state(),{})

    def test_changed_receipt_refuses_before_api_or_state(self):
        self.f.receipt_path.chmod(0o600);self.f.receipt_path.write_bytes(self.f.receipt_path.read_bytes()+b' ');self.f.receipt_path.chmod(0o400)
        result=self.pair();self.assertEqual(result.returncode,1);self.assertEqual(result.stdout,b'');self.assertEqual(self.calls,[]);self.assertEqual(self.state(),{})

if __name__=='__main__':unittest.main()
