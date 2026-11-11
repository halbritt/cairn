"""Class A producer-shaped SessionStart processes against a private Unix API.

The oracle bypasses only the already-characterized F04 binding/bridge prefix;
its entire memory body, real CLI transport, rendering and persistence run.
The candidate runs the actual receipt-selected public launcher and checked Go.
"""
import base64
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
import unittest

from test_f04_activation import Fixture, ROOT, SESSION, encoded, scalar_tree, sha, write


class Server(socketserver.UnixStreamServer):
    allow_reuse_address = True


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

    def source(self):
        script = '''import importlib.util,sys
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('source_memory',sys.argv[1])
hook=importlib.util.module_from_spec(spec); spec.loader.exec_module(hook)
config=Path(sys.argv[2]); sys.argv=[sys.argv[1],'--config',str(config)]
with patch.object(hook,'effective_inbox_config',side_effect=lambda config,*a,**k:config),patch.object(hook,'load_inbox_recall',return_value=None):
 sys.exit(hook.main())
'''
        return subprocess.run([sys.executable,'-B','-c',script,str(ROOT/'integrations/lifecycle/memory.py'),str(self.f.memory_path)],
                              input=self.f.raw,capture_output=True,cwd=self.f.root,env=self.f.env(),timeout=18)

    def state(self):
        directory = Path(self.f.memory['state_dir'])
        result = {}
        if directory.exists():
            for path in directory.iterdir():
                if path.suffix == '.lock':
                    continue
                body = json.loads(path.read_text())
                if 'last_recall' in body:
                    # Only wall/elapsed measurements differ between the real processes.
                    for key in ('at','duration_ms','elapsed_seconds'):
                        body['last_recall'].pop(key,None)
                result[path.name] = body
        return result

    def pair(self):
        source = self.source()
        source_state = self.state()
        source_calls = list(self.calls)
        shutil.rmtree(self.f.memory['state_dir'],ignore_errors=True)
        self.calls.clear()
        candidate = self.f.public('memory')
        self.assertEqual((candidate.returncode,candidate.stdout,candidate.stderr),
                         (source.returncode,source.stdout,source.stderr))
        candidate_state = self.state()
        self.assertEqual(candidate_state.keys(),source_state.keys())
        for name in source_state:
            self.assertEqual(scalar_tree(candidate_state[name]),scalar_tree(source_state[name]),name)
        self.assertEqual([c[0] for c in self.calls],[c[0] for c in source_calls])
        for (_,actual,headers),(_,expected,source_headers) in zip(self.calls,source_calls):
            actual,expected = dict(actual),dict(expected)
            # Fresh index IDs are each generated exactly once, never retried.
            for key in ('request_id',):
                actual.pop(key,None); expected.pop(key,None)
            self.assertEqual(actual,expected)
            self.assertEqual(headers['Authorization'],'Bearer f02-synthetic-token')
            declaration = headers.get('Cairn-Client-Diagnostics')
            self.assertIsNotNone(declaration)
            diagnostic = json.loads(base64.urlsafe_b64decode(declaration+'='*(-len(declaration)%4)))
            self.assertEqual(diagnostic['origin']['component'],'lifecycle-memory')
            self.assertEqual(diagnostic['origin']['implementation_id'],sha(self.f.binary.read_bytes()))
        return candidate

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
