"""Actual Source and Go ordinary/checked tool observation processes."""
import copy
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time
import unittest

import test_f02_checked_ordinary as fixture

ROOT=Path(__file__).resolve().parents[1]

class ToolObservationTests(unittest.TestCase):
    setUpClass=classmethod(fixture.CheckedOrdinaryTests.setUpClass.__func__)
    tearDownClass=classmethod(fixture.CheckedOrdinaryTests.tearDownClass.__func__)
    close=fixture.CheckedOrdinaryTests.close
    source=fixture.CheckedOrdinaryTests.source
    go_vector=fixture.CheckedOrdinaryTests.go_vector
    selected_vector=fixture.CheckedOrdinaryTests.selected_vector
    go_launcher=fixture.CheckedOrdinaryTests.go_launcher

    def setUp(self):
        fixture.CheckedOrdinaryTests.setUp(self)
        self.checked=False
        self.directory=Path(self.f.memory['state_dir'])
        self.trace_directory=self.f.memory_path.parent/'invocation-trace'
        self.trace_directory.mkdir(mode=0o700,exist_ok=True)
        (self.f.root/'alpha.py').write_text('alpha')
        (self.f.root/'beta.txt').write_text('beta')
        self.event('PostToolUse','Read',dict(file_path='alpha.py'))
        print('TOOL_IMAGES='+json.dumps(dict(go=fixture.sha(self.f.binary.read_bytes()),source_members={name:item['sha256'] for name,item in self.f.receipt['members'].items()})),flush=True)

    def event(self,name,tool=None,tool_input=None,**fields):
        self.f.event=dict(hook_event_name=name,session_id='ses_CheckedOrdinary1',cwd=str(self.f.root),**fields)
        if tool is not None:self.f.event['tool_name']=tool
        if tool_input is not None:self.f.event['tool_input']=tool_input
        self.f.raw=fixture.encoded(self.f.event)

    def seed(self,state):
        self.directory.mkdir(mode=0o700,exist_ok=True)
        fixture.write(self.directory/'ses_CheckedOrdinary1.json',fixture.encoded(state),0o600)

    def state(self,started,ended):
        result={}
        if not self.directory.exists():return result
        for path in sorted(self.directory.iterdir()):
            if path.suffix=='.lock':continue
            raw=path.read_bytes()
            try:body=json.loads(raw)
            except (ValueError,UnicodeError):result[path.name]=('raw',raw);continue
            if isinstance(body,dict) and isinstance(body.get('hints'),dict):
                hints=body['hints']
                for key in ('files','searches'):
                    if isinstance(hints.get(key),dict):
                        for name,stamp in hints[key].items():
                            if isinstance(stamp,float) and started<=stamp<=ended:
                                self.assertTrue(math.isfinite(stamp));hints[key][name]='wallclock'
                stamp=hints.get('error_at')
                if isinstance(stamp,float) and started<=stamp<=ended:
                    self.assertTrue(math.isfinite(stamp));hints['error_at']='wallclock'
            result[path.name]=body
        return result

    def trace(self,started,ended):
        path=self.trace_directory/'trace.jsonl'
        if not path.exists():return []
        rows=[json.loads(line) for line in path.read_bytes().splitlines()]
        for row in rows:
            stamp=row.pop('at');self.assertTrue(started*1000-5<=stamp<=ended*1000+5)
            invocation=row.pop('inv');self.assertEqual(len(invocation),32);int(invocation,16)
            if row['kind']=='entry':
                for key in ('pid','ppid'):self.assertGreater(row.pop(key),0)
            if row['kind']=='exit':self.assertGreaterEqual(row.pop('duration_ms'),0)
        return rows

    def pair(self):
        existed=self.directory.exists()
        seed={path.name:path.read_bytes() for path in self.directory.iterdir() if path.suffix!='.lock'} if existed else {}
        trace=self.trace_directory/'trace.jsonl';trace.unlink(missing_ok=True)
        self.calls.clear();started=time.time();source=self.source();ended=time.time()
        expected=self.state(started,ended);source_trace=self.trace(started,ended)
        self.assertEqual(self.calls,[],'Source observation must never call the API')
        shutil.rmtree(self.directory,ignore_errors=True)
        if existed:self.directory.mkdir(mode=0o700)
        for name,raw in seed.items():fixture.write(self.directory/name,raw,0o600)
        trace.unlink(missing_ok=True);self.calls.clear()
        command=[str(self.go_launcher())] if self.checked else [str(self.f.binary),'memory','--config',str(self.f.memory_path)]
        started=time.time();actual=subprocess.run(command,input=self.f.raw,cwd=self.f.root,env=self.f.env(),capture_output=True,timeout=18);ended=time.time()
        actual_state=self.state(started,ended);actual_trace=self.trace(started,ended)
        print('TOOL_PAIR='+json.dumps(dict(event=self.f.event['hook_event_name'],tool=self.f.event.get('tool_name'),checked=self.checked,source_exit=source.returncode,go_exit=actual.returncode,source_stdout=source.stdout.decode(errors='backslashreplace'),go_stdout=actual.stdout.decode(errors='backslashreplace'),source_state=expected,go_state=actual_state),default=repr),flush=True)
        self.assertEqual((actual.returncode,actual.stdout,actual.stderr),(source.returncode,source.stdout,source.stderr))
        self.assertEqual(fixture.scalar_tree(actual_state),fixture.scalar_tree(expected))
        self.assertEqual(self.calls,[],'Go observation must never call the API')
        self.assertEqual(actual_trace,source_trace)
        return actual

    def test_read_and_checked_observation_are_real_stateful_entries(self):
        for checked in (False,True):
            with self.subTest(checked=checked):
                shutil.rmtree(self.directory,ignore_errors=True);self.checked=checked
                result=self.pair();self.assertEqual((result.returncode,result.stdout),(0,b'{}\n'))
                state=json.loads((self.directory/'ses_CheckedOrdinary1.json').read_bytes())
                self.assertEqual(list(state['hints']['files']),['alpha.py'])

    def test_existing_nohint_observation_row_is_a_source_skip(self):
        self.event('PostToolUse')
        result=self.pair();self.assertEqual((result.returncode,result.stdout),(0,b'{}\n'));self.assertFalse(self.directory.exists())

    def test_failure_identifiers_are_bounded_without_command_output(self):
        self.event('PostToolUseFailure',error='private command output EACCES ParseError EACCES RuntimeException')
        result=self.pair();self.assertEqual(result.returncode,0)
        state=json.loads((self.directory/'ses_CheckedOrdinary1.json').read_bytes())
        self.assertEqual(state['hints']['errors'],['EACCES','ParseError','RuntimeException'])
        self.assertNotIn('private command output',json.dumps(state))

if __name__=='__main__':unittest.main()
