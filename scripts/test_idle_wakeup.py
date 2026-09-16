"""Idle host wakeups use protocol fixtures, real processes and persisted state."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('coordination_idle', ROOT/'integrations/lifecycle/coordination.py')
coordination = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coordination)

HOST = r'''#!/usr/bin/env python3
import json, pathlib, sys
root=pathlib.Path(__file__).parent
fixture=json.loads((root/'fixture.json').read_text())
args=sys.argv[1:]
if args[:2]==['agent','list']: result={'agents':fixture.get('extra_hosts',[])+[fixture['host']]}
elif args[:2]==['agent','get']: result={'agent':dict(fixture['host'],**fixture.get('on_recheck',{}))}
elif args[:2]==['pane','process-info']:
    if args[-1]==fixture.get('closed_pane'):
        print(json.dumps({'error':{'code':'pane_not_found'}}),file=sys.stderr)
        raise SystemExit(1)
    result={'process_info':fixture['process_info']}
elif args[:2]==['agent','prompt']:
    with (root/'prompts.jsonl').open('a') as f: f.write(json.dumps(args)+'\n')
    if fixture.get('lost_reply'): raise SystemExit(1)
    result={'type':'agent_prompted','agent':fixture['host']}
else: raise SystemExit('unexpected host command')
print(json.dumps({'result':result}))
'''

API = r'''#!/usr/bin/env python3
import json, pathlib, sys
root=pathlib.Path(__file__).parent
fixture=json.loads((root/'fixture.json').read_text())
request=json.load(sys.stdin)
operation=sys.argv[-1]
if operation=='agent-heartbeat': data=fixture['agent']
elif operation=='session-inbox-ready':
    count_path=root/'ready-count'
    count=int(count_path.read_text()) if count_path.exists() else 0
    count_path.write_text(str(count+1))
    data={'delivery_id':'' if fixture.get('vanish_on_recheck') and count else fixture['delivery']}
else: raise SystemExit('unexpected API operation '+operation)
print(json.dumps({'ok':True,'data':data}))
'''


class IdleWakeup(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.native = subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],
            env=dict(os.environ,HERDR_ENV='1',HERDR_SOCKET_PATH=str(self.root/'host.sock')))
        self.addCleanup(self.stop_native)
        for name, body in [('herdr',HOST),('cairn',API)]:
            path=self.root/name
            path.write_text(body)
            path.chmod(0o700)
        self.agent=dict(agent_id='agent-one', execution_id='execution-one', native_session_id='native-one',
                        database_generation=0, metadata=dict(harness='claude',state='idle',workspace=str(self.root),delivery_mode='existing-session'))
        self.fixture=dict(agent=self.agent,delivery='delivery-one',host=dict(agent='claude',agent_status='idle',
            pane_id='fixture:p1',terminal_id='terminal-one',revision=1,state_change_seq=1,focused=False,
            agent_session=dict(kind='id',value='native-one',agent='claude')),
            process_info=dict(foreground_processes=[dict(pid=self.native.pid)],foreground_process_group_id=os.getpgid(self.native.pid)))
        self.save_fixture()
        self.config=dict(cairn=str(self.root/'cairn'), socket=str(self.root/'api.sock'),token_file=str(self.root/'token'),
            state_dir=str(self.root/'state'),binding='fixture',repo='fixture',harness='claude',
            native_delivery=True,idle_wakeup=str(self.root/'herdr'))
        self.path=self.root/'state'/'session.json'
        coordination.write_state(self.path,dict(process=coordination.process_reference(self.native.pid),agent=self.agent,workspace=str(self.root)))

    def stop_native(self):
        if self.native.poll() is None:
            self.native.terminate()
        self.native.wait(timeout=5)

    def save_fixture(self):
        (self.root/'fixture.json').write_text(json.dumps(self.fixture))

    def watch(self):
        # A new CLI process on every poll exercises persisted retry suppression.
        config=self.root/'bindings'/'fixture.json'
        config.parent.mkdir(exist_ok=True)
        config.write_text(json.dumps(self.config))
        return subprocess.run([sys.executable,str(ROOT/'integrations/lifecycle/coordination.py'),'watch',
            '--config-dir',str(config.parent),'--once'],capture_output=True,text=True,timeout=10)

    def prompts(self):
        path=self.root/'prompts.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_idle_arrival_prompts_once_across_watcher_restarts(self):
        for _ in range(2):
            result=self.watch()
            self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(len(self.prompts()),1)
        self.assertIn('agent-one',self.prompts()[0][-1])
        self.assertEqual(json.loads(self.path.read_text())['idle_wake']['status'],'submitted')

    def test_uncertain_submission_is_not_repeated(self):
        self.fixture['lost_reply']=True
        self.save_fixture()
        result=self.watch()
        self.assertIn('HOST_UNAVAILABLE',result.stderr)
        self.assertEqual(json.loads(self.path.read_text())['idle_wake']['status'],'uncertain')
        self.watch()
        self.assertEqual(len(self.prompts()),1)

    def test_busy_blocked_unknown_or_other_session_never_get_prompted(self):
        for status in ('working','blocked','unknown'):
            with self.subTest(status=status):
                self.fixture['host']['agent_status']=status
                self.save_fixture()
                self.watch()
                self.assertEqual(self.prompts(),[])
        self.fixture['host']['agent_status']='idle'
        self.fixture['host']['agent_session']['value']='different-native'
        self.save_fixture()
        self.watch()
        self.assertEqual(self.prompts(),[])

    def test_another_foreground_process_or_missing_session_is_not_a_target(self):
        self.fixture['process_info']['foreground_processes']=[dict(pid=1)]
        self.save_fixture()
        self.watch()
        self.assertEqual(self.prompts(),[])
        self.fixture['process_info']['foreground_processes']=[dict(pid=self.native.pid)]
        self.fixture['host'].pop('agent_session')
        self.save_fixture()
        self.watch()
        self.assertEqual(self.prompts(),[])

    def test_empty_inbox_and_busy_registry_do_not_prompt(self):
        self.fixture['delivery']=''
        self.save_fixture()
        self.watch()
        self.assertEqual(self.prompts(),[])
        self.fixture['delivery']='delivery-one'
        self.agent['metadata']['state']='busy'
        self.save_fixture()
        self.watch()
        self.assertEqual(self.prompts(),[])

    def test_following_delivery_gets_a_new_wakeup(self):
        self.watch()
        self.fixture['delivery']='delivery-two'
        self.save_fixture()
        self.watch()
        self.assertEqual(len(self.prompts()),2)

    def test_unrelated_candidate_closing_does_not_hide_the_live_target(self):
        self.fixture['extra_hosts']=[dict(self.fixture['host'],pane_id='closed:p1')]
        self.fixture['closed_pane']='closed:p1'
        self.save_fixture()
        result=self.watch()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(len(self.prompts()),1)

    def test_host_transition_or_delivery_change_before_submission_defers(self):
        self.fixture['on_recheck']=dict(state_change_seq=2,agent_status='working')
        self.save_fixture()
        self.watch()
        self.assertEqual(self.prompts(),[])
        self.fixture.pop('on_recheck')
        (self.root/'ready-count').unlink()
        self.fixture['vanish_on_recheck']=True
        self.save_fixture()
        self.watch()
        self.assertEqual(self.prompts(),[])
        self.assertNotIn('idle_wake',json.loads(self.path.read_text()))

    def test_process_reference_reuse_does_not_prompt(self):
        state=json.loads(self.path.read_text())
        state['process']['start']+=1
        coordination.write_state(self.path,state)
        self.watch()
        self.assertEqual(self.prompts(),[])

    def test_focused_pane_or_another_foreground_group_defers_wakeup(self):
        self.fixture['host']['focused']=True
        self.save_fixture()
        self.watch()
        self.assertEqual(self.prompts(),[])

        self.fixture['host']['focused']=False
        self.fixture['process_info']['foreground_process_group_id']=0
        self.save_fixture()
        self.watch()
        self.assertEqual(self.prompts(),[])

    def test_project_opt_out_added_after_registration_prevents_wakeup(self):
        (self.root/'.cairn-no-coordination').touch()
        self.watch()
        self.assertEqual(self.prompts(),[])

    def test_codex_requires_one_matching_open_conversation(self):
        self.stop_native()
        native_id='243ce2f7-70bb-412b-b5ec-32584426a4bf'
        rollout=self.root/('rollout-2026-09-16T08-00-00-'+native_id+'.jsonl')
        rollout.write_text('NOT READ')
        code='import sys,time; f=open(sys.argv[1]); print("ready",flush=True); time.sleep(60)'
        self.native=subprocess.Popen([sys.executable,'-c',code,str(rollout)],stdout=subprocess.PIPE,text=True,
            env=dict(os.environ,HERDR_ENV='1',HERDR_SOCKET_PATH=str(self.root/'host.sock')))
        self.assertEqual(self.native.stdout.readline().strip(),'ready')
        self.addCleanup(self.native.stdout.close)
        self.config['harness']='codex'
        self.agent['metadata']['harness']='codex'
        self.fixture['host']['agent']='codex'
        self.fixture['host'].pop('agent_session')
        self.fixture['process_info']['foreground_processes']=[dict(pid=self.native.pid)]
        coordination.write_state(self.path,dict(process=coordination.process_reference(self.native.pid),agent=self.agent,workspace=str(self.root)))
        self.save_fixture()
        self.watch()
        self.assertEqual(self.prompts(),[])
        self.agent['native_session_id']=native_id
        self.save_fixture()
        self.watch()
        self.assertEqual(len(self.prompts()),1)


if __name__=='__main__':
    unittest.main()
