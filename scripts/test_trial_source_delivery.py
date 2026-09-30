"""Selected payload evidence, not API inspection or agent relevance claims."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

RID='12345678-1234-4234-8234-123456789abc'
OTHER='22345678-1234-4234-8234-123456789abc'
BODY='Concrete café guidance.\nPRIVATE_BODY'
def sha(value):return hashlib.sha256(value).hexdigest()
def selection(ident=RID,body=BODY):return {'record':{'record_id':ident,'version':1,'body':body},'mandatory':False}
def hook(view):return json.dumps({'hookSpecificOutput':{'additionalContext':'Cairn guidance\n'+json.dumps(view)}})

class DeliveryTests(unittest.TestCase):
    def test_observer_uses_only_emitted_payload_not_inspected_or_seen(self):
        observer=Path(__file__).with_name('trial_hook_observer.py')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);engine=root/'engine.py'
            engine.write_text('import json\n'+'''def recall(config,event,state=None):
 state['last_recall']={'records':[{'record_id':'INSPECTED_NOT_DELIVERED'}]}
 state['seen']={'SEEN_NOT_DELIVERED':1}
 return {'hookSpecificOutput':{'additionalContext':'Cairn guidance\\n'+json.dumps(VIEW)}}
def handle(config,event):return recall(config,event,{})
def main():
 print(json.dumps(handle({}, {'hook_event_name':'UserPromptSubmit'})))
 return 0
'''+ 'VIEW='+repr({'selected':[],'index':[],'candidate_bodies':[{'response':{'selection':selection()}}]})+'\n')
            out=root/'observations.jsonl'
            done=subprocess.run([sys.executable,str(observer),'--engine',str(engine),'--config',str(root/'unused'),'--observations',str(out)],capture_output=True,text=True,check=True)
            row=json.loads(out.read_text());delivery=row['source_delivery']
            self.assertEqual(delivery['status'],'observed')
            self.assertEqual(delivery['items'][0]['record_id'],RID)
            self.assertEqual(delivery['items'][0]['delivered_sha256'],sha(BODY.encode()))
            self.assertEqual(delivery['items'][0]['extent'],'full_body')
            self.assertNotIn('PRIVATE_BODY',json.dumps(row))
            self.assertNotIn('INSPECTED_NOT_DELIVERED',json.dumps(delivery))
            self.assertNotIn('SEEN_NOT_DELIVERED',json.dumps(delivery))
            self.assertIn('PRIVATE_BODY',done.stdout)

    def test_preview_required_competing_whole_and_partial_are_distinct(self):
        from trial_source_delivery import hook_delivery
        raw=BODY.encode();part=raw[10:17]
        span={'offset':10,'end':17,'total_bytes':len(raw),'body_base64':base64.b64encode(part).decode(),'sha256':sha(part),'source_sha256':sha(raw)}
        view={'selected':[dict(selection(),mandatory=True)],'index':[{'record_id':OTHER,'version':1,'summary':'PRIVATE_SUMMARY','body_sha256':sha(b'other'),'summary_span':{'offset':9,'length':15}}],
              'expanded':{'selection':selection(), 'competing':[selection(OTHER,'other')]},
              'candidate_bodies':[{'response':{'selection':selection(body=''),'span':span}}]}
        result=hook_delivery(hook(view));self.assertEqual(result['status'],'observed')
        self.assertEqual([x['extent'] for x in result['items']],['full_body','preview','full_body','full_body','partial_span'])
        self.assertEqual(result['items'][-1]['span'],{'offset':10,'end':17,'total_bytes':len(raw)})
        self.assertEqual(result['items'][-1]['delivered_sha256'],sha(part))
        self.assertTrue(result['items'][0]['mandatory'])
        self.assertNotIn('PRIVATE_',json.dumps(result))

    def test_source_opening_is_counted_as_a_separate_exact_emitted_span(self):
        from trial_source_delivery import bind_origins, hook_delivery
        raw=BODY.encode()
        def span(start,end):
            part=raw[start:end]
            return dict(offset=start,end=end,total_bytes=len(raw),body=part.decode(),
                        sha256=sha(part),source_sha256=sha(raw))
        candidate=dict(response=dict(selection=selection(body=''),span=span(25,len(raw))),
            source_opening_excerpt=dict(status='provided',origin='whole_pull',record_id=RID,
                                        version=1,span=span(0,10)))
        result=hook_delivery(hook(dict(candidate_bodies=[candidate])))
        self.assertEqual(result['status'],'observed')
        self.assertEqual(len(result['items']),2)
        opening=result['items'][1]
        self.assertEqual(opening['extent'],'partial_span')
        self.assertEqual(opening['source_component'],'source_opening_excerpt')
        self.assertEqual(opening['span'],dict(offset=0,end=10,total_bytes=len(raw)))
        self.assertEqual(opening['delivered_bytes'],10)
        self.assertEqual(opening['delivered_sha256'],sha(raw[:10]))
        self.assertNotIn('Concrete',json.dumps(result))
        origins=[dict(input_id='note',imported_record_id=RID,imported_version=1,body_sha256=sha(raw))]
        joined=bind_origins(result,origins,bodies={'note':BODY})
        self.assertEqual([item['input_id'] for item in joined['items']],['note','note'])
        self.assertEqual(sum(item['delivered_bytes'] for item in joined['items']),len(raw)-25+10)

    def test_unknown_or_inconsistent_opening_is_not_silently_excluded(self):
        from trial_source_delivery import hook_delivery
        raw=BODY.encode()
        matched=dict(offset=25,end=len(raw),total_bytes=len(raw),body=raw[25:].decode(),
                     sha256=sha(raw[25:]),source_sha256=sha(raw))
        prefix=dict(offset=0,end=10,total_bytes=len(raw),body=raw[:10].decode(),
                    sha256=sha(raw[:10]),source_sha256=sha(raw))
        for error in ('status','version','source_hash','span_hash','overlap','unknown_content'):
            with self.subTest(error=error):
                opening=dict(status='provided',origin='span_pull',record_id=RID,version=1,span=dict(prefix))
                if error=='status':opening['status']='future-unknown'
                if error=='version':opening['version']=2
                if error=='source_hash':opening['span']['source_sha256']='0'*64
                if error=='span_hash':opening['span']['sha256']='0'*64
                if error=='overlap':opening['span']['end']=26
                if error=='unknown_content':opening=dict(status='unavailable',body='UNCOUNTED_SOURCE')
                result=hook_delivery(hook(dict(candidate_bodies=[dict(
                    response=dict(selection=selection(body=''),span=matched),source_opening_excerpt=opening)])))
                self.assertEqual(result['status'],'unknown')
                self.assertEqual(result['items'],[])
        for opening in (dict(status='unavailable',reason='pull_limit'),dict(status='passage_starts_at_opening')):
            result=hook_delivery(hook(dict(candidate_bodies=[dict(
                response=dict(selection=selection(body=''),span=matched),source_opening_excerpt=opening)])))
            self.assertEqual(len(result['items']),1)
            self.assertEqual(result['status'],'observed')

    def test_native_history_errors_malformed_and_hash_mismatch(self):
        from trial_source_delivery import native_delivery
        history={'record_id':RID,'historical':True,'versions':[{'version':1,'body':BODY,'body_sha256':sha(BODY.encode())}]}
        def result(data):return {'content':[{'type':'text','text':json.dumps(data)}]}
        row=native_delivery('mcp__cairn__cairn_history',result(history))
        self.assertEqual(row['items'][0]['extent'],'full_body');self.assertTrue(row['items'][0]['historical'])
        history['versions'][0].pop('body')
        self.assertEqual(native_delivery('mcp__cairn__cairn_history',result(history))['items'][0]['extent'],'history_metadata')
        self.assertEqual(native_delivery('mcp__cairn__cairn_pull',{'is_error':True,'content':'PRIVATE_ERROR'})['status'],'error')
        bad={'selection':selection(),'span':{'offset':0,'end':2,'total_bytes':len(BODY.encode()),'body':'xx','sha256':'0'*64,'source_sha256':sha(BODY.encode())}}
        self.assertEqual(native_delivery('mcp__cairn__cairn_pull',result(bad))['status'],'unknown')
        for value in ({'body':BODY},{'selection':{'record':{'record_id':RID,'version':1,'body':{'PRIVATE_BODY':True}}}}):
            row=native_delivery('mcp__cairn__cairn_pull',result(value));self.assertEqual(row['status'],'unknown');self.assertNotIn('PRIVATE_BODY',json.dumps(row))

    def test_origin_binding_requires_matching_version_and_body_identity(self):
        from trial_source_delivery import bind_origins,hook_delivery
        origins=[{'input_id':'eligible-note','imported_record_id':RID,'imported_version':1,'body_sha256':sha(BODY.encode()),'provenance':{'private':'DO_NOT_COPY'}}]
        row=bind_origins(hook_delivery(hook({'selected':[selection()],'index':[]})),origins)
        self.assertEqual(row['items'][0]['input_id'],'eligible-note');self.assertEqual(row['items'][0]['origin_match'],'body_hash')
        origins[0]['body_sha256']='0'*64
        mismatch=bind_origins(row,origins);self.assertEqual(mismatch['status'],'unknown');self.assertNotIn('input_id',mismatch['items'][0]);self.assertNotIn('DO_NOT_COPY',json.dumps(mismatch))

    def test_span_must_match_frozen_source_not_just_self_consistent_response_hash(self):
        from trial_source_delivery import bind_origins,hook_delivery
        origins=[dict(input_id='note',imported_record_id=RID,imported_version=1,body_sha256=sha(BODY.encode()))]
        span=dict(offset=0,end=2,total_bytes=len(BODY.encode()),body='XX',sha256=sha(b'XX'),source_sha256=sha(BODY.encode()))
        result=hook_delivery(hook({'selected':[],'expanded':{'selection':selection(body=''),'span':span}}))
        self.assertEqual(result['status'],'observed')
        mapped=bind_origins(result,origins,bodies={'note':BODY})
        self.assertEqual(mapped['status'],'unknown');self.assertNotIn('input_id',mapped['items'][0])
        span.update(body=BODY[:2],sha256=sha(BODY[:2].encode()))
        valid=bind_origins(hook_delivery(hook({'expanded':{'selection':selection(body=''),'span':span}})),origins,bodies={'note':BODY})
        self.assertEqual(valid['items'][0]['input_id'],'note')

    def test_labels_require_explicit_boolean_and_metadata_limits_are_visible(self):
        from test_trial_task_prospective import fixture
        from trial_task_input import freeze_input
        from test_trial_native_observation import base,observe
        with tempfile.TemporaryDirectory() as directory:
            root=fixture(Path(directory)/'input');document=json.loads((root/'input.json').read_text())
            document['cases'][0]['relevance_labels_complete']='yes'
            (root/'input.json').write_text(json.dumps(document))
            with self.assertRaisesRegex(ValueError,'explicit boolean'):freeze_input(root)
        events=base()
        for index in range(129):
            events += [{'type':'assistant','message':{'content':[{'type':'tool_use','id':'toolu_'+str(index),'name':'mcp__cairn__cairn_pull','input':{}}]}},
                       {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'toolu_'+str(index),'is_error':True,'content':'PRIVATE_ERROR'}]}}]
        result=observe(events)
        self.assertEqual(len(result['source_deliveries']),128)
        self.assertEqual(result['source_delivery_events_omitted'],1)
        self.assertIn('source_delivery_limit',result['unknown'])

    def test_native_deliveries_are_extracted_from_actual_results_and_successful_hooks(self):
        from test_trial_native_observation import base, observe
        text=hook({'selected':[selection()],'index':[]})+'\n'
        events=base()+[
            {'type':'system','subtype':'hook_started','hook_id':'hook','hook_event':'UserPromptSubmit'},
            {'type':'system','subtype':'hook_response','hook_id':'hook','hook_event':'UserPromptSubmit','stdout':text,'outcome':'success'},
            {'type':'assistant','message':{'content':[{'type':'tool_use','id':'toolu_pull','name':'mcp__cairn__cairn_pull','input':{'PRIVATE_QUERY':'never retain'}}]}},
            {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'toolu_pull','content':[{'type':'text','text':json.dumps({'selection':selection(OTHER,'second body')})}]}]}}]
        rows=[dict(event='UserPromptSubmit',stdout_bytes=len(text.encode()),stdout_sha256=sha(text.encode()),exit_code=0,process_seconds=.1,
                   memory_call_attempts=dict(search=1,pull=0,history=0,other=0,**{'pull-evidence':0}))]
        observed=observe(events,rows)
        deliveries=observed['source_deliveries']
        self.assertEqual([x['channel'] for x in deliveries],['native','hook'])
        self.assertEqual([x['delivery']['items'][0]['record_id'] for x in deliveries],[OTHER,RID])
        self.assertNotIn('PRIVATE_',json.dumps(deliveries))
        events[4]['outcome']='error'
        self.assertEqual([x['channel'] for x in observe(events,rows)['source_deliveries']],['native'])

    def test_unlabelled_prospective_relevance_is_unknown_and_legacy_preserved(self):
        import trial_task_eval as te
        case=dict(expected=[],acceptable=[])
        memory=dict(delivered=['eligible-note'])
        te.classify_relevance(memory,case,prospective=True)
        self.assertEqual(memory['relevance_status'],'unknown_unlabelled')
        self.assertNotIn('irrelevant',memory);self.assertNotIn('delivered_expected',memory)
        declared=dict(case,relevance_labels_complete=True)
        te.classify_relevance(memory,declared,prospective=True)
        self.assertEqual(memory['irrelevant'],['eligible-note'])
        legacy=dict(delivered=['eligible-note'])
        te.classify_relevance(legacy,case)
        self.assertEqual(legacy['irrelevant'],['eligible-note']);self.assertNotIn('relevance_status',legacy)

if __name__=='__main__':unittest.main()
