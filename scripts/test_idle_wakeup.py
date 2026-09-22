"""Idle host wakeups use protocol fixtures, real processes and persisted state."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

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
elif operation=='agent-context': data=dict(fixture['agent'],context_revision=request.get('expected_revision',0)+1,metadata=request.get('metadata'))
elif operation=='session-inbox-ready':
    count_path=root/'ready-count'
    count=int(count_path.read_text()) if count_path.exists() else 0
    count_path.write_text(str(count+1))
    data={'delivery_id':'' if fixture.get('vanish_on_recheck') and count else fixture['delivery']}
else: raise SystemExit('unexpected API operation '+operation)
print(json.dumps({'ok':True,'data':data}))
'''

QUEUE_CHILD = r'''
import json, os, pathlib, sys, time
argv = sys.argv
listen = next(argument for argument in argv if argument.startswith('unix://'))
rounds = json.loads(pathlib.Path(argv[-2]).read_text())
gate = pathlib.Path(argv[-1]) if argv[-1] else None
from test_codex_queue import NativeQueue
fixture = NativeQueue()
fixture.setUp()
print('ready', flush=True)
if gate is not None:
    while not gate.exists():
        time.sleep(0.05)
os.rename(fixture.path, listen[7:])
fixture.listener.settimeout(20)
for round_spec in rounds:
    fixture.serve(start=round_spec.get('start', 'confirm'), add=round_spec.get('add', 'confirm'))
    fixture.join()
pathlib.Path(listen[7:]).with_suffix('.requests').write_text(json.dumps(fixture.requests))
print('served', flush=True)
time.sleep(60)
'''

CHANNEL_BRIDGE = r'''
import json, os, pathlib, socket, sys, time
endpoint = pathlib.Path(sys.argv[-2])
control = pathlib.Path(sys.argv[-1])
server = socket.socket(socket.AF_UNIX)
server.bind(str(endpoint))
os.chmod(endpoint, 0o600)
server.listen(4)
server.settimeout(60)
print('ready', flush=True)
log = endpoint.with_suffix('.log')
while True:
    try:
        conn, _ = server.accept()
    except OSError:
        break
    with conn:
        conn.settimeout(4)
        data = b''
        while b'\n' not in data and len(data) <= 8193:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        if not data:
            continue
        with log.open('a') as file:
            file.write(data.decode())
        status = json.loads(control.read_text()).get('status', 'written')
        if status != 'drop':
            conn.sendall(json.dumps({'status': status}).encode()+b'\n')
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
                        database_generation=0, metadata=dict(harness='agy',state='idle',workspace=str(self.root),delivery_mode='existing-session'))
        self.fixture=dict(agent=self.agent,delivery='delivery-one',host=dict(agent='agy',agent_status='idle',
            pane_id='fixture:p1',terminal_id='terminal-one',revision=1,state_change_seq=1,focused=False,
            agent_session=dict(kind='id',value='native-one',agent='agy')),
            process_info=dict(foreground_processes=[dict(pid=self.native.pid)],foreground_process_group_id=os.getpgid(self.native.pid)))
        self.save_fixture()
        self.config=dict(cairn=str(self.root/'cairn'), socket=str(self.root/'api.sock'),token_file=str(self.root/'token'),
            state_dir=str(self.root/'state'),binding='fixture',repo='fixture',harness='agy',
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

    def spawn_queue_child(self, rounds, gate=False, remote=False):
        endpoint=self.root/'native.sock'
        (self.root/'control.json').write_text(json.dumps(rounds))
        gate_path=str(self.root/'gate') if gate else ''
        self.stop_native()
        self.native=subprocess.Popen([sys.executable,'-c',QUEUE_CHILD,'--remote' if remote else 'app-server',
            '--remote' if remote else '--listen','unix://'+str(endpoint),str(self.root/'control.json'),gate_path],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
            env=dict(os.environ,PYTHONPATH=str(ROOT/'scripts')))
        self.addCleanup(self.native.stdout.close)
        self.addCleanup(self.native.stderr.close)
        self.assertEqual(self.native.stdout.readline().strip(),'ready')
        return endpoint

    def adopt_queue_child(self):
        self.config['harness']='codex'
        self.agent['metadata']['harness']='codex'
        self.save_fixture()
        coordination.write_state(self.path, dict(process=coordination.process_reference(self.native.pid),
            agent=self.agent, workspace=str(self.root)))

    def queue_requests(self, endpoint):
        path=endpoint.with_suffix('.requests')
        for _ in range(100):
            if path.exists():
                return json.loads(path.read_text())
            time.sleep(0.05)
        self.fail('native fixture never wrote its request log')

    def test_agy_idle_arrival_prompts_once_across_watcher_restarts(self):
        for _ in range(2):
            result=self.watch()
            self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(len(self.prompts()),1)
        self.assertIn('agent-one',self.prompts()[0][-1])
        self.assertEqual(json.loads(self.path.read_text())['idle_wake']['status'],'submitted')

    def test_native_turn_binding_requires_the_exact_queued_wake(self):
        wake = dict(transport='codex-queue', delivery_id='delivery-one',
                    session=coordination.session_ref(self.agent))
        state = dict(agent=self.agent, idle_wake=wake)
        event = dict(hook_event_name='UserPromptSubmit', turn_id='native-turn-one',
                     prompt=coordination.wake_message(wake))
        expected = dict(delivery_id='delivery-one', native_turn_id='native-turn-one')
        self.assertEqual(coordination.queued_wake_binding(state, event), expected)
        for changed in (dict(event, prompt='owner work'), dict(event, prompt='owner draft '+event['prompt']),
                        dict(event, prompt=coordination.wake_message(dict(wake, delivery_id='different-delivery'))),
                        dict(event, turn_id=''), dict(event, hook_event_name='Stop')):
            self.assertEqual(coordination.queued_wake_binding(state, changed), {})
        state['agent'] = dict(self.agent, execution_id='replacement')
        self.assertEqual(coordination.queued_wake_binding(state, event), {})

    def test_native_codex_queue_preserves_the_terminal_input_path(self):
        self.stop_native()
        endpoint = self.root/'native.sock'
        program = '''
import json, os, pathlib, sys, time
from test_codex_queue import NativeQueue
fixture = NativeQueue()
fixture.setUp()
os.rename(fixture.path, sys.argv[-1][7:])
fixture.listener.settimeout(10)
fixture.serve()
print('ready', flush=True)
fixture.join()
pathlib.Path(sys.argv[-1][7:]).with_suffix('.requests').write_text(json.dumps(fixture.requests))
print('submitted', flush=True)
time.sleep(30)
'''
        self.native = subprocess.Popen([sys.executable, '-c', program, 'app-server', '--listen', 'unix://'+str(endpoint)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=dict(os.environ, PYTHONPATH=str(ROOT/'scripts')))
        self.addCleanup(self.native.stdout.close)
        self.addCleanup(self.native.stderr.close)
        self.assertEqual(self.native.stdout.readline().strip(), 'ready')
        self.config['harness'] = 'codex'
        self.agent['metadata']['harness'] = 'codex'
        self.save_fixture()
        coordination.write_state(self.path, dict(process=coordination.process_reference(self.native.pid),
            agent=self.agent, workspace=str(self.root)))
        result = self.watch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        self.assertEqual(self.native.stdout.readline().strip(), 'submitted')
        requests = json.loads(endpoint.with_suffix('.requests').read_text())
        self.assertEqual(len([r for r in requests if r['method'] == 'thread/queue/add']), 1)
        wake = json.loads(self.path.read_text())['idle_wake']
        self.assertEqual(wake['status'], 'submitted')
        self.assertEqual(wake['queued_submission_id'], 'queued-one')
        self.assertEqual(self.prompts(), [], 'Native queue must never fall back to terminal submission')
        self.watch()
        self.assertEqual(self.prompts(), [])

    def test_paused_codex_queue_retries_only_the_retained_start(self):
        endpoint=self.spawn_queue_child([dict(start='busy'), dict(start='confirm')])
        self.adopt_queue_child()
        result=self.watch()
        self.assertEqual(result.returncode,0,result.stderr)
        wake=json.loads(self.path.read_text())['idle_wake']
        self.assertEqual(wake['status'],'queued')
        self.assertEqual(wake['queued_submission_id'],'queued-one')
        self.assertEqual(self.prompts(),[])
        result=self.watch()
        self.assertEqual(result.returncode,0,result.stderr)
        wake=json.loads(self.path.read_text())['idle_wake']
        self.assertEqual(wake['status'],'submitted')
        methods=[r['method'] for r in self.queue_requests(endpoint)]
        self.assertEqual(methods.count('thread/queue/add'),1)
        self.assertEqual(methods.count('thread/queue/start'),2)
        self.assertNotIn('thread/resume',methods)
        self.assertNotIn('turn/start',methods)
        self.assertEqual(self.prompts(),[])

    def test_lost_codex_queue_add_reply_is_never_duplicated(self):
        endpoint=self.spawn_queue_child([dict(add='refuse')])
        self.adopt_queue_child()
        result=self.watch()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('WAKE_UNCERTAIN',result.stderr)
        wake=json.loads(self.path.read_text())['idle_wake']
        self.assertEqual(wake['status'],'uncertain')
        self.assertNotIn('queued_submission_id',wake)
        result=self.watch()
        self.assertEqual(result.returncode,0,result.stderr)
        methods=[r['method'] for r in self.queue_requests(endpoint)]
        self.assertEqual(methods.count('thread/queue/add'),1)
        self.assertEqual(json.loads(self.path.read_text())['idle_wake']['status'],'uncertain')
        self.assertEqual(self.prompts(),[])

    def test_absent_queue_endpoint_recovers_on_a_later_cycle(self):
        endpoint=self.spawn_queue_child([dict(start='confirm')],gate=True)
        self.adopt_queue_child()
        result=self.watch()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('unavailable',result.stderr)
        self.assertNotIn('idle_wake',json.loads(self.path.read_text()))
        (self.root/'gate').touch()
        for _ in range(100):
            if endpoint.exists():
                break
            time.sleep(0.05)
        result=self.watch()
        self.assertEqual(result.returncode,0,result.stderr)
        wake=json.loads(self.path.read_text())['idle_wake']
        self.assertEqual(wake['status'],'submitted')
        self.assertEqual(wake['queued_submission_id'],'queued-one')
        methods=[r['method'] for r in self.queue_requests(endpoint)]
        self.assertEqual(methods.count('thread/queue/add'),1)
        self.assertEqual(self.prompts(),[])

    def test_missing_queue_helper_is_a_bounded_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            isolated=Path(directory)
            shutil.copyfile(ROOT/'integrations/lifecycle/coordination.py',isolated/'coordination.py')
            state_dir=isolated/'state'
            config_dir=isolated/'bindings'
            state_dir.mkdir()
            config_dir.mkdir()
            config=dict(cairn=str(self.root/'cairn'),socket=str(self.root/'api.sock'),
                token_file=str(self.root/'token'),state_dir=str(state_dir),binding='helper',
                repo='fixture',harness='codex',native_delivery=True,idle_wakeup=str(self.root/'herdr'))
            (config_dir/'helper.json').write_text(json.dumps(config))
            sleeper=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)',
                'app-server','--listen','unix:///absent-queue.sock'])
            self.addCleanup(lambda: (sleeper.terminate(), sleeper.wait(timeout=5)))
            coordination.write_state(state_dir/'session.json',dict(
                process=coordination.process_reference(sleeper.pid),agent=self.agent,
                workspace=str(self.root)))
            result=subprocess.run([sys.executable,str(isolated/'coordination.py'),'watch',
                '--config-dir',str(config_dir),'--once'],capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('helper is unavailable',result.stderr)
            self.assertNotIn('Traceback',result.stderr)
            self.assertNotIn('idle_wake',json.loads((state_dir/'session.json').read_text()))

    def spawn_channel_bridge(self, status='written'):
        self.config['harness'] = 'claude'
        self.agent['metadata']['harness'] = 'claude'
        self.fixture['host']['agent'] = 'claude'
        self.fixture['host']['agent_session']['agent'] = 'claude'
        self.save_fixture()
        channel_dir = self.root/'channels'
        channel_dir.mkdir(exist_ok=True)
        control = channel_dir/'control.json'
        control.write_text(json.dumps({'status': status}))
        socket_path = channel_dir/'bridge.sock'
        self.stop_native()
        self.native = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)', 'claude'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (self.native.terminate(), self.native.wait(timeout=5)))
        bridge = subprocess.Popen([sys.executable, '-c', CHANNEL_BRIDGE, str(socket_path), str(control)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (bridge.terminate(), bridge.wait(timeout=5)))
        self.addCleanup(bridge.stdout.close)
        self.addCleanup(bridge.stderr.close)
        self.assertEqual(bridge.stdout.readline().strip(), 'ready')
        (channel_dir/f"{self.native.pid}.json").write_text(json.dumps(dict(
            schema='cairn.claude-channel/1', parent=coordination.process_reference(self.native.pid),
            process=coordination.process_reference(bridge.pid), socket=str(socket_path))))
        self.config['claude_channel_dir'] = str(channel_dir)
        coordination.write_state(self.path, dict(process=coordination.process_reference(self.native.pid),
            agent=self.agent, workspace=str(self.root)))
        return control, socket_path

    def channel_lines(self, socket_path):
        log = socket_path.with_suffix('.log')
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def test_claude_channel_wake_writes_without_terminal_prompt(self):
        control, socket_path = self.spawn_channel_bridge('written')
        result = self.watch()
        self.assertEqual(result.returncode, 0, result.stderr)
        wake = json.loads(self.path.read_text())['idle_wake']
        self.assertEqual(wake['status'], 'submitted')
        self.assertEqual(wake['transport'], 'claude-channel')
        self.assertEqual(self.prompts(), [], 'channel wake must not use terminal submission')
        lines = self.channel_lines(socket_path)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]['parent'], coordination.process_reference(self.native.pid))
        self.assertEqual(lines[0]['meta']['delivery_id'], 'delivery-one')
        self.assertEqual(lines[0]['meta']['agent_id'], 'agent-one')
        self.assertIn('automatic inbox wakeup', lines[0]['content'])

    def test_claude_selected_session_wakes_after_native_process_replacement(self):
        _, socket_path = self.spawn_channel_bridge('written')
        self.config['claude_channel_sessions'] = ['native-one']
        original = json.loads(json.dumps(self.config))
        self.assertEqual(self.watch().returncode, 0)
        self.assertEqual(len(self.channel_lines(socket_path)), 1)
        registry = json.loads((Path(self.config['claude_channel_dir']) / f'{self.native.pid}.json').read_text())
        self.stop_native()
        self.native = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        replacement = coordination.process_reference(self.native.pid)
        coordination.write_state(self.path, dict(process=replacement, agent=self.agent, workspace=str(self.root)))
        self.assertEqual(self.watch().returncode, 0)
        self.assertNotIn('idle_wake', json.loads(self.path.read_text()), 'old process registry was reused')
        registry['parent'] = replacement
        (Path(self.config['claude_channel_dir']) / f'{self.native.pid}.json').write_text(json.dumps(registry))
        self.assertEqual(self.watch().returncode, 0)
        lines = self.channel_lines(socket_path)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[-1]['parent'], replacement)
        self.assertEqual(lines[-1]['meta']['agent_id'], self.agent['agent_id'])
        self.assertEqual(self.config, original)
        self.assertEqual(self.prompts(), [])

    def test_claude_unselected_session_keeps_boundary_claims_without_channel_wake(self):
        _, socket_path = self.spawn_channel_bridge('written')
        self.config['claude_channel_sessions'] = ['another-session']
        original = json.loads(json.dumps(self.config))
        self.assertEqual(self.watch().returncode, 0)
        self.assertNotIn('idle_wake', json.loads(self.path.read_text()))
        self.assertEqual(self.channel_lines(socket_path), [])
        self.assertEqual(self.prompts(), [])
        for event, phase in [('UserPromptSubmit', 'busy'), ('Stop', 'idle')]:
            state = json.loads(self.path.read_text())
            with mock.patch.object(coordination, 'call', return_value={'attempt': None}) as api:
                coordination.inbox_context(self.config, state, self.path,
                    dict(event=event, phase=phase, native_turn_id='owner-prompt'))
                self.assertEqual(api.call_count, 1)
                self.assertEqual(api.call_args.args[1], 'session-inbox-claim')
                self.assertEqual(api.call_args.args[2]['session'], coordination.session_ref(self.agent))
        self.assertEqual(self.config, original)

    def test_claude_selected_session_missing_channel_remains_closed(self):
        self.config.update(harness='claude', claude_channel_dir=str(self.root/'absent'), claude_channel_sessions=['native-one'])
        self.assertEqual(self.watch().returncode, 0)
        self.assertNotIn('idle_wake', json.loads(self.path.read_text()))
        self.assertEqual(self.prompts(), [])
        with mock.patch.object(coordination, 'call', side_effect=AssertionError('unexpected inbox claim')) as api:
            for event, phase in [('UserPromptSubmit', 'busy'), ('Stop', 'idle')]:
                state = json.loads(self.path.read_text())
                self.assertEqual(coordination.inbox_context(self.config, state, self.path,
                    dict(event=event, phase=phase, native_turn_id='owner-prompt')), '')
            api.assert_not_called()

    def test_claude_channel_uncertain_is_retained_without_fallback(self):
        control, socket_path = self.spawn_channel_bridge('uncertain')
        result = self.watch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('WAKE_UNCERTAIN', result.stderr)
        wake = json.loads(self.path.read_text())['idle_wake']
        self.assertEqual(wake['status'], 'uncertain')
        state = json.loads(self.path.read_text())
        state['delivered_since_idle'] = True
        coordination.write_state(self.path, state)
        coordination.inbox_context(self.config, state, self.path,
            dict(event='Stop', phase='idle', native_turn_id='previous-prompt'))
        self.assertEqual(json.loads(self.path.read_text())['idle_wake'], wake,
                         'idle bookkeeping rearmed an uncertain channel write')
        result = self.watch()
        self.assertEqual(len(self.channel_lines(socket_path)), 1, 'uncertain channel wake was resent')
        self.assertEqual(self.prompts(), [], 'uncertain channel wake fell back to terminal submission')

    def test_claude_channel_unavailable_retries_cleanly(self):
        control, socket_path = self.spawn_channel_bridge('unavailable')
        result = self.watch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('idle_wake', json.loads(self.path.read_text()))
        self.assertEqual(self.prompts(), [])
        control.write_text(json.dumps({'status': 'written'}))
        result = self.watch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.path.read_text())['idle_wake']['status'], 'submitted')
        self.assertEqual(len(self.channel_lines(socket_path)), 2)

    def test_channel_wake_binding_owns_only_its_exact_prompt(self):
        wake = dict(transport='claude-channel', delivery_id='delivery-one',
                    session=coordination.session_ref(self.agent))
        state = dict(agent=self.agent, idle_wake=wake)
        message = coordination.wake_message(wake)
        event = dict(hook_event_name='UserPromptSubmit', prompt_id='prompt-one',
                     prompt=f'<channel source="cairn-events">{message}</channel>')
        expected = dict(delivery_id='delivery-one', native_turn_id='claude-channel:prompt-one')
        self.assertEqual(coordination.channel_wake_binding(state, event), expected)
        for changed in (dict(event, prompt='owner work'),
                        dict(event, prompt=coordination.wake_message(dict(wake, delivery_id='different'))),
                        dict(event, prompt_id=''), dict(event, prompt_id='p\0id'),
                        dict(event, hook_event_name='Stop')):
            self.assertEqual(coordination.channel_wake_binding(state, changed), {})
        state['agent'] = dict(self.agent, execution_id='replacement')
        self.assertEqual(coordination.channel_wake_binding(state, event), {})
        # A prompt the session already observed cannot be claimed onto.
        self.assertEqual(coordination.channel_wake_binding(state, event, joined=True), {})

    def test_claude_ownership_key_comes_from_prompt_id_not_display_turn(self):
        config = dict(harness='claude')
        event = dict(session_id='native-one', cwd=str(self.root), hook_event_name='Stop',
                     prompt_id='prompt-one', turn_id='display-turn')
        observed = coordination.normalize(config, event)
        self.assertEqual(observed['native_turn_id'], 'claude-channel:prompt-one')
        self.assertEqual(coordination.normalize(config, dict(event, prompt_id=''))['native_turn_id'], '')
        with self.assertRaises(coordination.CoordinationError):
            coordination.normalize(config, dict(event, hook_event_name='Interrupt'))

    def test_another_claude_prompt_cannot_end_an_owned_request(self):
        state = dict(inbox_intent=dict(native_turn_id='claude-channel:prompt-one'))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'session.json'
            coordination.write_state(path, state)
            observation = dict(native_turn_id='claude-channel:prompt-two')
            with self.assertRaisesRegex(coordination.CoordinationError, 'NATIVE_TURN_MISMATCH'):
                coordination.inbox_context(dict(native_delivery=True), json.loads(path.read_text()),
                                           path, observation)

    def test_missing_configured_channel_does_not_admit_work_on_owner_prompts(self):
        config = dict(self.config, harness='claude', native_delivery=True,
                      claude_channel_dir=str(self.root/'missing-channel'))
        with mock.patch.object(coordination, 'call',
                               side_effect=AssertionError('owner prompt attempted inbox admission')) as api:
            for event, phase in [('UserPromptSubmit', 'busy'), ('Stop', 'idle')]:
                with self.subTest(event=event):
                    state = dict(agent=self.agent, process=coordination.process_reference(os.getpid()))
                    result = coordination.inbox_context(config, state, self.root/'state.json',
                        dict(event=event, phase=phase, native_turn_id='owner-prompt'))
                    self.assertEqual(result, '')
                    self.assertNotIn('inbox_intent', state)
            api.assert_not_called()

    def test_sequential_channel_deliveries_after_watcher_releases_completed_attempt(self):
        host = os.getppid()
        agent = dict(self.agent, context_revision=3, display_name='agent-one', inbox='agent/agent-one')
        config = dict(self.config, harness='claude', binding='sequential', process_names=[],
                      claude_channel_dir=str(self.root/'channel-not-running'))
        path = coordination.state_path(config, 'native-one')
        coordination.write_state(path, dict(schema='cairn.native-session/1',
            process=coordination.process_reference(host), agent=agent, workspace=str(self.root)))
        claims, completed = [], set()

        def fake_call(config, operation, request=None, **kwargs):
            current = json.loads(path.read_text())
            if operation == 'agent-heartbeat':
                return current['agent']
            if operation == 'agent-context':
                return dict(current['agent'], context_revision=current['agent']['context_revision']+1,
                            metadata=dict(request['metadata']))
            if operation == 'session-inbox-claim':
                claims.append(dict(request))
                delivery_id = request['delivery_id']  # Ordinary unbound claims must fail this fixture.
                return dict(attempt=dict(attempt_id=request['request_id'], session=request['session'],
                    native_turn_id=request['native_turn_id'], delivery=dict(delivery_id=delivery_id,
                        lease_id='lease-'+delivery_id, event=dict(event_id='event-'+delivery_id,
                            kind='request', ref=dict(record_id='record', version=1), **{'from': 'agent:x'}))))
            if operation == 'session-inbox-reconcile':
                delivery = current['inbox_attempt']['delivery']['delivery_id']
                if request['reason'] == 'delivery_completed' and delivery not in completed:
                    raise coordination.CoordinationError('DELIVERY_ACTIVE', 'not yet completed')
                return {}
            if operation == 'event-renew':
                return current['inbox_attempt']['delivery']
            raise AssertionError('unexpected API operation '+operation)

        def prompt(prompt_id, text, event='UserPromptSubmit'):
            return dict(session_id='native-one', cwd=str(self.root), host_pid=host,
                        hook_event_name=event, prompt_id=prompt_id, prompt=text)

        with mock.patch.object(coordination, 'call', fake_call):
            for number in range(3):
                delivery_id = 'delivery-'+str(number)
                marker = dict(transport='claude-channel', status='submitted', delivery_id=delivery_id,
                              session=coordination.session_ref(agent))
                state = json.loads(path.read_text())
                state['idle_wake'] = marker
                coordination.write_state(path, state)
                wake_prompt = prompt('wake-'+str(number),
                    f'<channel source="cairn-events">{coordination.wake_message(marker)}</channel>')
                result = coordination.handle(config, wake_prompt)
                self.assertIn('structured inbox context', result['hookSpecificOutput']['additionalContext'],
                              'missing context for '+delivery_id)
                self.assertEqual(len(claims), number+1, 'fresh channel delivery did not claim exactly once')
                self.assertEqual(claims[-1]['delivery_id'], delivery_id)
                self.assertEqual(claims[-1]['native_turn_id'], 'claude-channel:wake-'+str(number))
                if number != 1:
                    completed.add(delivery_id)
                    # The watcher can observe explicit completion before native Stop.
                    state = json.loads(path.read_text())
                    coordination.watch_inbox(config, state, path)
                    self.assertNotIn('inbox_intent', json.loads(path.read_text()))
                else:
                    self.assertIn('inbox_intent', json.loads(path.read_text()))
                self.assertTrue(json.loads(path.read_text())['delivered_since_idle'])
                coordination.handle(config, dict(wake_prompt, hook_event_name='Stop'))
                self.assertNotIn('inbox_intent', json.loads(path.read_text()))
                # Ordinary owner turns and repeated Stops must never consume the next delivery.
                owner = prompt('owner-'+str(number), 'ordinary owner work')
                coordination.handle(config, owner)
                coordination.handle(config, dict(owner, hook_event_name='Stop'))
                coordination.handle(config, dict(owner, hook_event_name='Stop'))
                self.assertEqual(len(claims), number+1)
            self.assertFalse(json.loads(path.read_text())['delivered_since_idle'])

    def test_channel_wake_joining_active_prompt_is_refused_and_retried(self):
        # Live probe evidence (2026-09-16, /tmp/cairn-claude-go-probe): while a
        # prompt ran a Bash tool, Claude injected channel B into the SAME
        # prompt (shared prompt_id), and the model answered both in one turn.
        # A wake joining an active prompt must never claim work onto it.
        host = os.getppid()
        channel_dir = self.root/'channels'
        channel_dir.mkdir()
        host_ref = coordination.process_reference(host)
        (channel_dir/f"{host_ref['pid']}.json").write_text(json.dumps(dict(
            schema='cairn.claude-channel/1', parent=host_ref,
            process=coordination.process_reference(os.getpid()),
            socket=str(channel_dir/'bridge.sock'))))
        agent = dict(self.agent, context_revision=3, display_name='agent-one', inbox='agent/agent-one')
        marker = dict(transport='claude-channel', delivery_id='delivery-one',
                      session=coordination.session_ref(agent))
        state = dict(schema='cairn.native-session/1', process=host_ref, agent=agent,
                     workspace=str(self.root), idle_wake=marker)
        state_dir = self.root/'join-state'
        state_dir.mkdir()
        config = dict(harness='claude', state_dir=str(state_dir), native_delivery=True,
                      idle_wakeup=str(self.root/'herdr'), claude_channel_dir=str(channel_dir),
                      cairn='/absent/cairn', socket='/absent/api.sock', token_file='/absent/token',
                      repo='fixture', binding='join', process_names=[])
        path = coordination.state_path(config, 'native-one')
        coordination.write_state(path, state)
        recorded = []

        def fake_call(config, operation, request=None, **kwargs):
            recorded.append(operation)
            if operation == 'agent-heartbeat':
                return json.loads(path.read_text())['agent']
            if operation == 'agent-context':
                current = json.loads(path.read_text())['agent']
                return dict(current, context_revision=current['context_revision']+1,
                            metadata=json.loads(json.dumps(request['metadata'])))
            if operation == 'session-inbox-claim':
                return dict(attempt=dict(attempt_id='attempt-one', session=request['session'],
                    native_turn_id=request.get('native_turn_id'),
                    delivery=dict(delivery_id='delivery-one', lease_id='lease-one',
                        event=dict(event_id='event-one', kind='request',
                                   ref=dict(record_id='r', version=1), **{'from': 'agent:x'}))))
            if operation == 'event-renew':
                return json.loads(path.read_text())['inbox_attempt']['delivery']
            if operation == 'session-inbox-reconcile':
                raise coordination.CoordinationError('DELIVERY_ACTIVE', 'delivery still active')
            raise AssertionError('unexpected API operation '+operation)

        owner_prompt = dict(session_id='native-one', cwd=str(self.root),
            hook_event_name='UserPromptSubmit', prompt_id='b59fa01b-5434-42f8-a44f-772308307eaa',
            prompt='Run a bounded sleep for the busy race', host_pid=host)
        joined_prompt = dict(owner_prompt,
            prompt=f'<channel source="cairn-events">{coordination.wake_message(marker)}</channel>')
        with mock.patch.object(coordination, 'call', fake_call):
            coordination.handle(config, owner_prompt)          # The owner prompt starts.
            self.assertIn('idle_wake', json.loads(path.read_text()))
            recorded.clear()
            with self.assertRaisesRegex(coordination.CoordinationError, 'NATIVE_PROMPT_REFUSED'):
                coordination.handle(config, joined_prompt)     # The wake joins the busy prompt.
            after_join = json.loads(path.read_text())
            self.assertNotIn('idle_wake', after_join, 'joined wake kept its marker')
            self.assertEqual(after_join['active_prompt'].get('refused_delivery'), 'delivery-one')
            self.assertNotIn('session-inbox-claim', recorded)  # Never claimed onto the owner prompt.
            coordination.handle(config, dict(owner_prompt, hook_event_name='Stop'))
            self.assertNotIn('active_prompt', json.loads(path.read_text()))
            self.assertNotIn('session-inbox-claim', recorded, 'prompt end claimed refused work')
            # Retry when idle: the watcher marks again and the next wake owns a fresh prompt.
            retried = json.loads(path.read_text())
            retried['idle_wake'] = marker
            retried['agent']['metadata']['state'] = 'idle'
            coordination.write_state(path, retried)
            recorded.clear()
            coordination.handle(config, dict(joined_prompt, prompt_id='fresh-prompt-one'))
            self.assertIn('session-inbox-claim', recorded)
            claimed = json.loads(path.read_text())['inbox_attempt']
            self.assertEqual(claimed['native_turn_id'], 'claude-channel:fresh-prompt-one')

    def test_owner_input_joining_a_wake_owned_prompt_shares_it_safely(self):
        # Inverse of the busy-race ordering (parent live evidence): owner
        # input can join a WAKE-owned prompt. First sighting never implies
        # exclusivity: the request stays bound to the shared prompt, nothing
        # is misreleased, and the shared prompt is marked for the
        # cancellation contract.
        host = os.getppid()
        agent = dict(self.agent, context_revision=3, display_name='agent-one', inbox='agent/agent-one')
        marker = dict(transport='claude-channel', delivery_id='delivery-two',
                      session=coordination.session_ref(agent))
        state = dict(schema='cairn.native-session/1',
            process=coordination.process_reference(host), agent=agent,
            workspace=str(self.root), idle_wake=marker)
        state_dir = self.root/'inverse-state'
        state_dir.mkdir()
        config = dict(harness='claude', state_dir=str(state_dir), native_delivery=True,
            idle_wakeup=str(self.root/'herdr'), claude_channel_dir=str(self.root/'channels-absent'),
            cairn='/absent/cairn', socket='/absent/api.sock', token_file='/absent/token',
            repo='fixture', binding='inverse', process_names=[])
        (self.root/'channels-absent').mkdir()
        path = coordination.state_path(config, 'native-one')
        coordination.write_state(path, state)
        recorded = []

        def fake_call(config, operation, request=None, **kwargs):
            recorded.append(operation)
            if operation == 'agent-heartbeat':
                return json.loads(path.read_text())['agent']
            if operation == 'agent-context':
                current = json.loads(path.read_text())['agent']
                return dict(current, context_revision=current['context_revision']+1,
                            metadata=json.dumps(request['metadata']) and json.loads(json.dumps(request['metadata'])))
            if operation == 'session-inbox-claim':
                return dict(attempt=dict(attempt_id='attempt-two', session=request['session'],
                    native_turn_id=request.get('native_turn_id'),
                    delivery=dict(delivery_id='delivery-two', lease_id='lease-two',
                        event=dict(event_id='event-two', kind='request',
                                   ref=dict(record_id='r2', version=1), **{'from': 'agent:x'}))))
            if operation == 'event-renew':
                return json.loads(path.read_text())['inbox_attempt']['delivery']
            if operation == 'session-inbox-reconcile':
                if request.get('reason') == 'delivery_completed':
                    raise coordination.CoordinationError('DELIVERY_ACTIVE', 'delivery still active')
                return {}  # turn_ended reconciliation ends the native attempt.
            raise AssertionError('unexpected API operation '+operation)

        wake_prompt = dict(session_id='native-one', cwd=str(self.root), hook_event_name='UserPromptSubmit',
            prompt_id='wake-owned-prompt', host_pid=host,
            prompt=f'<channel source="cairn-events">{coordination.wake_message(marker)}</channel>')
        with mock.patch.object(coordination, 'call', fake_call):
            coordination.handle(config, wake_prompt)          # The fresh wake claims its prompt.
            claimed = json.loads(path.read_text())['inbox_attempt']
            self.assertEqual(claimed['native_turn_id'], 'claude-channel:wake-owned-prompt')
            recorded.clear()
            owner_join = dict(wake_prompt, prompt='owner typed work while the wake runs')
            coordination.handle(config, owner_join)           # Owner input joins the wake prompt; never rejected.
            joined_state = json.loads(path.read_text())
            self.assertTrue(joined_state['active_prompt'].get('joined'), 'shared prompt not recorded')
            self.assertEqual(joined_state['inbox_attempt']['native_turn_id'], 'claude-channel:wake-owned-prompt')
            # The reconcile attempt is the normal same-turn reinjection check;
            # DELIVERY_ACTIVE refuses it, so the hold is never released mid-prompt.
            coordination.handle(config, dict(wake_prompt, hook_event_name='Stop'))
            final = json.loads(path.read_text())
            self.assertNotIn('active_prompt', final)

    def test_joined_channel_wake_hook_exits_two(self):
        # The actual hook command must exit 2 for a joined channel wake so
        # Claude erases only the channel text (proven live by the parent's
        # rejection probe: the owner prompt continued, draft intact).
        host = os.getppid()
        channel_dir = self.root/'channels-exit2'
        channel_dir.mkdir()
        host_ref = coordination.process_reference(host)
        (channel_dir/f"{host_ref['pid']}.json").write_text(json.dumps(dict(
            schema='cairn.claude-channel/1', parent={k: host_ref[k] for k in ('pid','start','boot')},
            process={k: coordination.process_reference(os.getpid())[k] for k in ('pid','start','boot')},
            socket=str(channel_dir/'bridge.sock'))))
        agent = dict(self.agent, context_revision=3, display_name='agent-one', inbox='agent/agent-one')
        marker = dict(transport='claude-channel', delivery_id='delivery-three',
                      session=coordination.session_ref(agent))
        state = dict(schema='cairn.native-session/1', process=host_ref, agent=agent,
            workspace=str(self.root), idle_wake=marker,
            active_prompt=dict(id='claude-channel:busy-owner-prompt'))
        state_dir = self.root/'exit2-state'
        state_dir.mkdir()
        config = dict(harness='claude', state_dir=str(state_dir), native_delivery=True,
            idle_wakeup=str(self.root/'herdr'), claude_channel_dir=str(channel_dir),
            cairn=str(self.root/'cairn'), socket=str(self.root/'api.sock'),
            token_file=str(self.root/'token'), repo='fixture', binding='exit2', process_names=[])
        path = coordination.state_path(config, 'native-one')
        coordination.write_state(path, state)
        self.agent['context_revision'] = 3
        self.agent['metadata']['state'] = 'busy'
        self.save_fixture()
        event = json.dumps(dict(session_id='native-one', cwd=str(self.root),
            hook_event_name='UserPromptSubmit', prompt_id='busy-owner-prompt', host_pid=host,
            prompt=f'<channel source="cairn-events">{coordination.wake_message(marker)}</channel>'))
        result = subprocess.run([sys.executable, str(ROOT/'integrations/lifecycle/coordination.py'),
            'hook', '--config', json.dumps(config) if False else self.write_exit2_config(config)],
            input=event, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('rejected', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        after = json.loads(path.read_text())
        self.assertNotIn('idle_wake', after)
        self.assertEqual(after['active_prompt'].get('refused_delivery'), 'delivery-three')

    def write_exit2_config(self, config):
        path = self.root/'exit2-config.json'
        path.write_text(json.dumps(config))
        return str(path)

    def test_interrupt_restores_presence_without_touching_inbox_work(self):
        # handle() resolves the native owner among its ancestors, so the
        # in-process observation anchors on this test process's own parent.
        host = os.getppid()
        busy_agent = dict(self.agent, context_revision=3, display_name='agent-one',
                          inbox='agent/agent-one')
        busy_agent['metadata'] = dict(self.agent['metadata'], state='busy')
        state = dict(schema='cairn.native-session/1', process=coordination.process_reference(host),
            agent=busy_agent, workspace=str(self.root),
            inbox_intent=dict(request_id='r', session=coordination.session_ref(self.agent),
                              native_turn_id='turn-one'),
            inbox_attempt=dict(attempt_id='a1', session=coordination.session_ref(self.agent),
                delivery=dict(delivery_id='d1', lease_id='l1', event=dict(event_id='e1',
                    kind='request', ref=dict(record_id='r', version=1), **{'from': 'agent:x'}))))
        state_dir = self.root/'interrupt-state'
        state_dir.mkdir(exist_ok=True)
        config = dict(harness='codex', state_dir=str(state_dir), native_delivery=True,
                      cairn='/absent/cairn', socket='/absent/api.sock', token_file='/absent/token',
                      repo='fixture', binding='interrupt', process_names=[])
        path = coordination.state_path(config, 'native-one')
        coordination.write_state(path, state)
        recorded = []

        def fake_call(config, operation, request=None, **kwargs):
            recorded.append(operation)
            if operation == 'agent-heartbeat':
                return json.loads(path.read_text())['agent']
            if operation == 'agent-context':
                agent = json.loads(path.read_text())['agent']
                return dict(agent, context_revision=agent['context_revision']+1,
                            metadata=json.loads(json.dumps(request['metadata'])))
            if operation == 'session-inbox-claim':
                return dict(attempt=None)
            if operation in ('session-inbox-reconcile', 'event-renew', 'session-inbox-ready'):
                return {}
            raise AssertionError('unexpected API operation '+operation)

        event = dict(session_id='native-one', cwd=str(self.root), hook_event_name='Interrupt',
                     turn_id='turn-one', host_pid=host)
        with mock.patch.object(coordination, 'call', fake_call):
            result = coordination.handle(config, event)
        self.assertEqual(recorded, ['agent-heartbeat', 'agent-context'])
        updated = json.loads(path.read_text())
        self.assertEqual(updated['agent']['metadata']['state'], 'idle', 'interrupt did not restore idle presence')
        self.assertIn('inbox_intent', updated, 'interrupt released or claimed inbox work')
        # The owning turn's Stop still ends the request normally.
        recorded.clear()
        with mock.patch.object(coordination, 'call', fake_call):
            coordination.handle(config, dict(event, hook_event_name='Stop'))
        self.assertIn('session-inbox-reconcile', recorded)
        self.assertNotIn('inbox_intent', json.loads(path.read_text()))
        # A fenced session must not treat Interrupt as proof of turn cleanup.
        recorded.clear()
        coordination.write_state(path, dict(
            schema='cairn.native-session/1', process=coordination.process_reference(host),
            agent=dict(self.agent, context_revision=3, display_name='agent-one',
                       inbox='agent/agent-one'), workspace=str(self.root),
            inbox_intent=dict(request_id='r', session=coordination.session_ref(self.agent),
                              native_turn_id='turn-one')))
        def stale_call(config, operation, request=None, **kwargs):
            recorded.append(operation)
            if operation == 'agent-heartbeat':
                raise coordination.CoordinationError('STALE_SESSION', 'fenced by restore')
            if operation == 'agent-directory':
                return dict(agents=[dict(self.agent, execution_id='newer')])
            raise AssertionError('unexpected API operation '+operation)
        with mock.patch.object(coordination, 'call', stale_call):
            with self.assertRaises(coordination.CoordinationError):
                coordination.handle(config, event)
        self.assertNotIn('session-inbox-reconcile', recorded)

    def test_remote_tui_wake_uses_the_shared_app_server_route(self):
        # An interactive TUI started with --remote unix://SOCK shares an
        # app-server: the watcher queues through that socket with user-level
        # verification and never submits terminal input.
        endpoint=self.spawn_queue_child([dict(start='confirm')], remote=True)
        self.adopt_queue_child()
        result=self.watch()
        self.assertEqual(result.returncode,0,result.stderr)
        wake=json.loads(self.path.read_text())['idle_wake']
        self.assertEqual(wake['transport'],'codex-queue')
        self.assertEqual(wake['owner'],'uid')
        self.assertEqual(wake['status'],'submitted')
        self.assertEqual(wake['queued_submission_id'],'queued-one')
        methods=[r['method'] for r in self.queue_requests(endpoint)]
        self.assertEqual(methods.count('thread/queue/add'),1)
        self.assertEqual(self.prompts(),[],'remote TUI wake must not use terminal submission')

    def test_agy_uncertain_submission_is_not_repeated(self):
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

    def test_agy_following_delivery_gets_a_new_wakeup(self):
        self.watch()
        self.fixture['delivery']='delivery-two'
        self.save_fixture()
        self.watch()
        self.assertEqual(len(self.prompts()),2)

    def test_agy_unrelated_candidate_closing_does_not_hide_the_live_target(self):
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

    def test_codex_without_native_queue_never_falls_back_even_with_matching_rollout(self):
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
        self.assertEqual(self.prompts(), [])
        self.assertNotIn('idle_wake', json.loads(self.path.read_text()))

    def test_claude_without_channel_never_falls_back_to_terminal(self):
        self.config['harness'] = 'claude'
        self.agent['metadata']['harness'] = 'claude'
        self.fixture['host']['agent'] = 'claude'
        self.fixture['host']['agent_session']['agent'] = 'claude'
        self.save_fixture()
        coordination.write_state(self.path, dict(process=coordination.process_reference(self.native.pid),
            agent=self.agent, workspace=str(self.root)))
        for _ in range(2):
            result = self.watch()
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.prompts(), [])
        self.assertNotIn('idle_wake', json.loads(self.path.read_text()))


if __name__=='__main__':
    unittest.main()
