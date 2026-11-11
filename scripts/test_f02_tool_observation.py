"""Actual Source and Go ordinary/checked tool observation processes."""
import copy
import fcntl
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time
import unittest
import uuid

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
    overwrite=fixture.CheckedOrdinaryTests.overwrite
    repin=fixture.CheckedOrdinaryTests.repin

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
        self.f.event=dict(hook_event_name=name,session_id='ses_CheckedOrdinary1',cwd=str(self.f.root));self.f.event.update(fields)
        if tool is not None:self.f.event['tool_name']=tool
        if tool_input is not None:self.f.event['tool_input']=tool_input
        self.f.raw=fixture.encoded(self.f.event)

    def seed(self,state):
        self.directory.mkdir(mode=0o700,exist_ok=True)
        fixture.write(self.directory/'ses_CheckedOrdinary1.json',fixture.encoded(state),0o600)

    def invoke(self,call):
        lock=None
        if getattr(self,'lock_busy',False) or getattr(self,'save_refusal',False):
            self.directory.mkdir(mode=0o700,exist_ok=True)
            lock=(self.directory/'ses_CheckedOrdinary1.lock').open('a')
            if getattr(self,'lock_busy',False):fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if getattr(self,'save_refusal',False):self.directory.chmod(0o500)
        try:return call()
        finally:
            if getattr(self,'save_refusal',False):self.directory.chmod(0o700)
            if lock is not None:lock.close()

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
        self.calls.clear();started=time.time();source=self.invoke(self.source);ended=time.time()
        expected=self.state(started,ended);source_trace=self.trace(started,ended)
        source_exists=self.directory.exists()
        self.assertEqual(self.calls,[],'Source observation must never call the API')
        shutil.rmtree(self.directory,ignore_errors=True)
        if existed:self.directory.mkdir(mode=0o700)
        for name,raw in seed.items():fixture.write(self.directory/name,raw,0o600)
        trace.unlink(missing_ok=True);self.calls.clear()
        command=[str(self.go_launcher())] if self.checked else [str(self.f.binary),'memory','--config',str(self.f.memory_path)]
        started=time.time();actual=self.invoke(lambda:subprocess.run(command,input=self.f.raw,cwd=self.f.root,env=self.f.env(),capture_output=True,timeout=18));ended=time.time()
        actual_state=self.state(started,ended);actual_trace=self.trace(started,ended)
        print('TOOL_PAIR='+json.dumps(dict(event=self.f.event['hook_event_name'],tool=self.f.event.get('tool_name'),checked=self.checked,source_exit=source.returncode,go_exit=actual.returncode,source_stdout=source.stdout.decode(errors='backslashreplace'),go_stdout=actual.stdout.decode(errors='backslashreplace'),source_state=expected,go_state=actual_state),default=repr),flush=True)
        self.assertEqual((actual.returncode,actual.stdout,actual.stderr),(source.returncode,source.stdout,source.stderr))
        self.assertEqual(fixture.scalar_tree(actual_state),fixture.scalar_tree(expected))
        self.assertEqual(self.directory.exists(),source_exists)
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

    def test_read_edit_write_and_pretool_vocabulary(self):
        for event in ('PreToolUse','PostToolUse'):
            for tool in ('Read','Edit','Write'):
                with self.subTest(event=event,tool=tool):
                    shutil.rmtree(self.directory,ignore_errors=True)
                    self.event(event,tool,dict(file_path='new/source.py' if tool=='Write' else 'alpha.py'))
                    self.assertEqual(self.pair().returncode,0)

    def test_grep_and_glob_existing_paths_and_pattern_terms(self):
        cases=(('Grep',dict(path='alpha.py',pattern=r'Beta_Error|token\b|1234')),
               ('Glob',dict(path='alpha.py',pattern='beta.txt')),
               ('Glob',dict(pattern='**/*.py')),
               ('Grep',dict(path='missing',pattern='StateManager.*state_manager')),
               ('Grep',dict(path=7,pattern=['not','text'])))
        for tool,tool_input in cases:
            with self.subTest(tool=tool,tool_input=tool_input):
                shutil.rmtree(self.directory,ignore_errors=True);self.event('PostToolUse',tool,tool_input)
                self.assertEqual(self.pair().returncode,0)

    def test_bash_search_flags_quotes_and_no_execution(self):
        cases=('rg -n -e "Alpha_Error beta" alpha.py',
               "git grep --regexp=StateManager -- beta.txt",
               "find . -maxdepth 3 -name '*.py'",
               "grep -g '*.txt' -eBetaError beta.txt",
               'rg "alpha beta" "" alpha.py',
               'rg -e "alpha\\q beta" alpha.py',
               "git\u00a0grep GammaError")
        for command in cases:
            with self.subTest(command=command):
                shutil.rmtree(self.directory,ignore_errors=True);self.event('PreToolUse','Bash',dict(command=command))
                self.assertEqual(self.pair().returncode,0)

    def test_nohint_and_shell_meta_cells_skip_before_state(self):
        for tool,tool_input in (('Other',{}),('Read',{}),('Read',[]),('Bash',dict(command='echo private')),
                                ('Bash',dict(command='rg alpha; touch owned')),
                                ('Bash',dict(command='rg "unterminated')),
                                ('Bash',dict(command='rg '+('x'*1024))),('Glob',dict(pattern='*.py'))):
            with self.subTest(tool=tool,tool_input=tool_input):
                shutil.rmtree(self.directory,ignore_errors=True);self.event('PostToolUse',tool,tool_input)
                self.assertEqual(self.pair().returncode,0);self.assertFalse(self.directory.exists())
        self.assertFalse((self.f.root/'owned').exists())

    def test_hint_recency_dedup_order_and_sixteen_entry_cap(self):
        seed=dict(binding=[None,''],hints=dict(files={f'old-{n}.py':float(n) for n in range(16)},searches={f'term-{n}':float(n) for n in range(16)}),unrelated=['keep',123])
        self.seed(seed)
        self.event('PostToolUse','Read',dict(file_path='old-2.py'));self.pair()
        state=json.loads((self.directory/'ses_CheckedOrdinary1.json').read_bytes())
        self.assertEqual(list(state['hints']['files']),[f'old-{n}.py' for n in range(16) if n!=2]+['old-2.py'])
        self.event('PostToolUse','Read',dict(file_path='new.py'));self.pair()
        state=json.loads((self.directory/'ses_CheckedOrdinary1.json').read_bytes())
        self.assertEqual(len(state['hints']['files']),16);self.assertNotIn('old-0.py',state['hints']['files'])
        self.event('PostToolUse','Grep',dict(pattern='term-2 newterm newterm'));self.pair()
        state=json.loads((self.directory/'ses_CheckedOrdinary1.json').read_bytes())
        self.assertEqual(list(state['hints']['searches'])[-2:],['term-2','newterm']);self.assertEqual(state['unrelated'],['keep',123])

    def test_failure_identifier_unicode_boundaries_dedup_and_eight_cap(self):
        self.event('PostToolUseFailure',error='éTypeError valid EABC EAB AError EACCES ParseError RuntimeException AnotherError CustomException ZeroError ExtraError TailError EACCES')
        self.pair();state=json.loads((self.directory/'ses_CheckedOrdinary1.json').read_bytes())
        self.assertEqual(state['hints']['errors'],['EABC','EACCES','ParseError','RuntimeException','AnotherError','CustomException','ZeroError','ExtraError'])

    def test_failure_identifier_overlapping_alternatives_match_whole_words(self):
        self.event('PostToolUseFailure',error='EABCError EAAAAException EABCError ValueError EABC_error')
        self.pair();state=json.loads((self.directory/'ses_CheckedOrdinary1.json').read_bytes())
        self.assertEqual(state['hints']['errors'],['EABCError','EAAAAException','ValueError'])

    def test_project_binding_reset_workstream_and_explicit_nohint_persistence(self):
        self.seed(dict(binding=['old-project','old'],hints=dict(files={'old.py':1.0}),unrelated='retired'))
        self.event('PostToolUse','Other',{},project_path=str(self.f.root),workstream='bounded observation')
        self.pair();state=json.loads((self.directory/'ses_CheckedOrdinary1.json').read_bytes())
        self.assertEqual(state,dict(binding=[str(self.f.root),'bounded observation'],workstream='Handoff: '+self.f.root.name+' / bounded observation',hints={}))

    def test_paths_resolve_symlinks_and_refuse_escape_or_root(self):
        outside=self.root/'outside.txt';outside.write_text('outside')
        (self.f.root/'escape.py').symlink_to(outside)
        (self.f.root/'inside.py').symlink_to(self.f.root/'alpha.py')
        for path,expected in (('inside.py','alpha.py'),('escape.py',None),('../outside.txt',None),('.',None),('`alpha.py`','alpha.py'),('日本語/new.py','日本語/new.py')):
            with self.subTest(path=path):
                shutil.rmtree(self.directory,ignore_errors=True);self.event('PostToolUse','Read',dict(file_path=path));self.pair()
                if expected is None:self.assertFalse(self.directory.exists())
                else:self.assertEqual(list(json.loads((self.directory/'ses_CheckedOrdinary1.json').read_bytes())['hints']['files']),[expected])

    def test_relative_symlink_parent_escape_and_nohint_skip_for_all_tools(self):
        outside=self.root/'outside'/'dir';outside.mkdir(parents=True)
        (self.f.root/'link').symlink_to(outside,target_is_directory=True)
        (self.f.root/'b.py').write_text('lexical decoy')
        path='link/../b.py'
        cases=[(tool,dict(file_path=path),[]) for tool in ('Read','Edit','Write')]
        cases += [('Grep',dict(path=path),[]),('Glob',dict(path=path),[]),
                  ('Glob',dict(pattern=path),['link','b.py']),
                  ('Bash',dict(command='rg foo '+path),['foo','link','b.py'])]
        for checked in (False,True):
            for tool,tool_input,searches in cases:
                with self.subTest(checked=checked,tool=tool,tool_input=tool_input):
                    shutil.rmtree(self.directory,ignore_errors=True);self.checked=checked
                    self.event('PostToolUse',tool,tool_input)
                    self.assertEqual(self.pair().returncode,0)
                    self.assertEqual(self.directory.exists(),bool(searches))
                    if searches:
                        hints=json.loads((self.directory/'ses_CheckedOrdinary1.json').read_bytes())['hints']
                        self.assertEqual(list(hints.get('files',{})),[])
                        self.assertEqual(list(hints['searches']),searches)

    def test_relative_symlink_parent_internal_controls_and_hint_order(self):
        target=self.f.root/'sub'/'dir';target.mkdir(parents=True)
        (target.parent/'beta.txt').write_text('resolved internal file')
        (self.f.root/'internal').symlink_to(target,target_is_directory=True)
        for path,expected in (('internal/../beta.txt','sub/beta.txt'),('sub/../beta.txt','beta.txt')):
            cases=[(tool,dict(file_path=path)) for tool in ('Read','Edit','Write')]
            cases += [('Grep',dict(path=path)),('Glob',dict(path=path)),('Glob',dict(pattern=path))]
            for tool,tool_input in cases:
                with self.subTest(path=path,tool=tool,tool_input=tool_input):
                    shutil.rmtree(self.directory,ignore_errors=True)
                    self.event('PreToolUse',tool,tool_input)
                    self.assertEqual(self.pair().returncode,0)
                    hints=json.loads((self.directory/'ses_CheckedOrdinary1.json').read_bytes())['hints']
                    self.assertEqual(list(hints['files']),[expected])
        self.seed(dict(binding=[None,''],hints=dict(files={'alpha.py':1.0,'sub/beta.txt':2.0},searches={'foo':3.0,'old':4.0})))
        self.event('PostToolUse','Bash',dict(command='rg foo internal/../beta.txt alpha.py internal/../beta.txt'))
        self.assertEqual(self.pair().returncode,0)
        hints=json.loads((self.directory/'ses_CheckedOrdinary1.json').read_bytes())['hints']
        self.assertEqual(list(hints['files']),['sub/beta.txt','alpha.py'])
        self.assertEqual(list(hints['searches']),['old','foo'])

    def test_disabled_privacy_identity_and_workspace_admission(self):
        original=self.f.env
        self.f.env=lambda:dict(original(),CAIRN_LIFECYCLE_DISABLED='1');self.pair();self.assertFalse(self.directory.exists())
        self.f.env=original
        marker=self.f.root/'.cairn-no-memory';marker.touch();self.pair();self.assertFalse(self.directory.exists());marker.unlink()
        for changes in (dict(session_id='wrong'),dict(cwd=str(self.root/'missing')),dict(project_path='relative'),dict(workstream='bad\nworkstream')):
            with self.subTest(changes=changes):
                self.event('PostToolUse','Read',dict(file_path='alpha.py'),**changes)
                self.assertEqual(self.pair().returncode,1);self.assertFalse(self.directory.exists())

    def test_account_home_guard_precedes_observation(self):
        self.f.memory.update(harness='claude',config_home=str(self.root/'account'))
        self.overwrite(self.f.memory_path,fixture.encoded(self.f.memory));self.repin()
        original=self.f.env;self.f.env=lambda:dict(original(),CLAUDE_CONFIG_DIR=str(self.root/'other-account'))
        self.event('PostToolUse','Read',dict(file_path='alpha.py'),session_id=str(uuid.uuid4()))
        self.pair();self.assertFalse(self.directory.exists())

    def test_native_harness_identities_and_hermes_status_state_key(self):
        base_environment=self.f.env()
        for harness in ('codex','claude','agy','hermes'):
            with self.subTest(harness=harness):
                shutil.rmtree(self.directory,ignore_errors=True)
                self.f.memory.update(harness=harness,config_home=str(self.root/'native-account'))
                self.overwrite(self.f.memory_path,fixture.encoded(self.f.memory));self.repin()
                self.f.env=lambda:dict(base_environment,CODEX_HOME=str(self.root/'native-account'),CLAUDE_CONFIG_DIR=str(self.root/'native-account'))
                session='hermes-observation-1335' if harness=='hermes' else str(uuid.uuid4())
                self.event('PostToolUse','Read',dict(file_path='alpha.py'),session_id=session)
                self.assertEqual(self.pair().returncode,0)
                if harness=='hermes':self.assertEqual([p.name for p in self.directory.iterdir() if p.suffix=='.json'],[fixture.sha(session.encode())+'.json'])

    def test_checked_pretool_and_failure_are_real_observation_entries(self):
        self.checked=True
        self.event('PreToolUse','Grep',dict(pattern='bounded_error'))
        self.assertEqual(self.pair().returncode,0)
        self.event('PostToolUseFailure',error='EACCES ParseError')
        self.assertEqual(self.pair().returncode,0)

    def test_checked_observation_admission_skip_and_refusal_trace(self):
        self.checked=True;original=self.f.env
        self.f.env=lambda:dict(original(),CAIRN_LIFECYCLE_DISABLED='1')
        self.assertEqual(self.pair().returncode,0);self.assertFalse(self.directory.exists())
        self.f.env=original
        marker=self.f.root/'.cairn-no-memory';marker.touch()
        self.assertEqual(self.pair().returncode,0);self.assertFalse(self.directory.exists());marker.unlink()
        self.event('PreToolUse','Read',dict(file_path='alpha.py'),session_id='invalid')
        self.assertEqual(self.pair().returncode,1);self.assertFalse(self.directory.exists())

    def test_lock_busy_is_labelled_without_state_commit(self):
        self.lock_busy=True;result=self.pair();self.assertEqual(result.returncode,1)
        self.assertIn(b'a memory hook is already running',result.stderr)
        self.assertFalse((self.directory/'ses_CheckedOrdinary1.json').exists())

    def test_malformed_state_and_nontext_error_preserve_existing_bytes(self):
        self.directory.mkdir(mode=0o700);path=self.directory/'ses_CheckedOrdinary1.json';fixture.write(path,b'{',0o600)
        self.assertEqual(self.pair().returncode,1);self.assertEqual(path.read_bytes(),b'{')
        self.seed(dict(binding=[None,''],unrelated='keep'))
        self.event('PostToolUseFailure',error=['not','text'])
        self.assertEqual(self.pair().returncode,1);self.assertEqual(json.loads(path.read_bytes()),dict(binding=[None,''],unrelated='keep'))

    def test_malformed_stored_file_list_preserves_typeerror_and_state(self):
        state=dict(binding=[None,''],hints=dict(files=[]),unrelated='keep')
        self.seed(state);self.assertEqual(self.pair().returncode,1)
        self.assertEqual(json.loads((self.directory/'ses_CheckedOrdinary1.json').read_bytes()),state)

    def test_final_state_save_refusal_preserves_prior_state(self):
        original=dict(binding=[None,''],hints=dict(files={'prior.py':123.0}),unrelated=['keep'])
        self.seed(original);self.save_refusal=True
        self.assertEqual(self.pair().returncode,1)
        self.assertEqual(json.loads((self.directory/'ses_CheckedOrdinary1.json').read_bytes()),original)

    def test_checked_observation_receipt_pin_precedes_body_and_skip(self):
        self.checked=True
        self.overwrite(self.f.receipt_path,self.f.receipt_path.read_bytes()+b' ')
        self.event('PostToolUse')
        result=self.pair();self.assertEqual(result.returncode,1)
        self.assertIn(b'compatibility_preflight',result.stderr);self.assertFalse(self.directory.exists())

    def test_nontext_file_hint_is_an_explicit_refusal_cell(self):
        self.event('PostToolUse','Read',dict(file_path=7))
        source=self.source();self.assertEqual((source.returncode,source.stdout),(1,b''));self.assertIn(b'AttributeError',source.stderr)
        self.assertEqual(self.calls,[]);self.assertFalse(self.directory.exists())
        actual=subprocess.run([str(self.f.binary),'memory','--config',str(self.f.memory_path)],input=self.f.raw,cwd=self.f.root,env=self.f.env(),capture_output=True,timeout=18)
        self.assertEqual((actual.returncode,actual.stdout),(1,b''));self.assertIn(b'nontext tool file hint remains unavailable',actual.stderr)
        self.assertEqual(self.calls,[]);self.assertFalse(self.directory.exists())

    def test_nonobject_stored_hints_is_an_explicit_refusal_cell(self):
        seed=dict(binding=[None,''],hints=[],unrelated='keep');self.seed(seed)
        source=self.source();self.assertEqual((source.returncode,source.stdout),(1,b''));self.assertIn(b'AttributeError',source.stderr)
        self.assertEqual(self.calls,[]);self.assertEqual(json.loads((self.directory/'ses_CheckedOrdinary1.json').read_bytes()),seed)
        actual=subprocess.run([str(self.f.binary),'memory','--config',str(self.f.memory_path)],input=self.f.raw,cwd=self.f.root,env=self.f.env(),capture_output=True,timeout=18)
        self.assertEqual((actual.returncode,actual.stdout),(1,b''));self.assertIn(b'stored tool hints shape remains unavailable',actual.stderr)
        self.assertEqual(self.calls,[]);self.assertEqual(json.loads((self.directory/'ses_CheckedOrdinary1.json').read_bytes()),seed)

if __name__=='__main__':unittest.main()
