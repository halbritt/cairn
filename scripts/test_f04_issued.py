"""Execute receipt-issued shell text through the actual retained inbox frontends."""
import json
import os
from pathlib import Path
import shlex
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_f04_activation import ROOT, SESSION, TURN, Fixture, encoded, module, sha, write

AGENT='33333333-3333-4333-8333-333333333333'
EXECUTION='44444444-4444-4444-8444-444444444444'
DELIVERY='55555555-5555-4555-8555-555555555555'
LEASE='66666666-6666-4666-8666-666666666666'
EVENT='77777777-7777-4777-8777-777777777777'
RESULT={'record_id':'88888888-8888-4888-8888-888888888888','version':1}
NOTE='Read this exact fixture source. 日本語.'


def ok(data, **extra): return dict(ok=True,status='OK',data=data,**extra)


class F04IssuedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build=tempfile.TemporaryDirectory()
        cls.binary=Path(cls.build.name)/'activation'
        subprocess.run(['go','build','-buildvcs=false','-o',str(cls.binary),'./cmd/cairn-lifecycle'],cwd=ROOT,check=True)

    @classmethod
    def tearDownClass(cls): cls.build.cleanup()

    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)

    def fixture(self, root, **kwargs):
        return Fixture(root, self.binary, **kwargs)

    def prepare(self, name, kind='request', compact=False, *, legacy=False, remind=False, body=NOTE,
                unavailable=False,api_suffix='',state_suffix='',refused=False,versioned_config=False):
        root=self.root/('選択 '+name+" ' quoted")
        f=(Fixture(root,self.binary,compact=compact) if legacy else self.fixture(root,compact=compact))
        if versioned_config and f.coord_path == f.root/'bindings'/(f.coord['binding']+'.json'):
            old_config=f.coord_path
            f.coord_path=f.root/'generations'/'selection-fixture'/'coordination-0.json'
            f.coord_path.parent.mkdir(mode=0o700,parents=True)
            manifest=json.loads(f.binding.read_text())
            manifest['coordination_configs'][0]['path']=str(f.coord_path)
            write(f.binding,encoded(manifest))
            f.publish_configuration()
            old_config.unlink()
        if api_suffix:
            target=f.api.with_name(f.api.name+api_suffix);f.api.rename(target);f.api=target
            f.memory['cairn']=f.coord['cairn']=str(target)
        if state_suffix: f.coord['state_dir']+=state_suffix
        if remind:
            f.coord['remind_unacknowledged_turn_end']=True
        if remind or api_suffix or state_suffix:
            f.publish_configuration()
        if legacy:
            bundle=module('f04_issued_legacy_bundle',ROOT/'integrations/lifecycle/lifecycle_bundle.py')
            bundle.stage_runtime(ROOT/'integrations/lifecycle',f.root)
            manifest=json.loads(f.binding.read_text())
            manifest.pop('binary');manifest['schema']='cairn.inbox-recall-binding/2'
            for key,member in (('engine','memory.py'),('bridge','inbox_recall.py')):
                manifest[key]=dict(path=str(f.root/member),sha256=sha((f.root/member).read_bytes()),bundle=bundle.bundle_descriptor(f.root))
            write(f.binding,encoded(manifest))
        f.event.update(prompt='An ordinary fixture prompt.',host_pid=os.getpid())
        f.raw=encoded(f.event)
        delivery=dict(delivery_id=DELIVERY,lease_id=LEASE,state='leased',event=dict(
            event_id=EVENT,kind=kind,ref=RESULT,**{'from':'agent/requester'}))
        attempt=dict(attempt_id='',session={},delivery=delivery,native_turn_id=TURN,batch_index=1)
        history=dict(record_id=RESULT['record_id'],historical=True,versions=[dict(version=1,
            payload_available=True,body=body,body_bytes=len(body.encode()),body_sha256=sha(body.encode()))])
        replies={'event-renew':[ok(delivery)],'session-inbox-reconcile':[dict(ok=False,status='DELIVERY_ACTIVE')],
                 'history':[ok(history)],'complete':[ok(dict(state='handled',delivery_id=DELIVERY,result=RESULT))],
                 'ack':[ok(dict(state='handled',delivery_id=DELIVERY,attempts=1))],
                 'publish':[ok(dict(event_id=EVENT,kind='response',ref=RESULT,causation_id=EVENT))]}
        if kind is not None:
            replies['session-inbox-claim']=[ok(dict(attempt=attempt),claim_identity=True)]
        if unavailable:
            replies['history']=[dict(ok=False,status='NOT_FOUND')]
            replies['ack']=[ok(dict(state='failed',delivery_id=DELIVERY,attempts=1))]
        f.replies(replies)
        result=(subprocess.run([sys.executable,'-I',str(f.root/'coordination.py'),'hook','--config',str(f.coord_path)],
                               input=f.raw,capture_output=True,cwd=f.root,env=f.env(),timeout=18)
                if legacy else f.public('coordination'))
        if refused:
            self.assertEqual((result.returncode,result.stdout),(1,b''))
            return f,None,None,result
        self.assertEqual((result.returncode,result.stderr),(0,b''),result.stdout)
        self.assertLessEqual(len(result.stdout),12000)
        contexts=list((Path(f.coord['state_dir'])/'inbox').glob('*.json'))
        if kind is None: return f,None,None,result
        self.assertEqual(len(contexts),1,result.stdout)
        context=json.loads(contexts[0].read_text())
        for key,text in context.get('shell_commands',{}).items():
            self.assertLessEqual(len(text.encode()),512)
            self.assertNotIn(' -c ',text)
            if key!='read': self.assertEqual(shlex.split(text)[0],str(f.launcher('coordination')))
        self.assertLessEqual(sum(len(s.encode()) for s in context.get('shell_commands',{}).values()),2048)
        return f,context,contexts[0],result

    def run_text(self,f,text,body=b'',extra_env=None):
        self.assertLessEqual(len(text.encode()),512)
        env=f.env()
        env.update(extra_env or {})
        result=subprocess.run(['/bin/sh','-c',text],input=body,capture_output=True,cwd=f.root,env=env,timeout=35)
        print('ISSUED_F04',text,repr(body),result.returncode,repr(result.stdout),repr(result.stderr),flush=True)
        return result

    def test_every_journaled_wrapper_executes_its_emitted_shell_text_and_stdin(self):
        for operation in ('completion','acknowledgement','response','finish_reply'):
            with self.subTest(operation=operation):
                f,c,path,public=self.prepare(operation,'notice' if operation=='acknowledgement' else 'request')
                if operation in ('acknowledgement','finish_reply'):
                    self.assertIn(c['shell_commands'][operation],json.loads(public.stdout)['hookSpecificOutput']['additionalContext'])
                body='Selected result: "quoted" 日本語\nsecond line\n'.encode()
                if operation=='response':
                    completed=self.run_text(f,c['shell_commands']['completion'],body)
                    self.assertEqual(completed.returncode,0)
                    ref=json.loads(completed.stdout)['data']['result']
                    text=c['shell_commands']['response']+' --version '+str(ref['version'])+' '+ref['record_id']
                    sent=b''
                else:
                    text=c['shell_commands'][operation]
                    sent=b'' if operation=='acknowledgement' else body
                before=Path(f.memory['state_dir'])
                self.assertFalse(before.exists(),'ordinary issued hook must not create an activation ledger')
                result=self.run_text(f,text,sent)
                self.assertEqual((result.returncode,result.stderr),(0,b''))
                calls=f.api_calls()
                selected=[row for row in calls if row['operation'] in ('complete','ack','publish')]
                expected=['complete','publish'] if operation in ('response','finish_reply') else ['ack' if operation=='acknowledgement' else 'complete']
                self.assertEqual([row['operation'] for row in selected],expected)
                for row in selected:
                    self.assertIn('--agent-id',row['argv'])
                    self.assertIn(AGENT,row['argv'])
                    self.assertIn(EXECUTION,row['argv'])
                    if row['operation']=='complete': self.assertEqual(row['input'],body.decode())
                    if row['operation']=='publish':
                        self.assertEqual(row['argv'][row['argv'].index('--causation-id')+1],EVENT)
                        self.assertEqual(row['argv'][-2:],['1',RESULT['record_id']])
                self.assertFalse(before.exists(),'issued commands must not re-enter activation')

    def test_compact_read_inspect_settle_execute_exact_public_cue_commands(self):
        for versioned in (False,True):
            with self.subTest(versioned_config=versioned):
                f,c,path,public=self.prepare('compact'+str(versioned),'notice',True,versioned_config=versioned)
                if versioned:
                    self.assertNotEqual(f.coord_path,f.root/'bindings'/(f.coord['binding']+'.json'))
                handle=c['compact_handle']
                prefix=shlex.join([str(f.launcher('coordination')),'inbox','compact'])
                env=dict(CAIRN_INBOX_ROOT=str(f.root),CODEX_THREAD_ID=SESSION)
                for operation in ('read','inspect'):
                    result=self.run_text(f,prefix+' '+operation+' '+handle,extra_env=env)
                    self.assertEqual((result.returncode,result.stderr),(0,b''))
                    data=json.loads(result.stdout)
                    self.assertTrue(data['ok'])
                    if operation=='read': self.assertEqual(data['body'],NOTE)
                    else: self.assertEqual(data['context']['attempt_id'],c['attempt_id'])
                cue=json.loads(public.stdout)['hookSpecificOutput']['additionalContext']
                exact=prefix+' settle '+handle+' handled'
                self.assertIn(exact,cue)
                settled=self.run_text(f,exact,extra_env=env)
                self.assertEqual((settled.returncode,settled.stderr),(0,b''))
                self.assertEqual(json.loads(settled.stdout)['status'],'handled')
                self.assertEqual([r['operation'] for r in f.api_calls()].count('ack'),1)
                self.assertGreaterEqual([r['operation'] for r in f.api_calls()].count('event-renew'),4)

    def test_wrong_compact_root_and_context_selection_refuse_before_api_effect(self):
        f,c,path,_=self.prepare('selection','notice',True,versioned_config=True)
        before=len(f.api_calls())
        text=shlex.join([str(f.launcher('coordination')),'inbox','compact','inspect',c['compact_handle']])
        result=self.run_text(f,text,extra_env={'CAIRN_INBOX_ROOT':str(self.root/'wrong')})
        self.assertEqual((result.returncode,result.stdout),(1,b''))
        self.assertEqual(len(f.api_calls()),before)
        other=f.root/'unselected-context.json'
        other.write_bytes(path.read_bytes());other.chmod(0o600)
        result=self.run_text(f,shlex.join([str(f.launcher('coordination')),'inbox','--context',str(other),'ack']))
        self.assertEqual((result.returncode,result.stdout),(1,b''))
        self.assertEqual(len(f.api_calls()),before)
        index=Path(f.coord['state_dir'])/'compact'/(c['compact_handle']+'.json')
        entry=json.loads(index.read_text())
        entry['binding']='unselected-binding'
        write(index,encoded(entry))
        result=self.run_text(f,text,extra_env={'CAIRN_INBOX_ROOT':str(f.root)})
        self.assertEqual((result.returncode,result.stdout),(1,b''))
        self.assertEqual(len(f.api_calls()),before)

    def test_compact_advance_executes_the_emitted_tail_and_keeps_the_next_notice_separate(self):
        f,c,path,public=self.prepare('advance','notice',True)
        env=dict(CAIRN_INBOX_ROOT=str(f.root),CODEX_THREAD_ID=SESSION)
        prefix=shlex.join([str(f.launcher('coordination')),'inbox','compact'])
        settled=self.run_text(f,prefix+' settle '+c['compact_handle']+' handled',extra_env=env)
        self.assertEqual(settled.returncode,0,settled.stdout)
        replies=json.loads(f.api.with_suffix('.replies').read_text())
        next_delivery=dict(delivery_id='99999999-9999-4999-8999-999999999999',lease_id=LEASE,state='leased',
                           event=dict(event_id=EVENT,kind='notice',ref=RESULT,**{'from':'agent/requester'}))
        attempt=dict(attempt_id='',session={},delivery=next_delivery,native_turn_id=TURN,
                     batch_root=c['attempt_id'],batch_index=2,batch_limit=3)
        replies['session-inbox-advance']=[ok(dict(attempt=attempt,remainder='empty'),advance_identity=True)]
        replies['event-renew']=[ok(next_delivery)]
        f.replies(replies)
        text=prefix+' advance '+c['compact_handle']
        self.assertIn(text,json.loads(public.stdout)['hookSpecificOutput']['additionalContext'])
        result=self.run_text(f,text,extra_env=env)
        self.assertEqual((result.returncode,result.stderr),(0,b''),result.stdout)
        returned=json.loads(result.stdout)
        self.assertEqual(returned['batch_index'],2)
        contexts=list(path.parent.glob('*.json'))
        self.assertEqual(len(contexts),2)
        next_context=next(json.loads(p.read_text()) for p in contexts if p!=path)
        self.assertNotEqual(next_context['compact_handle'],c['compact_handle'])
        next_text=prefix+' settle '+next_context['compact_handle']+' handled'
        self.assertIn(next_text,returned['next'])
        self.assertEqual([r['operation'] for r in f.api_calls()].count('ack'),1,'advance must not acknowledge its neighbor')

    def test_drain_uses_the_exact_tool_cue_and_its_own_command_deadline(self):
        f,_,_,_=self.prepare('drain',None)
        replies=json.loads(f.api.with_suffix('.replies').read_text())
        replies['session-inbox-pending']=[ok(dict(requests=0,notices=1,responses=0,latest_position=1))]
        f.replies(replies)
        f.raw=encoded(dict(f.event,hook_event_name='PostToolUse'))
        cue=f.public('coordination',timeout=2)
        self.assertEqual((cue.returncode,cue.stderr),(0,b''),cue.stdout)
        text=json.loads(cue.stdout)['hookSpecificOutput']['additionalContext']
        command=re.search(r'with `([^`]+)`',text).group(1)
        self.assertLessEqual(len(text.encode()),2048)
        self.assertEqual(shlex.split(command)[0],str(f.launcher('coordination',2)))
        delivery=dict(delivery_id=DELIVERY,lease_id=LEASE,state='leased',event=dict(
            event_id=EVENT,kind='notice',ref=RESULT,**{'from':'agent/requester'}))
        replies=json.loads(f.api.with_suffix('.replies').read_text())
        replies['event-next']=[ok(dict(delivery=delivery),delay=2.2)]
        f.replies(replies)
        result=self.run_text(f,command)
        self.assertEqual((result.returncode,result.stderr),(0,b''),result.stdout)
        response=json.loads(result.stdout)
        self.assertEqual(response['delivery_id'],DELIVERY)
        context=json.loads(Path(response['context']).read_text())
        self.assertEqual(shlex.split(context['shell_commands']['acknowledgement'])[0],str(f.launcher('coordination',2)))
        self.assertIn('drain-'+LEASE,response['context'])
        self.assertFalse(Path(f.memory['state_dir']).exists())

    def test_legacy_compact_handle_and_exact_bare_launcher_remain_usable(self):
        f,c,path,public=self.prepare('legacy','notice',True,legacy=True)
        installer=module('f04_legacy_installer',ROOT/'scripts/install-agent-coordination.py')
        home=f.root/'disposable-home'
        command_dir=home/'.local/bin'
        env=dict(f.env(),PATH=str(command_dir)+os.pathsep+os.environ['PATH'],CODEX_THREAD_ID=SESSION)
        with patch.dict(os.environ,{'PATH':env['PATH']}): installer.install_compact_inbox_launcher(f.root,home)
        raw=(command_dir/'cairn-inbox').read_bytes()
        text='cairn-inbox settle '+c['compact_handle']+' handled'
        self.assertIn(text,json.loads(public.stdout)['hookSpecificOutput']['additionalContext'])
        result=self.run_text(f,text,extra_env=env)
        self.assertEqual((result.returncode,result.stderr),(0,b''),result.stdout)
        self.assertEqual(json.loads(result.stdout)['status'],'handled')
        self.assertEqual((command_dir/'cairn-inbox').read_bytes(),raw)
        self.assertNotIn('shell_commands',c)

    def test_large_encoded_source_uses_whole_read_fallback_without_truncation_or_ack(self):
        body='😀'*1024  # exactly 4096 UTF-8 source bytes, over 12 KiB when escaped
        f,c,path,public=self.prepare('encoded-source','notice',True,body=body)
        cue=json.loads(public.stdout)['hookSpecificOutput']['additionalContext']
        self.assertNotIn(body,cue)
        command=shlex.join([str(f.launcher('coordination')),'inbox','compact','read',c['compact_handle']])
        self.assertIn(command,cue)
        self.assertLessEqual(len(public.stdout),12000)
        result=self.run_text(f,command,extra_env={'CAIRN_INBOX_ROOT':str(f.root),'CODEX_THREAD_ID':SESSION})
        self.assertEqual((result.returncode,result.stderr),(0,b''))
        self.assertEqual(json.loads(result.stdout)['body'],body)
        self.assertNotIn('ack',[r['operation'] for r in f.api_calls()])

    def test_long_legal_shell_text_reaches_512_and_an_unrepresentable_choice_keeps_the_hold(self):
        probe,c,_,_=self.prepare('sizedA','notice')
        counts={key:sum(arg.count(str(probe.root)) for arg in shlex.split(text)) for key,text in c['shell_commands'].items()}
        sizes={key:len(text.encode()) for key,text in c['shell_commands'].items()}
        padding=min((512-size)//counts[key] for key,size in sizes.items())
        padding=min(padding,(2048-sum(sizes.values()))//sum(counts.values()))
        sizes={key:size+padding*counts[key] for key,size in sizes.items()}
        dominant=max(sizes,key=sizes.get)
        extra=512-sizes[dominant]
        options={'api_suffix':'r'*extra} if dominant=='read' else {'state_suffix':'s'*extra}
        f,c,path,_=self.prepare('sizedB'+'x'*padding,'notice',**options)
        self.assertEqual(max(len(text.encode()) for text in c['shell_commands'].values()),512)
        read=self.run_text(f,c['shell_commands']['read'],encoded(c['read_input']))
        self.assertEqual((read.returncode,read.stderr),(0,b''))
        self.assertEqual(json.loads(read.stdout)['data']['versions'][0]['body'],NOTE)
        ack=self.run_text(f,c['shell_commands']['acknowledgement'])
        self.assertEqual((ack.returncode,ack.stderr),(0,b''))
        options={key:value+'x' for key,value in options.items()}
        bad,_,_,result=self.prepare('sizedC'+'x'*padding,'notice',refused=True,**options)
        self.assertIn(b'ValueError',result.stderr)
        self.assertNotIn('ack',[r['operation'] for r in bad.api_calls()])
        self.assertEqual(list((Path(bad.coord['state_dir'])/'inbox').glob('*.json')),[])
        state_path=Path(bad.coord['state_dir'])/(sha((bad.coord['binding']+'\0'+SESSION).encode())+'.json')
        state=json.loads(state_path.read_text())
        self.assertEqual(state['inbox_attempt']['delivery']['delivery_id'],DELIVERY)
        self.assertNotIn('inbox_turn_ended',state)

    def test_every_turn_end_reminder_variant_retains_checked_commands_and_stdin(self):
        variants=(('request',False,False),('request',False,True),('notice',False,False),('response',False,False),('notice',True,False))
        for number,(kind,unavailable,split_reply) in enumerate(variants):
            with self.subTest(kind=kind,unavailable=unavailable,split_reply=split_reply):
                f,c,path,_=self.prepare('reminder'+str(number),kind,kind!='request',remind=True,unavailable=unavailable)
                if split_reply:
                    c.pop('finish_reply');c['shell_commands'].pop('finish_reply')
                    write(path,encoded(c))
                state_path=Path(f.coord['state_dir'])/(sha((f.coord['binding']+'\0'+SESSION).encode())+'.json')
                state=json.loads(state_path.read_text());agent=state['agent']
                replies=json.loads(f.api.with_suffix('.replies').read_text())
                replies['agent-heartbeat']=[ok(agent)]
                replies['agent-context']=[ok(dict(agent,metadata=dict(agent['metadata'],state='idle'))),ok(agent)]
                f.replies(replies)
                f.raw=encoded(dict(f.event,hook_event_name='Stop'))
                result=f.public('coordination')
                self.assertEqual((result.returncode,result.stderr),(0,b''),result.stdout)
                reply=json.loads(result.stdout)
                self.assertEqual(reply['decision'],'block')
                reason=reply['reason']
                self.assertIn('Cairn inbox reminder:',reason)
                self.assertLessEqual(len(reason.encode()),2048)
                if unavailable:
                    command=shlex.join([str(f.launcher('coordination')),'inbox','compact','settle',c['compact_handle'],'failed','--code','source_unavailable'])
                else: command=c['shell_commands']['completion' if split_reply else 'finish_reply' if kind=='request' else 'acknowledgement']
                self.assertIn(command,reason)
                if split_reply: self.assertIn(c['shell_commands']['response'],reason)
                done=self.run_text(f,command,b'selected reminder result\n' if kind=='request' else b'',
                                   {'CAIRN_INBOX_ROOT':str(f.root),'CODEX_THREAD_ID':SESSION})
                self.assertEqual((done.returncode,done.stderr),(0,b''),done.stdout)


if __name__=='__main__': unittest.main()
