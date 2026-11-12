"""Provider-selected recall through the installed Source and checked Go entry."""
import copy
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import unittest
import uuid

import test_f02_opencode_ambient as ambient
import test_f02_checked_ordinary as ordinary


class OrdinaryProviderTests(ambient.OpenCodeAmbientTests):
    def setUp(self):
        super().setUp()
        self.responses = {'recall': {'result': {'structured_output': {'index': 0}}}}
        self.provider_sides = {}
        self.provider = self.root / 'private-selector'
        ordinary.write(self.provider, b'''#!/usr/bin/python3
import json,os,pathlib,sys,time
root=pathlib.Path(__file__).parent
argv=sys.argv[1:]
schema=json.loads(argv[argv.index('--json-schema')+1])
stage='preview' if 'indices' in schema['properties'] else 'recall'
body=json.load(sys.stdin)
spec=json.loads((root/'provider.json').read_text())[stage]
stat=pathlib.Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()
call=dict(stage=stage,argv=argv,body=body,pid=os.getpid(),start=stat[19],
          cwd=os.getcwd(),mode=os.stat('.').st_mode&0o777,entries=os.listdir(),
          child=os.environ.get('CAIRN_LIFECYCLE_CHILD'),claudecode=os.environ.get('CLAUDECODE'))
with (root/'provider.calls').open('a') as log:log.write(json.dumps(call)+'\\n')
if spec.get('delay'):time.sleep(spec['delay'])
sys.stdout.write(spec.get('stdout',json.dumps(spec.get('result',{}),ensure_ascii=False,separators=(',',':'))))
sys.stderr.write(spec.get('stderr',''))
sys.stdout.flush();sys.stderr.flush()
if spec.get('delay_after'):time.sleep(spec['delay_after'])
sys.exit(spec.get('exit',0))
''', 0o700)
        self.configure(semantic_fallback=True, claude=str(self.provider),
                       preview_model='fixture-preview', recall_model='fixture-recall')
        original = self.f.env
        self.f.env = lambda: dict(original(), CLAUDECODE='synthetic-parent')

    def invoke_provider_side(self, side, invoke):
        log = self.root / 'provider.calls'
        log.unlink(missing_ok=True)
        ordinary.write(self.root / 'provider.json', ordinary.encoded(self.responses))
        self.page_pulls = {}
        self.page_allowances = {}
        result = invoke()
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        for call in calls:
            pid, started = call.pop('pid'), call.pop('start')
            self.assertIs(type(pid), int)
            self.assertGreater(pid, 1)
            proc = Path('/proc', str(pid), 'stat')
            if proc.exists():
                self.assertNotEqual(proc.read_text().rsplit(')', 1)[1].split()[19], started)
            cwd = Path(call.pop('cwd'))
            self.assertTrue(cwd.name.startswith('cairn-selection-'))
            self.assertFalse(cwd.exists())
            self.assertEqual(call.pop('mode'), 0o700)
            self.assertEqual(call.pop('entries'), [])
            self.assertEqual(call['child'], '1')
            self.assertIsNone(call['claudecode'])
        self.provider_sides[side] = calls
        return result

    def reset_state(self):
        shutil.rmtree(self.f.memory['state_dir'], ignore_errors=True)

    def recall_status(self):
        return self.state()[self.state_key()+'.json']['last_recall']

    def state_key(self):
        session = self.f.event['session_id']
        return hashlib.sha256(session.encode()).hexdigest() if self.f.memory['harness'] == 'hermes' else session

    def normalized_stream(self, raw):
        if raw:
            body = json.loads(raw)
            self.assertEqual(raw,json.dumps(body,ensure_ascii=False,separators=(',',':')).encode()+b'\n',
                             'public compact encoding changed')
            self.measured_status(body.get('cairn_status', {}).get('last_recall', {}))
            raw = json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode()+b'\n'
        return ambient.OpenCodeAmbientTests.normalized_stream(self, raw)

    def check_pull_intents(self, calls, arguments):
        # Only delivered entries expose their original SDK UUIDv4 intent. Check
        # their exact UUIDv5 derivation; the base normalizer still validates the
        # version and uniqueness of every private inspection request.
        known = {(args['receipt_id'],args['handle']) for args in arguments}
        visible = [call for call in calls if call[0] != 'expand' or (call[1]['receipt_id'],call[1]['handle']) in known]
        ambient.OpenCodeAmbientTests.check_pull_intents(self, visible, arguments)

    def scripted_pages(self, lexical, semantic=()):
        """Supply API data, keeping the real Source/Go SDKs and selectors intact."""
        owner = self
        original = self.server.RequestHandlerClass
        class Handler(original):
            def do_POST(self):
                if self.path not in ('/v1/index', '/v1/expand'):
                    return super().do_POST()
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                op = self.path.removeprefix('/v1/')
                owner.calls.append((op, body, dict(self.headers)))
                failure = None
                if op == 'index':
                    spec = lexical if not body.get('semantic') else semantic[body.get('page_offset', 0)]
                    receipt = spec.get('receipt', '11111111-1111-4111-8111-111111111111')
                    owner.page_allowances[receipt] = min(owner.page_allowances.get(receipt, 4), spec.get('credits', 4))
                    entries = spec.get('entries', [])
                    index_entries = []
                    for i in entries:
                        entry = copy.deepcopy(owner.optional[i]['entry'])
                        if i in spec.get('spans', {}): entry['match_span'] = spec['spans'][i]
                        index_entries.append(entry)
                    semantic_data = dict(schema='cairn.semantic/17', mode='index', status='READY',
                        scope=body['scope'], purpose='context', destination=dict(name='hosted', allow_local=False),
                        query='sha256:fixture-query', available_tokens=32000,
                        selected=spec.get('selected', owner.selected), omitted={},
                        index=index_entries)
                    if 'discovery' in spec: semantic_data['discovery'] = spec['discovery']
                    if 'page' in spec: semantic_data['page'] = spec['page']
                    data = dict(package=dict(receipt_id=receipt, nonce='', seal='blake3:'+'a'*64, semantic=semantic_data),
                        handles=[dict(record_id=owner.optional[i]['entry']['record_id'], version=3, handle='optional-'+str(i)) for i in entries],
                        expires_at='2030-01-01T00:00:00Z', credits_remaining=spec.get('credits', 4), bytes_remaining=24000)
                else:
                    item = owner.optional[int(body['handle'].removeprefix('optional-'))]
                    entry = item['entry']; receipt = body['receipt_id']
                    failure = owner.expand_refusal if not body.get('span') else None
                    if not failure:
                        owner.page_pulls[receipt] = owner.page_pulls.get(receipt, 0)+1
                        data = dict(selection=dict(record=dict(record_id=entry['record_id'], version=3,
                            kind='note', **{'class':entry['class']}, body=item['body']), mandatory=False,
                            reason='candidate', evidence=[], authority=[], conflicts=entry.get('conflicts', [])),
                            credits_remaining=max(0, owner.page_allowances.get(receipt,4)-owner.page_pulls[receipt]), bytes_remaining=20000)
                        if body.get('span'):
                            hint=body['span']; raw=item['body'].encode(); part=raw[hint['offset']:hint['offset']+hint['length']]
                            data['selection']['record']['body']=''
                            data['span']=dict(offset=hint['offset'], end=hint['offset']+len(part), total_bytes=len(raw),
                                sha256=ordinary.sha(part), source_sha256=ordinary.sha(raw))
                            try: data['span']['body']=part.decode()
                            except UnicodeDecodeError: data['span']['body_base64']=base64.b64encode(part).decode()
                        if owner.expand_transform: owner.expand_transform(body, data)
                self.send_response(422 if failure else 200); self.end_headers()
                response = dict(schema='cairn.response/1', ok=False, status=failure, message='fixture refusal') if failure else dict(schema='cairn.response/1', ok=True, status='OK', data=data)
                self.wfile.write(ordinary.encoded(response))
        self.server.RequestHandlerClass = Handler

    def notes(self, count, body='Concrete retained guidance'):
        self.optional_note(body)
        first = self.optional[0]
        for i in range(1, count):
            item = copy.deepcopy(first)
            item['entry']['record_id'] = str(uuid.UUID(int=0x99999999999949998999999999999990+i))
            item['body'] = body+' '+str(i)
            item['entry']['body_sha256'] = ordinary.sha(item['body'].encode())
            self.optional.append(item)

    def source(self):
        return self.invoke_provider_side('source', lambda: super(OrdinaryProviderTests, self).source())

    def go(self):
        return self.invoke_provider_side('go', lambda: super(OrdinaryProviderTests, self).go())

    def measured_status(self, status):
        for key in ('at', 'duration_ms', 'elapsed_seconds', 'preview_seconds', 'pull_seconds', 'model_seconds'):
            if key in status:
                self.measurement(status[key])
                status[key] = 0
        for key in ('preview_process', 'model_process'):
            measured = {}
            for field in ('spawn_ms', 'first_stdout_ms', 'first_stderr_ms', 'elapsed_ms'):
                process = status.get(key, {})
                if field in process:
                    self.measurement(process[field])
                    measured[field] = 0
                    del process[field]
            # Pipe readiness determines insertion order of timing fields. Keep
            # their exact observed membership, with one comparison order.
            if key in status:
                status[key].update(measured)

    def normalize(self, row, side):
        row = copy.deepcopy(row)
        for name, item in row['files'].items():
            if name.endswith('.json'):
                body = json.loads(item['raw'])
                self.measured_status(body.get('last_recall', {}))
                item['raw'] = ordinary.encoded(body).decode()
        for operation, body, _ in row['calls']:
            if operation == 'recall-observation':
                for call in body['selector_calls']:
                    self.measurement(call['elapsed_ms'])
                    call['elapsed_ms'] = 0
        return super().normalize(row, side)

    def pair(self):
        result = super().pair()
        self.assertEqual(self.provider_sides['go'], self.provider_sides['source'])
        return result

    def test_checked_ambient_verifies_a_current_body(self):
        self.required('Required retention policy.')
        self.optional_note('Use PostgreSQL for durable storage. 日本語 😀')
        self.event('SessionStart', prompt='Which durable backend is used here?')
        result = self.pair()
        self.assertEqual((result.returncode, result.stderr), (0, b''))
        context = self.context(result)
        self.assertEqual(len(context['selected']), 1)
        self.assertTrue(context['selected'][0]['mandatory'])
        for field in ('record_id', 'version', 'body'):
            self.assertEqual(context['selected'][0]['record'][field], self.selected[0]['record'][field])
        self.assertEqual(context['expanded']['selection']['record']['body'], self.optional[0]['body'])
        self.assertEqual(len(self.provider_sides['go']), 1)
        self.assertEqual(self.provider_sides['go'][0]['stage'], 'recall')
        self.assertEqual(self.state()[self.state_key()+'.json']['last_recall']['discovery'], 'verified')

    def test_supported_harness_events_use_real_provider_and_scope(self):
        self.optional_note('Concrete current lifecycle guidance.')
        for harness in ('opencode', 'claude', 'hermes'):
            for event in ('SessionStart', 'UserPromptSubmit'):
                with self.subTest(harness=harness, event=event):
                    self.reset_state()
                    self.configure(harness=harness)
                    self.f.coord['harness'] = harness
                    self.overwrite(self.f.coord_path, ordinary.encoded(self.f.coord)); self.repin()
                    session = 'ses_AmbientFixture1' if harness == 'opencode' else '22222222-2222-4222-8222-222222222222'
                    self.event(event, session_id=session, prompt='storage')
                    result = self.pair()
                    self.assertEqual(self.context(result)['expanded']['selection']['record']['body'], self.optional[0]['body'])
                    index = next(body for op, body, _ in self.calls if op == 'index')
                    self.assertEqual(index['scope']['task_id'], harness+'/'+self.f.event['session_id'])
                    self.assertEqual(index['scope']['run_id'], self.f.event['session_id'])
                    self.assertEqual(len(self.provider_sides['go']), 1)

    def test_semantic_pages_merge_required_and_share_receipt_allowance(self):
        self.required(); self.notes(6)
        self.scripted_pages(dict(entries=[0, 1]), [
            dict(entries=[1, 2, 3], discovery=dict(state='ready', mode='semantic'), page=dict(offset=0, next_offset=1)),
            dict(entries=[4, 5], discovery=dict(state='ready', mode='semantic'), page=dict(offset=1))])
        self.responses['preview'] = dict(result=dict(structured_output=dict(indices=list(range(7)))))
        self.event('UserPromptSubmit', prompt='Find retained storage guidance.')
        result = self.pair(); status = self.recall_status()
        self.assertEqual(len(self.context(result)['selected']), 1)
        self.assertEqual(status['semantic_pages'], 2)
        self.assertEqual(status['semantic_previews'], 5)
        self.assertEqual(status['shortlist_candidates'], 4)
        self.assertEqual(status['receipt_pull_calls'], dict(lexical=4, semantic=4, **{'semantic:1':4}))
        self.assertEqual(status['rejected'], dict(receipt_exhausted=2))
        self.assertEqual([x['stage'] for x in self.provider_sides['go']], ['preview', 'recall'])
        self.assertEqual([x['index'] for x in self.provider_sides['go'][-1]['body']['candidates']], list(range(4)))

    def test_invalid_body_verdicts_keep_required_and_never_mark_seen(self):
        self.required(); self.optional_note('Concrete retained guidance.')
        for verdict, reason in (({}, 'structured_output_missing'),
            (dict(structured_output=None), 'structured_output_type'),
            (dict(structured_output=[]), 'structured_output_type'),
            (dict(structured_output={}), 'index_missing'),
            (dict(structured_output=dict(index=True)), 'index_type'),
            (dict(structured_output=dict(index=2)), 'index_out_of_range'),
            (dict(is_error=True, structured_output=dict(index=0)), 'reported_error')):
            with self.subTest(reason=reason):
                self.reset_state(); self.responses['recall'] = dict(result=verdict)
                result = self.pair()
                self.assertEqual(len(self.context(result)['selected']), 1)
                self.assertNotIn('expanded', self.context(result))
                self.assertEqual(self.recall_status()['model_process']['validation_error'], reason)
                self.assertEqual(self.recall_status()['rejected'], dict(invalid_verdict=1))
                self.assertEqual(self.state()[self.state_key()+'.json']['seen'], {})

    def test_preview_verdict_refusals_never_pull(self):
        self.required(); self.notes(5)
        for indices, reason in (([True], 'index_type'), ([0, 0], 'duplicate_indices'),
                                 ([-1], 'index_out_of_range'), (list(range(9)), 'too_many_indices')):
            with self.subTest(reason=reason):
                self.reset_state(); self.responses['preview'] = dict(result=dict(structured_output=dict(indices=indices)))
                result=self.pair()
                self.assertEqual(len(self.context(result)['selected']), 1)
                self.assertFalse(any(op == 'expand' for op, _, _ in self.calls))
                self.assertEqual(self.recall_status()['preview_process']['validation_error'], reason)
                self.assertEqual([x['stage'] for x in self.provider_sides['go']], ['preview'])

    def test_provider_abstention_invalid_json_nonzero_and_usage(self):
        self.required(); self.optional_note('Concrete retained guidance.')
        for response, reason in ((dict(result=dict(structured_output=dict(index=-1))), 'selector_not_relevant'),
            (dict(stdout='not-json'), 'verification_unavailable'),
            (dict(exit=7, stderr='private synthetic diagnostic'), 'verification_unavailable')):
            with self.subTest(reason=reason, response=response):
                self.reset_state(); self.responses['recall']=response
                result=self.pair()
                self.assertEqual(len(self.context(result)['selected']), 1)
                self.assertNotIn('expanded', self.context(result))
                self.assertEqual(self.recall_status()['rejected'], {reason:1})
                self.assertNotIn(b'private synthetic diagnostic', result.stdout+result.stderr)
        self.reset_state()
        self.responses['recall']=dict(result=dict(structured_output=dict(index=0), modelUsage={'fixture-reported':{}},
            usage=dict(input_tokens=11, output_tokens=7, cache_creation_input_tokens=3, cache_read_input_tokens=5),
            duration_ms=15, duration_api_ms=12, total_cost_usd=0.004))
        self.pair()
        report=next(body for op, body, _ in self.calls if op == 'recall-observation')
        call=report['selector_calls'][0]
        self.assertEqual((call['model'], call['model_source'], call['input_tokens'], call['output_tokens'], call['cost_usd']),
                         ('fixture-reported', 'reported', 11, 7, 0.004))

    def test_paid_whole_excerpt_and_budget_refused_span(self):
        self.configure(context_bytes=3000)
        self.optional_note('Concrete retained guidance.\n'+'filler '*1800, offset=0, length=26)
        result=self.pair()
        self.assertEqual(self.context(result)['expanded']['span_origin'], 'whole_pull')
        self.assertEqual(sum(op == 'expand' for op, _, _ in self.calls), 1)
        self.assertEqual(self.state()[self.state_key()+'.json']['seen'], {})
        self.reset_state(); self.expand_refusal='BUDGET_REFUSED'
        result=self.pair()
        self.assertEqual(self.context(result)['expanded']['source_extent'], 'partial_span')
        self.assertEqual(sum(op == 'expand' for op, _, _ in self.calls), 2)
        self.assertEqual(self.state()[self.state_key()+'.json']['seen'], {})

    def test_provider_timeout_keeps_required_and_reaps_child(self):
        self.required(); self.optional_note('Concrete retained guidance.')
        self.responses['recall']=dict(delay=30)
        result=self.pair()
        self.assertEqual(len(self.context(result)['selected']), 1)
        self.assertEqual(self.recall_status()['rejected'], dict(verification_timeout=1))
        self.assertEqual(self.recall_status()['model_process']['outcome'], 'timeout')

    def test_missing_invalid_provider_and_model_preserve_source_failures(self):
        self.required(); self.optional_note('Concrete retained guidance.')
        cases = (({'claude':None}, 'TypeError'), ({'model':1, 'recall_model':'fixture-recall'}, None),
                 ({'model':1}, 'TypeError'), ({'model':'bad\0model'}, 'ValueError'),
                 ({'recall_model':None}, None))
        for changes, error in cases:
            with self.subTest(changes=changes):
                self.reset_state()
                self.f.memory.pop('recall_model', None)
                self.f.memory.update(claude=str(self.provider), model='fixture-model')
                self.configure(**changes)
                result=self.pair()
                if error:
                    self.assertEqual((result.returncode, result.stdout), (1, b''))
                    self.assertIn(('error_class='+error).encode(), result.stderr)
                    self.assertEqual(self.provider_sides['go'], [])
                else:
                    self.assertEqual(result.returncode, 0)

    def test_changed_current_body_is_refused_before_selector(self):
        self.required(); self.optional_note('Concrete retained guidance.')
        for field, changed in (('version',99), ('body','Unverified replacement')):
            with self.subTest(field=field):
                self.reset_state()
                self.expand_transform=lambda request, response: response['selection']['record'].update({field:changed})
                result=self.pair()
                self.assertEqual(len(self.context(result)['selected']),1)
                self.assertNotIn('expanded',self.context(result))
                self.assertEqual(self.provider_sides['go'],[])
                self.assertEqual(self.recall_status()['rejected'],dict(unavailable=1))

    def test_body_selector_input_reuses_paid_excerpt_and_preview_input_is_bounded(self):
        self.configure(context_bytes=65536)
        self.optional_note('Concrete retained guidance.\n'+'filler '*5000, offset=0, length=26)
        result=self.pair()
        self.assertEqual(self.context(result)['expanded']['source_extent'],'partial_span')
        self.assertEqual(self.context(result)['expanded']['span_origin'],'whole_pull')
        self.assertEqual(sum(op == 'expand' for op, _, _ in self.calls),1)
        self.assertLessEqual(self.recall_status()['selector_input_bytes'],24000)
        self.reset_state(); self.notes(5)
        for item in self.optional: item['entry']['summary']='Preview text '*400
        self.required(); result=self.pair()
        self.assertEqual(len(self.context(result)['selected']),1)
        self.assertFalse(any(op == 'expand' for op, _, _ in self.calls))
        self.assertEqual(self.provider_sides['go'],[])
        self.assertEqual(self.recall_status()['rejected'],dict(preview_input_budget=5))

    def test_full_dedup_preserves_distinct_partial_passages(self):
        for partial in (False, True):
            with self.subTest(partial=partial):
                self.reset_state(); self.configure(context_bytes=3000 if partial else 12000)
                self.optional_note('Concrete retained guidance.\n'+'later passage guidance.\n'+'filler '*1800 if partial else 'Complete guidance.')
                self.scripted_pages(dict(entries=[]), [
                    dict(entries=[0], receipt='22222222-2222-4222-8222-222222222222',
                         spans={0:dict(offset=0,length=26)}, discovery=dict(state='ready'), page=dict(next_offset=1)),
                    dict(entries=[0], receipt='33333333-3333-4333-8333-333333333333',
                         spans={0:dict(offset=28,length=23)}, discovery=dict(state='ready'))])
                self.responses['recall']=dict(result=dict(structured_output=dict(index=1 if partial else 0)))
                self.event('UserPromptSubmit',prompt='Find concrete retained guidance.')
                result=self.pair()
                self.assertEqual(self.recall_status()['shortlist_candidates'],2 if partial else 1)
                self.assertEqual(sum(op == 'expand' for op, _, _ in self.calls),2 if partial else 1)
                if partial:
                    self.assertEqual(self.context(result)['expanded']['span']['offset'],28)
                    self.assertEqual(self.state()[self.state_key()+'.json']['seen'],{})

    def test_semantic_page_and_preview_limits_select_later_guidance(self):
        self.notes(45)
        pages=[dict(entries=list(range(10+i*8,18+i*8)),receipt=str(uuid.UUID(int=0x22222222222242228222222222222220+i)),
                    discovery=dict(state='ready'),page=dict(next_offset=i+1)) for i in range(4)]
        self.scripted_pages(dict(entries=list(range(12))),pages)
        self.responses['preview']=dict(result=dict(structured_output=dict(indices=[41,40,39,38,33])))
        self.responses['recall']=dict(result=dict(structured_output=dict(index=4)))
        self.event('UserPromptSubmit',prompt='Find concrete retained guidance.')
        result=self.pair(); status=self.recall_status()
        self.assertEqual((status['semantic_pages'],status['semantic_previews'],status['preview_count']),(4,32,42))
        self.assertEqual(sum(op == 'index' for op, _, _ in self.calls),5)
        self.assertEqual(sum(op == 'expand' for op, _, _ in self.calls),5)
        self.assertEqual(self.context(result)['expanded']['selection']['record']['body'],self.optional[33]['body'])

    def test_low_receipt_alias_credits_are_shared_after_duplicate_skips(self):
        self.notes(4)
        self.scripted_pages(dict(entries=[0,1],credits=4),[
            dict(entries=[1,2,3],credits=2,discovery=dict(state='ready'))])
        self.responses['preview']=dict(result=dict(structured_output=dict(indices=[0,1,2,3,4])))
        self.event('UserPromptSubmit',prompt='Find concrete retained guidance.')
        self.pair()
        self.assertEqual(sum(op == 'expand' for op, _, _ in self.calls),2)
        self.assertEqual(self.recall_status()['rejected'],dict(receipt_exhausted=2))
        self.assertEqual(self.provider_sides['go'][0]['body']['channel_credits'],dict(lexical=2,semantic=2))

    def test_unavailable_semantic_page_keeps_whole_required_union(self):
        self.required('Whole required policy from the semantic search.')
        self.scripted_pages(dict(entries=[],selected=[]),[
            dict(entries=[],selected=self.selected,discovery=dict(state='unavailable',mode='semantic'))])
        self.event('UserPromptSubmit',prompt='Find concrete retained guidance.')
        result=self.pair()
        self.assertEqual(self.context(result)['selected'][0]['record']['body'],self.selected[0]['record']['body'])
        self.assertEqual(self.provider_sides['go'],[])
        self.reset_state(); self.configure(context_bytes=1000)
        self.selected[0]['record']['body']='Whole required policy. '*200
        result=self.pair()
        self.assertEqual((result.returncode,result.stdout),(1,b''))
        self.assertIn(b'no partial instructions injected',result.stderr)
        self.assertFalse(any(op == 'expand' for op, _, _ in self.calls))

    def test_default_ambient_seen_and_taskless_deferral_do_not_repeat_selection(self):
        self.optional_note('Concrete retained guidance.')
        self.f.memory.pop('recall_mode');self.configure()
        self.event('UserPromptSubmit',prompt='storage')
        self.assertEqual(self.pair().returncode,0)
        self.assertEqual(self.state()[self.state_key()+'.json']['seen'],{self.optional[0]['entry']['record_id']:3})
        self.assertEqual(self.pair().stdout,b'{}\n')
        self.assertEqual(self.provider_sides['go'],[])
        self.assertFalse(any(op == 'expand' for op, _, _ in self.calls))
        self.reset_state();self.required();self.event('SessionStart',prompt='')
        result=self.pair()
        self.assertEqual(len(self.context(result)['selected']),1)
        self.assertEqual(self.provider_sides['go'],[])
        self.assertEqual(self.recall_status()['optional_deferred'],'taskless_startup')

    def test_provider_output_limit_abstains_without_retaining_text(self):
        self.required();self.optional_note('Concrete retained guidance.')
        self.responses['recall']=dict(stdout='x'*(2<<20),delay_after=30)
        result=self.pair()
        self.assertEqual(len(self.context(result)['selected']),1)
        self.assertEqual(self.recall_status()['rejected'],dict(verification_unavailable=1))
        process=self.recall_status()['model_process']
        self.assertEqual((process['outcome'],process['stdout_bytes'],process['returncode']),
                         ('communication_failed',(1<<20)+1,-9))
        self.assertLess(sum(len(x['raw']) for x in self.files().values()),8000)

    def test_preview_and_body_share_the_original_recall_deadline(self):
        self.required();self.notes(5)
        self.responses['preview']=dict(delay=4,result=dict(structured_output=dict(indices=[0])))
        self.responses['recall']=dict(delay=30)
        result=self.pair()
        self.assertEqual(len(self.context(result)['selected']),1)
        self.assertEqual([x['stage'] for x in self.provider_sides['go']],['preview','recall'])
        self.assertEqual(self.recall_status()['rejected'],dict(verification_timeout=1))
        for row in self.last_raw_pair:
            status=next(json.loads(v['raw'])['last_recall'] for k,v in row['files'].items() if k.endswith('.json'))
            self.assertGreaterEqual(status['elapsed_seconds'],10.8)
            self.assertLess(status['elapsed_seconds'],11.5)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(OrdinaryProviderTests(name) for name in
                             OrdinaryProviderTests.__dict__ if name.startswith('test_'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
