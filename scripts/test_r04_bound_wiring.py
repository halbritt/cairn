"""Real staged bound-memory commands, retained Source and disposable API effects."""
import copy
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import uuid
import unittest

from test_f02_bound_memory import BoundMemoryCase
from test_f04_activation import ROOT, SESSION, TURN, encoded, write
import test_r04_hook_settings as hooks


class R04BoundWiringTests(BoundMemoryCase):
    def stage_rows(self, harness='codex', mode='agent_tools', enabled=True, remove_package=True):
        f = self.f
        for config in (f.memory, f.coord):
            config['harness'] = harness
        if mode is None:
            f.memory.pop('recall_mode', None)
        else:
            f.memory['recall_mode'] = mode
        f.publish_configuration(enabled=enabled)
        f.renderer = f.package / 'stage_lifecycle_hooks.py'
        for name in ('stage_lifecycle_hooks.py', 'stage_lifecycle_compatibility.py'):
            shutil.copyfile(ROOT / 'scripts' / name, f.package / name)
        f.settings = f.account / ('hooks.json' if harness == 'codex' else 'settings.json')
        original = encoded(dict(unrelated=['café', 900719925474099312345])) + b'\n'
        write(f.settings, original)
        f.hook_root = f.root / 'hook-generations'
        f.hook_root.mkdir(mode=0o700, exist_ok=True)
        f.hook_plan = dict(id=str(uuid.uuid4()), selection=f.selected, harness=harness,
                           account_home=str(f.account), settings=hooks.pin(f.settings),
                           coordination_index=0, replace_commands=dict(memory=[], coordination=[]))
        if harness == 'opencode':
            f.coord_settings = f.account / 'cairn-coordination.json'
            previous_coordination = encoded(dict(unrelated='preserve coordination settings')) + b'\n'
            write(f.coord_settings, previous_coordination)
            plugins = {}
            for role, name in (('memory', 'lifecycle'), ('coordination', 'coordination')):
                target = f.package / (name + '.ts')
                shutil.copyfile(ROOT / 'integrations/opencode' / target.name, target)
                target.chmod(0o600)
                plugins[role] = hooks.pin(target)
            f.hook_plan.update(coordination_settings=hooks.pin(f.coord_settings), plugins=plugins)
        result = hooks.R04HookSettingsTests.stage(self, f)
        self.assertEqual((result.returncode, result.stderr), (0, b''), result.stdout)
        selected = json.loads(result.stdout)
        directory = Path(selected['selection']['path']).parent
        metadata = json.loads((directory / 'hook-selection.json').read_bytes())
        self.assertEqual(f.settings.read_bytes(), original)
        previous = 'previous-cairn-lifecycle.json' if harness == 'opencode' else 'previous-settings.json'
        self.assertEqual((directory / previous).read_bytes(), original)
        if harness == 'opencode':
            self.assertEqual(f.coord_settings.read_bytes(), previous_coordination)
            self.assertEqual((directory / 'previous-cairn-coordination.json').read_bytes(), previous_coordination)
        settings = directory / 'cairn-lifecycle.json' if harness == 'opencode' else Path(selected['settings']['path'])
        self.assertEqual(json.loads(settings.read_bytes())['unrelated'],
                         json.loads(original)['unrelated'])
        for key in ('activation', 'native_trust', 'refresh'):
            self.assertEqual(metadata[key], 'not-performed')
        self.rows = {r['event']: r for r in metadata['rows'] if r['role'] == 'memory'}
        if remove_package:
            shutil.rmtree(f.package)
        return metadata

    def staged_process(self, side):
        row = self.rows[self.f.event['hook_event_name']]
        if side == 'go':
            self.assertEqual(row['implementation'], 'go')
            command = row['hook']['command']
        else:
            command = shlex.join(row['retained_launcher'])
        return subprocess.run(['/bin/sh', '-c', command], input=self.f.raw, cwd=self.f.root,
                              env=self.f.env(), capture_output=True, timeout=165)

    def source(self):
        return self.staged_process('source')

    def go(self):
        return self.staged_process('go')

    def test_supported_bound_rows_keep_exact_installed_selection(self):
        for harness, mode, expected in (
                ('codex', 'agent_tools', {'SessionStart': 13, 'UserPromptSubmit': 13, 'PreCompact': 150, 'Stop': 150}),
                ('codex', 'ambient', {'PreCompact': 150, 'Stop': 150}),
                ('codex', None, {'PreCompact': 150, 'Stop': 150}),
                ('claude', 'agent_tools', {'SessionStart': 13, 'UserPromptSubmit': 13,
                                         'PostToolUse': 13, 'PostToolUseFailure': 13,
                                         'PreCompact': 150, 'SessionEnd': 150}),
                ('claude', 'ambient', {'PreCompact': 150, 'SessionEnd': 150}),
                ('claude', None, {'PreCompact': 150, 'SessionEnd': 150}),
                ('codex', 'invalid', {'PreCompact': 150, 'Stop': 150}),
                ('claude', 'invalid', {'PreCompact': 150, 'SessionEnd': 150})):
            with self.subTest(harness=harness, mode=mode):
                self.stage_rows(harness, mode, remove_package=False)
                anchors = json.loads((Path(self.f.selected['path']).parent / 'selection.json').read_bytes())['anchors']
                actual = {name: r['hook']['timeout'] for name, r in self.rows.items() if r['implementation'] == 'go'}
                self.assertEqual(actual, expected)
                for name, row in self.rows.items():
                    self.assertEqual(row['retained_launcher'], [str(self.f.launcher('memory', row['hook']['timeout']))])
                    self.assertEqual(row['argv'], row['checked_launcher'])
                    if name not in expected:
                        self.assertEqual(row['argv'], row['retained_launcher'])
                        continue
                    self.assertNotEqual(row['argv'], row['retained_launcher'])
                    raw = Path(row['argv'][0]).read_text()
                    vector = shlex.split(raw.splitlines()[1])
                    source_vector = anchors['memory:0:' + str(expected[name])]['vector']
                    self.assertEqual(vector[:3], ['exec', str(self.f.binary), 'memory'])
                    self.assertEqual(vector[-2:], ['--', '$@'])
                    for flag, value in (('--receipt', str(self.f.receipt_path)),
                                        ('--receipt-sha256', self.f.receipt_sha), ('--role', 'memory'),
                                        ('--config', str(self.f.memory_path)),
                                        ('--entrypoint', source_vector[source_vector.index('--entrypoint') + 1]),
                                        ('--hook-timeout', str(expected[name])),
                                        ('--launch-root', str(self.f.launch_root)), ('--shell', '/bin/sh')):
                        self.assertEqual(vector.count(flag), 1)
                        self.assertEqual(vector[vector.index(flag) + 1], value)
                    kind = ('recall' if name in ('SessionStart', 'UserPromptSubmit') else
                            'tool observation' if name in ('PostToolUse', 'PostToolUseFailure') else 'capture')
                    self.assertEqual(row['reason'], 'receipt-checked bound Go ' + kind)
                self.assertEqual(self.calls, [])
                self.assertEqual(self.state(), {})

    def recall_pairs(self, enabled):
        self.stage_rows(enabled=enabled)
        if enabled:
            self.selected = [dict(record=dict(record_id='55555555-5555-4555-8555-555555555555', version=1,
                                               body='Required current staged memory fixture.'),
                                  mandatory=True, reason='required', evidence=[], authority=[])]
        for name in ('SessionStart', 'UserPromptSubmit'):
            with self.subTest(event=name, enabled=enabled):
                self.reset_state({})
                self.event(name, prompt='Inspect the current staged memory source.', turn_id=TURN)
                result = self.pair()
                self.assertEqual((result.returncode, result.stderr), (0, b''))
                if enabled:
                    self.assertIn(b'Required current staged memory fixture.', result.stdout)
                ledger = self.state()[SESSION + '.memory-budget.json']
                self.assertNotIn('inbox_activation', ledger)
                if name == 'UserPromptSubmit':
                    self.assertTrue(ledger['turns'][TURN]['granted'])
                self.assertIn('index', [row[0] for row in self.calls])

    def test_codex_bound_start_and_prompt_match_source_after_package_removal(self):
        self.recall_pairs(True)

    def test_disabled_bound_codex_recall_keeps_source_activation_decision(self):
        self.recall_pairs(False)

    def test_bound_codex_wake_reuses_the_shared_activation_decision(self):
        self.stage_rows()
        self.event('UserPromptSubmit', prompt=self.f.wake(), turn_id=TURN)
        self.assertEqual(self.pair().stdout, b'{}\n')
        ledger = self.state()[SESSION + '.memory-budget.json']
        self.assertEqual(list(ledger['inbox_activation'].values()), [True])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.pair().stdout, b'{}\n')
        self.assertEqual(self.state()[SESSION + '.memory-budget.json'], ledger)
        self.assertEqual(self.calls, [])

    def prepare_provider(self):
        self.selection = dict(checkpoint=None, workstream=None, memories=[dict(record_id=None,
            kind='decision', title='Lifecycle staging policy', body='Retain the checked launcher for lifecycle staging.')])
        write(self.root / 'selector', b'''#!/usr/bin/python3
import json,os,pathlib,sys
root=pathlib.Path(__file__).parent
with (root/'selector.calls').open('a') as log:
 log.write(json.dumps(dict(argv=sys.argv[1:],body=json.load(sys.stdin),cwd=os.getcwd(),entries=os.listdir(),pid=os.getpid()))+'\\n')
print(json.dumps(dict(structured_output=json.loads((root/'selection.json').read_text()))))
''', 0o700)
        self.f.memory.update(claude=str(self.root / 'selector'), model='r04-synthetic-model', capture_observations=True)
        self.provider_observations = {}

    def before_run(self, side):
        if hasattr(self, 'selection'):
            self.side, self.writes = side, []
            (self.root / 'selector.calls').unlink(missing_ok=True)
            write(self.root / 'selection.json', encoded(self.selection))

    def create_reply(self, body):
        identity = str(uuid.uuid5(uuid.NAMESPACE_URL, body['request_id']))
        self.writes.append(dict(record_id=identity, draft=copy.deepcopy(body['draft'])))
        return dict(record_id=identity), None

    def after_run(self):
        if not hasattr(self, 'selection'):
            return
        rows = [json.loads(line) for line in (self.root / 'selector.calls').read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        for row in rows:
            self.assertFalse(Path('/proc', str(row.pop('pid'))).exists())
            self.assertEqual(row.pop('entries'), [])
            directory = Path(row.pop('cwd'))
            self.assertTrue(directory.name.startswith('cairn-selection-'))
            self.assertFalse(directory.exists())
        self.provider_observations[self.side] = rows

    def capture_pairs(self, harness):
        self.prepare_provider()
        self.stage_rows(harness)
        dialogue = [dict(role=role, text=text) for _ in range(3) for role, text in (
            ('user', 'Preserve the checked lifecycle staging policy for this project.'),
            ('assistant', 'Retain the selected launcher and document the exact rollback inputs.'))]
        if harness == 'codex':
            transcript = self.root / 'rollout.jsonl'
            write(transcript, b''.join(encoded(dict(type='response_item', payload=dict(type='message',
                role=row['role'], content=[dict(type='input_text' if row['role'] == 'user' else 'output_text',
                                               text=row['text'])]))) + b'\n' for row in dialogue))
            fields = dict(transcript_path=str(transcript))
        else:
            fields = dict(messages=dialogue)
        for event in ('PreCompact', 'Stop' if harness == 'codex' else 'SessionEnd'):
            with self.subTest(harness=harness, event=event):
                self.reset_state({})
                self.event(event, **fields)
                if harness == 'opencode':
                    self.f.event['session_id'] = 'ses_R04CaptureA1'
                    self.f.raw = encoded(self.f.event)
                result = self.pair()
                expected = encoded(dict(systemMessage='Cairn selected memories saved: ' +
                                        ', '.join(row['record_id'] for row in self.writes))) + b'\n'
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, expected, b''))
                self.assertEqual(self.provider_observations['go'], self.provider_observations['source'])
                self.assertEqual([row['draft']['kind'] for row in self.writes], ['decision'])
                session = self.f.event['session_id']
                state = self.state()[session + '.json']
                self.assertEqual(state['last_capture']['outcome'], 'saved')
                self.assertEqual(state['last_capture']['records'], [row['record_id'] for row in self.writes])
                self.assertNotIn(session + '.memory-budget.json', self.state())
                self.assertEqual(self.calls[-1][0], 'capture-observation')

    def test_codex_bound_capture_rows_save_selected_note_after_package_removal(self):
        self.capture_pairs('codex')

    def test_claude_bound_capture_rows_save_selected_note_after_package_removal(self):
        self.capture_pairs('claude')

    def refuse_changed_code(self, harness):
        self.stage_rows(harness)
        for event, row in self.rows.items():
            if row['implementation'] != 'go':
                continue
            self.event(event, turn_id=TURN)
            for artifact, path in (('receipt', self.f.receipt_path), ('image', self.f.binary),
                                   ('closure', Path(self.f.receipt['snapshot_root']) / 'memory.py'),
                                   ('launcher', Path(row['argv'][0]))):
                with self.subTest(harness=harness, event=event, artifact=artifact):
                    old = path.read_bytes()
                    self.overwrite(path, old + b'\n')
                    try:
                        result = self.go()
                        self.assertEqual((result.returncode, result.stdout, result.stderr),
                            (1, b'', b'Cairn lifecycle diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n'))
                        self.assertEqual(self.calls, [])
                        self.assertEqual(self.state(), {})
                    finally:
                        self.overwrite(path, old)

    def test_codex_bound_rows_refuse_changed_code_before_effects(self):
        self.refuse_changed_code('codex')

    def test_claude_bound_rows_refuse_changed_code_before_effects(self):
        self.refuse_changed_code('claude')


if __name__ == '__main__':
    unittest.main()
