"""Bound Codex prompt recall through the real checked launcher and private API."""
import json
import subprocess
import shutil
from pathlib import Path

import test_f02_start as start_fixture


class F02PromptTests(start_fixture.F02StartTests):
    """Retain the landed start corpus while adding actual prompt consumers."""

    def source(self,prefix=(),*,controlled_ids=False):
        return super().source(prefix,controlled_ids=controlled_ids,bridge=True)

    def prompt(self, **changes):
        self.f.event.update(hook_event_name='UserPromptSubmit',
                            turn_id='33333333-3333-4333-8333-333333333333',
                            prompt='Inspect the current bounded author task.')
        self.f.event.update(changes)
        self.f.raw = start_fixture.encoded(self.f.event)

    def select_image(self,helper):
        self.f.binary.chmod(0o700)
        start_fixture.write(self.f.binary,helper.read_bytes(),0o500)
        manifest=json.loads(self.f.binding.read_text())
        manifest['binary']['sha256']=start_fixture.sha(helper.read_bytes())
        start_fixture.write(self.f.binding,start_fixture.encoded(manifest))
        self.f.receipt['binary']=manifest['binary']
        self.f.publish_configuration()

    def optional_note(self,body='Current source: 日本語 😀\n',*,offset=0,length=32):
        length=min(length,len(body.encode())-offset)
        entry=dict(record_id='55555555-5555-4555-8555-555555555555',version=3,kind='note',**{'class':'A'},
                   summary=body.encode()[offset:offset+length].decode(),body_sha256=start_fixture.sha(body.encode()),
                   summary_span=dict(offset=offset,length=length))
        self.optional=[dict(entry=entry,body=body)]
        helper=self.controlled_image()
        self.select_image(helper)
        self.prompt()
        return entry

    def context(self,result):
        self.assertEqual(result.returncode,0,result.stderr)
        text=json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
        return json.loads(text[text.index('{"selected"'):])

    def test_current_whole_candidate_is_delivered_with_real_receipt_charge(self):
        self.optional_note()
        result=self.pair(controlled_ids=True)
        context=self.context(result)
        self.assertEqual(context['candidate_bodies'][0]['response']['selection']['record']['body'],self.optional[0]['body'])
        self.assertEqual(context['candidate_inspection']['pull_calls'],1)
        self.assertEqual(context['candidate_inspection']['delivered_records'],1)
        self.assertEqual(context['index'],[])
        self.assertEqual(sum(op=='expand' for op,_,_ in self.calls),1)

    def test_whole_refusal_falls_back_to_checked_span_without_refund(self):
        self.optional_note('Owner task opening.\n'+'preamble '*180+'Bounded matching source. '+'tail '*140,offset=1500,length=32)
        self.expand_refusal='BUDGET_REFUSED'
        result=self.pair(controlled_ids=True)
        context=self.context(result)
        supplied=context['candidate_bodies'][0]
        self.assertEqual(supplied['response']['source_extent'],'partial_span')
        self.assertEqual(supplied['source_opening_excerpt'],dict(status='unavailable',reason='pull_limit'))
        self.assertEqual(context['candidate_inspection']['pull_calls'],2)
        self.assertEqual(sum(op=='expand' for op,_,_ in self.calls),2)

    def test_whole_context_refusal_reuses_paid_bytes_and_opening(self):
        self.optional_note('Owner task opening.\n'+'prefix '*300+'Matching source context. '+'tail '*1400,offset=2000,length=32)
        context=self.context(self.pair(controlled_ids=True))
        supplied=context['candidate_bodies'][0]
        self.assertEqual(supplied['response']['source_extent'],'partial_span')
        self.assertEqual(supplied['response']['span_origin'],'whole_pull')
        opening=supplied['source_opening_excerpt']
        self.assertEqual(opening['status'],'provided')
        self.assertIn('Owner task opening.',opening['span']['body'])
        self.assertEqual(context['candidate_inspection']['pull_calls'],1)
        self.assertEqual(sum(op=='expand' for op,_,_ in self.calls),1)

    def test_oversized_checked_excerpt_fits_without_cutting_its_preview(self):
        entry=self.optional_note('Owner task.\n'+'prefix '*400+'matching '*500+'tail '*1400,offset=2400,length=32)
        entry['match_span']=dict(offset=2000,length=3000)
        context=self.context(self.pair(controlled_ids=True))
        response=context['candidate_bodies'][0]['response']
        span=response['span']
        self.assertEqual(span['offset'],2000)
        self.assertLess(span['end'],5000)
        self.assertGreaterEqual(span['end'],entry['summary_span']['offset']+entry['summary_span']['length'])
        self.assertIn(entry['summary'],span['body'])
        self.assertEqual(context['candidate_inspection']['pull_calls'],1)

    def test_paired_api_opening_is_checked_before_optional_delivery(self):
        self.optional_note('Owner task opening.\n'+'preamble '*180+'matching '*200,offset=1500,length=32)
        self.expand_refusal='BUDGET_REFUSED'
        raw=self.optional[0]['body'].encode()
        for corrupt in (False,True):
            with self.subTest(corrupt=corrupt):
                shutil.rmtree(self.f.memory['state_dir'],ignore_errors=True)
                self.calls.clear()
                def paired(request,response):
                    opening=raw[:128]
                    response['source_opening']=dict(offset=0,end=len(opening),total_bytes=len(raw),sha256='0'*64 if corrupt else start_fixture.sha(opening),body=opening.decode())
                self.expand_transform=paired
                context=self.context(self.pair(controlled_ids=True))
                if corrupt:
                    self.assertEqual(context['candidate_bodies'],[])
                    self.assertEqual(context['candidate_inspection']['refusals'],dict(whole_pull_budget=1,span_unavailable=1))
                else:
                    supplied=context['candidate_bodies'][0]
                    self.assertEqual(supplied['response']['source_opening']['body'],raw[:128].decode())
                    self.assertNotIn('source_opening_excerpt',supplied)
                self.assertEqual(context['candidate_inspection']['pull_calls'],2)

    def test_preview_without_immutable_body_hash_spends_no_pull(self):
        self.optional_note()
        self.optional[0]['entry']['body_sha256']=''
        context=self.context(self.pair(controlled_ids=True))
        self.assertEqual(len(context['index']),1)
        self.assertNotIn('candidate_inspection',context)
        self.assertFalse(any(op=='expand' for op,_,_ in self.calls))

    def test_changed_whole_bytes_remain_preview_with_charged_attempt(self):
        self.optional_note()
        self.expand_transform=lambda request,response: response['selection']['record'].update(body='Unconfirmed changed source.')
        context=self.context(self.pair(controlled_ids=True))
        self.assertEqual(context['candidate_bodies'],[])
        self.assertEqual(context['candidate_inspection']['refusals'],dict(whole_pull_unavailable=1))
        self.assertEqual(context['candidate_inspection']['pull_calls'],1)
        self.assertEqual(len(context['index']),1)

    def test_stale_handle_compact_view_preserves_recovery_without_retry(self):
        self.optional_note()
        self.f.memory['recall_view']='compact/1'
        self.f.publish_configuration()
        self.expand_refusal='STALE_HANDLE'
        context=self.context(self.pair(controlled_ids=True))
        self.assertEqual(context['candidate_inspection']['refusals'],dict(stale_handle=1))
        self.assertIn('Search again',context['candidate_recovery'])
        self.assertEqual(sum(op=='expand' for op,_,_ in self.calls),1)

    def test_native_canonical_zero_turn_retains_source_shape(self):
        self.prompt(turn_id='00000000-0000-0000-0000-000000000000')
        self.pair()
        grant=self.state()[start_fixture.SESSION+'.memory-budget.json']['turns'][self.f.event['turn_id']]
        self.assertTrue(grant['granted'])

    def test_retired_turn_does_not_replace_the_new_active_epoch(self):
        first='33333333-3333-4333-8333-333333333333'
        second='44444444-4444-4444-8444-444444444444'
        for turn in (first,second):
            self.prompt(turn_id=turn)
            self.calls.clear()
            self.pair()
        before=self.state()[start_fixture.SESSION+'.memory-budget.json']
        self.prompt(turn_id=first)
        self.calls.clear()
        self.assertEqual(self.pair().stdout,b'{}\n')
        after=self.state()[start_fixture.SESSION+'.memory-budget.json']
        self.assertEqual(after,before)
        self.assertEqual(after['active_turn_id'],second)

    def test_new_turn_optional_failure_does_not_borrow_retired_requirement(self):
        self.prompt()
        self.selected=[dict(record=dict(record_id='22222222-2222-4222-8222-222222222222',version=1,body='First current required source.'),mandatory=True,reason='required',evidence=[],authority=[])]
        self.pair()
        self.calls.clear()
        self.prompt(turn_id='44444444-4444-4444-8444-444444444444')
        self.refusal='API_UNAVAILABLE'
        self.selected=[]
        self.assertEqual(self.pair().returncode,1)
        self.calls.clear()
        self.refusal='OPEN_CONFLICT'
        self.assertEqual(self.pair().returncode,2)
        budget=self.state()[start_fixture.SESSION+'.memory-budget.json']
        self.assertEqual(budget['active_turn_id'],self.f.event['turn_id'])
        self.assertTrue(budget['turns'][self.f.event['turn_id']]['required']['pending'])
        self.assertEqual(len(budget['turns']),2)

    def test_final_grant_directory_sync_failure_cannot_refund_its_native_allowance(self):
        self.prompt()
        result=self.pair(('/usr/bin/strace','-f','-qq','-e','trace=fsync','-e','inject=fsync:error=EIO:when=6','-o',str(self.root/'final-sync-fault.trace')))
        self.assertEqual(result.returncode,1)
        self.assertEqual(result.stdout,b'')
        before=self.state()[start_fixture.SESSION+'.memory-budget.json']
        grant=before['turns'][before['active_turn_id']]
        self.assertTrue(grant['granted'])
        self.assertGreater(grant['reserved_native_bytes'],0)
        self.assertGreater(grant['emitted_hook_bytes'],0)
        self.calls.clear()
        self.assertEqual(self.pair().stdout,b'{}\n')
        self.assertEqual(self.state()[start_fixture.SESSION+'.memory-budget.json'],before)

    def test_disabled_binding_prompt_keeps_source_unbound_accounting(self):
        self.prompt()
        self.f.publish_configuration(enabled=False)
        self.assertEqual(self.pair().returncode,0)
        grant=self.state()[start_fixture.SESSION+'.memory-budget.json']['turns'][self.f.event['turn_id']]
        self.assertTrue(grant['granted'])
        self.assertNotIn('serialized_hook_bytes',grant)

    def test_prompt_go_python_go_keeps_retired_charges_and_opaque_fields(self):
        self.prompt()
        result=self.f.public('memory')
        self.assertEqual((result.returncode,result.stderr),(0,b''))
        path=Path(self.f.memory['state_dir'])/(start_fixture.SESSION+'.memory-budget.json')
        before=json.loads(path.read_text())
        first=before['active_turn_id']
        opaque=dict(integer=10**120,unicode='日本語 😀',negative_zero=-0.0)
        before['source_opaque']=opaque
        before['pending']['pending_opaque']=opaque
        before['turns'][first]['required_refresh_bytes']=10**120
        start_fixture.oracle.save_codex_ledger(path,before)
        self.prompt(turn_id='44444444-4444-4444-8444-444444444444',workstream='Changed binding')
        retained=self.source()
        self.assertEqual((retained.returncode,retained.stderr),(0,b''))
        second=json.loads(path.read_text())
        self.prompt(turn_id='55555555-5555-4555-8555-555555555555',workstream='Another binding')
        resumed=self.f.public('memory')
        self.assertEqual((resumed.returncode,resumed.stderr),(0,b''))
        after=json.loads(path.read_text())
        self.assertEqual(start_fixture.scalar_tree(after['source_opaque']),start_fixture.scalar_tree(opaque))
        self.assertEqual(start_fixture.scalar_tree(after['pending']['pending_opaque']),start_fixture.scalar_tree(opaque))
        self.assertEqual(after['turns'][first],before['turns'][first])
        self.assertEqual(after['turns'][second['active_turn_id']],second['turns'][second['active_turn_id']])
        self.assertEqual(len(after['turns']),3)

    def test_prompt_checked_dispatch_and_trace_report_actual_context(self):
        self.prompt()
        directory=self.f.root/'invocation-trace'; directory.mkdir(mode=0o700)
        log=self.f.root/'prompt-exec.trace'
        result=subprocess.run(['/usr/bin/strace','-f','-qq','-o',str(log),'-s','1024','-e','trace=execve,openat',str(self.f.launcher('memory'))],
                              input=self.f.raw,capture_output=True,cwd=self.f.root,env=self.f.env(),timeout=18)
        self.assertEqual((result.returncode,result.stderr),(0,b''))
        output=json.loads(result.stdout)['hookSpecificOutput']
        self.assertEqual(output['hookEventName'],'UserPromptSubmit')
        rows=log.read_text().splitlines()
        executions=[row for row in rows if 'execve("/proc/self/fd/' in row]
        self.assertEqual(len(executions),1)
        self.assertIn('"memory", "recall"',executions[0])
        self.assertFalse(any('execve("'+str(self.cli)+'"' in row for row in rows))
        records=[json.loads(line) for line in (directory/'trace.jsonl').read_text().splitlines()]
        self.assertEqual([r['kind'] for r in records],['entry','parsed','exit'])
        final=records[-1]
        self.assertEqual(final['outcome'],'engine_completed')
        self.assertEqual(final['context_bytes'],len(output['additionalContext'].encode()))
        self.assertEqual(final['main_return_code'],0)

    def competing_notes(self):
        first=self.optional_note('First current source.\n',length=20)
        second=dict(first,record_id='66666666-6666-4666-8666-666666666666',summary='Second current source.',body_sha256=start_fixture.sha(b'Second current source.\n'))
        conflict=dict(id='77777777-7777-4777-8777-777777777777',version=1,members=[dict(record_id=e['record_id'],version=e['version']) for e in (first,second)])
        first['conflicts']=second['conflicts']=[conflict]
        self.optional.append(dict(entry=second,body='Second current source.\n'))
        return second

    def test_whole_competing_group_is_delivered_in_one_receipt_call(self):
        second=self.competing_notes()
        def complete(request,response):
            response['competing']=[dict(record=dict(record_id=second['record_id'],version=3,kind='note',**{'class':'A'},body=self.optional[1]['body']),mandatory=False,reason='candidate',conflicts=second['conflicts'])]
        self.expand_transform=complete
        context=self.context(self.pair(controlled_ids=True))
        self.assertEqual(context['candidate_inspection']['delivered_records'],2)
        self.assertEqual(context['candidate_inspection']['pull_calls'],1)
        self.assertEqual(len(context['candidate_bodies'][0]['response']['competing']),1)
        self.assertEqual(context['index'],[])

    def test_incomplete_competing_expansion_never_splits_the_group(self):
        self.competing_notes()
        context=self.context(self.pair(controlled_ids=True))
        self.assertEqual(context['candidate_bodies'],[])
        self.assertEqual(len(context['index']),2)
        self.assertEqual(context['candidate_inspection']['refusals'],dict(whole_pull_unavailable=1))
        self.assertEqual(context['candidate_inspection']['pull_calls'],1)

    def test_whole_invalid_unicode_preserves_source_failure_and_grant(self):
        self.optional_note()
        self.expand_transform=lambda request,response: response['selection']['record'].update(body='\ud800')
        result=self.pair(controlled_ids=True)
        self.assertEqual(result.returncode,1)
        self.assertIn(b'error_class=ValueError',result.stderr)
        budget=self.state()[start_fixture.SESSION+'.memory-budget.json']
        grant=budget['turns'][self.f.event['turn_id']]
        self.assertFalse(grant['granted'])
        self.assertEqual(grant['emitted_hook_bytes'],0)
        self.assertEqual(sum(op=='expand' for op,_,_ in self.calls),1)

    def test_semantic_prompt_keeps_paths_as_entity_and_text_hints(self):
        self.optional_note()
        self.f.memory['semantic_fallback']=True
        self.f.memory['recall_observations']=True
        self.f.publish_configuration()
        self.discovery=dict(state='unavailable')
        self.prompt(prompt='Repair ./src/current.go and "exact source failure".')
        result=self.pair(controlled_ids=True)
        self.assertEqual(result.returncode,0)
        request=next(body for op,body,_ in self.calls if op=='index')
        self.assertTrue(request['semantic'])
        self.assertIn('src/current.go',request['query'])
        self.assertNotIn('"src/current.go"',request['query'])
        self.assertIn('"exact source failure"',request['query'])
        meter=next(body for op,body,_ in self.calls if op=='recall-observation')
        self.assertEqual(meter['hook_event'],'UserPromptSubmit')
        self.assertEqual(meter['search_calls'],1)
        self.assertEqual(meter['pull_calls'],1)
        self.assertEqual(meter['injected_bytes'],len(json.loads(result.stdout)['hookSpecificOutput']['additionalContext'].encode()))
        status=self.state()[start_fixture.SESSION+'.json']['last_recall']
        self.assertEqual(status['discovery'],'lexical_fallback')

    def test_valid_activation_required_promotion_and_protocol_refusal(self):
        self.prompt()
        self.charged_grant()
        path=Path(self.f.memory['state_dir'])/(start_fixture.SESSION+'.memory-budget.json')
        grant=json.loads(path.read_text())
        grant['turns'][grant['active_turn_id']]['required']=dict(count=1,selection_sha256='a'*64,pending=True)
        start_fixture.write(path,start_fixture.encoded(grant))
        before=path.read_bytes()
        trace=self.f.root/'invocation-trace'; trace.mkdir(mode=0o700)
        cases=((2,'','',None),
               (1,'','{"error":{"class":"ValueError","reason":"activation","errno":null}}\n','ValueError'),
               (1,'','{"error":{"class":"BlockingIOError","reason":"activation","errno":11}}\n','BlockingIOError'),
               (0,'{"stdout":"","stderr":"","exit_code":2,"trace":{}}\n','',None))
        for event in ('UserPromptSubmit','SessionStart'):
            self.f.event['hook_event_name']=event
            self.f.raw=start_fixture.encoded(self.f.event)
            for i,(code,out,err,label) in enumerate(cases):
                with self.subTest(event=event,code=code,label=label):
                    build=self.root/('private-fault-'+event+str(i)); build.mkdir()
                    script='package main\nimport("os";"io";"fmt")\nfunc main(){io.Copy(io.Discard,os.Stdin);fmt.Fprint(os.Stdout,'+json.dumps(out)+');fmt.Fprint(os.Stderr,'+json.dumps(err)+');os.Exit('+str(code)+')}\n'
                    (build/'main.go').write_text(script)
                    helper=build/'fault'
                    subprocess.run(['go','build','-o',str(helper),str(build/'main.go')],check=True)
                    self.select_image(helper)
                    result=self.f.public('memory')
                    if label:
                        diagnostic=('Cairn lifecycle diagnostic: phase=activation error_class='+label+'\n').encode()
                        expected=(2,b'',diagnostic+b'Cairn: current required context could not be restored.\n') if event=='UserPromptSubmit' else (0,b'{"continue":false,"stopReason":"Bound inbox recall cannot restore required context."}\n',diagnostic)
                    else:
                        expected=(1,b'',b'Cairn lifecycle diagnostic: phase=activation error_class=UnexpectedError\n')
                    self.assertEqual((result.returncode,result.stdout,result.stderr),expected)
                    self.assertEqual(path.read_bytes(),before)
                    final=json.loads((trace/'trace.jsonl').read_text().splitlines()[-1])
                    if label=='BlockingIOError':
                        self.assertEqual(final['cause'],'blocking_io')
                        self.assertEqual(final['outcome'],'required_refused')
        self.assertFalse(self.calls)

    def test_owner_prompt_receives_one_native_grant(self):
        self.prompt()
        result = self.pair()
        context = json.loads(result.stdout)['hookSpecificOutput']
        self.assertEqual(context['hookEventName'],'UserPromptSubmit')
        self.assertIn('remaining_memory_bytes',context['additionalContext'])
        budget = self.state()[start_fixture.SESSION+'.memory-budget.json']
        grant = budget['turns'][self.f.event['turn_id']]
        self.assertTrue(grant['granted'])
        self.assertEqual(grant['emitted_hook_bytes'],len(result.stdout))
        self.assertGreater(grant['reserved_native_bytes'],0)

    def test_degraded_prompt_retains_the_native_grant(self):
        self.status = 'DEGRADED_NO_EMBEDDINGS'
        self.prompt()
        self.assertEqual(self.pair().returncode,0)

    def test_prompt_required_context_refuses_without_partial_delivery(self):
        self.prompt()
        self.refusal='OPEN_CONFLICT'
        result=self.pair()
        self.assertEqual(result.returncode,2)
        self.assertEqual(result.stdout,b'')
        self.assertEqual(result.stderr,b'Cairn lifecycle diagnostic: phase=handle error_class=RequiredContextRefused\nCairn: current required context could not be restored.\n')

    def test_prompt_optional_api_failure_is_nonblocking(self):
        self.prompt()
        self.refusal='API_UNAVAILABLE'
        self.assertEqual(self.pair().returncode,1)

    def test_prompt_restart_cannot_renew_native_allowance(self):
        self.prompt()
        self.pair()
        previous=self.state()[start_fixture.SESSION+'.memory-budget.json']
        self.calls.clear()
        self.assertEqual(self.pair().stdout,b'{}\n')
        current=self.state()[start_fixture.SESSION+'.memory-budget.json']
        self.assertEqual(current,previous)

    def test_next_prompt_receives_a_distinct_grant(self):
        self.prompt()
        self.pair()
        self.calls.clear()
        self.prompt(turn_id='44444444-4444-4444-8444-444444444444')
        self.pair()
        current=self.state()[start_fixture.SESSION+'.memory-budget.json']
        self.assertEqual(len(current['turns']),2)
        self.assertTrue(all(g['granted'] for g in current['turns'].values()))

    def test_reserved_wake_leaves_context_to_qualified_coordination(self):
        self.prompt(prompt=self.f.wake())
        self.assertEqual(self.pair().stdout,b'{}\n')
        self.assertFalse(self.calls)
        self.assertNotIn(start_fixture.SESSION+'.json',self.state())

    def test_coordination_opt_out_keeps_independent_required_check(self):
        self.prompt(prompt=self.f.wake())
        (self.f.root/'.cairn-no-coordination').touch()
        self.assertEqual(self.pair().stdout,b'{}\n')
        self.assertIn('index',[call[0] for call in self.calls])
        status=self.state()[start_fixture.SESSION+'.json']['last_recall']
        self.assertEqual(status['native_turn_input'],'taskless_prompt')
        self.assertEqual(status['optional_deferred'],'native_turn_unbound')
