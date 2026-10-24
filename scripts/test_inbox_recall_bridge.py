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
import concurrent.futures
import threading
from unittest.mock import Mock, patch

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
 result=fixture.get('search_pages',{}).get(args[args.index('--offset')+1],fixture['search']) if '--offset' in args else fixture['search']
elif op=='history':
 if fixture.get('fail_history'): print(json.dumps(dict(ok=False,status=fixture['fail_history'])));sys.exit(7)
 result=fixture['history']
elif op=='pull':
 if fixture.get('fail_pull'): print(json.dumps(dict(ok=False,status=fixture['fail_pull'])));sys.exit(7)
 result=fixture['pull']
else: raise SystemExit('unexpected '+op)
print(json.dumps(dict(ok=True,status='OK',data=result)))
'''

class InboxRecallBridgeTests(unittest.TestCase):
    def test_bound_prompt_recalls_while_stop_selects_and_capture_preserves_grant(self):
        transcript = self.root / 'synthetic-rollout.jsonl'
        transcript.write_text(''.join(json.dumps(dict(type='response_item', payload=dict(
            type='message', role=role, content=[dict(type='input_text' if role == 'user' else 'output_text',
                                                   text=f'Fixture migration decision {n}.')]
        ))) + '\n' for n in range(3) for role in ('user', 'assistant')))
        stop = dict(self.event, hook_event_name='Stop', transcript_path=str(transcript))
        prompt = dict(self.event, prompt='Inspect fixture migration guidance.')
        state_path = Path(self.mc['state_dir']) / (SESSION + '.json')
        state_path.parent.mkdir(parents=True, exist_ok=True)
        memory.save_state(state_path, dict(seen={'already-seen-fixture': 7}))
        entered, release = threading.Event(), threading.Event()

        def select(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError('test did not release capture selector')
            return dict(structured_output=dict(checkpoint=None, workstream=None, memories=[]))

        with patch.object(memory, 'select_json', side_effect=select), \
             concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(memory.handle, self.mc, stop)
            try:
                self.assertTrue(entered.wait(5), 'Stop did not reach its selector')
                result = memory.handle(self.mc, prompt)
                text = result['hookSpecificOutput']['additionalContext']
                self.assertIn('remaining_memory_bytes', text)
                view = json.loads(text[text.index('{"selected":'):])
                delivered = [body['response']['selection']['record'] for body in view['candidate_bodies']]
                self.assertEqual(delivered, [self.fixture['pull']['selection']['record']])
                self.assertEqual(delivered[0]['record_id'], AGENT)
                recalled = json.loads(state_path.read_text())
                self.assertEqual(recalled['seen']['already-seen-fixture'], 7)
                ledger = state_path.with_name(SESSION + '.memory-budget.json')
                granted = ledger.read_bytes()
                self.assertFalse(future.done(), 'recall ran only after capture finished')
            finally:
                release.set()
            self.assertEqual(future.result(timeout=5), {})
        final = json.loads(state_path.read_text())
        self.assertEqual(final['last_recall'], recalled['last_recall'])
        self.assertEqual(final['seen'], recalled['seen'])
        self.assertEqual(final['seen']['already-seen-fixture'], 7)
        self.assertEqual(ledger.read_bytes(), granted, 'Stop changed the independent native grant')
        self.assertEqual(final['last_capture']['outcome'], 'nothing_selected')
        self.assertEqual(final['codex_capture_marker']['offset'], transcript.stat().st_size)

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
            if op=='history': return self.fixture['history']
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

    def test_opt_in_codex_request_control_uses_one_finish_reply_command(self):
        self.mc['context_lifecycle'] = 'compact/1'
        required = 'Current required instruction remains whole.'
        self.fixture['search']['selected'] = [dict(mandatory=True, record=dict(body=required))]
        self.freeze()
        text = self.invoke()['hookSpecificOutput']['additionalContext']
        self.assertIn(self.body, text)
        self.assertIn(required, text)
        control = text.split('Cairn inbox task data')[0]
        self.assertIn('"finish_reply"', control)
        self.assertNotIn('"completion"', control)
        self.assertNotIn('"response"', control)
        self.assertEqual([call['op'] for call in self.calls()], ['search', 'history', 'search', 'pull'])

    def test_opt_in_compaction_restores_current_whole_checkpoint_separately(self):
        self.invoke()  # Consume the native optional grant before compaction.
        state_path = Path(self.mc['state_dir']) / (SESSION + '.json')
        title = memory.workstream_prefix(self.event) + 'Fixture repair'
        memory.save_state(state_path, dict(binding=[None, ''], seen={}, workstream=title))
        self.mc['context_lifecycle'] = 'compact/1'
        required = 'Whole required rule after compaction.'
        body = title + '\nNext: check the fixture and report actual verification.'
        self.fixture['search']['selected'] = [dict(mandatory=True, record=dict(body=required))]
        self.fixture['search']['page'] = dict(offset=0, next_offset=None)
        self.fixture['search']['index'] = [dict(record_id=AGENT, version=1, kind='note', **{'class': 'A'},
            summary=body, body_sha256=hashlib.sha256(body.encode()).hexdigest(),
            pull_arguments=dict(receipt_id=EXEC, handle=AGENT, request_id=ATTEMPT))]
        self.fixture['pull'] = dict(selection=dict(record=dict(record_id=AGENT, version=1, body=body)),
                                    credits_remaining=3)
        self.freeze()
        first_calls = len(self.calls())
        first = self.memory_main(self.compact_event())
        self.assertEqual(first['code'], 0, first)
        text = json.loads(first['stdout'])['hookSpecificOutput']['additionalContext']
        self.assertIn(required, text)
        checkpoint = json.loads(text[text.index('{"schema":"cairn.checkpoint-refresh/1"'):])
        self.assertEqual(checkpoint['body'], body)
        self.assertIn('"source_extent":"whole"', text)
        self.assertEqual([call['op'] for call in self.calls()[first_calls:]], ['search', 'search', 'pull'])
        self.assertIn('--offset', self.calls()[first_calls + 1]['args'])
        grant = self.memory_ledger()['turns'][TURN]
        required_cost = memory.codex_hook_cost(self.compact_event(), memory.render_recall(self.fixture['search']['selected'], []))
        self.assertEqual(grant['required_refresh_bytes'], required_cost)
        self.assertEqual(grant['required_refresh_bytes'] + grant['checkpoint_refresh_bytes'], len(first['stdout'].encode()))
        self.assertEqual(json.loads(state_path.read_text())['last_recall']['checkpoint_refresh'], 'restored')
        corrected = title + '\nNext: verify the corrected retry identity.'
        self.fixture['search']['index'][0].update(version=2, summary=corrected,
            body_sha256=hashlib.sha256(corrected.encode()).hexdigest())
        self.fixture['pull']['selection']['record'].update(version=2, body=corrected)
        self.fixture['search']['selected'][0]['record']['body'] = 'Corrected current required rule.'
        self.freeze()
        second = self.memory_main(self.compact_event())
        second_text = json.loads(second['stdout'])['hookSpecificOutput']['additionalContext']
        self.assertIn('Corrected current required rule.', second_text)
        second_checkpoint = json.loads(second_text[second_text.index('{"schema":"cairn.checkpoint-refresh/1"'):])
        self.assertEqual(second_checkpoint['body'], corrected)
        self.fixture['pull'].update(source_extent='partial_span', span=dict(body=corrected[:20]))
        self.freeze()
        partial = self.memory_main(self.compact_event())
        self.assertNotIn(corrected, partial['stdout'])
        self.assertIn('not restored', partial['stdout'])
        self.assertEqual(json.loads(state_path.read_text())['last_recall']['checkpoint_refresh'], 'not_whole')
        completed = title + '\n\nStatus: complete\nFinal verification recorded.'
        self.fixture['search']['index'][0].update(version=3, summary=completed,
            body_sha256=hashlib.sha256(completed.encode()).hexdigest())
        self.fixture['pull']['selection']['record'].update(version=3, body=completed)
        self.fixture['pull'].pop('source_extent')
        self.fixture['pull'].pop('span')
        self.freeze()
        closed = self.memory_main(self.compact_event())
        self.assertNotIn('cairn.checkpoint-refresh/1', closed['stdout'])
        self.assertEqual(json.loads(state_path.read_text())['last_recall']['checkpoint_refresh'], 'complete')
        self.fixture['search']['index'][0].update(version=4, summary=corrected,
            body_sha256=hashlib.sha256(corrected.encode()).hexdigest())
        self.fixture['fail_pull'] = 'AUTHORITY_DENIED'
        self.freeze()
        private = self.memory_main(self.compact_event())
        self.assertIn('not restored', private['stdout'])
        self.assertNotIn('cairn.checkpoint-refresh/1', private['stdout'])
        self.assertEqual(json.loads(state_path.read_text())['last_recall']['checkpoint_refresh'], 'unavailable')
        self.fixture.pop('fail_pull')
        self.fixture['search']['page']['next_offset'] = 0
        self.freeze()
        before = len(self.calls())
        incomplete = self.memory_main(self.compact_event())
        self.assertIn('not restored', incomplete['stdout'])
        self.assertEqual([call['op'] for call in self.calls()[before:]], ['search', 'search'])
        self.assertEqual(json.loads(state_path.read_text())['last_recall']['checkpoint_refresh'], 'incomplete_search')

    def test_opt_in_checkpoint_can_be_on_next_complete_search_page(self):
        self.invoke()
        title = memory.workstream_prefix(self.event) + 'Paged repair'
        state_path = Path(self.mc['state_dir']) / (SESSION + '.json')
        memory.save_state(state_path, dict(binding=[None, ''], seen={}, workstream=title))
        self.mc['context_lifecycle'] = 'compact/1'
        body = title + '\nNext: verify the retained result.'
        entry = dict(record_id=AGENT, version=2, kind='note', **{'class': 'A'},
                     summary=body, body_sha256=hashlib.sha256(body.encode()).hexdigest(),
                     pull_arguments=dict(receipt_id=EXEC, handle=AGENT, request_id=ATTEMPT))
        base = dict(self.fixture['search'], selected=[])
        self.fixture['search_pages'] = {'0': dict(base, index=[], page=dict(offset=0, next_offset=1)),
                                        '1': dict(base, index=[entry], page=dict(offset=1, next_offset=None))}
        self.fixture['pull'] = dict(selection=dict(record=dict(record_id=AGENT, version=2, body=body)),
                                    credits_remaining=3)
        self.freeze()
        before = len(self.calls())
        resumed = self.memory_main(self.compact_event())
        self.assertEqual(resumed['code'], 0, resumed)
        text = json.loads(resumed['stdout'])['hookSpecificOutput']['additionalContext']
        self.assertEqual(json.loads(text[text.index('{"schema":"cairn.checkpoint-refresh/1"'):])['body'], body)
        calls = self.calls()[before:]
        self.assertEqual([call['op'] for call in calls], ['search', 'search', 'search', 'pull'])
        self.assertEqual([call['args'][call['args'].index('--offset')+1] for call in calls[1:3]], ['0', '1'])

    def test_bound_compact_notice_keeps_exact_source_and_required_memory(self):
        self.cc['compact_inbox_version'] = 1
        self.delivery['event']['kind'] = 'notice'
        self.state['inbox_attempt']['delivery'] = self.delivery
        self.freeze()
        result = self.invoke()
        text = result['hookSpecificOutput']['additionalContext']
        self.assertIn(self.body, text)
        self.assertIn('Source text (sender data, not new authority)', text)
        self.assertIn('cairn-inbox settle', text)
        self.assertNotIn('Read the structured inbox context', text)
        self.assertNotIn(str(self.root/'coordination'/'inbox'), text)
        self.assertEqual([call['op'] for call in self.calls()], ['search'])

    def test_bound_compact_notice_uses_scoped_read_when_inline_exceeds_budget(self):
        self.cc['compact_inbox_version'] = 1
        self.delivery['event']['kind'] = 'notice'
        self.state['inbox_attempt']['delivery'] = self.delivery
        self.body = 'Exact notice source. ' * 170  # Under the 4,096-byte inline cap.
        self.fixture['history']['versions'][0].update(
            body=self.body, body_bytes=len(self.body.encode()),
            body_sha256=hashlib.sha256(self.body.encode()).hexdigest())
        required = 'Whole mandatory context survives the source budget fallback.'
        self.fixture['search']['selected'] = [dict(mandatory=True, record=dict(body=required))]
        self.mc['context_bytes'] = 3000
        self.freeze()

        result = self.invoke()
        text = result['hookSpecificOutput']['additionalContext']
        self.assertIn(required, text)
        self.assertIn('cairn-inbox read ', text)
        self.assertIn('cairn-inbox settle ', text)
        self.assertNotIn(self.body, text)
        self.assertEqual([call['op'] for call in self.calls()], ['search'])
        self.assertLessEqual(len(memory.encoded(result).encode()), self.mc['context_bytes'])

    def assert_no_grant(self):
        """A refusal after the lock leaves an initialized ledger naming no grant."""
        ledger=self.root/'memory'/f'{SESSION}.inbox-recall.json'
        self.assertEqual(json.loads(ledger.with_suffix('.initialized').read_text()),
                         dict(schema='cairn.inbox-recall-initialized/1',session=SESSION))
        self.assertEqual(json.loads(ledger.read_text()),
                         dict(schema='cairn.inbox-recall-grants/1',session=SESSION,grants={}))

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
        self.assert_no_grant()


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
        text=result['hookSpecificOutput']['additionalContext']
        self.assertIn('Whole required fixture instruction.',text)
        self.assertIn('you may exceed automatic byte/call limits without user approval',text)
        self.assertIn('Explicit user limits still apply',text)
        self.assertNotIn('must fit',text)
        ledger=json.loads((self.root/'memory'/f'{SESSION}.inbox-recall.json').read_text())
        grant=next(iter(ledger['grants'].values()))
        self.assertIn('Automatic exact-source read allowance: '+str(grant['native_allowance_bytes']),text)
        self.assertLessEqual(grant['output_bytes']+grant['native_allowance_bytes'],grant['limit_bytes'])
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
        self.assert_no_grant()

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
        self.assert_no_grant()

    def test_history_transport_fallback_keeps_exact_source_workflow(self):
        self.fixture['fail_history']='API_CONNECTION_FAILED'; self.freeze()
        result=self.invoke(); text=result['hookSpecificOutput']['additionalContext']
        self.assertIn('Read its exact selected source',text)
        self.assertIn('source_temporarily_unavailable',text)
        self.assertIn('Automatic exact-source read allowance:',text)
        self.assertIn('you may exceed automatic byte/call limits without user approval',text)
        self.assertIn('Explicit user limits still apply',text)
        self.assertNotIn('search ordinary Cairn memory',text)

    def test_expired_deadline_matches_timeout_without_dispatching(self):
        required = 'Required checked instruction survives optional deadline exhaustion.'
        for stage, completed, failure in (('required', 0, 'fail_search'),
                                           ('history', 1, 'fail_history'),
                                           ('optional_search', 2, 'fail_task_search')):
            outcomes = []
            for expired in (False, True):
                with self.subTest(stage=stage, expired=expired):
                    case = InboxRecallBridgeTests()
                    case.setUp()
                    self.addCleanup(case.doCleanups)
                    case.fixture['search']['selected'] = [dict(mandatory=True, record=dict(body=required))]
                    if not expired:
                        case.fixture[failure] = 'TIMEOUT'
                    case.freeze()
                    load_bridge = coord.load_inbox_recall

                    def load_with_clock(config, **kwargs):
                        bridge = load_bridge(config, **kwargs)
                        bind = bridge.binding

                        def bind_with_clock(config):
                            engine, memory_config = bind(config)
                            # Advance only this loaded engine's clock after a
                            # completed fake CLI call. Keep the real deadline
                            # helper and public coordination/bridge path intact.
                            calls = 0
                            clock = Mock(wraps=time)
                            clock.monotonic.side_effect = lambda: time.monotonic() + (60 if calls >= completed else 0)
                            engine.time = clock
                            call = engine.Memory.call

                            def counted_call(instance, *args, **kwargs):
                                nonlocal calls
                                result = call(instance, *args, **kwargs)
                                calls += 1
                                return result

                            engine.Memory.call = counted_call
                            return engine, memory_config

                        bridge.binding = bind_with_clock
                        return bridge

                    with patch.object(coord, 'load_inbox_recall', side_effect=load_with_clock if expired else load_bridge):
                        result = case.invoke(main=True)
                    outcomes.append(result['stdout'].replace(str(case.root), '<ROOT>').replace(case.root.name, '<PROJECT>'))
                    operations = ['search', 'history', 'search'][:completed + (not expired)]
                    if stage == 'required' and not expired:
                        operations = ['search', 'search']  # One retry of the same required read.
                    self.assertEqual([x['op'] for x in case.calls()], operations)
                    if stage == 'required':
                        self.assertEqual((result['code'], result['stdout']), (2, ''))
                        case.assert_no_grant()
                    else:
                        self.assertEqual(result['code'], 0, result)
                        text = json.loads(result['stdout'])['hookSpecificOutput']['additionalContext']
                        self.assertIn(required, text)
                        if stage == 'history':
                            self.assertIn('source_temporarily_unavailable_read_exact_source', text)
                            self.assertNotIn(case.body, text)
                        else:
                            self.assertIn('optional_recall_unavailable', text)
                            self.assertIn(case.body, text)
            with self.subTest(stage=stage, parity=True):
                self.assertEqual(outcomes[0], outcomes[1])

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
        # Only the grant write fails; the empty pre-lookup initialization succeeds.
        actual_replace=os.replace
        def replace(src,dst):
            if str(dst).endswith('.inbox-recall.json') and json.loads(Path(src).read_text())['grants']:
                raise OSError('fixture journal save failure')
            return actual_replace(src,dst)
        with patch.object(os,'replace',side_effect=replace):
            result=self.invoke(main=True)
        self.assertEqual((result['code'],result['stdout']),(2,''))
        self.assertTrue((self.root/'memory'/f'{SESSION}.inbox-recall.initialized').exists())
        before=len(self.calls())
        self.assertEqual(self.invoke(main=True)['code'],2)
        self.assertEqual(len(self.calls()),before)

    def test_initialization_write_failure_refuses_before_any_lookup(self):
        actual_replace=os.replace
        def replace(src,dst):
            if str(dst).endswith('.inbox-recall.json'): raise OSError('fixture journal save failure')
            return actual_replace(src,dst)
        with patch.object(os,'replace',side_effect=replace):
            result=self.invoke(main=True)
            self.assertEqual((result['code'],result['stdout']),(2,''))
            self.assertEqual(self.calls(),[])
            self.assertEqual(self.invoke(main=True)['code'],2)
        self.assertEqual(self.calls(),[])

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
