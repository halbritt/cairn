import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

from trial_native_observation import capture, measure
import trial_task_eval as te


def observe(events, rows=(), expects_hooks=False):
    text = ''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in events)
    timings = [dict(line=i+1,received_seconds=(40 if e.get('late') else 1),
                    sha256=hashlib.sha256(line.encode()).hexdigest())
               for i,(e,line) in enumerate(zip(events,text.splitlines(keepends=True)))]
    return measure(text,timings,rows,input_bytes=500,expects_hooks=expects_hooks,model='fixture',prompt='new task')


def base():
    return [dict(type='system',subtype='init',model='fixture'),
            dict(type='user',message={'content':'new task'}),
            dict(type='result',permission_denials=[],result='done')]


class NativeObservationTest(unittest.TestCase):
    def test_native_prompt_provenance_is_bounded_and_never_inferred_from_absence(self):
        events=base()+[
            dict(type='user',promptId='native-next',source='system',isSynthetic=True,isReplay=False,
                 parent_tool_use_id='toolu_parent',message={'content':'PRIVATE internal prompt'}),
            dict(type='user',source='PRIVATE unknown',isSynthetic='PRIVATE',message={'content':'new task'})]
        result=observe(events)
        rows=result['native_prompt_events']
        self.assertEqual(rows[0]['origin'],'absent')
        self.assertTrue(rows[0]['matches_frozen_prompt'])
        self.assertIsNone(rows[0]['isSynthetic'])
        self.assertEqual(rows[1]['origin'],'system')
        self.assertFalse(rows[1]['matches_frozen_prompt'])
        self.assertIs(rows[1]['isSynthetic'],True)
        self.assertIs(rows[1]['isReplay'],False)
        self.assertEqual(rows[1]['prompt_id_sha256'],hashlib.sha256(b'native-next').hexdigest())
        self.assertEqual(rows[1]['parent_tool_use_id_sha256'],hashlib.sha256(b'toolu_parent').hexdigest())
        self.assertEqual(rows[2]['origin'],'unknown')
        self.assertIsNone(rows[2]['isSynthetic'])
        self.assertNotIn('PRIVATE',json.dumps(result))
        bounded=observe(events+[dict(type='user',message={'content':'PRIVATE many'})]*40)
        self.assertEqual(len(bounded['native_prompt_events']),32)
        self.assertEqual(bounded['native_prompt_events_omitted'],11)

    def test_successful_hook_missing_error_bytes_is_unknown(self):
        row=dict(event='UserPromptSubmit',stdout_bytes=3,process_seconds=.1,exit_code=0,
                 memory_call_attempts=dict(search=1,pull=0,history=0,other=0,**{'pull-evidence':0}))
        result=observe(base(),[row])
        self.assertIn('hook_error_bytes_missing_or_malformed',result['unknown'])

    def test_hook_error_bytes_remain_charged(self):
        row=dict(event='UserPromptSubmit',stdout_bytes=3,stderr_bytes=113,
                 process_seconds=.1,exit_code=1,
                 memory_call_attempts=dict(search=1,pull=0,history=0,other=0,**{'pull-evidence':0}))
        result=observe(base(),[row])
        self.assertEqual(result['hook_wire_bytes'],116)
        self.assertEqual(result['total_bytes'],616)
        self.assertIn('hook_failed',result['failures'])

    def test_error_results_late_bodies_and_repeated_context_all_count(self):
        events=base()
        for i in range(5):
            events += [dict(type='assistant',message={'content':[dict(type='tool_use',id='toolu_'+str(i),name='mcp__cairn__cairn_history',input={'version':1})]}),
                       dict(type='user',late=True,message={'content':[dict(type='tool_result',tool_use_id='toolu_'+str(i),is_error=True,content='界'*1000)]})]
        result=observe(events)
        self.assertEqual(result['total_actual_pull_calls'],5)
        self.assertGreater(result['total_bytes'],9500)
        self.assertIn('memory_call_limit',result['failures'])
        self.assertIn('last_memory_exceeded_30_seconds',result['failures'])
        self.assertIn('selected_input_exceeded_9500_bytes',result['failures'])

    def test_missing_later_hook_and_malformed_denials_never_pass(self):
        text='{}\n'; digest=hashlib.sha256(text.encode()).hexdigest()
        events=base(); rows=[]
        for i,name in enumerate(('SessionStart','UserPromptSubmit')):
            events += [dict(type='system',subtype='hook_started',hook_id=str(i),hook_event=name),
                       dict(type='system',subtype='hook_response',hook_id=str(i),hook_event=name,stdout=text,outcome='success')]
            rows.append(dict(event=name,stdout_bytes=3,stderr_bytes=0,stdout_sha256=digest,exit_code=0,process_seconds=.1,
                             memory_call_attempts=dict(search=1,pull=0,history=0,other=0,**{'pull-evidence':0})))
        self.assertEqual(observe(events,rows,True)['status'],'within_observed_limits')
        events += [dict(type='system',subtype='hook_started',hook_id='2',hook_event='SessionStart')]
        self.assertEqual(observe(events,rows,True)['status'],'unknown')
        for value in (None,False,0,{},'bad'):
            e=base(); e[-1]['permission_denials']=value
            self.assertEqual(observe(e)['status'],'unknown')

    def test_background_ack_cannot_satisfy_successful_command_output(self):
        events=[dict(type='assistant',message={'content':[dict(type='tool_use',id='x',name='Bash',input={'command':'make check'})]}),
                dict(type='user',message={'content':[dict(type='tool_result',tool_use_id='x',content='moved to the background',is_error=False)]},
                     tool_use_result={'backgroundTaskId':'untrusted'})]
        text='\n'.join(map(json.dumps,events))
        self.assertEqual(te.parse_tool_outputs(text,require_completed=True),[])
        self.assertEqual(len(te.parse_tool_outputs(text)),1)  # Historical reader unchanged.

    def test_actual_child_timeout_is_reaped_and_timing_has_no_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'timing.json'
            out,err,code,rows=capture([sys.executable,'-u','-c',"import time; print('PRIVATE'); time.sleep(10)"],.1,path)
            self.assertEqual(code,124)
            self.assertIn(b'PRIVATE',out)
            self.assertNotIn('PRIVATE',path.read_text())

class NativeMessageShapeTests(unittest.TestCase):
    def test_status_strings_preserve_checked_partial_delivery_and_limits(self):
        import uuid
        body='évidence'.encode()
        source='0'*64
        result={'selection':{'record':{'record_id':str(uuid.uuid4()),'version':2,'body':''}},
                'span':{'offset':0,'end':len(body),'total_bytes':100,'body':body.decode(),
                        'sha256':hashlib.sha256(body).hexdigest(),'source_sha256':source}}
        events=base()+[
            dict(type='assistant',message={'content':[dict(type='tool_use',id='p',name='mcp__cairn__cairn_pull',input={})]}),
            dict(type='user',late=True,message={'content':[dict(type='tool_result',tool_use_id='p',content=[dict(type='text',text=json.dumps(result))])]}),
        ]
        expected=observe(events)
        actual=observe(events+[dict(type='system',subtype='status',message='arbitrary status'),
                               dict(type='system',subtype='notification',message=['status'])])
        self.assertEqual(actual,expected)
        item=actual['source_deliveries'][0]['delivery']['items'][0]
        self.assertEqual(item['span'],{'offset':0,'end':len(body),'total_bytes':100})
        self.assertEqual(item['delivered_sha256'],hashlib.sha256(body).hexdigest())
        self.assertEqual(actual['total_actual_pull_calls'],1)
        self.assertIn('last_memory_exceeded_30_seconds',actual['failures'])

    def test_malformed_relevant_messages_are_unknown_not_complete(self):
        for kind in ('user','assistant'):
            for message in (None,'status',[],{'content':None},{'content':False},
                            {'content':[None]}, {'content':[{}]}, {'content':[{'type':'text','text':None}]}):
                with self.subTest(kind=kind,message=message):
                    result=observe(base()+[dict(type=kind,message=message)])
                    self.assertEqual(result['status'],'unknown')
                    self.assertIn('native_message_shape_unknown',result['unknown'])

    def test_technical_readers_skip_status_and_malformed_messages(self):
        events=base()+[
            dict(type='system',subtype='status',message='status'),
            dict(type='assistant',message='unsupported'),
            dict(type='user',message={'content':False}),
            dict(type='assistant',message={'content':[dict(type='tool_use',id='toolu_x',name='Bash',input={'command':'printf checked'})]}),
            dict(type='user',message={'content':[dict(type='tool_result',tool_use_id='toolu_x',content='checked',is_error=False)]})]
        text='\n'.join(map(json.dumps,events))
        trace=te.parse_stream(text)
        self.assertEqual(trace['answer'],'done')
        self.assertEqual(trace['commands'],['printf checked'])
        outputs=te.parse_tool_outputs(text,require_completed=True)
        self.assertEqual(len(outputs),1)
        self.assertEqual(outputs[0]['output'],'checked')
        self.assertEqual(observe(events)['status'],'unknown')
