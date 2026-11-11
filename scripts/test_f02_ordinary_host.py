"""Real ordinary Go host and retained Source body against the same Unix API.

The inherited fixture supplies transport data, never a recall/render oracle.
The Source process stages the complete /2 bridge bundle; no body is replaced.
"""
import base64
import fcntl
import os
import sys
import json
from pathlib import Path
import shutil
import shlex
import subprocess
import unittest
import uuid

import test_f02_start as start_fixture
from test_f04_activation import SESSION, encoded, scalar_tree, sha, write, module


class OrdinaryHostTests(unittest.TestCase):
    setUpClass = classmethod(start_fixture.F02StartTests.setUpClass.__func__)
    tearDownClass = classmethod(start_fixture.F02StartTests.tearDownClass.__func__)
    close_server = start_fixture.F02StartTests.close_server

    def setUp(self):
        start_fixture.F02StartTests.setUp(self)
        self.configure('opencode')
        self.state_directory=Path(self.f.memory['state_dir'])

    def state(self):
        if 'state_dir' not in self.f.memory:return {}
        return start_fixture.F02StartTests.state(self)

    def configure(self, harness, event='UserPromptSubmit', **fields):
        self.f.memory.update(harness=harness,recall_mode='agent_tools')
        self.f.memory.pop('inbox_recall_binding',None)
        self.f.event = dict(hook_event_name=event,session_id='ses_OrdinaryA1' if harness=='opencode' else SESSION,
                            cwd=str(self.f.root),prompt='repair lifecycle recall implementation',**fields)
        self.f.raw=encoded(self.f.event)
        self.f.publish_configuration()

    def source(self, *, bridge=True, corrupt_bridge=False):
        # A complete retained Source bundle and its real /2 disabled staging
        # transition. Only the opt-in differs before Source strips it; the
        # effective recall configuration, external API and state seed are equal.
        root=self.root/'retained-source'
        loader=module('ordinary_source_bundle',start_fixture.ROOT/'integrations/lifecycle/lifecycle_bundle.py')
        loader.stage_runtime(start_fixture.ROOT/'integrations/lifecycle',root)
        descriptor=loader.bundle_descriptor(root)
        config=dict(self.f.memory,inbox_recall_binding=str(self.f.binding))
        manifest=dict(schema='cairn.inbox-recall-binding/2',enabled=False,
                      bridge=dict(path=str(root/'inbox_recall.py'),sha256=sha((root/'inbox_recall.py').read_bytes()),bundle=descriptor),
                      engine=dict(path=str(root/'memory.py'),sha256=sha((root/'memory.py').read_bytes()),bundle=descriptor))
        if corrupt_bridge:
            with (root/'inbox_recall.py').open('ab') as target:target.write(b'\n# changed pinned Source bridge\n')
        original_config=self.f.memory_path.read_bytes();original_binding=self.f.binding.read_bytes()
        write(self.f.memory_path,encoded(config));write(self.f.binding,encoded(manifest))
        try:
            command=[sys.executable,'-B',str(root/'memory.py'),'--config',str(self.f.memory_path)]
            if getattr(self,'shell_transport',False):command=['/bin/sh','-c','exec '+shlex.join(command)]
            return subprocess.run(command,
                                  input=self.f.raw,cwd=self.f.root,env=self.f.env(),capture_output=True,timeout=18)
        finally:
            write(self.f.memory_path,original_config);write(self.f.binding,original_binding)

    def normalized_stream(self,raw):
        if not self.optional or not raw:return raw,[]
        outer=json.loads(raw)
        self.assertEqual(raw,json.dumps(outer,ensure_ascii=False,separators=(',',':')).encode()+b'\n','public compact encoding changed')
        context=outer.get('hookSpecificOutput',{}).get('additionalContext')
        if context is None:return raw,[]
        opening=context.index('{"selected":')
        payload=json.loads(context[opening:])
        self.assertEqual(context[opening:].encode(),json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode(),'inner compact encoding changed')
        arguments=[];aliases={}
        def visit(node):
            if isinstance(node,dict):
                pull=node.get('pull_arguments')
                if isinstance(pull,dict):
                    original=dict(pull);arguments.append(original)
                    ident=original['request_id']
                    self.assertEqual(str(uuid.UUID(ident)),ident)
                    self.assertEqual(uuid.UUID(ident).version,4)
                    if ident not in aliases:aliases[ident]='fresh-pull-intent-'+str(len(aliases))
                    pull['request_id']=aliases[ident]
                    if 'pull_command' in node:
                        self.assertIn(ident,node['pull_command'])
                        node['pull_command']=node['pull_command'].replace(ident,aliases[ident])
                for child in node.values():visit(child)
            elif isinstance(node,list):
                for child in node:visit(child)
        visit(payload)
        outer['hookSpecificOutput']['additionalContext']=(context[:opening],scalar_tree(payload))
        return scalar_tree(outer),arguments

    def check_pull_intents(self,calls,arguments):
        # Fresh caller UUIDv4 is the only public text normalization. Whole
        # inspections must still derive the exact Source UUIDv5 from that
        # side's original caller intent; span requests use fresh UUIDv4.
        derived={str(uuid.uuid5(uuid.NAMESPACE_URL,json.dumps(['cairn.lifecycle.whole-pull/1',args],sort_keys=True,ensure_ascii=False,separators=(',',':')))) for args in arguments}
        seen=set()
        for op,body,_ in calls:
            if op!='expand':continue
            ident=body['request_id']
            if body.get('span'):
                self.assertEqual(uuid.UUID(ident).version,4)
            else:self.assertIn(ident,derived)
            self.assertNotIn(ident,seen);seen.add(ident)

    def pair(self):
        directory=Path(self.f.memory.get('state_dir',self.state_directory))
        seed={p.name:p.read_bytes() for p in directory.iterdir() if p.suffix!='.lock'} if directory.exists() else {}
        self.calls.clear()
        source=self.source(bridge=True)
        expected_state=self.state();expected_calls=list(self.calls)
        if directory.exists():
            for path in directory.iterdir():
                if path.suffix!='.lock':path.unlink()
        if seed:
            directory.mkdir(mode=0o700,exist_ok=True)
            for name,raw in seed.items():write(directory/name,raw)
        self.calls.clear()
        command=[str(self.binary),'memory','--config',str(self.f.memory_path)]
        if getattr(self,'shell_transport',False):command=['/bin/sh','-c','exec '+shlex.join(command)]
        command=[*getattr(self,'candidate_prefix',()),*command]
        candidate=subprocess.run(command,input=self.f.raw,
                                 cwd=self.f.root,env=self.f.env(),capture_output=True,timeout=18)
        actual_stdout,actual_arguments=self.normalized_stream(candidate.stdout)
        expected_stdout,expected_arguments=self.normalized_stream(source.stdout)
        self.assertEqual((candidate.returncode,actual_stdout,candidate.stderr),(source.returncode,expected_stdout,source.stderr))
        self.check_pull_intents(self.calls,actual_arguments);self.check_pull_intents(expected_calls,expected_arguments)
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

    def test_empty_real_recall_for_source_shaped_harness_identities(self):
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

    def optional_note(self,body='Current lifecycle source: 日本語 😀\n',*,offset=0,length=32):
        length=min(length,len(body.encode())-offset)
        entry=dict(record_id='55555555-5555-4555-8555-555555555555',version=3,kind='note',**{'class':'A'},
                   summary=body.encode()[offset:offset+length].decode(),body_sha256=sha(body.encode()),
                   summary_span=dict(offset=offset,length=length))
        self.optional=[dict(entry=entry,body=body)]
        return entry

    def test_privacy_skips_before_api_or_state(self):
        (self.f.root/'.cairn-no-memory').touch()
        self.assertEqual(self.pair().stdout,b'{}\n')
        self.assertEqual(self.calls,[])
        self.assertFalse(Path(self.f.memory['state_dir']).exists())

    def test_identity_and_workspace_admission_refusals(self):
        for changed in (dict(session_id='not-a-session'),dict(cwd='/nonexistent/ordinary-host'),dict(project_path='relative')):
            with self.subTest(changed=changed):
                self.configure('opencode');self.f.event.update(changed);self.f.raw=encoded(self.f.event)
                self.assertEqual(self.pair().returncode,1)
                self.assertEqual(self.calls,[])
                self.assertFalse(Path(self.f.memory['state_dir']).exists())

    def test_non_boundary_and_unknown_event_skip(self):
        for event in ('Stop','UnknownEvent'):
            with self.subTest(event=event):
                self.configure('opencode',event)
                self.assertEqual(self.pair().stdout,b'{}\n')
                self.assertEqual(self.calls,[])

    def test_contended_lock_is_optional_failure(self):
        directory=Path(self.f.memory['state_dir']);directory.mkdir(mode=0o700)
        with (directory/'ses_OrdinaryA1.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            result=self.pair()
        self.assertEqual(result.returncode,1)
        self.assertEqual(self.calls,[])

    def test_actual_meter_and_image_headers(self):
        self.f.memory['recall_observations']=True;self.f.publish_configuration()
        result=self.pair()
        report=next(body for op,body,_ in self.calls if op=='recall-observation')
        context=json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
        self.assertEqual(report['harness'],'opencode')
        self.assertEqual((report['search_calls'],report['pull_calls'],report['selector_calls'],report['injected_bytes']),(1,0,[],len(context.encode())))

    def test_agy_does_not_invent_source_recall_observations(self):
        self.configure('agy');self.f.memory['recall_observations']=True;self.f.publish_configuration()
        self.pair()
        self.assertNotIn('recall-observation',[op for op,_,_ in self.calls])

    def test_search_refusal_saves_failure_diagnostics(self):
        for refusal in ('OPEN_CONFLICT','BUDGET_REFUSED','POLICY_UNENFORCEABLE'):
            with self.subTest(refusal=refusal):
                shutil.rmtree(self.f.memory['state_dir'],ignore_errors=True);self.refusal=refusal
                result=self.pair()
                self.assertEqual(result.returncode,1)
                self.assertEqual(self.state()['ses_OrdinaryA1.json']['last_recall']['outcome'],'failed')

    def test_state_binding_reset_and_retained_seen(self):
        directory=Path(self.f.memory['state_dir']);directory.mkdir(mode=0o700)
        write(directory/'ses_OrdinaryA1.json',encoded(dict(binding=[None,''],seen={'old':1,'retained':2},hints={})))
        self.f.event['retained_record_ids']=['retained'];self.f.raw=encoded(self.f.event)
        self.pair()
        self.assertEqual(self.state()['ses_OrdinaryA1.json']['seen'],{'retained':2})

    def test_invocation_trace_agrees_with_source(self):
        directory=self.f.root/'invocation-trace';directory.mkdir(mode=0o700)
        result=self.pair()
        records=[json.loads(line) for line in (directory/'trace.jsonl').read_text().splitlines()]
        self.assertEqual([r['kind'] for r in records],['entry','parsed','exit']*2)
        left,right=records[:3],records[3:]
        for rows in (left,right):
            self.assertEqual(len({r['inv'] for r in rows}),1)
            self.assertRegex(rows[0]['inv'],r'^[0-9a-f]{32}$')
            for record in rows:
                for key in ('at','duration_ms','pid','ppid'):
                    if key in record:self.assertGreaterEqual(record.pop(key),0)
                record.pop('inv')
        self.assertEqual(left,right)
        self.assertEqual(right[-1]['context_bytes'],len(json.loads(result.stdout)['hookSpecificOutput']['additionalContext'].encode()))
        self.assertNotIn('session',right[-1],'Source trace accepts only canonical UUIDs, not OpenCode labels')

    def test_raw_input_failures_have_exact_source_phase_and_exit(self):
        for raw in (b'[]',b'null',b'{',b'\xff',b' '*(1024*1024+1)):
            with self.subTest(length=len(raw),opening=raw[:12]):
                self.f.raw=raw
                result=self.pair()
                self.assertEqual(result.returncode,1)
                self.assertEqual(result.stdout,b'')
                self.assertIn(b'phase=input',result.stderr)
                self.assertFalse(Path(self.f.memory['state_dir']).exists())

    def test_context_budget_refusal_is_optional_for_this_harness(self):
        self.f.memory['context_bytes']=1000;self.f.publish_configuration()
        self.selected=[dict(record=dict(record_id=SESSION,version=1,body='Required project decision\n'+'x'*1200),mandatory=True,reason='required',evidence=[],authority=[],conflicts=[])]
        result=self.pair()
        self.assertEqual(result.returncode,1)
        self.assertEqual(result.stdout,b'')
        self.assertNotIn(b'current required context could not be restored',result.stderr)

    def test_public_literal_config_grammar(self):
        root=self.root/'grammar-source'
        loader=module('ordinary_grammar_bundle',start_fixture.ROOT/'integrations/lifecycle/lifecycle_bundle.py')
        loader.stage_runtime(start_fixture.ROOT/'integrations/lifecycle',root)
        for args in ([],['--help'],['--config'],['--config','--help'],['--unknown'],['--config',str(self.f.memory_path),'extra']):
            with self.subTest(args=args):
                source=subprocess.run([sys.executable,'-B',str(root/'memory.py'),*args],input=self.f.raw,cwd=self.f.root,env=self.f.env(),capture_output=True,timeout=18)
                candidate=subprocess.run([str(self.binary),'memory',*args],input=self.f.raw,cwd=self.f.root,env=self.f.env(),capture_output=True,timeout=18)
                self.assertEqual((candidate.returncode,candidate.stdout,candidate.stderr),(source.returncode,source.stdout,source.stderr))

    def context(self,result):
        self.assertEqual(result.returncode,0,result.stderr)
        text=json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
        return json.loads(text[text.index('{"selected"'):])

    def test_eager_whole_body_and_meter_use_real_receipt(self):
        self.optional_note()
        self.f.memory['recall_observations']=True;self.f.publish_configuration()
        result=self.pair();context=self.context(result)
        self.assertEqual(context['candidate_bodies'][0]['response']['selection']['record']['body'],self.optional[0]['body'])
        self.assertEqual(context['candidate_inspection']['pull_calls'],1)
        self.assertEqual(context['candidate_inspection']['delivered_records'],1)
        self.assertEqual(context['index'],[])
        self.assertEqual(sum(op=='expand' for op,_,_ in self.calls),1)
        report=next(body for op,body,_ in self.calls if op=='recall-observation')
        self.assertEqual(report['pull_calls'],1)
        self.assertEqual(report['receipt_ids'],[SESSION])

    def test_eager_paid_whole_excerpt_reuses_existing_span_helper(self):
        self.optional_note('Owner task opening.\n'+'prefix '*300+'Matching source context. '+'tail '*1400,offset=2000,length=32)
        context=self.context(self.pair())
        supplied=context['candidate_bodies'][0]
        self.assertEqual(supplied['response']['source_extent'],'partial_span')
        self.assertEqual(supplied['response']['span_origin'],'whole_pull')
        self.assertEqual(context['candidate_inspection']['pull_calls'],1)
        self.assertEqual(sum(op=='expand' for op,_,_ in self.calls),1)

    def test_eager_whole_refusal_spends_actual_span_call(self):
        self.optional_note('Owner task opening.\n'+'preamble '*180+'Bounded matching source. '+'tail '*140,offset=1500,length=32)
        self.expand_refusal='BUDGET_REFUSED'
        context=self.context(self.pair())
        self.assertEqual(context['candidate_bodies'][0]['response']['source_extent'],'partial_span')
        self.assertEqual(context['candidate_inspection']['pull_calls'],2)
        self.assertEqual(sum(op=='expand' for op,_,_ in self.calls),2)

    def test_lexical_ambient_whole_body_seen_and_relevance(self):
        self.f.memory.update(recall_mode='ambient',semantic_fallback=False);self.f.publish_configuration()
        self.optional_note('Current lifecycle recall implementation\nKeep the actual host state and scope.')
        result=self.pair();context=self.context(result)
        self.assertEqual(context['expanded']['selection']['record']['body'],self.optional[0]['body'])
        self.assertEqual(self.state()['ses_OrdinaryA1.json']['seen'],{self.optional[0]['entry']['record_id']:3})
        self.assertEqual(sum(op=='expand' for op,_,_ in self.calls),1)

    def test_lexical_ambient_without_relevant_context_is_empty(self):
        self.f.memory.update(recall_mode='ambient',semantic_fallback=False);self.f.publish_configuration()
        self.assertEqual(self.pair().stdout,b'{}\n')

    def test_default_ambient_taskless_start_is_non_provider(self):
        self.configure('opencode','SessionStart');self.f.event.pop('prompt');self.f.raw=encoded(self.f.event)
        self.f.memory.pop('recall_mode');self.f.memory['semantic_fallback']=True;self.f.publish_configuration()
        self.selected=[dict(record=dict(record_id=SESSION,version=1,body='Project decision\nUse the existing contract.'),mandatory=True,reason='required',evidence=[],authority=[],conflicts=[])]
        self.assertIn(b'Project decision',self.pair().stdout)

    def test_ordinary_semantic_recall_is_not_codex_turn_gated(self):
        self.configure('opencode','SessionStart')
        self.f.event['prompt']='Repair ./src/current.go and "exact source failure".';self.f.raw=encoded(self.f.event)
        self.f.memory['semantic_fallback']=True;self.f.publish_configuration();self.discovery=dict(state='unavailable')
        self.pair()
        request=next(body for op,body,_ in self.calls if op=='index')
        self.assertTrue(request['semantic'])
        self.assertIn('src/current.go',request['query'])
        self.assertNotIn('"src/current.go"',request['query'])

    def test_taskless_wake_reaches_pinned_legacy_bridge_and_real_recall(self):
        self.f.event['prompt']=self.f.wake();self.f.raw=encoded(self.f.event)
        result=self.pair()
        self.assertEqual(result.stdout,b'{}\n')
        request=next(body for op,body,_ in self.calls if op=='index')
        self.assertEqual(request['query'],self.f.root.name)
        self.assertEqual(self.state()['ses_OrdinaryA1.json']['last_recall']['optional_deferred'],'taskless_notification')

    def test_source_oracle_actually_refuses_a_changed_pinned_bridge(self):
        self.f.event['prompt']=self.f.wake();self.f.raw=encoded(self.f.event)
        result=self.source(corrupt_bridge=True)
        self.assertEqual(result.returncode,1)
        self.assertEqual(result.stdout,b'')
        self.assertIn(b'ValueError: pinned lifecycle bundle changed',result.stderr)
        self.assertEqual(self.calls,[])

    def test_shell_is_actual_launch_transport_with_existing_harness(self):
        self.shell_transport=True
        self.optional_note()
        self.assertEqual(self.pair().returncode,0)
        request=next(body for op,body,_ in self.calls if op=='index')
        self.assertEqual(request['scope']['task_id'],'opencode/ses_OrdinaryA1')

    def test_actual_ordinary_process_does_not_spawn_cli_or_python(self):
        path=self.root/'ordinary-execs.log'
        self.candidate_prefix=('/usr/bin/strace','-f','-qq','-o',str(path),'-s','1024','-e','trace=execve')
        self.pair()
        rows=path.read_text().splitlines()
        self.assertEqual(len([row for row in rows if 'execve(' in row]),1)
        self.assertIn('"memory", "--config"',rows[0])
        self.assertNotIn(str(self.cli),path.read_text())
        self.assertNotIn('python',path.read_text())

    def test_selected_full_compact_and_resume_prompt_source_matrix(self):
        self.selected=[dict(record=dict(record_id=SESSION,version=1,body='Project contract\nKeep the current decision.'),mandatory=True,reason='required',evidence=[],authority=[],conflicts=[])]
        for event in ('SessionStart','UserPromptSubmit'):
            for view in ('full','compact/1'):
                for source in ('startup','resume','compact'):
                    with self.subTest(event=event,view=view,source=source):
                        shutil.rmtree(self.f.memory['state_dir'],ignore_errors=True)
                        self.configure('opencode',event,source=source)
                        self.f.memory['recall_view']=view;self.f.publish_configuration()
                        self.assertIn(b'Project contract',self.pair().stdout)

    def test_env_guards_precede_invalid_host_identity(self):
        original=self.f.env
        for variable in ('CAIRN_LIFECYCLE_DISABLED','CAIRN_LIFECYCLE_CHILD'):
            with self.subTest(variable=variable):
                self.f.env=lambda:dict(original(),**{variable:'1'})
                self.f.event['session_id']='invalid';self.f.raw=encoded(self.f.event)
                self.assertEqual(self.pair().stdout,b'{}\n')
                self.assertEqual(self.calls,[])
                self.assertFalse(Path(self.f.memory['state_dir']).exists())

    def test_required_configuration_keys_keep_source_order_and_class(self):
        for key in ('repo','cairn','socket','token_file','state_dir'):
            with self.subTest(key=key):
                saved=self.f.memory.pop(key);self.f.publish_configuration()
                result=self.pair()
                self.assertEqual(result.returncode,1)
                self.assertIn(b'error_class=KeyError',result.stderr)
                self.assertEqual(self.calls,[])
                self.f.memory[key]=saved

    def test_invalid_recall_parameters_keep_source_failure_state(self):
        for changes in (dict(recall_mode='invalid'),dict(recall_view='invalid'),dict(context_lifecycle='compact/1'),dict(context_bytes=True),dict(context_bytes=999),dict(recall_task_key=SESSION)):
            with self.subTest(changes=changes):
                shutil.rmtree(self.f.memory['state_dir'],ignore_errors=True)
                self.f.memory.update(changes);self.f.publish_configuration()
                self.assertEqual(self.pair().returncode,1)
                for key in changes:self.f.memory.pop(key)
                self.f.memory.update(recall_mode='agent_tools',context_bytes=9500)

    def test_unported_cells_refuse_before_api_or_state(self):
        cases=(('bound',dict(inbox_recall_binding=str(self.f.binding)),'UserPromptSubmit'),
               ('provider',dict(recall_mode='ambient',semantic_fallback=True),'UserPromptSubmit'),
               ('capture',{},'PreCompact'),('observation',{},'PostToolUse'),('invented-shell',dict(harness='shell'),'SessionStart'))
        for name,changes,event in cases:
            with self.subTest(cell=name):
                shutil.rmtree(self.f.memory.get('state_dir',self.state_directory),ignore_errors=True)
                self.calls.clear();self.configure('opencode',event);self.f.memory.update(changes);self.f.publish_configuration()
                result=subprocess.run([str(self.binary),'memory','--config',str(self.f.memory_path)],input=self.f.raw,
                                      cwd=self.f.root,env=self.f.env(),capture_output=True,timeout=18)
                self.assertEqual(result.returncode,1)
                self.assertEqual(result.stdout,b'')
                self.assertEqual(self.calls,[])
                if name!='provider':self.assertFalse(Path(self.f.memory['state_dir']).exists())
                self.f.memory.pop('inbox_recall_binding',None);self.f.memory.pop('semantic_fallback',None)

    def unavailable_preview_pair(self, relevant):
        # B-1: a confirmed whole read can be charged and then rejected for
        # changed version. Only a remaining preview justifies the warning.
        self.f.memory.pop('recall_mode')
        self.f.memory.update(semantic_fallback=False,recall_observations=True)
        self.f.publish_configuration()
        self.selected=[dict(record=dict(record_id=SESSION,version=1,body='Required project decision\nKeep the current host contract.'),mandatory=True,reason='required',evidence=[],authority=[],conflicts=[])]
        entry=self.optional_note('Unrelated gardening context\nUse a different planting season.')
        entry['summary']='lifecycle recall implementation' if relevant else 'Unrelated gardening context'
        charged=[]
        def changed_version(request,response):
            response['selection']['record']['version']=99
            charged.append(response['credits_remaining'])
        self.expand_transform=changed_version
        trace=self.f.root/'invocation-trace';trace.mkdir(mode=0o700)
        source=self.source()
        expected_state=self.state();expected_calls=list(self.calls)
        expected_trace=[json.loads(line) for line in (trace/'trace.jsonl').read_text().splitlines()]
        self.assertEqual((self.confirmed_expands,charged),(1,[3]))
        shutil.rmtree(self.f.memory['state_dir'])
        (trace/'trace.jsonl').unlink();self.calls.clear();charged.clear()
        candidate=subprocess.run([str(self.binary),'memory','--config',str(self.f.memory_path)],input=self.f.raw,
                                 cwd=self.f.root,env=self.f.env(),capture_output=True,timeout=18)
        actual_state=self.state()
        actual_trace=[json.loads(line) for line in (trace/'trace.jsonl').read_text().splitlines()]
        self.assertEqual((self.confirmed_expands,charged),(1,[3]))
        actual_stdout,actual_arguments=self.normalized_stream(candidate.stdout)
        expected_stdout,expected_arguments=self.normalized_stream(source.stdout)
        self.assertEqual((candidate.returncode,actual_stdout,candidate.stderr),(source.returncode,expected_stdout,source.stderr))
        self.assertEqual(candidate.returncode,0)
        self.assertEqual(scalar_tree(actual_state),scalar_tree(expected_state))
        self.assertEqual([op for op,_,_ in self.calls],['version','index','expand','recall-observation'])
        self.assertEqual([op for op,_,_ in self.calls],[op for op,_,_ in expected_calls])
        for calls,arguments in ((self.calls,actual_arguments),(expected_calls,expected_arguments)):
            if relevant:
                self.check_pull_intents(calls,arguments)
            else:
                # No caller pull intent is displayed in this branch. The
                # private whole-read UUID must still be canonical UUIDv5.
                self.assertEqual(arguments,[])
                ident=next(body['request_id'] for op,body,_ in calls if op=='expand')
                self.assertEqual(str(uuid.UUID(ident)),ident)
                self.assertEqual(uuid.UUID(ident).version,5)
        for (op,actual,headers),(_,expected,source_headers) in zip(self.calls,expected_calls):
            actual,expected=dict(actual),dict(expected)
            for request in (actual,expected):
                ident=request.pop('request_id',None)
                if op!='version':self.assertEqual(str(uuid.UUID(ident)),ident)
                if op=='recall-observation':
                    for key in ('elapsed_ms','search_ms','pulls_ms','selector_ms'):self.assertGreaterEqual(request.pop(key),0)
            self.assertEqual(actual,expected)
            for declared,image in ((headers,sha(self.binary.read_bytes())),(source_headers,sha((start_fixture.ROOT/'integrations/lifecycle/memory.py').read_bytes()))):
                self.assertEqual(declared['Authorization'],'Bearer f02-synthetic-token')
                raw=declared['Cairn-Client-Diagnostics'];diagnostic=json.loads(base64.urlsafe_b64decode(raw+'='*(-len(raw)%4)))
                self.assertEqual(diagnostic['origin']['component'],'lifecycle-memory')
                self.assertEqual(diagnostic['origin']['implementation_id'],image)
        for records in (actual_trace,expected_trace):
            self.assertEqual([row['kind'] for row in records],['entry','parsed','exit'])
            self.assertEqual(len({row['inv'] for row in records}),1)
            for record in records:
                for key in ('at','duration_ms','pid','ppid'):
                    if key in record:self.assertGreaterEqual(record.pop(key),0)
                self.assertRegex(record.pop('inv'),r'^[0-9a-f]{32}$')
        self.assertEqual(actual_trace,expected_trace)
        context=json.loads(candidate.stdout)['hookSpecificOutput']['additionalContext']
        payload=self.context(candidate)
        self.assertEqual(bool(payload['index']),relevant)
        self.assertEqual('Optional body unavailable; search again before relying on its preview.\n' in context,relevant)
        status=actual_state['ses_OrdinaryA1.json']['last_recall']
        self.assertEqual(status['rejected'],{'unavailable':1})
        self.assertEqual(status['records'],[dict(record_id=entry['record_id'],version=3)] if relevant else [])
        self.assertEqual(status['bytes'],len(context.encode()))
        self.assertEqual(actual_trace[-1]['context_bytes'],len(context.encode()))
        report=next(body for op,body,_ in self.calls if op=='recall-observation')
        self.assertEqual((report['pull_calls'],report['injected_bytes']),(1,len(context.encode())))

    def test_unavailable_body_without_displayed_preview_keeps_only_required_context(self):
        self.unavailable_preview_pair(False)

    def test_unavailable_body_with_relevant_preview_displays_warning(self):
        self.unavailable_preview_pair(True)

    def test_trace_failure_stages_and_nil_uuid_match_source(self):
        original_env=self.f.env
        for case in ('disabled','identity','nil_uuid'):
            with self.subTest(case=case):
                self.calls.clear();self.f.env=original_env
                self.configure('agy' if case=='nil_uuid' else 'opencode')
                if case=='disabled':
                    self.f.env=lambda:dict(original_env(),CAIRN_LIFECYCLE_DISABLED='1')
                elif case=='identity':self.f.event['session_id']='invalid'
                else:
                    self.f.event.update(session_id=str(uuid.UUID(int=0)),turn_id=str(uuid.UUID(int=0)))
                self.f.raw=encoded(self.f.event)
                directory=self.f.root/'invocation-trace';directory.mkdir(mode=0o700,exist_ok=True)
                path=directory/'trace.jsonl'
                if path.exists():path.unlink()
                self.pair()
                records=[json.loads(line) for line in path.read_text().splitlines()]
                self.assertEqual([r['kind'] for r in records],['entry','parsed','exit']*2)
                left,right=records[:3],records[3:]
                for rows in (left,right):
                    self.assertEqual(len({r['inv'] for r in rows}),1)
                    for record in rows:
                        for key in ('at','duration_ms','pid','ppid'):
                            if key in record:self.assertGreaterEqual(record.pop(key),0)
                        self.assertRegex(record.pop('inv'),r'^[0-9a-f]{32}$')
                self.assertEqual(left,right)
                if case in ('disabled','identity'):self.assertEqual(right[-1]['stage'],'activation')
                else:
                    for record in right:
                        self.assertNotIn('session',record)
                        self.assertNotIn('turn',record)


if __name__=='__main__':unittest.main()
