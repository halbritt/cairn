"""Selected OpenCode plugins and shell rows against real installed Go/Source."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest
import uuid

from test_f04_activation import ROOT, SESSION, encoded, write
from test_r02_compatibility_install import InstalledTools
import test_f04_issued as issued
import test_r04_hook_settings as hooks

pin = hooks.pin


class UnboundFixture(hooks.HookFixture):
    def configuration_plan(self, **kwargs):
        plan = super().configuration_plan(**kwargs)
        plan.update(profile_mode='unbound', enabled=False)
        for config, selected in [(self.memory, plan['memory_config']), (self.coord, plan['coordination_configs'][0])]:
            config['inbox_recall_binding'] = None
            # Preserve whitespace, a non-ASCII value and an opaque large int.
            raw = (json.dumps(dict(config, opaque=['café', 900719925474099312345]), indent=2) + '\n').encode()
            write(Path(selected['path']), raw)
            selected['sha256'] = hashlib.sha256(raw).hexdigest()
        return plan


class R04HostWiringTests(InstalledTools, unittest.TestCase):
    stage = hooks.R04HookSettingsTests.stage
    recall_api = hooks.R04HookSettingsTests.recall_api

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def fixture(self, name, harness='opencode', bound=False):
        cls = hooks.HookFixture if bound else UnboundFixture
        f = cls(self.root / name, self.binary, stager=self.stager, harness=harness)
        # The external API fixture must accept the plugin's real disposal call.
        f.api.chmod(0o700)
        write(f.api, f.api.read_bytes().replace(b"elif op!='search':",
                                              b"elif op=='agent-leave': data={}\nelif op!='search':"), 0o500)
        f.renderer = f.package / 'stage_lifecycle_hooks.py'
        shutil.copyfile(ROOT / 'scripts/stage_lifecycle_hooks.py', f.renderer)
        shutil.copyfile(ROOT / 'scripts/stage_lifecycle_compatibility.py', f.package / 'stage_lifecycle_compatibility.py')
        f.settings = f.account / 'settings.json'
        write(f.settings, b'{"unrelated":{"keep":true},"python":"unused","script":"unused","engine_config":"unused"}\n')
        f.hook_root = f.root / 'hook-generations'
        f.hook_root.mkdir(mode=0o700)
        f.hook_plan = dict(id=str(uuid.uuid4()), selection=f.selected, harness=harness, account_home=str(f.account),
                           settings=pin(f.settings), coordination_index=0, replace_commands=dict(memory=[], coordination=[]))
        if harness == 'opencode':
            f.coord_settings = f.account / 'cairn-coordination.json'
            write(f.coord_settings, b'{"unrelated":"coordination","python":"unused","script":"unused","config":"unused"}\n')
            plugins = {}
            for role, name in [('memory', 'lifecycle'), ('coordination', 'coordination')]:
                target = f.package / (name + '.ts')
                shutil.copyfile(ROOT / 'integrations/opencode' / target.name, target)
                target.chmod(0o600)
                plugins[role] = pin(target)
            f.hook_plan.update(coordination_settings=pin(f.coord_settings), plugins=plugins)
        return f

    def selected(self, f):
        result = self.stage(f)
        self.assertEqual((result.returncode, result.stderr), (0, b''), result.stdout)
        selected = json.loads(result.stdout)
        directory = Path(selected['selection']['path']).parent
        record = json.loads((directory / 'hook-selection.json').read_bytes())
        for path in directory.iterdir():
            if path.is_file(): self.assertEqual(path.stat().st_mode & 0o777, 0o400)
        return selected, directory, record

    def test_coordination_rows_select_receipt_checked_go_hook(self):
        for harness in ('codex', 'claude', 'opencode'):
            for bound in (False, True):
                with self.subTest(harness=harness, bound=bound):
                    f = self.fixture(harness + str(bound), harness=harness, bound=bound)
                    _, _, record = self.selected(f)
                    for row in record['rows']:
                        if row['role'] != 'coordination':
                            continue
                        self.assertEqual(row['implementation'], 'go')
                        self.assertEqual(row['retained_launcher'], [str(f.launcher('coordination', row['hook']['timeout']))])
                        self.assertEqual(row['argv'], [*row['checked_launcher'], 'hook', '--config', str(f.coord_path)])
                        launcher = Path(row['checked_launcher'][0])
                        raw = launcher.read_bytes()
                        self.assertEqual(launcher.parent.name, hashlib.sha256(raw).hexdigest())
                        self.assertEqual(launcher.stat().st_mode & 0o777, 0o500)
                        vector = shlex.split(raw.decode().splitlines()[1])
                        self.assertEqual(vector[:3], ['exec', str(f.binary), 'coordination'])
                        self.assertEqual(vector[-2:], ['--', '$@'])
                        for flag, expected in (('--receipt', str(f.receipt_path)),
                                               ('--receipt-sha256', f.receipt_sha),
                                               ('--role', 'coordination'),
                                               ('--hook-timeout', str(row['hook']['timeout'])),
                                               ('--launch-root', str(f.launch_root))):
                            self.assertEqual(vector.count(flag), 1)
                            self.assertEqual(vector[vector.index(flag) + 1], expected)

    def test_agy_stages_only_existing_coordination_events(self):
        f = self.fixture('agy hook settings', harness='agy')
        foreign = dict(type='command', command='another hook', timeout=6, custom='keep')
        data = dict(opaque=['日本語', 900719925474099312345], **{'cairn-coordination': {
            'PreInvocation': [foreign, dict(type='command', command='old coordination', timeout=1)],
            'Stop': [dict(type='command', command='old coordination', timeout=1)],
            'OtherEvent': [foreign]}})
        original = (json.dumps(data, indent=2) + '\n').encode()
        write(f.settings, original)
        f.hook_plan.update(settings=pin(f.settings), replace_commands=dict(memory=[], coordination=['old coordination']))
        selected, directory, record = self.selected(f)
        self.assertEqual(f.settings.read_bytes(), original)
        self.assertEqual((directory / 'previous-settings.json').read_bytes(), original)
        settings = json.loads(Path(selected['settings']['path']).read_bytes())
        self.assertEqual(settings['opaque'], data['opaque'])
        self.assertEqual(settings['cairn-coordination']['OtherEvent'], [foreign])
        self.assertEqual(settings['cairn-coordination']['PreInvocation'][0], foreign)
        self.assertEqual([(r['role'], r['event']) for r in record['rows']],
                         [('coordination', 'PreInvocation'), ('coordination', 'Stop')])
        self.assertEqual(record['unsupported_operations'], ['ordinary memory hook'])
        for row in record['rows']:
            self.assertEqual(row['implementation'], 'go')
            self.assertEqual(row['hook']['timeout'], 15)
            self.assertEqual(row['argv'], [*row['checked_launcher'], 'hook', '--config', str(f.coord_path), '--event', row['event']])
            self.assertEqual(settings['cairn-coordination'][row['event']][-1], row['hook'])
        repeated = self.stage(f)
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertEqual(json.loads(repeated.stdout)['selection'], selected['selection'])
        f.hook_plan.update(id=str(uuid.uuid4()), settings=selected['settings'])
        again, _, _ = self.selected(f)
        self.assertEqual(json.loads(Path(again['settings']['path']).read_bytes()), settings)
        self.assertEqual(f.api_calls(), [])

    def test_agy_installed_go_events_match_retained_source_after_package_removal(self):
        f = self.fixture('agy real processes', harness='agy')
        _, _, record = self.selected(f)
        shutil.rmtree(f.package)
        environment = dict(f.env(), ANTIGRAVITY_CLI_CONFIG_DIR=str(f.account))
        observations = []
        for side in ('source', 'go'):
            shutil.rmtree(f.coord['state_dir'], ignore_errors=True)
            for suffix in ('.calls', '.jsonl'):
                f.api.with_suffix(suffix).unlink(missing_ok=True)
            outputs = []
            for row, fully_idle in ((record['rows'][0], False), (record['rows'][1], False), (record['rows'][1], True)):
                # The selected event flag supplies the native Agy event name.
                raw = encoded(dict(conversationId=SESSION, workspacePaths=[str(f.root)], modelName='fixture-model',
                                   fullyIdle=fully_idle, host_pid=os.getpid(), prompt_id='agy-turn'))
                argv = row['argv'] if side == 'go' else [*row['retained_launcher'], 'hook',
                                                        '--config', str(f.coord_path), '--event', row['event']]
                result = subprocess.run(['/bin/sh', '-c', shlex.join(argv)], input=raw, cwd=f.root,
                                        env=environment, capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                outputs.append((result.returncode, result.stdout, result.stderr))
                reported = [json.loads(call['input'])['metadata']['state'] for call in f.api_calls()
                            if call['operation'] in ('agent-register', 'agent-context')]
                self.assertEqual(reported[-1], 'idle' if row['event'] == 'Stop' and fully_idle else 'busy')
            key = hashlib.sha256((f.coord['binding']+'\0'+SESSION).encode()).hexdigest()
            state = json.loads((Path(f.coord['state_dir'])/(key+'.json')).read_bytes())
            self.assertEqual(state['agent']['agent_id'], issued.AGENT)
            operations = [call['operation'] for call in f.api_calls()]
            self.assertEqual(operations.count('agent-register'), 1)
            requests = [(call['operation'], {key: value for key, value in json.loads(call['input']).items() if key != 'request_id'})
                        for call in f.api_calls() if call['operation'] in ('agent-register', 'agent-context')]
            observations.append((outputs, operations, requests, state['agent']))
        self.assertEqual(observations[0], observations[1])
        self.assertFalse(Path(f.memory['state_dir']).exists())

    def test_agy_invalid_native_events_keep_source_refusal_before_effects(self):
        f = self.fixture('agy invalid native events', harness='agy')
        _, _, record = self.selected(f)
        row = record['rows'][0]
        shutil.rmtree(f.package)
        invalid = [dict(session_id=SESSION, cwd=str(f.root)),
                   dict(conversationId=SESSION, workspacePaths=['relative']),
                   dict(conversationId=SESSION, workspacePaths=[str(f.root)], modelName=5)]
        for event in invalid:
            with self.subTest(event=event):
                results = []
                for argv in (row['argv'], [*row['retained_launcher'], 'hook', '--config', str(f.coord_path), '--event', 'PreInvocation']):
                    result = subprocess.run(argv, input=encoded(event), cwd=f.root,
                                            env=f.env(), capture_output=True, timeout=20)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, b'')
                    results.append((result.returncode, result.stdout, result.stderr))
                self.assertEqual(results[0], results[1])
                self.assertEqual(f.api_calls(), [])
                self.assertFalse(Path(f.coord['state_dir']).exists())

    def test_agy_corrupt_profile_refuses_before_effects_in_both_selected_paths(self):
        f = self.fixture('agy bad selected profile', harness='agy')
        _, _, record = self.selected(f)
        row = record['rows'][0]
        f.coord_path.chmod(0o600)
        f.coord_path.write_bytes(f.coord_path.read_bytes()+b'\n')
        f.coord_path.chmod(0o400)
        shutil.rmtree(f.package)
        results = []
        for argv in (row['argv'], [*row['retained_launcher'], 'hook', '--config', str(f.coord_path), '--event', 'PreInvocation']):
            result = subprocess.run(argv, input=encoded(dict(session_id=SESSION, cwd=str(f.root))),
                                    cwd=f.root, env=f.env(), capture_output=True, timeout=20)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b'')
            results.append((result.returncode, result.stdout, result.stderr))
        self.assertEqual(results[0], results[1])
        self.assertEqual(f.api_calls(), [])
        self.assertFalse(Path(f.coord['state_dir']).exists())

    def test_agy_bound_memory_replacement_and_ambiguous_hooks_refuse(self):
        for fault in ('bound', 'memory-replacement', 'duplicate', 'event-shape'):
            with self.subTest(fault=fault):
                f = self.fixture('agy refusal '+fault, harness='agy', bound=fault == 'bound')
                if fault == 'memory-replacement':
                    f.hook_plan['replace_commands']['memory'] = ['old memory']
                if fault in ('duplicate', 'event-shape'):
                    entries = [dict(command='old coordination'), dict(command='old coordination')] if fault == 'duplicate' else 'invalid'
                    write(f.settings, encoded({'cairn-coordination': {'PreInvocation': entries}}))
                    f.hook_plan['settings'] = pin(f.settings)
                    f.hook_plan['replace_commands']['coordination'] = ['old coordination']
                before = f.settings.read_bytes()
                result = self.stage(f)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(f.settings.read_bytes(), before)
                self.assertFalse((f.hook_root/('selection-'+f.hook_plan['id'])).exists())
                self.assertEqual(f.api_calls(), [])

    def test_opencode_uses_checked_source_cells_and_preserves_reverse_inputs(self):
        f = self.fixture('ordinary host')
        calls = self.recall_api(f)
        selected, directory, record = self.selected(f)
        self.assertEqual((directory / 'previous-cairn-lifecycle.json').read_bytes(), f.settings.read_bytes())
        self.assertEqual((directory / 'previous-cairn-coordination.json').read_bytes(), f.coord_settings.read_bytes())
        for selected_config, input_pin in [(f.memory_path, f.plan['memory_config']), (f.coord_path, f.plan['coordination_configs'][0])]:
            self.assertEqual(selected_config.read_bytes(), Path(input_pin['path']).read_bytes())
        implementations = {(r['role'], r['event']): r['implementation'] for r in record['rows']}
        self.assertTrue(all(impl == ('go' if role == 'coordination' or event in ('SessionStart', 'UserPromptSubmit') else 'python')
                            for (role, event), impl in implementations.items()))
        for row in record['rows']:
            if row['role'] == 'memory' and row['event'] in ('SessionStart', 'UserPromptSubmit'):
                self.assertEqual(row['argv'], row['checked_launcher'])
                self.assertNotEqual(row['checked_launcher'], row['retained_launcher'])
                self.assertEqual(row['reason'], 'receipt-checked ordinary Go recall')
        self.assertTrue(all(implementations['memory', event] == 'python'
                            for event in ('PreCompact', 'SessionEnd', 'PostToolUse', 'PostToolUseFailure')))
        # Arrange the documented staged layout only in a disposable account.
        home = f.root / 'installed home with spaces'
        (home / 'plugins').mkdir(parents=True, mode=0o700)
        for name, relative in selected['layout'].items():
            shutil.copyfile(directory / name, home / relative)
        self.assertEqual(json.loads((home / 'cairn-lifecycle.json').read_bytes())['unrelated'], {'keep': True})
        # No staged command may need the installer package or its source tree.
        shutil.rmtree(f.package)
        result = subprocess.run(['node', '--experimental-strip-types', str(ROOT / 'scripts/run_r04_opencode_hooks.mjs'),
                                 str(home), str(f.root)], env=f.env(), cwd=f.root, capture_output=True, timeout=45)
        self.assertEqual((result.returncode, result.stderr), (0, b''), result.stdout)
        self.assertEqual(json.loads(result.stdout), {'memory': True, 'coordination': True})
        self.assertIn('index', [operation for operation, _ in calls])
        self.assertTrue(all(authorization == 'Bearer r04-synthetic-token' for _, authorization in calls))
        states = [json.loads(path.read_bytes()) for path in Path(f.coord['state_dir']).glob('*.json')]
        self.assertEqual(len(states), 1)
        self.assertEqual(states[0]['agent']['agent_id'], '33333333-3333-4333-8333-333333333333')
        memory_state = json.loads((Path(f.memory['state_dir']) / 'ses_wiring.json').read_bytes())
        self.assertIn(memory_state['last_capture']['outcome'], ('courtesy', 'unchanged'))

    def test_shell_transport_keeps_checked_memory_and_selects_go_coordination(self):
        for harness in ('opencode', 'codex', 'claude'):
            with self.subTest(harness=harness):
                f = self.fixture(harness, harness=harness)
                if harness == 'opencode': self.recall_api(f)
                _, _, record = self.selected(f)
                memory = next(r for r in record['rows'] if r['role'] == 'memory' and r['event'] == 'SessionStart')
                coord = next(r for r in record['rows'] if r['role'] == 'coordination')
                self.assertEqual((memory['implementation'], coord['implementation']),
                                 ('go' if harness == 'opencode' else 'python', 'go'))
                session = 'ses_shell' if harness == 'opencode' else SESSION
                event = dict(hook_event_name='SessionStart', session_id=session, cwd=str(f.root), host_pid=os.getpid())
                shutil.rmtree(f.package)
                result = subprocess.run(['/bin/sh', '-c', memory['hook']['command']], input=encoded(event),
                                        cwd=f.root, env=f.env(), capture_output=True, timeout=20)
                self.assertEqual((result.returncode, result.stderr), (0, b''), result.stdout)
                self.assertIsInstance(json.loads(result.stdout), dict)
                result = subprocess.run(['/bin/sh', '-c', coord['hook']['command']], input=encoded(event),
                                        cwd=f.root, env=f.env(), capture_output=True, timeout=20)
                self.assertEqual((result.returncode, result.stderr), (0, b''), result.stdout)
                key = hashlib.sha256((f.coord['binding'] + '\0' + session).encode()).hexdigest()
                state = json.loads((Path(f.coord['state_dir']) / (key + '.json')).read_bytes())
                self.assertEqual(state['agent']['agent_id'], '33333333-3333-4333-8333-333333333333')

    def test_bound_and_provider_cells_never_silently_select_ordinary_go(self):
        for route in ('bound', 'provider'):
            with self.subTest(route=route):
                f = self.fixture(route, bound=route == 'bound')
                if route == 'provider':
                    f.memory['recall_mode'] = 'ambient'
                    f.publish_configuration()
                    f.hook_plan['selection'] = f.selected
                _, _, record = self.selected(f)
                rows = [r for r in record['rows'] if r['role'] == 'memory']
                self.assertTrue(all(r['implementation'] == 'python' for r in rows))
                self.assertTrue(any(r['reason'] == ('bound callback' if route == 'bound' else 'provider-selected recall') for r in rows))
                self.assertTrue(all(r['implementation'] == 'go' for r in record['rows'] if r['role'] == 'coordination'))

    def test_go_rows_refuse_changed_selections_before_api_or_state(self):
        for role in ('memory', 'coordination'):
            for artifact in ('receipt', 'profile', 'image', 'closure', 'launcher'):
                with self.subTest(role=role, artifact=artifact):
                    f = self.fixture(role + artifact)
                    calls = self.recall_api(f)
                    _, _, record = self.selected(f)
                    row = next(r for r in record['rows'] if r['role'] == role)
                    selected = dict(receipt=f.receipt_path,
                                    profile=f.memory_path if role == 'memory' else f.coord_path,
                                    image=f.binary,
                                    closure=Path(f.receipt['snapshot_root']) / 'coordination.py',
                                    launcher=Path(row['checked_launcher' if role == 'memory' else 'retained_launcher'][0]))
                    target = selected[artifact]
                    mode = target.stat().st_mode & 0o777
                    target.chmod(0o700)
                    target.write_bytes(target.read_bytes() + b'\n')
                    target.chmod(mode)
                    shutil.rmtree(f.package)
                    event = dict(hook_event_name='SessionStart', session_id='ses_refusal',
                                 cwd=str(f.root), host_pid=os.getpid())
                    result = subprocess.run(row['argv'], input=encoded(event), cwd=f.root,
                                            env=f.env(), capture_output=True, timeout=20)
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(result.stdout, b'')
                    self.assertEqual(calls, [])
                    self.assertEqual(f.api_calls(), [])
                    self.assertFalse(Path(f.memory['state_dir']).exists())
                    self.assertFalse(Path(f.coord['state_dir']).exists())

    def test_changed_published_go_launcher_refuses_new_settings_generation(self):
        f = self.fixture('changed Go launcher', harness='codex')
        _, _, record = self.selected(f)
        row = next(r for r in record['rows'] if r['role'] == 'coordination')
        launcher = Path(row['checked_launcher'][0])
        launcher.chmod(0o700)
        launcher.write_bytes(launcher.read_bytes() + b'# changed\n')
        launcher.chmod(0o500)
        f.hook_plan['id'] = str(uuid.uuid4())
        result = self.stage(f)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((f.hook_root / ('selection-' + f.hook_plan['id'])).exists())
        self.assertEqual(f.api_calls(), [])

    def test_go_hook_issues_working_retained_launcher_acknowledgement(self):
        f = self.fixture('issued notice', harness='codex', bound=True)
        _, _, record = self.selected(f)
        row = next(r for r in record['rows'] if r['role'] == 'coordination' and r['event'] == 'UserPromptSubmit')
        delivery = dict(delivery_id=issued.DELIVERY, lease_id=issued.LEASE, state='leased',
                        event=dict(event_id=issued.EVENT, kind='notice', ref=issued.RESULT,
                                   **{'from': 'agent/requester'}))
        attempt = dict(attempt_id='', session={}, delivery=delivery,
                       native_turn_id=issued.TURN, batch_index=1)
        f.replies({'session-inbox-claim': [issued.ok(dict(attempt=attempt), claim_identity=True)],
                   'event-renew': [issued.ok(delivery)],
                   'session-inbox-reconcile': [dict(ok=False, status='DELIVERY_ACTIVE')],
                   'ack': [issued.ok(dict(state='handled', delivery_id=issued.DELIVERY, attempts=1))]})
        shutil.rmtree(f.package)
        raw = encoded(dict(f.event, hook_event_name='UserPromptSubmit', prompt='An ordinary fixture prompt.',
                           host_pid=os.getpid()))
        result = subprocess.run(row['argv'], input=raw, cwd=f.root, env=f.env(), capture_output=True, timeout=20)
        self.assertEqual((result.returncode, result.stderr), (0, b''), result.stdout)
        paths = list((Path(f.coord['state_dir']) / 'inbox').glob('*.json'))
        self.assertEqual(len(paths), 1, result.stdout)
        context = json.loads(paths[0].read_bytes())
        text = context['shell_commands']['acknowledgement']
        self.assertEqual(shlex.split(text)[0], row['retained_launcher'][0])
        self.assertLessEqual(len(text.encode()), 512)
        self.assertNotIn(' -c ', text)
        settled = subprocess.run(['/bin/sh', '-c', text], cwd=f.root, env=f.env(), capture_output=True, timeout=20)
        self.assertEqual((settled.returncode, settled.stderr), (0, b''), settled.stdout)
        acknowledgements = [call for call in f.api_calls() if call['operation'] == 'ack']
        self.assertEqual(len(acknowledgements), 1)
        for identifier in (issued.AGENT, issued.EXECUTION, issued.DELIVERY, issued.LEASE):
            self.assertIn(identifier, acknowledgements[0]['argv'])

    def test_unbound_mode_refuses_a_bound_input_and_cannot_enable_binding(self):
        f = self.fixture('refusal')
        for change in ('bound', 'enabled'):
            with self.subTest(change=change):
                plan = copy.deepcopy(f.plan)
                plan['id'] = str(uuid.uuid4())
                if change == 'enabled': plan['enabled'] = True
                else:
                    config = json.loads(Path(plan['memory_config']['path']).read_bytes())
                    config['inbox_recall_binding'] = '/selected/old-binding.json'
                    path = f.root / 'bound-input.json'
                    write(path, encoded(config)); plan['memory_config'] = pin(path)
                result = f.installer_call('stage', '--root', str(f.install_root), body=plan)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((f.install_root / 'generations' / ('selection-' + plan['id'])).exists())

    def test_unsupported_harness_and_changed_plugin_or_selection_are_refused(self):
        for kind in ('hermes', 'plugin', 'selection'):
            with self.subTest(kind=kind):
                f = self.fixture(kind)
                plan = copy.deepcopy(f.hook_plan)
                if kind == 'hermes': plan['harness'] = 'hermes'
                elif kind == 'plugin': plan['plugins']['memory']['sha256'] = '0' * 64
                else: plan['selection']['sha256'] = '0' * 64
                result = self.stage(f, plan)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((f.hook_root / ('selection-' + plan['id'])).exists())

    def test_changed_unbound_profile_refuses_before_source_fallback(self):
        f = self.fixture('changed profile')
        _, _, record = self.selected(f)
        row = next(r for r in record['rows'] if r['role'] == 'memory' and r['event'] == 'PostToolUse')
        f.memory_path.chmod(0o600)
        write(f.memory_path, encoded(dict(f.memory, repo='changed-after-selection')), 0o400)
        event = dict(hook_event_name='PostToolUse', session_id='ses_changed', cwd=str(f.root),
                     tool_name='read', tool_input=dict(file_path=str(f.root / 'store.go')))
        result = subprocess.run(row['argv'], input=encoded(event), cwd=f.root, env=f.env(),
                                capture_output=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((Path(f.memory['state_dir']) / 'ses_changed.json').exists())
        self.assertEqual(f.api_calls(), [])


if __name__ == '__main__':
    unittest.main()
