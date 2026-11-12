"""Real H13 Source and checked Go Claude lexical ambient recall."""
import copy
import json
from pathlib import Path
import shutil
import unittest

import test_f02_checked_ordinary as ordinary
import test_f02_hermes_recall as hermes
import test_f02_hermes_ambient as helpers
import test_f02_opencode_ambient as ambient
import test_f02_ordinary_host as host


SESSION = '11111111-1111-4111-8111-111111111111'


class ClaudeAmbientTests(unittest.TestCase):
    setUpClass = classmethod(ordinary.CheckedOrdinaryTests.setUpClass.__func__)
    tearDownClass = classmethod(ordinary.CheckedOrdinaryTests.tearDownClass.__func__)
    close_server = hermes.HermesRecallTests.close_server
    go_vector = ordinary.CheckedOrdinaryTests.go_vector
    selected_vector = ordinary.CheckedOrdinaryTests.selected_vector
    go_launcher = ordinary.CheckedOrdinaryTests.go_launcher
    overwrite = ordinary.CheckedOrdinaryTests.overwrite
    repin = ordinary.CheckedOrdinaryTests.repin
    configure = hermes.HermesRecallTests.configure
    state_key = ambient.OpenCodeAmbientTests.state_key
    state_files = hermes.HermesRecallTests.state_files
    seed = hermes.HermesRecallTests.seed
    context = hermes.HermesRecallTests.context
    optional_note = host.OrdinaryHostTests.optional_note
    check_pull_intents = host.OrdinaryHostTests.check_pull_intents
    selection = ambient.OpenCodeAmbientTests.selection
    files = ambient.OpenCodeAmbientTests.files
    state = ambient.OpenCodeAmbientTests.state
    required = ambient.OpenCodeAmbientTests.required
    same_keys = ambient.OpenCodeAmbientTests.same_keys
    measurement = ambient.OpenCodeAmbientTests.measurement
    normalize = ambient.OpenCodeAmbientTests.normalize
    normalized_stream = helpers.HermesAmbientTests.normalized_stream
    refused = hermes.HermesRecallTests.refused

    def event(self, name, **changes):
        self.f.event = dict(hook_event_name=name, session_id=SESSION,
                            cwd=str(self.f.root), prompt='Inspect current retention policy.',
                            prompt_id='native-fixture-prompt')
        self.f.event.update(changes)
        self.f.raw = ordinary.encoded(self.f.event)

    def setUp(self):
        hermes.HermesRecallTests.setUp(self)
        for config, path in ((self.f.memory, self.f.memory_path), (self.f.coord, self.f.coord_path)):
            config['harness'] = 'claude'
            self.overwrite(path, ordinary.encoded(config))
        self.trace = self.f.memory_path.parent/'invocation-trace'/'trace.jsonl'
        self.trace.parent.mkdir(mode=0o700, exist_ok=True)
        self.selection()

    def launch(self, side, invoke):
        raw = self.f.raw
        profiles = {str(path): dict(raw_hex=path.read_bytes().hex(),
                    sha256=ordinary.sha(path.read_bytes()))
                    for path in (self.f.memory_path, self.f.coord_path)}
        result = invoke()
        command = list(result.args)
        artifact = Path(command[0]).read_bytes()
        print('CLAUDE_AMBIENT_PROCESS='+json.dumps(dict(
            side=side, event=self.f.event['hook_event_name'], input=raw.decode(),
            input_sha256=ordinary.sha(raw), command=command, cwd=str(self.f.root),
            launcher=artifact.decode() if len(command) == 1 else None,
            executable_sha256=ordinary.sha(artifact),
            executable_mode=Path(command[0]).stat().st_mode & 0o777,
            receipt_sha256=self.f.receipt_sha, receipt=self.f.receipt,
            config=self.f.memory, profiles=profiles, exit=result.returncode,
            stdout=result.stdout.decode(), stderr=result.stderr.decode(),
            files=self.files(), calls=copy.deepcopy(list(self.calls)),
            locks=list(self.report_locks),
            trace=[json.loads(line) for line in self.trace.read_bytes().splitlines()]
                  if self.trace.exists() else []), ensure_ascii=True), flush=True)
        return result

    def source(self):
        return self.launch('source', lambda: ambient.OpenCodeAmbientTests.source(self))

    def go(self):
        return self.launch('go', lambda: ordinary.CheckedOrdinaryTests.go(self))

    def validate_keysets(self, rows):
        ambient.OpenCodeAmbientTests.validate_keysets(self, rows)
        if rows[0]['stdout'] or rows[1]['stdout']:
            self.same_keys(json.loads(rows[0]['stdout']), json.loads(rows[1]['stdout']), ('stdout',))

    def pair(self):
        result = ambient.OpenCodeAmbientTests.pair(self)
        for row in self.last_raw_pair:
            reports = sum(op == 'recall-observation' for op, _, _ in row['calls'])
            self.assertEqual(row['locks'], [True]*reports)
            self.assertEqual(reports, 1)
            for op, body, headers in row['calls']:
                self.assertEqual(headers['Cairn-Protocol'], '2')
                self.assertEqual(headers['Content-Type'], 'application/json')
                if op == 'index':
                    self.assertEqual(body['scope']['task_id'], self.f.memory.get('task_id', 'claude/'+self.f.event['session_id']))
                    self.assertEqual(body['scope']['run_id'], self.f.memory.get('run_id', self.f.event['session_id']))
        return result

    def test_event_mode_fallback_cells_with_selected_and_optional_context(self):
        self.required('Required retention policy. 日本語 🛡.')
        self.optional_note('Current retention policy implementation. 日本語 😀\nKeep complete recall context.')
        for mode in (False, True):
            for fallback in (False, True):
                for name in ('SessionStart', 'UserPromptSubmit'):
                    with self.subTest(mode=mode, fallback=fallback, event=name):
                        shutil.rmtree(self.f.memory['state_dir'], ignore_errors=True)
                        self.selection(mode=mode, fallback=fallback)
                        self.event(name)
                        self.pair()
                        for row in self.last_raw_pair:
                            self.assertEqual(row['exit'], 0)
                            text = json.loads(row['stdout'])['hookSpecificOutput']['additionalContext']
                            self.assertTrue(text)
                            context = json.loads(text[text.index('{"selected"'):])
                            self.assertTrue(context['selected'])
                            self.assertEqual(context['selected'][0]['record']['body'], self.selected[0]['record']['body'])
                            self.assertTrue(context['selected'][0]['mandatory'])
                            self.assertEqual(context['expanded']['selection']['record']['body'], self.optional[0]['body'])
                            self.assertTrue(context['expanded']['selection']['record']['body'])
                            self.assertEqual([op for op, _, _ in row['calls']], ['version', 'index', 'expand', 'recall-observation'])
                            request = next(body for op, body, _ in row['calls'] if op == 'index')
                            self.assertFalse(request.get('semantic', False))
                            state = json.loads(row['files'][self.state_key()+'.json']['raw'])
                            self.assertEqual(state['seen'], {self.optional[0]['entry']['record_id']: 3})

    test_no_match_and_taskless_defer = helpers.HermesAmbientTests.test_no_match_and_taskless_defer
    test_optional_seen_and_start_reset = helpers.HermesAmbientTests.test_optional_seen_and_start_reset
    test_retained_seen_and_binding_reset = hermes.HermesRecallTests.test_seen_retention_start_reset_and_binding_reset

    def test_uuid_state_names_and_literal_scope_overrides(self):
        self.required('Required retention policy.')
        self.optional_note('Current retention policy implementation.')
        for session in (SESSION, '22222222-2222-4222-8222-222222222222'):
            with self.subTest(session=session):
                shutil.rmtree(self.f.memory['state_dir'], ignore_errors=True)
                self.event('UserPromptSubmit', session_id=session)
                self.assertEqual(self.pair().returncode, 0)
                self.assertEqual(set(self.files()), {session+'.json', session+'.lock'})
        self.configure(task_id='literal-claude-task', run_id='literal-claude-run')
        self.event('SessionStart')
        self.assertEqual(self.pair().returncode, 0)

    def test_provider_malformed_and_unsupported_inputs_refuse_before_effects(self):
        for fallback in (True, 'provider-selected', None, 0, 1, '', [], {}):
            with self.subTest(fallback=fallback):
                self.configure(semantic_fallback=fallback)
                self.refused()
        self.selection()
        for mode in ('unsupported', None, False):
            with self.subTest(mode=mode):
                self.configure(recall_mode=mode)
                self.refused()
        self.selection()
        for name in ('PreToolUse', 'PostToolUse', 'PostToolUseFailure', 'PreCompact', 'SessionEnd', 'Stop', 'Unknown'):
            with self.subTest(event=name):
                self.event(name)
                self.refused()
        self.event('SessionStart')
        for path in (self.f.memory_path, self.f.coord_path):
            with self.subTest(bound_profile=path.name):
                old = path.read_bytes()
                config = json.loads(old)
                config['inbox_recall_binding'] = str(self.f.binding)
                self.overwrite(path, ordinary.encoded(config))
                self.repin()
                try:
                    self.refused()
                finally:
                    self.overwrite(path, old)
                    self.repin()
        self.seed(dict(opaque=['preserved exactly', 900719925474099312345]))
        self.selection_overrides = {'hook-timeout': '150'}
        self.refused()

    test_tampered_receipt_config_image_closure_launcher_refuse = ambient.OpenCodeAmbientTests.test_tampered_receipt_config_image_closure_launcher_refuse

    def test_non_uuid_identity_is_a_separate_safe_refusal(self):
        for label in ('not-shaped', 'Hermes session/run:日本語-01', 'h'*129):
            with self.subTest(label=label):
                self.event('UserPromptSubmit', session_id=label)
                before = self.state_files()
                source = self.source()
                self.assertEqual((source.returncode, source.stdout), (1, b''))
                self.assertEqual(self.calls, [])
                self.calls.clear()
                go = self.go()
                self.assertEqual((go.returncode, go.stdout), (1, b''))
                self.assertTrue(go.stderr.startswith(b'Cairn lifecycle diagnostic: phase=handle '))
                self.assertIn(b'use native tools or an explicit handoff.', go.stderr)
                self.assertEqual(self.calls, [])
                self.assertEqual(self.state_files(), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
