"""Selected provider observations and staged Go delegation across Hermes callbacks."""
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

from test_f04_activation import ROOT, SESSION, TURN, encoded, write
import test_f04_issued as issued
from test_r02_compatibility_install import InstalledTools
from test_r04_host_wiring import UnboundFixture
import stage_lifecycle_hooks as hook_stage


class HermesProviderObservation(unittest.TestCase):
    def test_only_owner_provider_failure_and_recovery_are_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home/'cairn-coordination.json').write_text(json.dumps(dict(script='engine',config='config')))
            modules = {'hermes_constants': types.SimpleNamespace(get_hermes_home=lambda:home),
                'tools.terminal_tool':types.SimpleNamespace(get_session_cwd=lambda _:directory,resolve_task_overrides=lambda _:{})}
            spec = importlib.util.spec_from_file_location('hermes_coordination_test',ROOT/'integrations/hermes/coordination.py')
            plugin = importlib.util.module_from_spec(spec)
            with patch.dict(sys.modules,modules):
                spec.loader.exec_module(plugin)
            callbacks = {}
            unload = []
            cli = types.SimpleNamespace(session_id='owner', _active_turn_id='owner:turn',
                                        _admission_lock=threading.RLock())
            ctx = types.SimpleNamespace(
                _manager=types.SimpleNamespace(_cli_ref=cli),
                register_hook=lambda name, callback: callbacks.update({name: callback}),
                register_middleware=lambda name, callback: None,
                on_unload=unload.append,
            )
            plugin.register(ctx)
            sent = []
            def run(*_,**kwargs):
                sent.append(json.loads(kwargs['input']))
                return types.SimpleNamespace(returncode=0,stdout='{}')
            with patch.object(plugin.subprocess,'run',side_effect=run), patch.dict(os.environ,CAIRN_WAKE_CONTEXT='wake.json'):
                try:
                    callbacks['pre_llm_call'](session_id='owner',turn_id='owner:turn',platform='cli')
                    callbacks['pre_llm_call'](session_id='child',parent_session_id='owner')
                    callbacks['api_request_error'](session_id='child',status_code=429,reason='rate_limit')
                    self.assertEqual(len(sent),1)
                    callbacks['api_request_error'](session_id='owner',status_code=429,reason='rate_limit',error={'message':'PRIVATE'},request={'body':'PRIVATE'})
                    self.assertEqual(sent[-1]['provider_failure'],dict(harness='hermes',source='native-hook',kind='rate_limit',code='rate_limit',status=429))
                    callbacks['post_api_request'](session_id='owner',response={'text':'PRIVATE'})
                    self.assertIsNone(sent[-1]['provider_failure'])
                    callbacks['api_request_error'](session_id='owner',status_code=402,reason='billing')
                    self.assertEqual(sent[-1]['provider_failure']['kind'],'billing')
                    callbacks['api_request_error'](session_id='owner',status_code=500,reason='server_error')
                    self.assertIsNone(sent[-1]['provider_failure'])
                    self.assertNotIn('PRIVATE',json.dumps(sent))
                    callbacks['post_llm_call'](session_id='owner',turn_id='owner:turn',platform='cli')
                finally:
                    for close in unload:
                        close()


BRIDGE_CHILD = r'''
import importlib.util, json, os, sys, threading, types
from pathlib import Path
home = Path(sys.argv[1]); root = Path(sys.argv[2]); mode = sys.argv[3]
sys.modules['hermes_constants'] = types.SimpleNamespace(get_hermes_home=lambda: home)
sys.modules['tools.terminal_tool'] = types.SimpleNamespace(get_session_cwd=lambda _: str(home), resolve_task_overrides=lambda _: {})
spec = importlib.util.spec_from_file_location('hermes_bridge_child', root/'integrations/hermes/coordination.py')
plugin = importlib.util.module_from_spec(spec); spec.loader.exec_module(plugin)
unload = []
cli = types.SimpleNamespace(session_id='owner', _active_turn_id='', _admission_lock=threading.RLock())
ctx = types.SimpleNamespace(_manager=types.SimpleNamespace(_cli_ref=cli), register_hook=lambda *a: None,
                            register_middleware=lambda *a: None, on_unload=unload.append)
path = f'/tmp/cairn-hermes-{os.getpid()}.sock'
import socket, time
holders = []
if mode == 'saturated':
    # Another live bridge whose accept loop is wedged: listen(1) and never accept.
    live = socket.socket(socket.AF_UNIX); live.bind(path); live.listen(1); holders.append(live)
    for _ in range(4):
        queued = socket.socket(socket.AF_UNIX); queued.setblocking(False)
        try:
            queued.connect(path)
        except (BlockingIOError, OSError):
            pass
        holders.append(queued)
    live_inode = os.stat(path).st_ino
elif mode == 'stale':
    dead = socket.socket(socket.AF_UNIX); dead.bind(path); dead.close()  # file left behind, nobody listening
started = time.monotonic()
plugin.register(ctx)
elapsed = time.monotonic() - started
if mode == 'saturated':
    print(json.dumps(dict(pid=os.getpid(), bound=False, elapsed=elapsed, kept_live=os.path.exists(path) and os.stat(path).st_ino == live_inode)), flush=True)
    os.unlink(path)
    raise SystemExit(0)
print(json.dumps(dict(pid=os.getpid(), bound=os.path.exists(path))), flush=True)
if mode == 'pipelined':
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(2)
        client.connect(path)
        requests = [dict(id=n, method='session/status', params=dict(session_id='owner')) for n in (1, 2)]
        client.sendall((''.join(json.dumps(request) + '\n' for request in requests)).encode('utf-8'))
        reader = client.makefile('r', encoding='utf-8')
        responses = [json.loads(reader.readline()) for _ in requests]
    print(json.dumps(dict(responses=responses)), flush=True)
    for close in unload:
        close()
elif mode == 'unload':
    for close in unload:
        close()
    print(json.dumps(dict(after_unload=os.path.exists(path))), flush=True)
elif mode == 'fork':
    child = os.fork()
    if child == 0:
        sys.exit(0)  # a forked child's normal exit must not remove the parent's socket
    os.waitpid(child, 0)
    print(json.dumps(dict(after_fork_child=os.path.exists(path))), flush=True)
# mode 'exit' and the others fall through to a normal interpreter exit without unload
'''


class HermesBridgeSocketLifetime(unittest.TestCase):
    """The per-process bridge socket must not outlive its Hermes process."""

    def run_child(self, mode):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home/'cairn-coordination.json').write_text(json.dumps(dict(script='engine', config='config')))
            import subprocess
            result = subprocess.run([sys.executable, '-c', BRIDGE_CHILD, str(home), str(ROOT), mode],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = [json.loads(line) for line in result.stdout.splitlines()]
            return lines, Path(f"/tmp/cairn-hermes-{lines[0]['pid']}.sock")

    def test_socket_is_bound_before_register_returns_and_removed_on_immediate_unload(self):
        lines, path = self.run_child('unload')
        self.assertTrue(lines[0]['bound'], 'bridge socket was not bound synchronously by register()')
        self.assertFalse(lines[1]['after_unload'], 'unload immediately after register left the socket')
        self.assertFalse(path.exists())

    def test_complete_requests_in_one_write_are_drained(self):
        lines, path = self.run_child('pipelined')
        self.assertEqual([response['id'] for response in lines[1]['responses']], [1, 2])
        self.assertEqual([response['result']['session_id'] for response in lines[1]['responses']],
                         ['owner', 'owner'])
        self.assertFalse(path.exists())

    def test_socket_is_removed_on_exit_without_unload(self):
        lines, path = self.run_child('exit')
        self.assertTrue(lines[0]['bound'])
        self.assertFalse(path.exists(), 'normal interpreter exit without plugin unload leaked the socket')

    def test_wedged_live_bridge_does_not_block_register_or_lose_its_socket(self):
        lines, path = self.run_child('saturated')
        self.assertLess(lines[0]['elapsed'], 3, 'register() blocked on a saturated existing listener')
        self.assertTrue(lines[0]['kept_live'], "an unresponsive live bridge's socket was replaced")

    def test_stale_socket_file_is_replaced(self):
        lines, path = self.run_child('stale')
        self.assertTrue(lines[0]['bound'], 'a refused stale socket was not replaced')
        self.assertFalse(path.exists())

    def test_forked_child_exit_keeps_the_parent_socket(self):
        lines, path = self.run_child('fork')
        self.assertTrue(lines[1]['after_fork_child'], "a forked child's exit removed the parent's socket")
        self.assertFalse(path.exists())


HERMES_EVENTS = {'TurnStart': 15, 'TurnEnd': 15, 'ProviderObservation': 15, 'SessionEnd': 2}


class HermesCoordinationFixture(UnboundFixture):
    def env(self):
        return dict(super().env(), HERMES_HOME=str(self.account), TERMINAL_CWD=str(self.root))


class HermesStagedGoCoordinationDelegation(InstalledTools, unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def fixture(self, name):
        f = HermesCoordinationFixture(self.root / name, self.binary, stager=self.stager, harness='hermes')
        f.api.chmod(0o700)
        write(f.api, f.api.read_bytes().replace(
            b"elif op!='search':",
            b"elif op in ('agent-leave','wake-change'): data={}\nelif op!='search':"), 0o500)
        generation = f.install_root / 'generations' / ('selection-' + f.plan['id'])
        selection = json.loads((generation / 'selection.json').read_bytes())
        f.go_launchers = {}
        f.retained_launchers = {}
        for timeout in (2, 15):
            label = f'coordination:1:{timeout}'
            vector = selection['anchors'][label]['vector']
            f.retained_launchers[timeout] = selection['launchers'][label]
            f.go_launchers[timeout] = hook_stage.checked_go_launcher(
                str(f.stager), str(f.binary), 'coordination', vector)
        f.plugin_copy = f.account / 'cairn-coordination-plugin.py'
        shutil.copyfile(ROOT / 'integrations/hermes/coordination.py', f.plugin_copy)
        f.plugin_copy.chmod(0o400)
        return f

    def commands_for(self, f, side):
        launchers = f.go_launchers if side == 'go' else f.retained_launchers
        return {event: [*launchers[timeout], 'hook', '--config', str(f.coord_path)]
                for event, timeout in HERMES_EVENTS.items()}

    def load_plugin(self, f, config_payload):
        write(f.account / 'cairn-coordination.json', encoded(config_payload), 0o600)
        modules = {
            'hermes_constants': types.SimpleNamespace(get_hermes_home=lambda: f.account),
            'tools.terminal_tool': types.SimpleNamespace(
                get_session_cwd=lambda _: str(f.root),
                resolve_task_overrides=lambda _: {}),
        }
        spec = importlib.util.spec_from_file_location('hermes_staged_coord_test', f.plugin_copy)
        plugin = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, modules):
            spec.loader.exec_module(plugin)
        callbacks, middlewares, unload = {}, {}, []
        cli = types.SimpleNamespace(session_id=SESSION, _active_turn_id='',
                                    _agent_running=False, _admission_lock=threading.RLock())
        ctx = types.SimpleNamespace(
            _manager=types.SimpleNamespace(_cli_ref=cli),
            register_hook=lambda name, cb: callbacks.update({name: cb}),
            register_middleware=lambda name, cb: middlewares.update({name: cb}),
            on_unload=unload.append,
        )
        plugin.register(ctx)
        return plugin, ctx, cli, callbacks, middlewares, unload

    def test_installed_go_adapter_matches_retained_source_after_package_removal(self):
        f = self.fixture('hermes real adapter processes')
        for timeout, launcher_argv in f.go_launchers.items():
            launcher = Path(launcher_argv[0])
            raw = launcher.read_bytes()
            self.assertEqual(launcher.parent.name, hashlib.sha256(raw).hexdigest())
            self.assertEqual(launcher.stat().st_mode & 0o777, 0o500)
            vector = shlex.split(raw.decode().splitlines()[1])
            self.assertEqual(vector[:3], ['exec', str(f.binary), 'coordination'])
            self.assertEqual(vector[-2:], ['--', '$@'])
            for flag, expected in (('--receipt', str(f.receipt_path)),
                                   ('--receipt-sha256', f.receipt_sha),
                                   ('--role', 'coordination'),
                                   ('--hook-timeout', str(timeout)),
                                   ('--launch-root', str(f.launch_root))):
                self.assertEqual(vector.count(flag), 1)
                self.assertEqual(vector[vector.index(flag) + 1], expected)
        shutil.rmtree(f.package)
        attempt_id = '55555555-5555-4555-8555-555555555555'
        observations = []
        for side in ('source', 'go'):
            shutil.rmtree(f.coord['state_dir'], ignore_errors=True)
            for suffix in ('.calls', '.jsonl', '.agent'):
                f.api.with_suffix(suffix).unlink(missing_ok=True)
            wake_dir = f.root / 'wake'
            shutil.rmtree(wake_dir, ignore_errors=True)
            wake_dir.mkdir(mode=0o700, exist_ok=True)
            wake_path = wake_dir / 'wake.json'
            provider_file = wake_dir / f'{attempt_id}.provider.json'
            write(wake_path, encoded(dict(
                schema='cairn.wake-context/1',
                attempt_id=attempt_id,
                collection=f.coord['repo'],
                socket=f.coord['socket'],
                token_file=f.coord['token_file'],
                native_registration=True,
                provider_harness='hermes',
                provider_observation_file=str(provider_file),
            )), 0o600)
            config_payload = dict(
                unrelated='keep',
                script='/absent/unused-source.py',
                config='/absent/unused-config.json',
                commands=self.commands_for(f, side),
            )
            _, _, cli, callbacks, middlewares, unload = self.load_plugin(f, config_payload)
            with patch.dict(os.environ, dict(f.env(), CAIRN_WAKE_CONTEXT=str(wake_path))):
                try:
                    callbacks['pre_llm_call'](session_id=SESSION, turn_id=TURN, platform='cli',
                                              model='hermes-model', user_message='ordinary prompt')
                    self.assertEqual(cli._active_turn_id, TURN)
                    original_messages = [{'role': 'user', 'content': 'ordinary prompt'}]
                    req_in = {'messages': list(original_messages)}
                    rewritten = middlewares['llm_request'](req_in, session_id=SESSION)
                    self.assertEqual(req_in['messages'], original_messages)
                    injected = rewritten['request']['messages'][-1]['content']
                    self.assertIn('Cairn session agent-fixture has inbox agent/fixture.', injected)
                    callbacks['api_request_error'](
                        session_id=SESSION, status_code=429, reason='rate_limit',
                        error={'message': 'PRIVATE'}, request={'body': 'PRIVATE'})
                    rate_limit_obs = json.loads(provider_file.read_bytes())
                    self.assertNotIn('PRIVATE', json.dumps(rate_limit_obs))
                    self.assertEqual(rate_limit_obs['failure']['kind'], 'rate_limit')
                    callbacks['post_api_request'](session_id=SESSION, response={'text': 'PRIVATE'})
                    recovered_obs = json.loads(provider_file.read_bytes())
                    self.assertIsNone(recovered_obs['failure'])
                    callbacks['api_request_error'](session_id=SESSION, status_code=402, reason='billing')
                    billing_obs = json.loads(provider_file.read_bytes())
                    self.assertEqual(billing_obs['failure']['kind'], 'billing')
                    callbacks['post_llm_call'](session_id=SESSION, turn_id=TURN, platform='cli', model='hermes-model')
                    key = hashlib.sha256((f.coord['binding'] + '\0' + SESSION).encode()).hexdigest()
                    cli_state = json.loads((Path(f.coord['state_dir']) / (key + '.json')).read_bytes())
                finally:
                    pass
            with patch.dict(os.environ, f.env(), clear=False):
                os.environ.pop('CAIRN_WAKE_CONTEXT', None)
                try:
                    callbacks['pre_llm_call'](session_id='gateway-sess', turn_id='gw-turn', platform='slack',
                                              model='gw-model', user_message='gw prompt')
                    callbacks['post_llm_call'](session_id='gateway-sess', turn_id='gw-turn', platform='slack', model='gw-model')
                finally:
                    for close in unload:
                        close()
            wake_after = json.loads(wake_path.read_bytes())
            operations = [call['operation'] for call in f.api_calls()]
            self.assertEqual(operations, [
                'agent-register', 'wake-change', 'agent-heartbeat', 'agent-context', 'wake-change',
                'agent-register', 'session-inbox-claim', 'agent-leave', 'agent-leave'])
            requests = [(call['operation'], {k: v for k, v in json.loads(call['input']).items() if k != 'request_id'})
                        for call in f.api_calls()]
            observations.append((injected, rate_limit_obs, recovered_obs, billing_obs,
                                 cli_state['agent'], wake_after, operations, requests))
        self.assertEqual(observations[0], observations[1])
        self.assertFalse(Path(f.memory['state_dir']).exists())

    def test_go_delegated_turn_start_issues_working_retained_launcher_acknowledgement(self):
        f = self.fixture('hermes issued notice')
        delivery = dict(delivery_id=issued.DELIVERY, lease_id=issued.LEASE, state='leased',
                        event=dict(event_id=issued.EVENT, kind='notice', ref=issued.RESULT,
                                   **{'from': 'agent/requester'}))
        attempt = dict(attempt_id='', session={}, delivery=delivery,
                       native_turn_id=TURN, batch_index=1)
        f.replies({'session-inbox-claim': [issued.ok(dict(attempt=attempt), claim_identity=True)],
                   'event-renew': [issued.ok(delivery)],
                   'session-inbox-reconcile': [dict(ok=False, status='DELIVERY_ACTIVE'), issued.ok(dict(attempt=None))],
                   'ack': [issued.ok(dict(state='handled', delivery_id=issued.DELIVERY, attempts=1))]})
        shutil.rmtree(f.package)
        _, _, _, callbacks, middlewares, unload = self.load_plugin(
            f, dict(commands=self.commands_for(f, 'go')))
        with patch.dict(os.environ, f.env()):
            try:
                callbacks['pre_llm_call'](session_id=SESSION, turn_id=TURN, platform='cli',
                                          model='hermes-model', user_message='handle inbox notice')
                rewritten = middlewares['llm_request']({'messages': [{'role': 'user', 'content': 'hi'}]}, session_id=SESSION)
                self.assertIn('Cairn notice from agent/requester.', rewritten['request']['messages'][-1]['content'])
                paths = list((Path(f.coord['state_dir']) / 'inbox').glob('*.json'))
                self.assertEqual(len(paths), 1)
                context = json.loads(paths[0].read_bytes())
                self.assertEqual(context['event_id'], issued.EVENT)
                text = context['shell_commands']['acknowledgement']
                self.assertEqual(shlex.split(text)[0], f.retained_launchers[15][0])
                self.assertLessEqual(len(text.encode()), 512)
                self.assertNotIn(' -c ', text)
                settled = subprocess.run(
                    [str(f.binary), 'coordination', 'inbox', '--context', str(paths[0]), 'ack'],
                    stdin=subprocess.DEVNULL, cwd=f.root, env=f.env(), capture_output=True, timeout=20)
                self.assertEqual((settled.returncode, settled.stderr), (0, b''), settled.stdout)
                acknowledgements = [call for call in f.api_calls() if call['operation'] == 'ack']
                self.assertEqual(len(acknowledgements), 1)
                for identifier in (issued.AGENT, issued.EXECUTION, issued.DELIVERY, issued.LEASE):
                    self.assertIn(identifier, acknowledgements[0]['argv'])
            finally:
                for close in unload:
                    close()

    def test_in_process_custody_queue_and_cancellation_preserved_under_go_delegation(self):
        f = self.fixture('hermes custody and cancellation')
        shutil.rmtree(f.package)
        _, ctx, cli, callbacks, _, unload = self.load_plugin(
            f, dict(commands=self.commands_for(f, 'go')))
        queued_items = []
        interrupted_turns = []
        cli._agent_running = True
        cli._active_request_id = 'req-active'
        cli._pending_input = types.SimpleNamespace(mutex=threading.Lock(), queue=queued_items)
        cli.agent = types.SimpleNamespace(
            interrupt=lambda hard_cancel=False: interrupted_turns.append((cli._active_turn_id, hard_cancel)))

        def queue_msg(content, expected_session_id, role='user', source='cairn-wake',
                      on_consumed=None, request_id=None, delivery_id=None, turn_id=None):
            item = types.SimpleNamespace(
                content=content, expected_session_id=expected_session_id, role=role,
                source=source, on_consumed=on_consumed, request_id=request_id,
                delivery_id=delivery_id, turn_id=turn_id)
            queued_items.append(item)
            return item

        ctx.queue_message = queue_msg
        bridge_path = f'/tmp/cairn-hermes-{os.getpid()}.sock'

        def bridge_rpc(method, **params):
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(3)
                client.connect(bridge_path)
                client.sendall((json.dumps(dict(id=1, method=method, params=params)) + '\n').encode())
                return json.loads(client.makefile('r', encoding='utf-8').readline())

        with patch.dict(os.environ, f.env()):
            try:
                callbacks['pre_llm_call'](session_id=SESSION, turn_id=TURN, platform='cli',
                                          model='hermes-model', user_message='active turn')
                self.assertEqual(cli._active_turn_id, TURN)
                before_calls = len(f.api_calls())
                # Mismatched turn_id and child/subagent calls must be ignored by custody gate.
                callbacks['pre_llm_call'](session_id=SESSION, turn_id='intruder-turn', platform='cli')
                callbacks['post_llm_call'](session_id=SESSION, turn_id='intruder-turn', platform='cli')
                callbacks['pre_llm_call'](session_id='child', parent_session_id=SESSION, platform='cli')
                callbacks['pre_llm_call'](session_id=SESSION, turn_id=TURN, platform='subagent')
                self.assertEqual(len(f.api_calls()), before_calls)
                self.assertEqual(cli._active_turn_id, TURN)
                q_resp = bridge_rpc('session/queue_message', session_id=SESSION, expected_session_id=SESSION,
                                    text='queued wake', client_id='wake-1', request_id='req-1',
                                    delivery_id='deliv-1', turn_id='turn-queued')
                self.assertTrue(q_resp['result']['queued'])
                self.assertEqual(bridge_rpc('session/wake_status', client_id='wake-1')['result']['status'], 'pending')
                # Refusals on mismatched expected_request_id, mismatched expected_turn_id, or unspecified abort.
                self.assertEqual(bridge_rpc('session/abort', session_id=SESSION,
                                            expected_request_id='wrong-req')['error']['code'], -32001)
                self.assertEqual(bridge_rpc('session/abort', session_id=SESSION,
                                            expected_turn_id='wrong-turn')['error']['code'], -32003)
                self.assertEqual(bridge_rpc('session/abort', session_id=SESSION)['error']['code'], -32004)
                self.assertEqual(len(queued_items), 1)
                # Abort queued item before admission.
                deq = bridge_rpc('session/abort', session_id=SESSION, expected_request_id='req-1')['result']
                self.assertTrue(deq['aborted'])
                self.assertEqual(deq['turn_stop'], 'dequeued_before_admission')
                self.assertEqual(len(queued_items), 0)
                self.assertEqual(bridge_rpc('session/wake_status', client_id='wake-1')['result']['status'], 'refused')
                # Abort active bound turn with verified-PID tool termination and recycled-PID refusal.
                killed = []
                procs = [
                    dict(session_id='tool-owned', turn_id=TURN, status='running', pid=4001, host_start_time=100),
                    dict(session_id='tool-recycled', turn_id=TURN, status='running', pid=4002, host_start_time=200),
                    dict(session_id='tool-other-turn', turn_id='other-turn', status='running', pid=4003, host_start_time=100),
                ]
                registry_mod = types.SimpleNamespace(
                    process_registry=types.SimpleNamespace(
                        list_sessions=lambda session_key=None: list(procs),
                        kill_process=lambda proc_id, source=None: killed.append((proc_id, source)),
                        poll=lambda proc_id: dict(status='exited', exited=True),
                    ),
                    ProcessRegistry=types.SimpleNamespace(
                        _host_pid_is_ours=lambda pid, start: pid == 4001 and start == 100),
                )
                with patch.dict(sys.modules, {'tools.process_registry': registry_mod}):
                    aborted = bridge_rpc('session/abort', session_id=SESSION, expected_turn_id=TURN)['result']
                self.assertTrue(aborted['aborted'])
                self.assertEqual(aborted['turn_stop'], 'interrupted')
                self.assertEqual(interrupted_turns, [(TURN, True)])
                self.assertEqual(killed, [('tool-owned', 'cairn.abort')])
                self.assertEqual(aborted['tools'], [
                    dict(item_id='tool-owned', process_id='4001', stop_state='terminated'),
                    dict(item_id='tool-recycled', process_id='4002', stop_state='unverified_process_identity'),
                ])
                callbacks['post_llm_call'](session_id=SESSION, turn_id=TURN, platform='cli', model='hermes-model')
            finally:
                for close in unload:
                    close()

    def test_invalid_commands_and_tampered_staged_artifacts_refuse_without_source_fallback(self):
        # 1. Invalid or missing commands entries must never fall back to script/config.
        f_bad = self.fixture('hermes invalid commands')
        valid_source_script = str(Path(f_bad.receipt['snapshot_root']) / 'coordination.py')
        for bad_commands in (None, {}, {'TurnStart': []}, {'TurnStart': ['/bin/true', 'bad\0arg']}):
            with self.subTest(bad_commands=bad_commands):
                payload = dict(python=sys.executable, script=valid_source_script,
                               config=str(f_bad.coord_path), commands=bad_commands)
                _, _, _, callbacks, middlewares, unload = self.load_plugin(f_bad, payload)
                with patch.dict(os.environ, dict(f_bad.env(), CAIRN_WAKE_CONTEXT='wake.json')):
                    try:
                        callbacks['pre_llm_call'](session_id=SESSION, turn_id=TURN, platform='cli')
                        self.assertIsNone(middlewares['llm_request']({'messages': []}, session_id=SESSION))
                        callbacks['api_request_error'](session_id=SESSION, status_code=429, reason='rate_limit')
                        callbacks['post_llm_call'](session_id=SESSION, turn_id=TURN, platform='cli')
                    finally:
                        for close in unload:
                            close()
                self.assertEqual(f_bad.api_calls(), [])
                self.assertFalse(Path(f_bad.coord['state_dir']).exists())

        # 2. Tampered staged artifacts fail closed at compatibility_preflight without source fallback.
        for artifact in ('receipt', 'profile', 'image', 'closure', 'launcher'):
            with self.subTest(artifact=artifact):
                f = self.fixture('hermes tamper ' + artifact)
                selected = dict(
                    receipt=f.receipt_path,
                    profile=f.coord_path,
                    image=f.binary,
                    closure=Path(f.receipt['snapshot_root']) / 'coordination.py',
                    launcher=Path(f.retained_launchers[15][0]),
                )
                target = selected[artifact]
                mode = target.stat().st_mode & 0o777
                target.chmod(0o700)
                target.write_bytes(target.read_bytes() + b'\n')
                target.chmod(mode)
                shutil.rmtree(f.package)
                commands = self.commands_for(f, 'go')
                direct = subprocess.run(
                    commands['TurnStart'],
                    input=encoded(dict(hook_event_name='TurnStart', session_id=SESSION,
                                       cwd=str(f.root), host_pid=os.getpid())),
                    cwd=f.root, env=f.env(), capture_output=True, timeout=20)
                self.assertEqual(
                    (direct.returncode, direct.stdout, direct.stderr),
                    (1, b'', b'Cairn coordination diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n'))
                _, _, _, callbacks, middlewares, unload = self.load_plugin(
                    f, dict(python=sys.executable, script=valid_source_script,
                            config=str(f.coord_path), commands=commands))
                with patch.dict(os.environ, dict(f.env(), CAIRN_WAKE_CONTEXT='wake.json')):
                    try:
                        callbacks['pre_llm_call'](session_id=SESSION, turn_id=TURN, platform='cli')
                        self.assertIsNone(middlewares['llm_request']({'messages': []}, session_id=SESSION))
                        callbacks['post_llm_call'](session_id=SESSION, turn_id=TURN, platform='cli')
                    finally:
                        for close in unload:
                            close()
                self.assertEqual(f.api_calls(), [])
                self.assertFalse(Path(f.coord['state_dir']).exists())

        # 3. Malformed presence response shapes fail closed without injecting non-string context.
        for bad_stdout in ('[]', '{"hookSpecificOutput":[]}', '{"hookSpecificOutput":{"additionalContext":123}}'):
            with self.subTest(bad_stdout=bad_stdout):
                emitter = [sys.executable, '-c', f'import sys; sys.stdout.write({bad_stdout!r})']
                _, _, _, callbacks, middlewares, unload = self.load_plugin(
                    f_bad, dict(commands={event: emitter for event in HERMES_EVENTS}))
                try:
                    callbacks['pre_llm_call'](session_id=SESSION, turn_id=TURN, platform='cli')
                    self.assertIsNone(middlewares['llm_request']({'messages': []}, session_id=SESSION))
                finally:
                    for close in unload:
                        close()


if __name__ == '__main__':
    unittest.main()
