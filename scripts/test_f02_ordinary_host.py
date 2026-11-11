"""Real ordinary Go host and retained Source body against the same Unix API.

The inherited fixture supplies transport data, never a recall/render oracle.
The Source process stages the complete /2 bridge bundle; no body is replaced.
"""
import base64
import json
from pathlib import Path
import shutil
import subprocess
import unittest
import uuid

import test_f02_start as start_fixture
from test_f04_activation import SESSION, encoded, scalar_tree, sha, write


class OrdinaryHostTests(unittest.TestCase):
    setUpClass = classmethod(start_fixture.F02StartTests.setUpClass.__func__)
    tearDownClass = classmethod(start_fixture.F02StartTests.tearDownClass.__func__)
    close_server = start_fixture.F02StartTests.close_server
    state = start_fixture.F02StartTests.state
    source = start_fixture.F02StartTests.source

    def setUp(self):
        start_fixture.F02StartTests.setUp(self)
        self.configure('opencode')

    def configure(self, harness, event='UserPromptSubmit', **fields):
        self.f.memory.update(harness=harness,recall_mode='agent_tools')
        self.f.memory.pop('inbox_recall_binding',None)
        self.f.event = dict(hook_event_name=event,session_id='ses_OrdinaryA1' if harness=='opencode' else SESSION,
                            cwd=str(self.f.root),prompt='repair lifecycle recall implementation',**fields)
        self.f.raw=encoded(self.f.event)
        self.f.publish_configuration()

    def pair(self):
        directory=Path(self.f.memory['state_dir'])
        seed={p.name:p.read_bytes() for p in directory.iterdir()} if directory.exists() else {}
        self.calls.clear()
        source=self.source(bridge=True)
        expected_state=self.state();expected_calls=list(self.calls)
        shutil.rmtree(directory,ignore_errors=True)
        if seed:
            directory.mkdir(mode=0o700)
            for name,raw in seed.items():write(directory/name,raw)
        self.calls.clear()
        candidate=subprocess.run([str(self.binary),'memory','--config',str(self.f.memory_path)],input=self.f.raw,
                                 cwd=self.f.root,env=self.f.env(),capture_output=True,timeout=18)
        self.assertEqual((candidate.returncode,candidate.stdout,candidate.stderr),(source.returncode,source.stdout,source.stderr))
        self.assertEqual(scalar_tree(self.state()),scalar_tree(expected_state))
        self.assertEqual([c[0] for c in self.calls],[c[0] for c in expected_calls])
        for (op,actual,headers),(_,expected,source_headers) in zip(self.calls,expected_calls):
            actual,expected=dict(actual),dict(expected)
            for request in (actual,expected):
                ident=request.pop('request_id',None)
                if op!='version':self.assertEqual(str(uuid.UUID(ident)),ident)
                if op=='recall-observation':
                    for key in ('elapsed_ms','search_ms','pulls_ms','selector_ms'):self.assertGreaterEqual(request.pop(key),0)
            self.assertEqual(actual,expected)
            self.assertEqual(headers['Authorization'],'Bearer f02-synthetic-token')
            for declared,image in ((headers,sha(self.binary.read_bytes())),(source_headers,sha((start_fixture.ROOT/'integrations/lifecycle/memory.py').read_bytes()))):
                data=declared['Cairn-Client-Diagnostics']
                diagnostic=json.loads(base64.urlsafe_b64decode(data+'='*(-len(data)%4)))
                self.assertEqual(diagnostic['origin']['component'],'lifecycle-memory')
                self.assertEqual(diagnostic['origin']['implementation_id'],image)
        return candidate

    def test_empty_real_recall_for_both_installed_identities(self):
        for harness in ('opencode','agy'):
            with self.subTest(harness=harness):
                self.configure(harness)
                result=self.pair()
                self.assertIn(b'candidate_search',result.stdout)
                request=next(body for op,body,_ in self.calls if op=='index')
                self.assertEqual(request['scope']['task_id'],harness+'/'+self.f.event['session_id'])
                shutil.rmtree(self.f.memory['state_dir'],ignore_errors=True)

    def test_selected_context_real_render(self):
        self.selected=[dict(record=dict(record_id=SESSION,version=1,body='Required project decision\nUse the existing lifecycle interface.'),mandatory=True,reason='required',evidence=[],authority=[],conflicts=[])]
        result=self.pair()
        self.assertIn(b'Required project decision',result.stdout)

    def test_taskless_start_defers_optional(self):
        self.configure('opencode','SessionStart')
        self.f.event.pop('prompt');self.f.raw=encoded(self.f.event)
        self.pair()
        self.assertEqual(self.state()['ses_OrdinaryA1.json']['last_recall']['optional_deferred'],'taskless_startup')


if __name__=='__main__':unittest.main()
