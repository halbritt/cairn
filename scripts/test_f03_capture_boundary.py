"""Actual retained Source and Go unbound capture boundary processes.

Only external Unix API and transcript/state fixtures are manufactured. The
Source capture body, marker logic, locks and public handler remain unchanged.
"""
import base64
import fcntl
import ctypes
import select
import struct
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
    def source(self, **kwargs):
        # Same retained /2 staging, with an optional external syscall scheduler.
        if not getattr(self,'source_prefix',()):return ordinary_fixture.OrdinaryHostTests.source(self,**kwargs)
        root=self.root/'scheduled-source'
        loader=ordinary_fixture.module('capture_scheduled_bundle',ROOT/'integrations/lifecycle/lifecycle_bundle.py')
        loader.stage_runtime(ROOT/'integrations/lifecycle',root);descriptor=loader.bundle_descriptor(root)
        manifest=dict(schema='cairn.inbox-recall-binding/2',enabled=False,
                      bridge=dict(path=str(root/'inbox_recall.py'),sha256=sha((root/'inbox_recall.py').read_bytes()),bundle=descriptor),
                      engine=dict(path=str(root/'memory.py'),sha256=sha((root/'memory.py').read_bytes()),bundle=descriptor))
        saved_config=self.f.memory_path.read_bytes();saved_binding=self.f.binding.read_bytes()
        write(self.f.memory_path,encoded(dict(self.f.memory,inbox_recall_binding=str(self.f.binding))));write(self.f.binding,encoded(manifest))
        try:
            return subprocess.run([*self.source_prefix,os.sys.executable,'-B',str(root/'memory.py'),'--config',str(self.f.memory_path)],input=self.f.raw,cwd=self.f.root,env=self.f.env(),capture_output=True,timeout=18)
        finally:write(self.f.memory_path,saved_config);write(self.f.binding,saved_binding)

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
                if 'last_capture' in body:
                    at=body['last_capture'].pop('at');self.assertIn(type(at),(int,float));self.assertGreaterEqual(at,0)
                result[path.name]=body
        return result
    def pair(self):
        directory=Path(self.f.memory['state_dir'])
        seed={p.name:p.read_bytes() for p in directory.iterdir() if p.suffix!='.lock'} if directory.exists() else {}
        self.calls.clear();self.report_locks.clear()
        self.start_scheduler('source')
        source=self.source();self.finish_scheduler()
        source_state=self.state();source_calls=list(self.calls);source_locks=list(self.report_locks)
        if directory.exists():
            for path in directory.iterdir():
                if path.suffix!='.lock':path.unlink()
        if seed:
            directory.mkdir(mode=0o700,exist_ok=True)
            for name,raw in seed.items():write(directory/name,raw)
        self.calls.clear();self.report_locks.clear()
        if getattr(self,'before_candidate',None):self.before_candidate()
        self.start_scheduler('go')
        candidate=subprocess.run([*getattr(self,'candidate_prefix',()),str(self.binary),'memory','--config',str(self.f.memory_path)],input=self.f.raw,cwd=self.f.root,env=self.f.env(),capture_output=True,timeout=18)
        self.finish_scheduler()
        def output(raw):
            if self.f.memory['harness']!='hermes' or not raw:return raw
            body=json.loads(raw)
            last=body.get('cairn_status',{}).get('last_capture')
            if last is not None:self.assertGreaterEqual(last.pop('at'),0)
            return scalar_tree(body)
        self.assertEqual((candidate.returncode,output(candidate.stdout),candidate.stderr),(source.returncode,output(source.stdout),source.stderr))
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

    def start_scheduler(self,side):
        action=getattr(self,'before_commit',None)
        if action is None:return
        directory=Path(self.f.memory['state_dir']);directory.mkdir(mode=0o700,exist_ok=True)
        libc=ctypes.CDLL(None,use_errno=True)
        fd=libc.inotify_init1(os.O_CLOEXEC|os.O_NONBLOCK)
        self.assertGreaterEqual(fd,0)
        self.assertGreaterEqual(libc.inotify_add_watch(fd,os.fsencode(directory),0x20),0)
        self.scheduler_error=[]
        def watch():
            try:
                opened=0;target=(self.session_key()+'.lock').encode()
                while opened<2:
                    if not select.select([fd],[],[],8)[0]:raise AssertionError('capture did not reach final state-lock open')
                    raw=os.read(fd,65536);position=0
                    while position<len(raw):
                        _,mask,_,length=struct.unpack_from('iIII',raw,position);position+=16
                        name=raw[position:position+length].split(b'\0',1)[0];position+=length
                        if mask&0x20 and name==target:opened+=1
                action(directory/(self.session_key()+'.json'))
            except BaseException as error:self.scheduler_error.append(error)
            finally:os.close(fd)
        self.scheduler=threading.Thread(target=watch,daemon=True);self.scheduler.start()
    def finish_scheduler(self):
        thread=getattr(self,'scheduler',None)
        if thread is None:return
        thread.join(timeout=9);self.assertFalse(thread.is_alive());self.scheduler=None
        if self.scheduler_error:raise self.scheduler_error[0]
        release=getattr(self,'release_done',None)
        if release is not None:self.assertTrue(release.wait(8));self.release_done=None

    def rollout(self,messages,path=None):
        path=path or self.root/'rollout.jsonl'
        path.write_text(''.join(json.dumps(dict(type='response_item',payload=dict(type='message',role=m['role'],content=[dict(type='input_text' if m['role']=='user' else 'output_text',text=m['text'])])))+'\n' for m in messages))
        return path
    def courtesy_messages(self,count=6):
        return [dict(role='user' if i%2==0 else 'assistant',text='thanks' if i%2==0 else 'no problem') for i in range(count)]

    def test_short_stop_and_repeated_append_truncate_keep_exact_offsets(self):
        path=self.rollout(self.courtesy_messages(2))
        self.configure('codex','Stop',transcript_path=str(path))
        self.pair();self.assertEqual(self.outcome(),'too_little_dialogue')
        self.assertNotIn('codex_capture_marker',self.state()[SESSION+'.json'])
        self.configure('codex','PreCompact',transcript_path=str(path));self.pair()
        first=self.state()[SESSION+'.json']['codex_capture_marker']['offset']
        self.assertEqual(first,path.stat().st_size)
        path=self.rollout(self.courtesy_messages(4),path)
        self.configure('codex','Stop',transcript_path=str(path));self.pair()
        self.assertEqual(self.outcome(),'too_little_dialogue');self.assertEqual(self.state()[SESSION+'.json']['codex_capture_marker']['offset'],first)
        path=self.rollout(self.courtesy_messages(8),path);self.pair()
        self.assertEqual(self.outcome(),'courtesy');self.assertEqual(self.state()[SESSION+'.json']['codex_capture_marker']['offset'],path.stat().st_size)
        self.assertGreater(path.stat().st_size,first)
        self.rollout(self.courtesy_messages(2),path);self.pair()
        self.assertEqual(self.outcome(),'too_little_dialogue')
        self.configure('codex','PreCompact',transcript_path=str(path));self.pair()
        self.assertEqual(self.state()[SESSION+'.json']['codex_capture_marker']['offset'],path.stat().st_size)

    def test_capture_and_shared_lock_busy_have_no_marker(self):
        directory=Path(self.f.memory['state_dir']);directory.mkdir(mode=0o700)
        for suffix in ('.capture.lock','.lock'):
            with self.subTest(suffix=suffix),(directory/(self.session_key()+suffix)).open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                result=self.pair();self.assertEqual(result.returncode,1)
                self.assertEqual(self.state(),{})
                self.assertEqual(self.outcome(),'error')
                self.assertFalse(self.report_locks[0][suffix])

    def test_invalid_dialogue_and_missing_transcript_do_not_advance_status(self):
        for fields in (dict(messages={}),dict(messages=[dict(role='system',text='private')]),{}):
            with self.subTest(fields=fields):
                self.configure('opencode','PreCompact',**fields)
                result=self.pair();self.assertEqual(result.returncode,1)
                self.assertEqual(self.state(),{})

    def scheduled(self):
        self.source_prefix=('/usr/bin/strace','-f','-qq','-o',str(self.root/'source-schedule.log'),'-e','trace=flock','-e','inject=flock:delay_enter=200ms')
        self.candidate_prefix=('/usr/bin/strace','-f','-qq','-o',str(self.root/'go-schedule.log'),'-e','trace=flock','-e','inject=flock:delay_enter=200ms')

    def test_changed_capture_fields_or_binding_refuse_cas_before_marker(self):
        self.scheduled();directory=Path(self.f.memory['state_dir']);directory.mkdir(mode=0o700)
        for changes in (dict(capture_pending=None),dict(binding=['/different','task'])):
            with self.subTest(changes=changes):
                state_path=directory/(self.session_key()+'.json');write(state_path,encoded({}))
                def change(path):
                    with path.with_suffix('.lock').open('a') as lock:
                        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                        state=json.loads(path.read_text());state.update(changes);write(path,encoded(state))
                self.before_commit=change
                result=self.pair();self.assertEqual(result.returncode,1)
                self.assertIn(b'capture state changed before commit',result.stderr)
                self.assertEqual(self.state()[self.session_key()+'.json'],changes)
                self.assertEqual(self.outcome(),'state_commit_error')

    def test_concurrent_recall_fields_survive_capture_commit(self):
        self.scheduled();directory=Path(self.f.memory['state_dir']);directory.mkdir(mode=0o700)
        write(directory/(self.session_key()+'.json'),encoded({}))
        def change(path):
            with path.with_suffix('.lock').open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                state=json.loads(path.read_text());state.update(hints=dict(files={'current.go':123}),last_recall=dict(outcome='current'))
                write(path,encoded(state))
        self.before_commit=change
        self.assertEqual(self.pair().returncode,0)
        state=self.state()[self.session_key()+'.json']
        self.assertEqual(state['hints'],dict(files={'current.go':123}));self.assertEqual(state['last_recall'],dict(outcome='current'))

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

    def test_exact_former_missing_opencode_transcript_row_has_source_filesystem_effects(self):
        # Ownership join0d9c15b1/v1 moves only the obsolete old F02 row here.
        self.f.memory.pop('capture_observations',None)
        self.f.memory['harness']='opencode'
        self.f.event=dict(hook_event_name='PreCompact',session_id='ses_OrdinaryA1',cwd=str(self.f.root),prompt='repair lifecycle recall implementation')
        self.f.raw=encoded(self.f.event);self.f.publish_configuration()
        directory=Path(self.f.memory['state_dir'])
        def filesystem():
            self.assertTrue(directory.is_dir())
            self.assertEqual(sorted(p.name for p in directory.iterdir()),['ses_OrdinaryA1.capture.lock','ses_OrdinaryA1.lock'])
            return (directory.stat().st_mode&0o777,{p.name:p.stat().st_mode&0o777 for p in directory.iterdir()})
        self.assertFalse(directory.exists())
        source=self.source();expected=filesystem();source_calls=list(self.calls)
        self.assertEqual(source.returncode,1);self.assertEqual(source.stdout,b'')
        self.assertIn(b'phase=handle error_class=KeyError',source.stderr)
        shutil.rmtree(directory);self.calls.clear()
        self.assertFalse(directory.exists())
        candidate=subprocess.run([str(self.binary),'memory','--config',str(self.f.memory_path)],input=self.f.raw,cwd=self.f.root,env=self.f.env(),capture_output=True,timeout=18)
        self.assertEqual((candidate.returncode,candidate.stdout,candidate.stderr),(source.returncode,source.stdout,source.stderr))
        self.assertEqual(filesystem(),expected)
        self.assertEqual(self.calls,source_calls);self.assertEqual(self.calls,[])
        self.assertEqual(self.state(),{})

    def test_state_replace_failure_retries_without_false_digest_or_marker(self):
        self.configure('opencode','PreCompact',messages=self.courtesy_messages(2))
        self.source_prefix=('/usr/bin/strace','-f','-qq','-o',str(self.root/'source-replace.log'),'-e','trace=rename,renameat,renameat2','-e','inject=rename,renameat,renameat2:error=EACCES:when=1')
        self.candidate_prefix=('/usr/bin/strace','-f','-qq','-o',str(self.root/'go-replace.log'),'-e','trace=rename,renameat,renameat2','-e','inject=rename,renameat,renameat2:error=EACCES:when=1')
        result=self.pair();self.assertEqual(result.returncode,1)
        self.assertEqual(self.outcome(),'state_commit_error');self.assertEqual(self.state(),{})
        self.assertIn('EACCES', (self.root/'source-replace.log').read_text());self.assertIn('EACCES',(self.root/'go-replace.log').read_text())
        self.source_prefix=();self.candidate_prefix=()
        self.assertEqual(self.pair().returncode,0);self.assertEqual(self.outcome(),'courtesy')
        state=self.state()[self.session_key()+'.json'];self.assertIn('captured_digest',state);self.assertNotIn('saved',state['last_capture']['outcome'])

    def test_append_after_snapshot_remains_for_later_stop(self):
        self.scheduled();path=self.rollout(self.courtesy_messages(6));original=path.read_bytes()
        self.configure('codex','PreCompact',transcript_path=str(path))
        directory=Path(self.f.memory['state_dir']);directory.mkdir(mode=0o700)
        write(directory/(SESSION+'.json'),encoded({}))
        def append(_):self.rollout(self.courtesy_messages(8),path)
        self.before_commit=append;self.before_candidate=lambda:path.write_bytes(original)
        self.assertEqual(self.pair().returncode,0)
        self.assertEqual(self.state()[SESSION+'.json']['codex_capture_marker']['offset'],len(original))
        self.assertGreater(path.stat().st_size,len(original))
        self.before_commit=None;self.before_candidate=None;self.source_prefix=();self.candidate_prefix=()
        self.configure('codex','Stop',transcript_path=str(path));self.pair()
        self.assertEqual(self.outcome(),'too_little_dialogue')
        self.assertEqual(self.state()[SESSION+'.json']['codex_capture_marker']['offset'],len(original))

    def test_substantive_capture_and_bound_callback_remain_unavailable(self):
        self.configure('opencode','PreCompact',messages=[dict(role='user',text='Record the new substantive project decision.')])
        result=subprocess.run([str(self.binary),'memory','--config',str(self.f.memory_path)],input=self.f.raw,cwd=self.f.root,env=self.f.env(),capture_output=True,timeout=18)
        self.assertEqual(result.returncode,1);self.assertIn(b'provider-selected substantive capture remains unavailable',result.stderr)
        self.assertEqual(self.state(),{});self.assertEqual(self.outcome(),'error')
        self.assertEqual([op for op,_,_ in self.calls],['capture-observation'])
        shutil.rmtree(self.f.memory['state_dir']);self.calls.clear()
        self.f.memory['inbox_recall_binding']=str(self.f.binding);self.f.publish_configuration()
        result=subprocess.run([str(self.binary),'memory','--config',str(self.f.memory_path)],input=self.f.raw,cwd=self.f.root,env=self.f.env(),capture_output=True,timeout=18)
        self.assertEqual(result.returncode,1);self.assertIn(b'phase=activation',result.stderr)
        self.assertEqual(self.calls,[]);self.assertFalse(Path(self.f.memory['state_dir']).exists())

    def test_installed_harness_capture_events_and_non_codex_stop_vocabulary(self):
        for harness,events in (('codex',('PreCompact',)),('claude',('PreCompact','SessionEnd')),('opencode',('PreCompact','SessionEnd')),('hermes',('PreCompact','SessionEnd'))):
            for event in events:
                with self.subTest(harness=harness,event=event):
                    shutil.rmtree(self.f.memory['state_dir'],ignore_errors=True)
                    fields=dict(messages=[])
                    if harness=='codex':fields['transcript_path']=str(self.rollout([]))
                    self.configure(harness,event,**fields)
                    self.assertEqual(self.pair().returncode,0);self.assertEqual(self.outcome(),'empty')
        for harness in ('claude','opencode','hermes'):
            with self.subTest(harness=harness,event='Stop'):
                shutil.rmtree(self.f.memory['state_dir'],ignore_errors=True)
                self.configure(harness,'Stop',messages=[])
                self.assertEqual(self.pair().stdout,b'{}\n');self.assertEqual(self.calls,[])
                self.assertFalse(Path(self.f.memory['state_dir']).exists())

    def test_final_commit_waits_for_short_legacy_writer_and_times_out_without_marker(self):
        self.scheduled();directory=Path(self.f.memory['state_dir']);directory.mkdir(mode=0o700)
        for seconds,expected in ((0.6,0),(6.2,1)):
            with self.subTest(seconds=seconds):
                path=directory/(self.session_key()+'.json');write(path,encoded({}))
                def hold(path):
                    lock=path.with_suffix('.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    done=threading.Event();self.release_done=done
                    def release():lock.close();done.set()
                    threading.Timer(seconds,release).start()
                self.before_commit=hold
                result=self.pair();self.assertEqual(result.returncode,expected)
                if expected:
                    self.assertIn(b'capture could not commit state',result.stderr)
                    self.assertEqual(self.outcome(),'state_commit_error');self.assertEqual(self.state()[path.name],{})
                else:self.assertEqual(self.outcome(),'empty')

    def test_preparation_failure_does_not_report_or_create_state(self):
        self.f.memory.pop('cairn');self.f.publish_configuration()
        result=self.pair();self.assertEqual(result.returncode,1)
        self.assertIn(b'error_class=KeyError',result.stderr)
        self.assertEqual(self.calls,[]);self.assertFalse(Path(self.f.memory['state_dir']).exists())

    def test_report_failure_does_not_change_confirmed_capture_state(self):
        self.f.memory['socket']=str(self.root/'missing.sock');self.f.publish_configuration()
        self.assertEqual(self.pair().returncode,0)
        self.assertEqual(self.calls,[])
        self.assertEqual(self.state()[self.session_key()+'.json']['last_capture']['outcome'],'empty')

    def test_capture_trace_preserves_phase_stage_outcome_and_zero_context(self):
        directory=self.f.root/'invocation-trace';directory.mkdir(mode=0o700)
        trace=directory/'trace.jsonl'
        path=self.rollout(self.courtesy_messages(2))
        for harness,event,fields in (('opencode','PreCompact',dict(messages=[])),
                                     ('codex','Stop',{}),
                                     ('codex','Stop',dict(transcript_path=str(path))),
                                     ('opencode','PreCompact',{})):
            with self.subTest(harness=harness,event=event,fields=fields):
                shutil.rmtree(self.f.memory['state_dir'],ignore_errors=True)
                if trace.exists():trace.unlink()
                self.configure(harness,event,**fields);self.pair()
                records=[json.loads(line) for line in trace.read_text().splitlines()]
                self.assertEqual([row['kind'] for row in records],['entry','parsed','exit']*2)
                source,candidate=records[:3],records[3:]
                for rows in (source,candidate):
                    self.assertEqual(len({row['inv'] for row in rows}),1)
                    for row in rows:
                        self.assertRegex(row.pop('inv'),r'^[0-9a-f]{32}$')
                        for key in ('at','duration_ms','pid','ppid'):
                            if key in row:self.assertGreaterEqual(row.pop(key),0)
                self.assertEqual(candidate,source)
                if candidate[-1]['main_return_code']==0:self.assertEqual(candidate[-1]['context_bytes'],0)

    def test_bounded_transcript_tail_filters_non_owner_parts_and_keeps_snapshot_offset(self):
        path=self.root/'bounded.jsonl'
        records=[dict(type='reasoning',text='x'*(2*1024*1024+100)),
                 dict(type='response_item',payload=dict(type='message',role='user',
                      content=[dict(type='input_text',text='private injected context'),dict(type='input_text',text='thanks')],
                      internal_chat_message_metadata_passthrough=dict(content_item_kinds=['context','user.text']))),
                 dict(type='response_item',payload=dict(type='message',role='assistant',content=[dict(type='output_text',text='no problem')])),
                 dict(type='response_item',payload=dict(type='message',role='user',content=[dict(type='input_text',text='# AGENTS.md instructions: private')])),
                 dict(type='response_item',payload=dict(type='message',role='user',content=[dict(type='input_text',text='<environment_context>private</environment_context>thank you')])),
                 dict(type='response_item',payload=dict(type='message',role='assistant',content=[dict(type='output_text',text="you're welcome")])),
                 dict(type='event_msg',payload=dict(type='user_message',message='not a second owner message'))]
        path.write_bytes(b''.join(encoded(row)+b'\n' for row in records))
        self.configure('codex','PreCompact',transcript_path=str(path))
        self.assertEqual(self.pair().returncode,0);self.assertEqual(self.outcome(),'courtesy')
        state=self.state()[SESSION+'.json'];self.assertEqual(state['captured_messages'],4)
        self.assertEqual(state['codex_capture_marker']['offset'],path.stat().st_size-len(encoded(records[-1]))-1)

    def test_only_ascii_blank_transcript_lines_are_skipped(self):
        # B1: bytes.strip in Source does not discard a Unicode-space record.
        dialogue=b''.join(encoded(dict(type=role,message=dict(content=text)))+b'\n'
                          for role,text in (('user','thanks'),('assistant','no problem')))
        print('B1 process images Source='+sha((ROOT/'integrations/lifecycle/memory.py').read_bytes())+' Go='+sha(self.binary.read_bytes()))
        for name,raw,status in (('nbsp',b'\xc2\xa0\n',1),
                                ('nbsp-before-dialogue',b'\xc2\xa0\n'+dialogue,1),
                                ('ascii-blank',b' \t\n\r\v\f\n',0),
                                ('ascii-before-dialogue',b' \t\n\r\v\f\n'+dialogue,0)):
            with self.subTest(case=name):
                shutil.rmtree(self.f.memory['state_dir'],ignore_errors=True)
                path=self.root/'blank-lines.jsonl';path.write_bytes(raw)
                self.configure('claude','PreCompact',transcript_path=str(path))
                result=self.pair();self.assertEqual(result.returncode,status)
                self.assertEqual([operation for operation,_,_ in self.calls],['capture-observation'])
                if status:
                    self.assertEqual(result.stdout,b'');self.assertIn(b'error_class=ValueError',result.stderr)
                    self.assertEqual(self.state(),{});self.assertEqual(self.outcome(),'error')
                else:
                    self.assertEqual(self.outcome(),'courtesy' if name.endswith('dialogue') else 'empty')

    def test_unexpected_transcript_shape_is_redacted_one_without_marker(self):
        path=self.root/'malformed.jsonl';path.write_text('[]\n')
        self.configure('codex','PreCompact',transcript_path=str(path))
        source=self.source();source_calls=list(self.calls)
        self.assertEqual(source.returncode,1);self.assertEqual(source.stdout,b'')
        self.assertIn(b'AttributeError',source.stderr)
        self.calls.clear()
        candidate=subprocess.run([str(self.binary),'memory','--config',str(self.f.memory_path)],input=self.f.raw,cwd=self.f.root,env=self.f.env(),capture_output=True,timeout=18)
        self.assertEqual((candidate.returncode,candidate.stdout,candidate.stderr),(1,b'',b'Cairn lifecycle diagnostic: phase=handle error_class=UnexpectedError\n'))
        self.assertEqual(self.state(),{})
        self.assertEqual([op for op,_,_ in self.calls],[op for op,_,_ in source_calls])
        self.assertEqual(self.outcome(),'error')

if __name__=='__main__':unittest.main()
