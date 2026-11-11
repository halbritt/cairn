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

ROOT=Path(__file__).resolve().parents[1]
PRODUCER=Path(os.environ.get('CHECKED_ORDINARY_PRODUCER','/opt/checked-producer'))
sys.path.insert(0,str(PRODUCER/'scripts'))
from test_r04_host_wiring import UnboundFixture
from test_f04_activation import encoded,write,sha,scalar_tree

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
        return [str(self.f.binary),'memory','--receipt',str(self.f.receipt_path),'--receipt-sha256',self.f.receipt_sha,'--role','memory','--entrypoint','memory-hook.py','--config',str(self.f.memory_path),'--hook-timeout','13','--launch-root',str(self.f.launch_root),'--shell','/bin/sh']
    def go(self):
        vector=self.go_vector();raw=('#!/bin/sh\nexec '+shlex.join(vector)+' -- "$@"\n').encode()
        directory=self.f.launch_root/sha(raw);directory.mkdir(mode=0o700,exist_ok=True)
        launcher=directory/'inbox'
        if launcher.exists():self.assertEqual(launcher.read_bytes(),raw)
        else:write(launcher,raw,0o500)
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
        source=self.f.public('memory');expected=self.state();calls=list(self.calls)
        shutil.rmtree(self.f.memory['state_dir'],ignore_errors=True);self.calls.clear()
        go=self.go()
        self.assertEqual((go.returncode,go.stdout,go.stderr),(source.returncode,source.stdout,source.stderr))
        self.assertEqual(scalar_tree(self.state()),scalar_tree(expected))
        self.assertEqual([c[0] for c in self.calls],[c[0] for c in calls])
        return go
    def test_real_installed_unbound_start_and_prompt_reach_same_body(self):
        for event in ('SessionStart','UserPromptSubmit'):
            with self.subTest(event=event):
                shutil.rmtree(self.f.memory['state_dir'],ignore_errors=True);self.calls.clear()
                self.f.event['hook_event_name']=event;self.f.raw=encoded(self.f.event)
                result=self.pair();self.assertEqual(result.returncode,0);self.assertTrue(self.calls)
    def test_changed_receipt_refuses_before_api_or_state(self):
        self.f.receipt_path.chmod(0o600);self.f.receipt_path.write_bytes(self.f.receipt_path.read_bytes()+b' ');self.f.receipt_path.chmod(0o400)
        result=self.pair();self.assertEqual(result.returncode,1);self.assertEqual(result.stdout,b'');self.assertEqual(self.calls,[]);self.assertEqual(self.state(),{})

if __name__=='__main__':unittest.main()
