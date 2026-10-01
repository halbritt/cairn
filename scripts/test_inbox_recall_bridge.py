"""Opt-in inbox recall through both actual public hook handlers, no provider."""
import hashlib
import contextlib
import io
import sys
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

coord = module('inbox_recall_coord', ROOT/'integrations/lifecycle/coordination.py')
memory = module('inbox_recall_memory', ROOT/'integrations/lifecycle/memory.py')
SESSION = '11111111-1111-4111-8111-111111111111'
TURN = '22222222-2222-4222-8222-222222222222'
AGENT = '33333333-3333-4333-8333-333333333333'
EXEC = '44444444-4444-4444-8444-444444444444'
DELIVERY = '55555555-5555-4555-8555-555555555555'
ATTEMPT = '66666666-6666-4666-8666-666666666666'
RECORD = '77777777-7777-4777-8777-777777777777'
FAKE = '''#!/usr/bin/env python3
import json,pathlib,sys
root=pathlib.Path(__file__).parent
fixture=json.loads((root/'fixture.json').read_text())
args=sys.argv[1:]; op=args[5]
body=sys.stdin.read()
with (root/'calls.jsonl').open('a') as f:f.write(json.dumps(dict(op=op,args=args,body=body))+'\\n')
if op=='search':
 if fixture.get('fail_search'): print(json.dumps(dict(ok=False,status=fixture['fail_search'] if isinstance(fixture['fail_search'],str) else 'AUTHORITY_DENIED')));sys.exit(7)
 if fixture.get('fail_task_search') and len([x for x in (root/'calls.jsonl').read_text().splitlines() if json.loads(x)['op']=='search'])>1:
  print(json.dumps(dict(ok=False,status=fixture['fail_task_search'])));sys.exit(7)
 result=fixture['search']
elif op=='history':
 if fixture.get('fail_history'): print(json.dumps(dict(ok=False,status=fixture['fail_history'])));sys.exit(7)
 result=fixture['history']
elif op=='pull': result=fixture['pull']
else: raise SystemExit('unexpected '+op)
print(json.dumps(dict(ok=True,status='OK',data=result)))
'''

class InboxRecallBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'.git').mkdir()
        self.cli = self.root/'cairn'; self.cli.write_text(FAKE); self.cli.chmod(0o700)
        self.binding = self.root/'binding.json'
        common = dict(cairn=str(self.cli), socket=str(self.root/'api.sock'), token_file=str(self.root/'token'),
                      repo='fixture', harness='codex', inbox_recall_binding=str(self.binding))
        # These tests assert the exact retrieval traffic, so they do not enable the recall observation report.
        self.mc = dict(common, state_dir=str(self.root/'memory'), context_bytes=9500, recall_mode='agent_tools', recall_observations=False)
        self.cc = dict(common, state_dir=str(self.root/'coordination'), binding='fixture', native_delivery=True)
        self.process = dict(pid=os.getpid(), start=123, boot='fixture')
        self.native = TURN
        self.event = dict(session_id=SESSION, turn_id=TURN, cwd=str(self.root), hook_event_name='UserPromptSubmit')
        self.delivery = dict(delivery_id=DELIVERY, lease_id=ATTEMPT, state='leased', event=dict(
            event_id=TURN, kind='request', ref=dict(record_id=RECORD, version=2), **{'from':'agent/sender'}))
        session = dict(agent_id=AGENT, execution_id=EXEC)
        self.state = dict(process=self.process, workspace=str(self.root), agent=dict(session, native_session_id=SESSION,
            display_name='agent-fixture', inbox='agent/'+AGENT, context_revision=1,
            metadata=dict(harness='codex', model='', project=self.root.name, workspace=str(self.root), state='busy', delivery_mode='existing-session')),
            inbox_intent=dict(session=session, request_id=ATTEMPT, native_turn_id=TURN),
            inbox_attempt=dict(attempt_id=ATTEMPT, session=session, native_turn_id=TURN, delivery=self.delivery))
        self.body='Repair fixture validation while retaining current scope. 日本語 source condition.'
        note='Preserve the fixture scope until the source verification completes.'
        self.fixture=dict(search=dict(status='READY', destination=dict(name='hosted',allow_local=False), selected=[], omitted={},
            index=[dict(record_id=AGENT,version=1,summary='Fixture guidance',body_sha256=hashlib.sha256(note.encode()).hexdigest(),
                        pull_arguments=dict(receipt_id=EXEC,handle=AGENT,request_id=ATTEMPT))]),
            history=dict(historical=True,record_id=RECORD,versions=[dict(version=2,payload_available=True,body=self.body,
                body_bytes=len(self.body.encode()),body_sha256=hashlib.sha256(self.body.encode()).hexdigest())]),
            pull=dict(selection=dict(record=dict(record_id=AGENT,version=1,body=note)),credits_remaining=3))
        self.freeze()

    def freeze(self):
        for name,value in [('memory.json',self.mc),('coordination.json',self.cc),('fixture.json',self.fixture)]:
            (self.root/name).write_text(json.dumps(value)); (self.root/name).chmod(0o600)
        for name in ('memory.py','inbox_recall.py'):
            target=self.root/name; target.write_bytes((ROOT/'integrations/lifecycle'/name).read_bytes()); target.chmod(0o600)
        def file(path): return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        self.binding.write_text(json.dumps(dict(schema='cairn.inbox-recall-binding/1',enabled=True,
            bridge=file(self.root/'inbox_recall.py'),engine=file(self.root/'memory.py'),
            memory_config=file(self.root/'memory.json'),coordination_configs=[file(self.root/'coordination.json')])))
        self.binding.chmod(0o600)
        self.event['prompt']=coord.wake_message(dict(session=dict(agent_id=AGENT,execution_id=EXEC),delivery_id=DELIVERY))
        coord.write_state(coord.state_path(self.cc,SESSION),self.state)

    def calls(self):
        p=self.root/'calls.jsonl'
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def invoke(self, *, lease_failure=False, main=False):
        def api(config,op,request,**kwargs):
            if op=='event-renew':
                if lease_failure: raise coord.CoordinationError('STALE_LEASE','fixture refusal')
                return self.delivery
            if op=='agent-context': return self.state['agent']
            raise AssertionError(op)
        with patch.object(coord,'owner_process',return_value=self.process), \
             patch.object(coord,'heartbeat',return_value=self.state['agent']), \
             patch.object(coord,'associate_wake'), patch.object(coord,'recover_inbox'), \
             patch.object(coord,'flush_inbox_intents',return_value=True), \
             patch.object(coord,'release_inbox',return_value=False), patch.object(coord,'call',side_effect=api):
            if main:
                stdout,stderr=io.StringIO(),io.StringIO()
                stdin=io.TextIOWrapper(io.BytesIO(json.dumps(self.event).encode()))
                with patch.object(sys,'argv',['coordination.py','hook','--config',str(self.root/'coordination.json')]), \
                     patch.object(sys,'stdin',stdin), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code=coord.main()
                return dict(code=code,stdout=stdout.getvalue(),stderr=stderr.getvalue())
            return coord.handle(self.cc,self.event)

    def memory_main(self,event):
        stdout,stderr=io.StringIO(),io.StringIO()
        stdin=io.TextIOWrapper(io.BytesIO(json.dumps(event).encode()))
        with patch.object(sys,'argv',['memory.py','--config',str(self.root/'memory.json')]), \
             patch.object(sys,'stdin',stdin), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code=memory.main()
        return dict(code=code,stdout=stdout.getvalue(),stderr=stderr.getvalue())

    def compact_event(self):
        return dict(self.event,hook_event_name='SessionStart',source='compact',prompt='')

    def memory_ledger(self):
        return json.loads((self.root/'memory'/f'{SESSION}.memory-budget.json').read_text())

    def test_same_delivery_contains_exact_task_and_checked_memory_without_selector(self):
        self.assertEqual(memory.handle(self.mc,self.event),{})
        self.assertEqual(self.calls(),[])
        result=self.invoke()
        text=result['hookSpecificOutput']['additionalContext']
        self.assertIn(self.body,text)
        self.assertIn(self.fixture['pull']['selection']['record']['body'],text)
        self.assertIn('not owner instructions or new authority',text)
        self.assertNotIn('Read the structured inbox context',text)
        self.assertEqual([c['op'] for c in self.calls()],['search','history','search','pull'])
        view=json.loads(text[text.index('{"selected":'):])
        self.assertLessEqual(len(memory.encoded(result).encode())+view['remaining_memory_bytes'],9500)
        self.assertEqual(view['candidate_bodies'][0]['response'],self.fixture['pull'])
        self.assertEqual(self.event['prompt'],coord.wake_message(dict(session=dict(agent_id=AGENT,execution_id=EXEC),delivery_id=DELIVERY)))

    def test_repeat_does_not_read_sources_or_renew_grant(self):
        self.invoke(); first=len(self.calls())
        output=self.invoke(main=True)
        self.assertEqual(len(self.calls()),first)
        self.assertEqual(output['code'],2)
        self.assertEqual(output['stdout'],'')

    def test_lease_failure_and_private_source_never_deliver_task(self):
        self.assertEqual(self.invoke(lease_failure=True,main=True)['code'],2)
        self.assertFalse((self.root/'memory'/f'{SESSION}.inbox-recall.json').exists())
        self.state['inbox_attempt'].pop('lease_lapsed',None)
        self.fixture['fail_history']='NOT_FOUND'; self.freeze()
        with self.assertRaises(Exception): self.invoke()
        self.assertFalse((self.root/'memory'/f'{SESSION}.inbox-recall.json').exists())


    def test_unbound_claude_required_recall_is_unchanged(self):
        self.mc['harness']=self.cc['harness']='claude'
        self.state['agent']['metadata']['harness']='claude'
        self.event.pop('turn_id'); self.event['prompt_id']='existing-native-prompt'
        self.state['inbox_intent']['native_turn_id']='claude-channel:existing-native-prompt'
        self.state['inbox_attempt']['native_turn_id']='claude-channel:existing-native-prompt'
        self.fixture['search']['selected']=[dict(mandatory=True,record=dict(body='Whole Claude required instruction.'))]
        self.freeze()
        # No activation means the supported independent required-only path and
        # original exact-source workflow are unchanged.
        self.mc.pop('inbox_recall_binding'); self.cc.pop('inbox_recall_binding')
        self.freeze()
        result=memory.handle(self.mc,self.event)
        self.assertIn('Whole Claude required instruction.',result['hookSpecificOutput']['additionalContext'])
        text=self.invoke()['hookSpecificOutput']['additionalContext']
        self.assertIn('Read its exact selected source',text)
        self.assertNotIn(self.body,text)

    def test_admitted_source_search_uses_semantic_anchor_contract_only_when_requested(self):
        for semantic in (False, True):
            with self.subTest(semantic=semantic):
                case = InboxRecallBridgeTests()
                case.setUp()
                self.addCleanup(case.doCleanups)
                (case.root/'CONTRIBUTING.md').write_text('repository orientation')
                case.mc['semantic_fallback'] = semantic
                case.body = 'Repair src/cache.c after "RATE_OVERFLOW". Read CONTRIBUTING.md before coding.'
                case.fixture['history']['versions'][0].update(body=case.body,
                    body_bytes=len(case.body.encode()),body_sha256=hashlib.sha256(case.body.encode()).hexdigest())
                case.freeze()
                result = case.invoke()
                searches = [c for c in case.calls() if c['op'] == 'search']
                self.assertEqual(len(searches), 2)  # required context, then exact admitted task
                self.assertNotIn('--semantic', searches[0]['args'])
                args = searches[1]['args']
                query = args[-1]
                self.assertEqual('--semantic' in args, semantic)
                entities = [args[i+1] for i, arg in enumerate(args[:-1]) if arg == '--entity-file']
                self.assertEqual(entities, ['src/cache.c', 'CONTRIBUTING.md'])
                self.assertIn('src/cache.c', query)
                self.assertIn('CONTRIBUTING.md', query)
                self.assertIn('"RATE_OVERFLOW"', query)
                for path in entities:
                    self.assertEqual('"'+path+'"' in query, not semantic)
                self.assertIn('Repair src/cache.c', result['hookSpecificOutput']['additionalContext'])
                self.assertEqual([c['op'] for c in case.calls()], ['search','history','search','pull'])

    def test_notice_keeps_required_context_without_task_lookup(self):
        self.delivery['event']['kind']='notice'
        self.fixture['search']['selected']=[dict(mandatory=True,record=dict(body='Whole required fixture instruction.'))]
        self.freeze()
        result=self.invoke()
        self.assertIn('Whole required fixture instruction.',result['hookSpecificOutput']['additionalContext'])
        self.assertEqual([c['op'] for c in self.calls()],['search'])


    def test_no_admitted_request_keeps_required_context(self):
        self.state.pop('inbox_attempt'); self.state.pop('inbox_intent')
        self.fixture['search']['selected']=[dict(mandatory=True,record=dict(body='Required even without inbox work.'))]
        self.freeze()
        self.assertEqual(memory.handle(self.mc,self.event),{})
        result=self.invoke()
        self.assertIn('Required even without inbox work.',result['hookSpecificOutput']['additionalContext'])
        self.assertEqual([c['op'] for c in self.calls()],['search'])

    def test_wrong_source_version_and_oversize_required_are_refused(self):
        self.fixture['history']['versions'][0]['version']=1; self.freeze()
        with self.assertRaises(coord.NativePromptRefused): self.invoke()
        self.fixture['history']['versions'][0]['version']=2
        self.fixture['search']['selected']=[dict(mandatory=True,record=dict(body='日' * 4000))]; self.freeze()
        with self.assertRaises(coord.NativePromptRefused): self.invoke()
        self.assertFalse((self.root/'memory'/f'{SESSION}.inbox-recall.json').exists())

    def test_oversize_source_keeps_original_workflow_without_optional_lookup(self):
        self.body='large complete assignment ' * 600
        self.fixture['history']['versions'][0].update(body=self.body,body_bytes=len(self.body.encode()),
            body_sha256=hashlib.sha256(self.body.encode()).hexdigest()); self.freeze()
        result=self.invoke(); text=result['hookSpecificOutput']['additionalContext']
        self.assertIn('Read its exact selected source',text)
        self.assertIn('source_exceeds_delivery_budget',text)
        self.assertNotIn(self.body,text)
        self.assertEqual([c['op'] for c in self.calls()],['search','history'])
        self.assertLessEqual(len(memory.encoded(result).encode()),9500)

    def test_refused_history_budget_uses_labelled_original_workflow(self):
        self.fixture['fail_history']='BUDGET_REFUSED'; self.freeze()
        result=self.invoke()
        self.assertIn('source_read_budget_refused',result['hookSpecificOutput']['additionalContext'])
        self.assertEqual([c['op'] for c in self.calls()],['search','history'])

    def test_memory_lock_race_refuses_without_work_or_grant(self):
        import fcntl
        directory=self.root/'memory'; directory.mkdir(mode=0o700)
        with (directory/(SESSION+'.lock')).open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(coord.NativePromptRefused): self.invoke()
        self.assertEqual(self.calls(),[])
        self.assertFalse((directory/(SESSION+'.inbox-recall.json')).exists())
        self.assertIn(self.body,self.invoke()['hookSpecificOutput']['additionalContext'])

    def test_missing_grant_after_initialized_marker_fails_closed(self):
        self.invoke()
        (self.root/'memory'/f'{SESSION}.inbox-recall.json').unlink()
        before=len(self.calls())
        with self.assertRaises(coord.NativePromptRefused): self.invoke()
        self.assertEqual(len(self.calls()),before)

    def test_private_workspace_and_foreign_native_turn_do_not_read_memory(self):
        (self.root/'.cairn-no-memory').touch()
        self.assertEqual(memory.handle(self.mc,self.event),{})
        self.assertEqual(self.invoke(),{})
        self.assertEqual(self.calls(),[])
        (self.root/'.cairn-no-memory').unlink()
        self.event['turn_id']=AGENT
        with self.assertRaises(coord.NativePromptRefused): self.invoke()
        self.assertEqual(self.calls(),[])

    def test_actual_main_refuses_source_auth_required_failure_and_missing_codex_owner(self):
        for failure in ('private','required','missing_owner'):
            with self.subTest(failure=failure):
                self.fixture.pop('fail_history',None); self.fixture.pop('fail_search',None)
                self.state['inbox_attempt']['native_turn_id']=TURN
                if failure=='private': self.fixture['fail_history']='NOT_FOUND'
                if failure=='required': self.fixture['fail_search']=True
                if failure=='missing_owner': self.state['inbox_attempt'].pop('native_turn_id')
                self.freeze()
                result=self.invoke(main=True)
                self.assertEqual(result['code'],2)
                self.assertEqual(result['stdout'],'')
                self.assertNotIn(self.body,result['stderr'])

    def test_project_path_opt_out_matches_memory_hook(self):
        project=self.root/'separate'; project.mkdir(); (project/'.cairn-no-memory').touch()
        self.event['project_path']=str(project)
        self.assertEqual(memory.handle(self.mc,self.event),{})
        result=self.invoke()
        self.assertNotIn(self.body,result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(self.calls(),[])

    def test_transport_fallback_retains_required_context_but_policy_refusal_blocks(self):
        required='Required checked context survives optional transport failure.'
        self.fixture['search']['selected']=[dict(mandatory=True,record=dict(body=required))]
        self.fixture['fail_task_search']='API_CONNECTION_FAILED'; self.freeze()
        result=self.invoke(); text=result['hookSpecificOutput']['additionalContext']
        self.assertIn(self.body,text); self.assertIn(required,text)
        self.assertIn('optional_recall_unavailable',text)
        self.assertNotIn('search ordinary Cairn memory',text)
        self.assertEqual([x['op'] for x in self.calls()],['search','history','search'])

    def test_second_search_policy_failure_is_native_refusal(self):
        self.fixture['fail_task_search']='OPEN_CONFLICT'; self.freeze()
        self.assertEqual(self.invoke(main=True)['code'],2)
        self.assertFalse((self.root/'memory'/f'{SESSION}.inbox-recall.json').exists())

    def test_history_transport_fallback_keeps_exact_source_workflow(self):
        self.fixture['fail_history']='API_CONNECTION_FAILED'; self.freeze()
        result=self.invoke(); text=result['hookSpecificOutput']['additionalContext']
        self.assertIn('Read its exact selected source',text)
        self.assertIn('source_temporarily_unavailable',text)
        self.assertIn('No new optional lookup allowance',text)
        self.assertNotIn('search ordinary Cairn memory',text)

    def test_coordination_opt_out_does_not_drop_required_memory(self):
        self.fixture['search']['selected']=[dict(mandatory=True,record=dict(body='Independent required context.'))]
        self.freeze()
        with patch.dict(os.environ, {'CAIRN_COORDINATION_DISABLED':'1'}):
            self.assertEqual(self.invoke(),{})
            text=memory.handle(self.mc,self.event)['hookSpecificOutput']['additionalContext']
            self.assertIn('Independent required context.',text)
        (self.root/'.cairn-no-coordination').touch()
        self.assertEqual(self.invoke(),{})
        self.assertIn('Independent required context.',memory.handle(self.mc,self.event)['hookSpecificOutput']['additionalContext'])
        self.assertTrue(all(x['op']=='search' for x in self.calls()))

    def test_write_failure_after_marker_never_regrants(self):
        actual_replace=os.replace
        def replace(src,dst):
            if str(dst).endswith('.inbox-recall.json'): raise OSError('fixture journal save failure')
            return actual_replace(src,dst)
        with patch.object(os,'replace',side_effect=replace):
            self.assertEqual(self.invoke(main=True)['code'],2)
        self.assertTrue((self.root/'memory'/f'{SESSION}.inbox-recall.initialized').exists())
        before=len(self.calls())
        self.assertEqual(self.invoke(main=True)['code'],2)
        self.assertEqual(len(self.calls()),before)

    def test_malformed_grant_is_refused_without_more_reads(self):
        self.invoke()
        path=self.root/'memory'/f'{SESSION}.inbox-recall.json'
        data=json.loads(path.read_text()); data['grants'][next(iter(data['grants']))]=None
        path.write_text(json.dumps(data)); before=len(self.calls())
        self.assertEqual(self.invoke(main=True)['code'],2)
        self.assertEqual(len(self.calls()),before)

    def test_startup_context_is_charged_with_inbox_source_and_memory(self):
        self.fixture['search']['selected']=[dict(mandatory=True,record=dict(body='Required 日本語 "quoted" startup context.'))]
        self.freeze()
        startup=memory.handle(self.mc,dict(self.event,hook_event_name='SessionStart',source='startup',prompt='',turn_id=None))
        result=self.invoke(); text=result['hookSpecificOutput']['additionalContext']
        view=json.loads(text[text.index('{"selected":'):])
        self.assertLessEqual(len(memory.encoded(startup).encode())+len(memory.encoded(result).encode())+view['remaining_memory_bytes'],9500)
        ledger=json.loads((self.root/'memory'/f'{SESSION}.memory-budget.json').read_text())
        self.assertEqual(ledger['active_turn_id'],TURN)
        self.assertTrue(ledger['turns'][TURN]['granted'])


    def test_shared_codex_config_selects_explicit_active_coordination_home(self):
        self.cc['config_home']=str(self.root/'one'); self.freeze()
        second=dict(self.cc,config_home=str(self.root/'two'),binding='two',state_dir=str(self.root/'coordination-two'))
        path=self.root/'coordination-two.json'; path.write_text(json.dumps(second)); path.chmod(0o600)
        manifest=json.loads(self.binding.read_text())
        manifest['coordination_configs'].append(dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        self.binding.write_text(json.dumps(manifest))
        for home in ('one','two'):
            with self.subTest(home=home),patch.dict(os.environ,{'CODEX_HOME':str(self.root/home)}):
                self.assertEqual(memory.handle(self.mc,self.event),{})
        self.assertEqual(self.calls(),[])
        self.fixture['search']['selected']=[dict(mandatory=True,record=dict(body='Unmatched profile keeps required-only recall.'))]
        (self.root/'fixture.json').write_text(json.dumps(self.fixture))
        with patch.dict(os.environ,{'CODEX_HOME':str(self.root/'unmatched')}):
            result=memory.handle(self.mc,self.event)
            self.assertIn('Unmatched profile keeps required-only recall.',result['hookSpecificOutput']['additionalContext'])
        # Ambiguous home declarations refuse, never select the first row.
        second['config_home']=self.cc['config_home']; path.write_text(json.dumps(second))
        manifest['coordination_configs'][1]['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        self.binding.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError,'distinct explicit'): memory.handle(self.mc,self.event)

    def test_actual_stdout_unicode_framing_and_allowance_fit_exact_budget(self):
        self.fixture['search']['selected']=[dict(mandatory=True,record=dict(body='日本語 "quoted" \\ required.'))]
        self.freeze()
        output=self.invoke(main=True)
        self.assertEqual(output['code'],0)
        result=json.loads(output['stdout'])
        text=result['hookSpecificOutput']['additionalContext']
        view=json.loads(text[text.index('{"selected":'):])
        self.assertLessEqual(len(output['stdout'].encode())+view['remaining_memory_bytes'],9500)
        ledger=json.loads((self.root/'memory'/f'{SESSION}.inbox-recall.json').read_text())
        grant=next(iter(ledger['grants'].values()))
        self.assertEqual(grant['output_bytes'],len(output['stdout'].encode()))
        self.assertEqual(grant['native_allowance_bytes'],view['remaining_memory_bytes'])

    def test_unknown_required_outage_profile_budget_config_and_lock_do_not_stop(self):
        import fcntl
        self.invoke()  # This grant has no required selections.
        for failure in ('API_UNAVAILABLE','AUTHORITY_DENIED','BUDGET_REFUSED','config','lock'):
            with self.subTest(failure=failure):
                self.fixture.pop('fail_search',None); self.freeze()
                if failure not in ('config','lock'):
                    self.fixture['fail_search']=failure
                    (self.root/'fixture.json').write_text(json.dumps(self.fixture))
                if failure=='config':
                    config=dict(self.mc,unreviewed=True)
                    (self.root/'memory.json').write_text(json.dumps(config))
                with contextlib.ExitStack() as stack:
                    if failure=='lock':
                        lock=stack.enter_context((self.root/'memory'/f'{SESSION}.lock').open('a'))
                        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    result=self.memory_main(self.compact_event())
                self.assertEqual(result['code'],1)
                self.assertEqual(result['stdout'],'')

    def test_consumed_optional_grant_keeps_whole_current_required_refresh(self):
        body='Required 日本語 rule survives compact without optional renewal.'
        self.fixture['search']['selected']=[dict(mandatory=True,record=dict(body=body))]
        self.freeze(); self.invoke()
        before=self.memory_ledger()['turns'][TURN]
        total=0
        for _ in range(2):
            start=len(self.calls()); result=self.memory_main(self.compact_event())
            self.assertEqual(result['code'],0)
            self.assertIn(body,json.loads(result['stdout'])['hookSpecificOutput']['additionalContext'])
            total+=len(result['stdout'].encode())
            self.assertLessEqual(len(result['stdout'].encode()),9500)
            self.assertEqual([x['op'] for x in self.calls()[start:]],['search'])
        after=self.memory_ledger()['turns'][TURN]
        self.assertEqual(after['emitted_hook_bytes'],before['emitted_hook_bytes'])
        self.assertEqual(after['reserved_native_bytes'],before['reserved_native_bytes'])
        self.assertEqual(after['required_refresh_bytes'],total)
        self.assertGreater(after['emitted_hook_bytes']+after['reserved_native_bytes']+total,9500)
        self.assertTrue(after['granted'])
        self.assertEqual(after['required']['count'],1)
        self.assertNotIn(body,json.dumps(after))

    def test_known_required_outage_stops_until_current_empty_check_clears(self):
        self.fixture['search']['selected']=[dict(mandatory=True,record=dict(body='Known whole requirement.'))]
        self.freeze(); self.invoke()
        self.fixture['fail_search']='API_UNAVAILABLE'
        (self.root/'fixture.json').write_text(json.dumps(self.fixture))
        result=self.memory_main(self.compact_event())
        self.assertEqual(result['code'],0)
        self.assertIs(json.loads(result['stdout'])['continue'],False)
        self.assertTrue(self.memory_ledger()['turns'][TURN]['required']['pending'])
        # Only an authenticated successful fresh check can clear the requirement.
        self.fixture.pop('fail_search'); self.fixture['search']['selected']=[]
        (self.root/'fixture.json').write_text(json.dumps(self.fixture))
        self.assertEqual(json.loads(self.memory_main(self.compact_event())['stdout']),{})
        self.assertFalse(self.memory_ledger()['turns'][TURN]['required']['pending'])
        self.fixture['fail_search']='API_UNAVAILABLE'
        (self.root/'fixture.json').write_text(json.dumps(self.fixture))
        self.assertEqual(self.memory_main(self.compact_event())['code'],1)

    def test_first_known_required_save_failure_is_not_mislabelled_optional(self):
        self.fixture['search']['selected']=[dict(mandatory=True,record=dict(body='First currently required instruction.'))]
        self.freeze()
        event=dict(self.event,hook_event_name='SessionStart',source='startup',prompt='',turn_id=None)
        with patch.object(memory,'save_codex_budget',side_effect=OSError('fixture failure')):
            result=self.memory_main(event)
        self.assertEqual(result['code'],0)
        self.assertIs(json.loads(result['stdout'])['continue'],False)
        self.assertNotIn('First currently required instruction.',result['stdout'])
        self.assertFalse((self.root/'memory'/f'{SESSION}.memory-budget.json').exists())

    def test_current_required_wire_oversize_is_typed_and_persisted(self):
        self.invoke()
        selected=[dict(mandatory=True,record=dict(body=''))]
        selected[0]['record']['body']='x'*(9499-len(memory.render_recall(selected,[]).encode()))
        self.assertEqual(len(memory.render_recall(selected,[]).encode()),9499)
        self.fixture['search']['selected']=selected
        (self.root/'fixture.json').write_text(json.dumps(self.fixture))
        result=self.memory_main(self.compact_event())
        self.assertEqual(result['code'],0)
        self.assertIs(json.loads(result['stdout'])['continue'],False)
        grant=self.memory_ledger()['turns'][TURN]
        self.assertTrue(grant['required']['pending'])
        self.assertEqual(grant['required']['count'],1)
        self.assertEqual(len(grant['required']['selection_sha256']),64)
        self.assertEqual(grant.get('required_refresh_bytes',0),0)

    def test_explicit_policy_failure_without_prior_selection_stays_pending(self):
        self.invoke()
        self.fixture['fail_search']='POLICY_UNENFORCEABLE'
        (self.root/'fixture.json').write_text(json.dumps(self.fixture))
        first=self.memory_main(self.compact_event())
        self.assertIs(json.loads(first['stdout'])['continue'],False)
        self.assertTrue(self.memory_ledger()['turns'][TURN]['required']['pending'])
        self.fixture['fail_search']='API_UNAVAILABLE'
        (self.root/'fixture.json').write_text(json.dumps(self.fixture))
        second=self.memory_main(self.compact_event())
        self.assertIs(json.loads(second['stdout'])['continue'],False)

    def test_new_turn_does_not_inherit_old_task_required_failure(self):
        self.fixture['search']['selected']=[dict(mandatory=True,record=dict(body='Old task requirement.'))]
        self.freeze(); self.invoke()
        self.fixture['fail_search']='API_UNAVAILABLE'
        (self.root/'fixture.json').write_text(json.dumps(self.fixture))
        result=self.memory_main(dict(self.event,turn_id=AGENT,prompt='A new ordinary task.'))
        self.assertEqual(result['code'],1)
        self.assertEqual(result['stdout'],'')

    def test_bound_startup_wire_unit_is_not_double_charged(self):
        self.fixture['search']['selected']=[dict(mandatory=True,record=dict(body='Initial required 日本語 context.'))]
        self.freeze()
        event=dict(self.event,hook_event_name='SessionStart',source='startup',prompt='',turn_id=None)
        result=self.memory_main(event)
        self.assertEqual(result['code'],0)
        pending=self.memory_ledger()['pending']
        self.assertEqual(pending['emitted_hook_bytes'],len(result['stdout'].encode()))
        self.assertEqual(pending['serialized_hook_bytes'],pending['emitted_hook_bytes'])
        self.invoke()
        ledger=json.loads((self.root/'memory'/f'{SESSION}.inbox-recall.json').read_text())
        self.assertEqual(next(iter(ledger['grants'].values()))['prior_hook_charge_bytes'],len(result['stdout'].encode()))
        self.assertEqual(self.memory_ledger()['pending']['emitted_hook_bytes'],0)

    def test_legacy_writer_counter_change_invalidates_wire_stamp(self):
        self.fixture['search']['selected']=[dict(mandatory=True,record=dict(body='Initial requirement.'))]
        self.freeze()
        event=dict(self.event,hook_event_name='SessionStart',source='startup',prompt='',turn_id=None)
        self.memory_main(event)
        path=self.root/'memory'/f'{SESSION}.memory-budget.json'
        data=json.loads(path.read_text()); data['pending']['emitted_hook_bytes']+=10
        expected=memory.codex_emitted_wire_bytes(data['pending'])
        self.assertGreater(expected,data['pending']['emitted_hook_bytes'])
        path.write_text(json.dumps(data)); self.invoke()
        ledger=json.loads((self.root/'memory'/f'{SESSION}.inbox-recall.json').read_text())
        self.assertEqual(next(iter(ledger['grants'].values()))['prior_hook_charge_bytes'],expected)
        grant=self.memory_ledger()['turns'][TURN]
        self.assertEqual(grant['serialized_hook_bytes'],grant['emitted_hook_bytes'])

    def test_legacy_startup_reset_keeps_stale_stamp_without_refunding_grants(self):
        self.fixture['search']['selected']=[dict(mandatory=True,record=dict(body='Startup requirement.'))]
        self.freeze()
        event=dict(self.event,hook_event_name='SessionStart',source='startup',prompt='',turn_id=None)
        self.memory_main(event)
        path=self.root/'memory'/f'{SESSION}.memory-budget.json'
        data=json.loads(path.read_text()); old_emitted=data['pending']['emitted_hook_bytes']
        # Exact legacy transition: copy only old fields into the new turn, then
        # reset pending's counter while leaving unknown additive fields intact.
        data['turns'][AGENT]=dict(limit_bytes=9500,emitted_hook_bytes=old_emitted,
                                 reserved_native_bytes=0,granted=True)
        data['active_turn_id']=AGENT; data['pending']['emitted_hook_bytes']=0
        path.write_text(json.dumps(data))
        self.assertGreater(data['pending']['serialized_hook_bytes'],0)
        loaded=memory.codex_budget_ledger(path,SESSION,9500)
        self.assertEqual(loaded['turns'][AGENT]['emitted_hook_bytes'],old_emitted)
        self.assertEqual(memory.codex_emitted_wire_bytes(loaded['pending']),0)
        self.assertGreater(memory.codex_emitted_wire_bytes(loaded['turns'][AGENT]),old_emitted)
        self.assertEqual(self.memory_main(self.compact_event())['code'],0)
        self.assertEqual(self.memory_ledger()['turns'][AGENT]['emitted_hook_bytes'],old_emitted)
        self.assertGreater(self.memory_ledger()['turns'][AGENT]['required_refresh_bytes'],0)

    def test_disabled_manifest_keeps_original_paths_during_partial_staging(self):
        self.fixture['search']['selected']=[dict(mandatory=True,record=dict(body='Required during staging.'))]
        self.freeze()
        manifest=json.loads(self.binding.read_text()); manifest['enabled']=False
        self.binding.write_text(json.dumps(manifest))
        # A staged config can differ from its final pin while disabled.
        (self.root/'coordination.json').write_text(json.dumps(dict(self.cc,staging=True)))
        context=self.invoke()['hookSpecificOutput']['additionalContext']
        self.assertIn('Read its exact selected source',context)
        self.assertIn('search ordinary Cairn memory',context)
        self.assertNotIn(self.body,context)
        result=memory.handle(self.mc,self.event)
        self.assertIn('Required during staging.',result['hookSpecificOutput']['additionalContext'])
        self.assertEqual([x['op'] for x in self.calls()],['search'])
        self.assertFalse((self.root/'memory'/f'{SESSION}.inbox-recall.json').exists())
        before=self.memory_ledger()['pending']['emitted_hook_bytes']
        self.assertNotIn('serialized_hook_bytes',self.memory_ledger()['pending'])
        # Full validation remains available before the single activation flip.
        bridge=module('bridge_staged_validation',self.root/'inbox_recall.py')
        with self.assertRaises(ValueError): bridge.validate_binding(self.mc)
        (self.root/'coordination.json').write_text(json.dumps(self.cc))
        bridge.validate_binding(self.mc)
        manifest['enabled']=True; self.binding.write_text(json.dumps(manifest))
        # The old turn keeps its disabled decision across the manifest flip.
        output=self.invoke(main=True)
        self.assertEqual(output['code'],0)
        self.assertNotIn(self.body,json.loads(output['stdout'])['hookSpecificOutput']['additionalContext'])
        self.assertFalse((self.root/'memory'/f'{SESSION}.inbox-recall.json').exists())
        ledger=self.memory_ledger()
        self.assertIs(ledger['inbox_activation'][TURN],False)
        self.assertIsNone(ledger['active_turn_id'])
        self.assertEqual(ledger['pending']['emitted_hook_bytes'],before)
        # A genuinely new native turn may select the now enabled configuration;
        # choosing a mode itself creates no grant or active-turn mutation.
        resolved=memory.effective_inbox_config(self.mc,dict(self.event,turn_id=AGENT))
        self.assertIn('inbox_recall_binding',resolved)
        self.assertIs(self.memory_ledger()['inbox_activation'][AGENT],True)
        self.assertIsNone(self.memory_ledger()['active_turn_id'])

    def test_memory_first_disabled_pair_straddles_activation_without_new_grant(self):
        self.fixture['search']['selected']=[dict(mandatory=True,record=dict(body='Whole required across activation.'))]
        self.freeze()
        manifest=json.loads(self.binding.read_text()); manifest['enabled']=False
        self.binding.write_text(json.dumps(manifest))
        first=memory.handle(self.mc,self.event)
        self.assertIn('Whole required across activation.',first['hookSpecificOutput']['additionalContext'])
        before=self.memory_ledger()
        manifest['enabled']=True; self.binding.write_text(json.dumps(manifest))
        second=self.invoke(main=True)
        self.assertEqual(second['code'],0)
        text=json.loads(second['stdout'])['hookSpecificOutput']['additionalContext']
        self.assertIn('Read its exact selected source',text)
        self.assertNotIn(self.body,text)
        self.assertEqual(self.memory_ledger(),before)
        self.assertFalse((self.root/'memory'/f'{SESSION}.inbox-recall.json').exists())

    def test_coordinator_first_disabled_pair_preserves_independent_required_memory(self):
        self.fixture['search']['selected']=[dict(mandatory=True,record=dict(body='Whole required after old manual cue.'))]
        self.freeze()
        manifest=json.loads(self.binding.read_text()); manifest['enabled']=False
        self.binding.write_text(json.dumps(manifest))
        first=self.invoke()['hookSpecificOutput']['additionalContext']
        self.assertIn('Read its exact selected source',first)
        manifest['enabled']=True; self.binding.write_text(json.dumps(manifest))
        second=memory.handle(self.mc,self.event)
        self.assertIn('Whole required after old manual cue.',second['hookSpecificOutput']['additionalContext'])
        self.assertIs(self.memory_ledger()['inbox_activation'][TURN],False)
        self.assertFalse((self.root/'memory'/f'{SESSION}.inbox-recall.json').exists())
        self.assertEqual([x['op'] for x in self.calls()],['search'])

    def test_enabled_pair_keeps_decision_across_disable_until_both_callbacks_finish(self):
        self.assertEqual(memory.handle(self.mc,self.event),{})
        manifest=json.loads(self.binding.read_text()); manifest['enabled']=False
        self.binding.write_text(json.dumps(manifest))
        output=self.invoke(main=True)
        self.assertEqual(output['code'],0)
        self.assertIn(self.body,json.loads(output['stdout'])['hookSpecificOutput']['additionalContext'])
        self.assertIs(self.memory_ledger()['inbox_activation'][TURN],True)
        before=len(self.calls())
        self.assertEqual(memory.handle(self.mc,self.event),{})
        self.assertEqual(len(self.calls()),before)

    def test_disabled_manifest_does_not_enable_required_stop_or_new_units(self):
        manifest=json.loads(self.binding.read_text()); manifest['enabled']=False
        self.binding.write_text(json.dumps(manifest))
        self.fixture['fail_search']='POLICY_UNENFORCEABLE'
        (self.root/'fixture.json').write_text(json.dumps(self.fixture))
        result=self.memory_main(self.compact_event())
        self.assertEqual(result['code'],1)
        self.assertEqual(result['stdout'],'')

    def test_global_opt_out_and_child_never_read_manifest_or_write_activation(self):
        self.binding.unlink()
        for flag in ('CAIRN_LIFECYCLE_DISABLED','CAIRN_LIFECYCLE_CHILD'):
            with self.subTest(flag=flag),patch.dict(os.environ,{flag:'1'}):
                self.assertEqual(memory.handle(self.mc,self.event),{})
                self.assertEqual(self.invoke(),{})
        self.assertFalse((self.root/'memory').exists())
        self.assertEqual(self.calls(),[])
        self.fixture['search']['selected']=[dict(mandatory=True,record=dict(body='Independent required rule.'))]
        (self.root/'fixture.json').write_text(json.dumps(self.fixture))
        with patch.dict(os.environ,{'CAIRN_COORDINATION_DISABLED':'1'}):
            self.assertEqual(self.invoke(),{})
            self.assertIn('Independent required rule.',memory.handle(self.mc,self.event)['hookSpecificOutput']['additionalContext'])
        self.assertNotIn('inbox_activation',self.memory_ledger())

    def test_binding_drift_refuses_before_suppression_or_source_read(self):
        (self.root/'memory.json').write_text('{}')
        with self.assertRaises(ValueError): memory.handle(self.mc,self.event)
        self.assertEqual(self.calls(),[])

if __name__=='__main__': unittest.main()
