"""Exact-request bridge contracts over real sockets; no provider or live registry.

The native composition case runs only with an explicitly supplied HERMES_ROOT,
inside a fresh process and temporary HERMES_HOME before native imports.
"""
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def snapshot():
    return dict(session_id='session', request_id='request', delivery_id='delivery',
                turn_id='session:turn', ownership_token='token', exclusive=True,
                revoked=False, cancelled=False, foreground_ended=False,
                turn_ended=False, tool_admission_closed=False, active_native_runs=1,
                active_tool_calls=0, executor_capture_gap=False)


class BridgeControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cairn-bridge-control-')
        self.addCleanup(self.tmp.cleanup)
        home = Path(self.tmp.name)
        (home/'cairn-coordination.json').write_text(json.dumps(dict(script='fixture', config='fixture')))
        self.records = []
        modules = {
            'hermes_constants': SimpleNamespace(get_hermes_home=lambda: home),
            'tools.terminal_tool': SimpleNamespace(get_session_cwd=lambda _: str(home), resolve_task_overrides=lambda _: {}),
            'tools.process_registry': SimpleNamespace(process_registry=SimpleNamespace(list_sessions=lambda: self.records)),
        }
        self.modules = patch.dict(sys.modules, modules)
        self.modules.start()
        self.addCleanup(self.modules.stop)
        spec = importlib.util.spec_from_file_location('bridge_control_fixture', ROOT/'integrations/hermes/coordination.py')
        self.plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.plugin)
        self.owned = snapshot()
        self.cancel_calls = []
        self.cli = SimpleNamespace(session_id='session', _active_turn_id='session:turn',
                                   _admission_lock=threading.RLock(), _agent_running=True)
        def read(expected_ownership_token=None):
            with self.cli._admission_lock:
                if expected_ownership_token not in (None, 'token'):
                    raise ValueError('OWNERSHIP_UNAVAILABLE')
                return dict(self.owned)
        def cancel(**params):
            with self.cli._admission_lock:
                self.cancel_calls.append(params)
                if self.owned['revoked']:
                    raise ValueError('OWNERSHIP_REVOKED')
                self.owned.update(cancelled=True, tool_admission_closed=True)
                return dict(self.owned)
        self.cli.request_ownership_snapshot = read
        self.cli.abort_owned_request = cancel
        self.hooks, self.middleware, self.unload = {}, {}, []
        self.ctx = SimpleNamespace(_manager=SimpleNamespace(_cli_ref=self.cli),
            register_hook=lambda name, cb: self.hooks.update({name: cb}),
            register_middleware=lambda name, cb: self.middleware.update({name: cb}),
            on_unload=self.unload.append)
        self.sent = []
        def invoke(*args, **kwargs):
            self.sent.append(json.loads(kwargs['input']))
            return SimpleNamespace(returncode=0, stdout='{}')
        self.run_patch = patch.object(self.plugin.subprocess, 'run', side_effect=invoke)
        self.run_patch.start()
        self.addCleanup(self.run_patch.stop)
        self.sock = f'/tmp/cairn-hermes-{os.getpid()}.sock'
        if os.path.exists(self.sock):
            self.fail('fixture refuses an existing bridge socket')
        self.plugin.register(self.ctx)
        self.addCleanup(lambda: [close() for close in self.unload])
        deadline = time.monotonic()+3
        while True:
            try:
                with socket.socket(socket.AF_UNIX) as probe:
                    probe.settimeout(.2)
                    probe.connect(self.sock)
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if time.monotonic() >= deadline:
                    self.fail('bridge did not begin listening')
                time.sleep(.005)
        self.params = {k: self.owned[k] for k in ('session_id', 'request_id', 'turn_id', 'ownership_token')}

    def rpc(self, method, params=None):
        with socket.socket(socket.AF_UNIX) as conn:
            conn.settimeout(2)
            conn.connect(self.sock)
            conn.sendall(json.dumps(dict(id='rpc', method=method, params=self.params if params is None else params)).encode()+b'\n')
            reply = b''
            while b'\n' not in reply:
                chunk = conn.recv(4096)
                if not chunk:
                    return None  # Post-effect failure must not look like definite refusal.
                reply += chunk
            return json.loads(reply)

    def test_exact_identity_and_legacy_token_requirement_precede_effects(self):
        for key in self.params:
            for replacement in (None, '', 'foreign'):
                params = dict(self.params, **{key: replacement})
                reply = self.rpc('session/request_cancel', params)
                self.assertIn('error', reply)
        legacy = dict(session_id='session', expected_request_id='request', expected_turn_id='session:turn')
        self.assertIn('error', self.rpc('session/abort', legacy))
        self.assertEqual(self.cancel_calls, [])
        self.assertFalse(self.owned['cancelled'])

    def test_cancel_ack_is_not_quiescence_or_cleanup(self):
        reply = self.rpc('session/request_cancel')['result']
        self.assertTrue(reply['ownership']['cancelled'])
        self.assertTrue(reply['ownership']['tool_admission_closed'])
        self.assertFalse(reply['ownership']['turn_ended'])
        self.assertEqual(reply['terminal_scan'], 'unknown_remaining')
        self.assertEqual(len(self.cancel_calls), 1)
        params = dict(self.params, tools=[dict(item_id='owned-tool', process_id='123')])
        self.assertEqual(self.rpc('session/request_cleanup', params)['error']['message'], 'CLEANUP_UNAVAILABLE')
        self.assertEqual(len(self.cancel_calls), 1)

    def install_native_inventory(self):
        self.inventory = dict(ownership=self.owned, tools=[], inventory_complete=True,
                              terminal_scan='unknown_remaining')
        self.cli.request_process_status = lambda **kwargs: dict(self.inventory, ownership=dict(self.owned))

    def test_inventory_uses_native_ledger_and_excludes_private_fields(self):
        self.install_native_inventory()
        self.inventory['tools'] = [dict(item_id='scope', process_id='123:456',
            native_turn_id='session:turn', stop_state='captured', command='SECRET')]
        result = self.rpc('session/request_status')['result']
        self.assertEqual(result['tools'], [dict(item_id='scope', process_id='123:456',
            native_turn_id='session:turn', stop_state='captured')])
        self.assertTrue(result['inventory_complete'])
        self.assertNotIn('SECRET', json.dumps(result))
        self.inventory['tools'][0]['native_turn_id'] = 'foreign'
        self.assertIn('error', self.rpc('session/request_status'))
        self.inventory['tools'] = [dict(item_id=str(i), process_id=str(i+1),
            native_turn_id='session:turn', stop_state='captured') for i in range(65)]
        self.assertIn('error', self.rpc('session/request_status'))

    def test_missing_native_inventory_never_means_empty_complete(self):
        result = self.rpc('session/request_status')['result']
        self.assertEqual(result['tools'], [])
        self.assertFalse(result['inventory_complete'])
        self.assertEqual(result['terminal_scan'], 'unknown_remaining')

    def test_exact_cleanup_then_fresh_native_verification(self):
        self.install_native_inventory()
        tool = dict(item_id='scope', process_id='123:456', native_turn_id='session:turn', stop_state='captured')
        self.inventory['tools'] = [tool]
        calls = []
        def cleanup(**params):
            calls.append(params)
            self.assertEqual(params['expected_ownership_token'], 'token')
            self.assertEqual(params['item_id'], 'scope')
            self.assertEqual(params['process_id'], '123:456')
            # A successful signal acknowledgement alone cannot report clear.
            return 'terminated'
        self.cli.cleanup_owned_process = cleanup
        self.owned.update(cancelled=True, tool_admission_closed=True, turn_ended=True,
                          foreground_ended=True, active_native_runs=0)
        params = dict(self.params, tools=[dict(item_id='scope', process_id='123:456')])
        result = self.rpc('session/request_cleanup', params)['result']
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['terminal_scan'], 'unknown_remaining')
        tool['stop_state'] = 'terminated'
        self.inventory['terminal_scan'] = 'clear'
        self.assertEqual(self.rpc('session/request_status')['result']['terminal_scan'], 'clear')
        self.owned['executor_capture_gap'] = True
        self.assertEqual(self.rpc('session/request_status')['result']['terminal_scan'], 'unknown_remaining')

    def test_partial_cleanup_refusal_is_uncertain(self):
        self.install_native_inventory()
        calls = []
        def cleanup(**params):
            calls.append(params)
            if len(calls) == 2:
                raise ValueError('PROCESS_OWNERSHIP_MISMATCH')
            return 'terminated'
        self.cli.cleanup_owned_process = cleanup
        params = dict(self.params, tools=[dict(item_id='first', process_id='1:2'),
                                         dict(item_id='second', process_id='3:4')])
        reply = self.rpc('session/request_cleanup', params)
        self.assertTrue(reply is None or reply.get('id') != 'rpc')
        self.assertEqual(len(calls), 2)

    def test_post_effect_malformed_response_is_uncertain(self):
        def malformed(**kwargs):
            self.owned.update(cancelled=True, tool_admission_closed=True)
            return {}
        self.cli.abort_owned_request = malformed
        reply = self.rpc('session/request_cancel')
        self.assertTrue(reply is None or reply.get('id') != 'rpc')
        self.assertTrue(self.owned['cancelled'])

    def test_revoked_request_refuses_cancel(self):
        self.owned.update(exclusive=False, revoked=True)
        self.assertEqual(self.rpc('session/request_cancel')['error']['message'], 'OWNERSHIP_REVOKED')
        self.assertFalse(self.owned['cancelled'])

    def test_turnstart_ipc_does_not_hold_locks_or_overwrite_later_turn(self):
        entered, release = threading.Event(), threading.Event()
        def blocked(*args, **kwargs):
            payload = json.loads(kwargs['input'])
            self.sent.append(payload)
            if payload['hook_event_name'] == 'TurnStart':
                entered.set()
                if not release.wait(3):
                    raise AssertionError('fixture failed to release IPC')
            return SimpleNamespace(returncode=0, stdout=json.dumps(dict(hookSpecificOutput=dict(additionalContext='stale'))))
        self.plugin.subprocess.run.side_effect = blocked
        thread = threading.Thread(target=lambda: self.hooks['pre_llm_call'](
            session_id='session', turn_id='session:turn', platform='cli'))
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertEqual(self.sent[0]['request_ownership'], self.owned)
            # This requires the native lock. Status also enumerates registry and
            # a second status call requires the bridge lock, while IPC is blocked.
            self.assertTrue(self.rpc('session/request_cancel')['result']['ownership']['cancelled'])
            self.assertEqual(self.rpc('session/status', dict(session_id='session'))['result']['status'], 'busy')
            self.hooks['post_llm_call'](session_id='session', turn_id='session:turn', platform='cli')
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertIsNone(self.middleware['llm_request'](dict(messages=[]), session_id='session'))
        self.assertEqual(self.cli._active_turn_id, 'session:turn')

    def test_foreign_hooks_never_invoke_or_mutate_admitted_turn(self):
        for turn in ('', 'foreign'):
            self.hooks['pre_llm_call'](session_id='session', turn_id=turn, platform='cli')
        self.hooks['pre_llm_call'](session_id='foreign-session', turn_id='foreign', platform='cli')
        self.assertEqual(self.sent, [])
        self.hooks['pre_llm_call'](session_id='session', turn_id='session:turn', platform='cli')
        self.hooks['post_llm_call'](session_id='session', turn_id='foreign', platform='cli')
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.rpc('session/status', dict(session_id='session'))['result']['status'], 'busy')

    def test_end_provider_and_unload_ipc_release_both_locks(self):
        def inspect_locks(*args, **kwargs):
            done = threading.Event()
            def acquire():
                with self.cli._admission_lock:
                    self.middleware['llm_request'](dict(messages=[]), session_id='session')
                    done.set()
            thread = threading.Thread(target=acquire)
            thread.start()
            self.assertTrue(done.wait(1), 'lifecycle IPC held a native or bridge lock')
            thread.join(1)
            self.sent.append(json.loads(kwargs['input']))
            return SimpleNamespace(returncode=0, stdout='{}')
        self.plugin.subprocess.run.side_effect = inspect_locks
        with patch.dict(os.environ, CAIRN_WAKE_CONTEXT='fixture'):
            self.hooks['pre_llm_call'](session_id='session', turn_id='session:turn', platform='cli')
            self.hooks['api_request_error'](session_id='session', status_code=429, reason='rate_limit')
            self.hooks['post_llm_call'](session_id='session', turn_id='session:turn', platform='cli')
            self.hooks['pre_llm_call'](session_id='session', turn_id='session:turn', platform='cli')
            self.unload[0]()
        self.assertEqual([p['hook_event_name'] for p in self.sent],
                         ['TurnStart', 'ProviderObservation', 'TurnEnd', 'TurnStart', 'SessionEnd'])
        self.assertFalse(os.path.exists(self.sock))

    def test_admission_callback_records_history_without_lifecycle_ipc(self):
        def queue_message(**kwargs):
            kwargs['on_consumed']('admitted', 'session')
            return SimpleNamespace(turn_id='session:generated')
        self.ctx.queue_message = queue_message
        params = dict(session_id='session', expected_session_id='session', client_id='client',
                      request_id='request', delivery_id='delivery', text='wake')
        self.assertTrue(self.rpc('session/queue_message', params)['result']['queued'])
        self.assertEqual(self.rpc('session/wake_status', dict(client_id='client'))['result']['status'], 'admitted')
        self.assertEqual(self.sent, [])



def native_probe():
    # This entry point is always a fresh subprocess. Every native import and
    # logging handler therefore belongs to this temporary home.
    with tempfile.TemporaryDirectory(prefix='cairn-native-control-') as native_home:
        os.environ['HERMES_HOME'] = native_home
        native_root = Path(os.environ['HERMES_ROOT']).resolve()
        for site in (Path.home()/'.hermes/hermes-agent/venv/lib').glob('python*/site-packages'):
            sys.path.append(str(site))
        sys.path.insert(0, str(native_root))
        try:
            from cli import HermesCLI
            from hermes_cli.plugins import QueuedMessage
            from run_agent import AIAgent
            from tools import process_registry as registry_module
            if not Path(registry_module.CHECKPOINT_PATH).resolve().is_relative_to(Path(native_home).resolve()):
                raise AssertionError('native registry escaped fixture home')
            cli = HermesCLI.__new__(HermesCLI)
            cli.session_id = 'session'
            cli._admission_lock = threading.RLock()
            cli._agent_running = False
            cli.agent = AIAgent.__new__(AIAgent)
            cli.agent._pending_steer_lock = threading.Lock()
            cli.agent._pending_steer = None
            interrupts = []
            cli.agent.hard_interrupt = lambda: interrupts.append('interrupted')
            fixture = BridgeControlTests()
            try:
                fixture.setUp()
                fixture.cli = cli
                fixture.ctx._manager._cli_ref = cli
                queued = []
                def enqueue(**kwargs):
                    item = QueuedMessage(text=kwargs.pop('content'), **kwargs)
                    queued.append(item)
                    cli._dequeue_pending_input(item)
                    return item
                fixture.ctx.queue_message = enqueue
                reply = fixture.rpc('session/queue_message', dict(session_id='session',
                    expected_session_id='session', client_id='client', request_id='request',
                    delivery_id='delivery', text='wake'))
                fixture.assertTrue(reply['result']['queued'])
                fixture.assertEqual(queued[0].status, 'admitted')
                owned = cli.request_ownership_snapshot()
                fixture.assertEqual(owned['turn_id'], queued[0].turn_id)
                fixture.assertEqual(owned['ownership_token'], queued[0].ownership_token)
                fixture.assertEqual(fixture.sent, [])
                fixture.params = {k: owned[k] for k in ('session_id', 'request_id', 'turn_id', 'ownership_token')}
                cli._agent_running = True
                fixture.hooks['pre_llm_call'](session_id='session', turn_id=owned['turn_id'], platform='cli')
                fixture.assertEqual(fixture.sent[-1]['request_ownership'], owned)
                result = fixture.rpc('session/request_cancel')['result']
                fixture.assertTrue(result['ownership']['cancelled'])
                fixture.assertFalse(result['ownership']['turn_ended'])
                fixture.assertEqual(interrupts, ['interrupted'])
                fixture.assertFalse(cli.agent.steer('future owner payload'))
                cli._reset_admission_state()
                cli._agent_running = False
                cli._dequeue_pending_input('future owner payload')
                newer_turn = cli._active_turn_id
                result = fixture.rpc('session/request_status')['result']
                fixture.assertEqual(result['ownership']['turn_id'], owned['turn_id'])
                fixture.assertTrue(result['ownership']['turn_ended'])
                fixture.assertEqual(result['terminal_scan'], 'unknown_remaining')
                fixture.assertEqual(cli._active_turn_id, newer_turn)
                fixture.assertEqual(interrupts, ['interrupted'])
                fixture.hooks['post_llm_call'](session_id='session', turn_id=owned['turn_id'], platform='cli')
                # A real same-turn owner join revokes native exclusivity; the
                # RPC must refuse without hard-interrupting that owner's input.
                cli._dequeue_pending_input(QueuedMessage(text='second wake', request_id='second', delivery_id='second-delivery'))
                cli._agent_running = True
                second = cli.request_ownership_snapshot()
                fixture.params = {k: second[k] for k in ('session_id', 'request_id', 'turn_id', 'ownership_token')}
                fixture.assertTrue(cli.agent.steer('owner joins now'))
                fixture.assertEqual(fixture.rpc('session/request_cancel')['error']['message'], 'OWNERSHIP_REVOKED')
                fixture.assertEqual(interrupts, ['interrupted'])
            finally:
                fixture.doCleanups()
        finally:
            logs = sys.modules.get('hermes_logging')
            if logs is not None:
                logs._reset_queued_handlers()
    print('native admission, token RPC, owner join and retained-turn composition passed')


@unittest.skipUnless(os.environ.get('HERMES_ROOT'), 'explicit native ownership checkout required')
class NativeBridgeComposition(unittest.TestCase):
    def test_native_cli_composition_in_fresh_isolated_process(self):
        result = subprocess.run([sys.executable, '-B', __file__, '--native-probe'],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('composition passed', result.stdout)


if __name__ == '__main__':
    if '--native-probe' in sys.argv:
        native_probe()
    else:
        unittest.main()
