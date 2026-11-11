"""Actual retained Source and Go unbound capture boundary processes.

Only external Unix API and transcript/state fixtures are manufactured. The
Source capture body, marker logic, locks and public handler remain unchanged.
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
import tempfile
import threading
import unittest
import uuid

import test_f02_start as start_fixture
import test_f02_ordinary_host as ordinary_fixture
from test_f04_activation import Fixture,ROOT,SESSION,encoded,scalar_tree,sha,write

class Server(socketserver.ThreadingMixIn,socketserver.UnixStreamServer):
    daemon_threads=True

class CaptureBoundaryTests(unittest.TestCase):
    setUpClass=classmethod(start_fixture.F02StartTests.setUpClass.__func__)
    tearDownClass=classmethod(start_fixture.F02StartTests.tearDownClass.__func__)
    source=ordinary_fixture.OrdinaryHostTests.source

    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.f=Fixture(self.root/'project',self.binary)
        self.f.memory.update(cairn=str(self.cli),socket=str(self.root/'api.sock'),token_file=str(self.root/'token'),capture_observations=True)
        self.f.memory.pop('inbox_recall_binding',None)
        write(self.root/'token',b'f03-synthetic-token',0o600)
        self.calls=[];self.report_locks=[]
        self.configure('opencode','PreCompact',messages=[])
        owner=self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                operation=self.path.removeprefix('/v1/')
                owner.calls.append((operation,body,dict(self.headers)))
                if operation!='capture-observation':self.send_error(400);return
                locks={}
                directory=Path(owner.f.memory['state_dir'])
                session=owner.session_key()
                for suffix in ('.capture.lock','.lock'):
                    with (directory/(session+suffix)).open('a') as lock:
                        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);locks[suffix]=True
                        except BlockingIOError:locks[suffix]=False
                owner.report_locks.append(locks)
                self.send_response(200);self.end_headers();self.wfile.write(encoded(dict(schema='cairn.response/1',ok=True,status='OK',data={})))
        self.server=Server(str(self.root/'api.sock'),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close_server)

    def close_server(self):self.server.shutdown();self.server.server_close();self.thread.join()
    def session_key(self):return sha(self.f.event['session_id'].encode()) if self.f.memory['harness']=='hermes' else self.f.event['session_id']
    def configure(self,harness,event,**fields):
        self.f.memory['harness']=harness
        self.f.event=dict(hook_event_name=event,session_id='ses_CaptureA1' if harness=='opencode' else SESSION,cwd=str(self.f.root),**fields)
        self.f.raw=encoded(self.f.event);self.f.publish_configuration()
    def state(self):
        directory=Path(self.f.memory['state_dir']);result={}
        if directory.exists():
            for path in directory.iterdir():
                if path.suffix=='.lock':continue
                body=json.loads(path.read_text())
                if 'last_capture' in body:body['last_capture'].pop('at',None)
                result[path.name]=body
        return result
    def pair(self):
        directory=Path(self.f.memory['state_dir'])
        seed={p.name:p.read_bytes() for p in directory.iterdir() if p.suffix!='.lock'} if directory.exists() else {}
        self.calls.clear();self.report_locks.clear()
        source=self.source();source_state=self.state();source_calls=list(self.calls);source_locks=list(self.report_locks)
        if directory.exists():
            for path in directory.iterdir():
                if path.suffix!='.lock':path.unlink()
        if seed:
            directory.mkdir(mode=0o700,exist_ok=True)
            for name,raw in seed.items():write(directory/name,raw)
        self.calls.clear();self.report_locks.clear()
        candidate=subprocess.run([str(self.binary),'memory','--config',str(self.f.memory_path)],input=self.f.raw,cwd=self.f.root,env=self.f.env(),capture_output=True,timeout=18)
        self.assertEqual((candidate.returncode,candidate.stdout,candidate.stderr),(source.returncode,source.stdout,source.stderr))
        self.assertEqual(scalar_tree(self.state()),scalar_tree(source_state))
        self.assertEqual(self.report_locks,source_locks)
        self.assertEqual([c[0] for c in self.calls],[c[0] for c in source_calls])
        for (operation,actual,headers),(_,expected,source_headers) in zip(self.calls,source_calls):
            actual,expected=dict(actual),dict(expected)
            for body in (actual,expected):
                ident=body.pop('request_id');self.assertEqual(str(uuid.UUID(ident)),ident)
                self.assertGreaterEqual(body.pop('duration_ms'),0)
            self.assertEqual(actual,expected)
            for declared,image in ((headers,sha(self.binary.read_bytes())),(source_headers,sha((ROOT/'integrations/lifecycle/memory.py').read_bytes()))):
                self.assertEqual(declared['Authorization'],'Bearer f03-synthetic-token')
                raw=declared['Cairn-Client-Diagnostics'];diagnostic=json.loads(base64.urlsafe_b64decode(raw+'='*(-len(raw)%4)))
                self.assertEqual(diagnostic['origin']['component'],'lifecycle-memory');self.assertEqual(diagnostic['origin']['implementation_id'],image)
        return candidate
    def outcome(self):return self.calls[-1][1]['outcome']

    def test_empty_messages_reach_capture_and_post_lock_report(self):
        result=self.pair();self.assertEqual(result.returncode,0);self.assertEqual(self.outcome(),'empty')
        self.assertEqual(self.state()['ses_CaptureA1.json']['last_capture'],dict(outcome='empty',records=[],selector_calls=0))
        self.assertEqual(self.report_locks,[{'.capture.lock':True,'.lock':True}])

    def test_missing_codex_transcript_is_pending_not_saved(self):
        self.configure('codex','Stop')
        result=self.pair();self.assertIn(b'capture is pending',result.stdout)
        self.assertEqual(self.outcome(),'transcript_unavailable')
        self.assertEqual(self.state()[SESSION+'.json']['capture_pending'],dict(reason='transcript_unavailable',hook_event='Stop'))

    def test_courtesy_then_duplicate_use_actual_fingerprint(self):
        self.configure('opencode','SessionEnd',messages=[dict(role='user',text='Thank you!'),dict(role='assistant',text='You’re welcome.')])
        self.assertEqual(self.pair().returncode,0);self.assertEqual(self.outcome(),'courtesy')
        self.assertEqual(self.pair().returncode,0);self.assertEqual(self.outcome(),'unchanged')
        self.assertEqual(self.calls[-1][1]['admission_candidate'],'confirmed_duplicate')

if __name__=='__main__':unittest.main()
