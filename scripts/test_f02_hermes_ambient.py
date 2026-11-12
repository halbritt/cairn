"""Actual H13 checked Hermes lexical ambient recall, using existing fixture engines."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import unittest

import test_f02_checked_ordinary as ordinary
import test_f02_hermes_recall as hermes
import test_f02_opencode_ambient as ambient
import test_f02_ordinary_host as host


class HermesAmbientTests(unittest.TestCase):
    setUpClass = classmethod(ordinary.CheckedOrdinaryTests.setUpClass.__func__)
    tearDownClass = classmethod(ordinary.CheckedOrdinaryTests.tearDownClass.__func__)
    close_server = hermes.HermesRecallTests.close_server
    go_vector = ordinary.CheckedOrdinaryTests.go_vector
    selected_vector = ordinary.CheckedOrdinaryTests.selected_vector
    go_launcher = ordinary.CheckedOrdinaryTests.go_launcher
    overwrite = ordinary.CheckedOrdinaryTests.overwrite
    repin = ordinary.CheckedOrdinaryTests.repin
    configure = hermes.HermesRecallTests.configure
    state_key = hermes.HermesRecallTests.state_key
    state_files = hermes.HermesRecallTests.state_files
    seed = hermes.HermesRecallTests.seed
    event = hermes.HermesRecallTests.event
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
    refused = hermes.HermesRecallTests.refused

    def setUp(self):
        hermes.HermesRecallTests.setUp(self)
        self.trace = self.f.memory_path.parent/'invocation-trace'/'trace.jsonl'
        self.trace.parent.mkdir(mode=0o700, exist_ok=True)
        self.selection()

    def launch(self, side, invoke):
        raw = self.f.raw
        result = invoke()
        command = list(result.args)
        artifact = Path(command[0]).read_bytes()
        print('HERMES_AMBIENT_PROCESS='+json.dumps(dict(
            side=side, event=self.f.event['hook_event_name'], input=raw.decode(),
            input_sha256=ordinary.sha(raw), command=command, cwd=str(self.f.root),
            launcher=artifact.decode() if len(command) == 1 else None,
            executable_sha256=ordinary.sha(artifact),
            executable_mode=Path(command[0]).stat().st_mode & 0o777,
            receipt_sha256=self.f.receipt_sha, receipt=self.f.receipt,
            config=self.f.memory, exit=result.returncode,
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

    def normalized_stream(self, raw):
        if not raw:
            return host.OrdinaryHostTests.normalized_stream(self, raw)
        body = json.loads(raw)
        self.assertEqual(raw, json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode()+b'\n')
        status = body.get('cairn_status', {}).get('last_recall')
        if status is not None:
            for key in ('at', 'elapsed_seconds', 'duration_ms'):
                self.measurement(status[key])
                status[key] = 0
        return host.OrdinaryHostTests.normalized_stream(
            self, json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode()+b'\n')

    def pair(self):
        result = ambient.OpenCodeAmbientTests.pair(self)
        for row in self.last_raw_pair:
            reports = sum(op == 'recall-observation' for op, _, _ in row['calls'])
            self.assertEqual(row['locks'], [True]*reports)
            self.assertEqual(reports, 1)
            for op, body, _ in row['calls']:
                if op != 'index':
                    continue
                self.assertEqual(body['scope']['task_id'], 'hermes/'+self.f.event['session_id'])
                self.assertEqual(body['scope']['run_id'], self.f.event['session_id'])
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
                        result = self.pair()
                        context = self.context(result)
                        self.assertEqual(result.returncode, 0)
                        self.assertEqual(context['selected'][0]['record']['body'], self.selected[0]['record']['body'])
                        self.assertTrue(context['selected'][0]['mandatory'])
                        self.assertEqual(context['expanded']['selection']['record']['body'], self.optional[0]['body'])
                        self.assertEqual([op for op, _, _ in self.calls], ['version', 'index', 'expand', 'recall-observation'])
                        request = next(body for op, body, _ in self.calls if op == 'index')
                        self.assertFalse(request.get('semantic', False))
                        self.assertEqual(self.state()[self.state_key()+'.json']['seen'],
                                         {self.optional[0]['entry']['record_id']: 3})

    def test_no_match_and_taskless_defer(self):
        self.optional_note('Unrelated gardening advice. Plant bonsai in another season.')
        for name in ('SessionStart', 'UserPromptSubmit'):
            with self.subTest(no_match=name):
                self.event(name)
                result = self.pair()
                self.assertEqual(result.returncode, 0)
                self.assertNotIn(self.optional[0]['body'], result.stdout.decode())
                self.assertNotIn('expanded', self.context(result))
                self.assertEqual(self.state()[self.state_key()+'.json']['last_recall']['rejected'], {'not_relevant': 1})
        for required in (False, True):
            if required:
                self.required()
            for name, prompt, reason in (('SessionStart', '', 'taskless_startup'),
                                         ('UserPromptSubmit', self.f.wake(), 'taskless_notification')):
                with self.subTest(required=required, event=name):
                    self.event(name, prompt=prompt)
                    result = self.pair()
                    self.assertEqual(result.returncode, 0)
                    self.assertNotIn('expand', [op for op, _, _ in self.calls])
                    request = next(body for op, body, _ in self.calls if op == 'index')
                    self.assertEqual(request['kinds'], ['decision', 'preference'])
                    self.assertEqual(self.state()[self.state_key()+'.json']['last_recall']['optional_deferred'], reason)
                    self.assertEqual(bool(self.context(result)), required)

    def test_optional_seen_and_start_reset(self):
        self.optional_note('Current retention policy implementation. 日本語 😀\n')
        self.event('UserPromptSubmit')
        first = self.pair()
        self.assertEqual(self.context(first)['expanded']['selection']['record']['body'], self.optional[0]['body'])
        self.assertEqual(self.state()[self.state_key()+'.json']['seen'], {self.optional[0]['entry']['record_id']: 3})
        repeated = self.pair()
        self.assertNotIn('expanded', self.context(repeated))
        self.assertNotIn('expand', [op for op, _, _ in self.calls])
        self.event('SessionStart')
        self.assertEqual(self.pair().returncode, 0)
        self.assertIn('expand', [op for op, _, _ in self.calls])

    test_retained_seen_and_binding_reset = hermes.HermesRecallTests.test_seen_retention_start_reset_and_binding_reset

    def test_literal_hermes_labels_keep_raw_scope_and_hashed_state(self):
        for label in ('Hermes session/run:日本語-01', '../label/with:separators',
                      '11111111-1111-4111-8111-111111111111', 'h'*128, 'legal\x7flabel'):
            with self.subTest(label=label):
                shutil.rmtree(self.f.memory['state_dir'], ignore_errors=True)
                self.event('UserPromptSubmit', session_id=label)
                self.assertEqual(self.pair().returncode, 0)
                self.assertEqual(list(self.state()), [hashlib.sha256(label.encode()).hexdigest()+'.json'])

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

    def test_bare_hermes_start_and_prompt_remain_unavailable(self):
        for name in ('SessionStart', 'UserPromptSubmit'):
            with self.subTest(event=name):
                self.event(name)
                result = self.launch('bare-go', lambda: subprocess.run(
                    [str(self.f.binary), 'memory', '--config', str(self.f.memory_path)],
                    input=self.f.raw, cwd=self.f.root, env=self.f.env(), capture_output=True, timeout=18))
                self.assertEqual((result.returncode, result.stdout), (1, b''))
                self.assertIn(b'ordinary memory harness remains unavailable', result.stderr)
                self.assertEqual(self.calls, [])
                self.assertFalse(Path(self.f.memory['state_dir']).exists())

    test_malformed_surrogate_identity_is_a_stable_class_b_refusal = hermes.HermesRecallTests.test_malformed_surrogate_identity_is_a_stable_class_b_refusal


if __name__ == '__main__':
    unittest.main(verbosity=2)
