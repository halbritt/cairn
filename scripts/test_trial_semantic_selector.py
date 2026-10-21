"""Synthetic semantic-fallback selector identity: fake binary files and fabricated hook rows.

No provider, model, store, case or oracle is used, and no selector is spawned.
Hook rows mirror trial_hook_observer's ``cairn.task-hook-observation/1``;
process records mirror what ``memory.bounded_command`` and ``select_json``
write. ``selector_argv`` loads the repository's own lifecycle ``memory.py`` and
replaces its ``run_json``, so the argv is built and never run.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import unittest
from unittest.mock import patch

import trial_semantic_selector as selector

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / 'integrations/lifecycle/memory.py'
BINARY_BYTES = b'#!/bin/sh\necho synthetic-selector\n'
BINARY_SHA = hashlib.sha256(BINARY_BYTES).hexdigest()
MODEL = 'synthetic-selector-model'
CHANNEL = 'synthetic-test-channel'
LEGACY_HOOK_EVENTS = ('SessionStart', 'UserPromptSubmit', 'PostToolUse', 'PostToolUseFailure')


def declaration(path='/opt/synthetic/claude', **changes):
    document = dict(schema='cairn.semantic-selector-identity/1', binary=dict(path=path, sha256=BINARY_SHA), model=MODEL)
    document.update(changes)
    return document


def process(model=MODEL, outcome='completed', **extra):
    record = dict(requested_model=model, outcome=outcome, elapsed_ms=12.5, spawn_ms=0.4, returncode=0,
                  stdout_bytes=10, stderr_bytes=0, **extra)
    if model is ...:
        del record['requested_model']
    return record


def row(event='UserPromptSubmit', stdout=b'', **recall):
    attempted = event in ('SessionStart', 'UserPromptSubmit')
    return dict(schema='cairn.task-hook-observation/1', event=event, source=None, recall_attempted=attempted,
                recall=dict(outcome='delivered', **recall) if attempted else None, exit_code=0,
                stdout_sha256=hashlib.sha256(stdout).hexdigest(), stdout_bytes=len(stdout))


def jsonl(rows):
    return b''.join(json.dumps(r).encode() + b'\n' for r in rows)


def native(rows):
    return [dict(hook_id='h%d' % i, event=r['event'], stdout_sha256=r['stdout_sha256']) for i, r in enumerate(rows)]


def attempt_rows(**recall):
    return [row('SessionStart', b'start'), row('UserPromptSubmit', b'prompt', **recall),
            row('PostToolUse', b'tool')]


class DeclarationTest(unittest.TestCase):
    def test_typed_declaration_keeps_binary_pin_and_model(self):
        self.assertEqual(selector.typed(declaration()),
                         dict(binary=dict(path='/opt/synthetic/claude', sha256=BINARY_SHA), model=MODEL))

    def test_malformed_declarations_raise_a_clean_value_error(self):
        malformed = [
            None, [], 'opaque', {},
            declaration(schema='cairn.semantic-selector-identity/2'), declaration(extra=True),
            {k: v for k, v in declaration().items() if k != 'model'},
            declaration(model=''), declaration(model='two words'), declaration(model=' padded'),
            declaration(model='x' * 257), declaration(model='ctl\x07'), declaration(model=7),
            declaration(binary=None), declaration(binary=dict(path='/opt/x')),
            declaration(binary=dict(path='relative/claude', sha256=BINARY_SHA)),
            declaration(binary=dict(path='/opt/\0claude', sha256=BINARY_SHA)),
            declaration(binary=dict(path='/opt/claude', sha256=BINARY_SHA.upper())),
            declaration(binary=dict(path='/opt/claude', sha256=BINARY_SHA[:63])),
            declaration(binary=dict(path='/opt/claude', sha256=BINARY_SHA, extra=1)),
            declaration(recall_model='other'), declaration(preview_model='other'),
        ]
        for document in malformed:
            with self.subTest(document=document):
                with self.assertRaises(ValueError) as caught:
                    selector.typed(document)
                self.assertEqual(str(caught.exception), 'semantic_selector_declaration')

    def test_pinned_declaration_file_is_read_through_its_own_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'selector.json'
            path.write_bytes(json.dumps(declaration()).encode())
            reference = dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(selector.load_declaration(reference)['model'], MODEL)
            with self.assertRaisesRegex(ValueError, '^semantic_selector_unavailable$'):
                selector.load_declaration(dict(reference, sha256='0' * 64))
            path.write_bytes(b'{"schema": "opaque"}')
            reference['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, '^semantic_selector_declaration$'):
                selector.load_declaration(reference)


class ArmSchemaTest(unittest.TestCase):
    def arm(self, schema, mode='ambient', fallback=True, pin=...):
        arm = {field: dict(path='/p/' + field, sha256='a' * 64) for field in selector.execution_arm_fields(schema)}
        arm.update(recall_mode=mode, semantic_fallback=fallback)
        if pin is not ...:
            arm['semantic_selector'] = pin
        return arm

    def test_v1_arm_fields_are_exactly_the_current_v1_contract(self):
        source = (ROOT / 'scripts/trial_weekly_input.py').read_text()
        literal = re.search(r"shape\(arm, '([^']*)'\s*'([^']*)', 'weekly_arm_shape'\)", source)
        self.assertEqual(selector.execution_arm_fields('cairn.weekly-execution/1'),
                         tuple((literal.group(1) + literal.group(2)).split()))
        self.assertEqual(selector.execution_arm_fields('cairn.weekly-execution/2'),
                         selector.execution_arm_fields('cairn.weekly-execution/1') + ('semantic_selector',))
        with self.assertRaisesRegex(ValueError, '^weekly_execution_schema$'):
            selector.execution_arm_fields('cairn.weekly-execution/3')

    def test_v2_selector_pin_required_exactly_for_ambient_fallback(self):
        v2 = 'cairn.weekly-execution/2'
        pin = dict(path='/opt/selector.json', sha256='b' * 64)
        self.assertEqual(selector.check_arm_selector(v2, self.arm(v2, pin=pin)), pin)
        for mode, fallback in (('ambient', False), ('agent_tools', True), ('agent_tools', False)):
            with self.subTest(mode=mode, fallback=fallback):
                self.assertIsNone(selector.check_arm_selector(v2, self.arm(v2, mode, fallback, pin=None)))
                with self.assertRaisesRegex(ValueError, '^weekly_selector_forbidden$'):
                    selector.check_arm_selector(v2, self.arm(v2, mode, fallback, pin=pin))
        with self.assertRaisesRegex(ValueError, '^weekly_selector_required$'):
            selector.check_arm_selector(v2, self.arm(v2, pin=None))
        for bad in (dict(path='/x'), dict(path='rel', sha256='b' * 64), dict(path='/x', sha256='B' * 64), 'opaque'):
            with self.subTest(pin=bad), self.assertRaisesRegex(ValueError, '^weekly_selector_pin$'):
                selector.check_arm_selector(v2, self.arm(v2, pin=bad))

    def test_v1_is_never_reinterpreted_or_defaulted(self):
        v1, v2 = 'cairn.weekly-execution/1', 'cairn.weekly-execution/2'
        # A /1 ambient fallback arm carries no selector; the check returns nothing to lift the refusal.
        self.assertIsNone(selector.check_arm_selector(v1, self.arm(v1)))
        with self.assertRaisesRegex(ValueError, '^weekly_arm_shape$'):
            selector.check_arm_selector(v1, self.arm(v1, pin=None))  # a /2 field inside /1
        with self.assertRaisesRegex(ValueError, '^weekly_arm_shape$'):
            selector.check_arm_selector(v2, self.arm(v1))  # a missing /2 field is never defaulted


class CustodyTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / 'claude'
        self.source.write_bytes(BINARY_BYTES)
        self.declared = selector.typed(declaration(str(self.source)))

    def test_retained_copy_is_bound_read_only_at_the_target(self):
        (self.root / 'out1').mkdir()
        retained = selector.retain(self.declared, self.root / 'out1')
        copied = Path(retained['source'])
        self.assertEqual(copied, self.root / 'out1/selector/claude')
        self.assertEqual(stat.S_IMODE(copied.stat().st_mode), 0o500)
        self.assertEqual(copied.read_bytes(), BINARY_BYTES)
        mounted = selector.mount(retained)
        self.assertEqual(mounted['bind'], (str(copied), '/tmp/trial/selector/claude', 'ro'))
        self.assertEqual(mounted['observed'], dict(source=str(copied), target='/tmp/trial/selector/claude',
                                                   sha256=BINARY_SHA))
        os.chmod(copied, 0o700)
        copied.write_bytes(BINARY_BYTES + b'# changed\n')
        with self.assertRaisesRegex(ValueError, '^semantic_selector_binary_changed$'):
            selector.mount(retained)
        copied.unlink()
        with self.assertRaisesRegex(ValueError, '^semantic_selector_binary_changed$'):
            selector.mount(retained)

    def test_mismatched_or_missing_source_refuses_without_a_copy(self):
        self.source.write_bytes(BINARY_BYTES + b'# other\n')
        with self.assertRaisesRegex(ValueError, '^semantic_selector_binary_mismatch$'):
            selector.retain(self.declared, self.root / 'out2')
        self.assertFalse((self.root / 'out2/selector/claude').exists())
        self.source.unlink()
        with self.assertRaisesRegex(ValueError, '^semantic_selector_binary_unobserved$'):
            selector.retain(self.declared, self.root / 'out3')


class ConfigArgvTest(unittest.TestCase):
    declared = selector.typed(declaration())
    base = dict(repo='synthetic', state_dir='/tmp/trial/hookstate', semantic_fallback=True, recall_mode='ambient')

    def test_hook_config_sets_declared_selector_and_refuses_overrides(self):
        config = selector.hook_config(self.base, self.declared)
        self.assertEqual(config, dict(self.base, claude='/tmp/trial/selector/claude', model=MODEL))
        for conflict in (dict(recall_model='other'), dict(preview_model='other'), dict(model='sonnet'),
                         dict(claude='/usr/bin/claude')):
            with self.subTest(conflict=conflict), \
                    self.assertRaisesRegex(ValueError, '^semantic_selector_config_conflict$'):
                selector.hook_config(dict(self.base, **conflict), self.declared)

    def test_pinned_hook_builds_the_exact_selector_argv_without_spawning(self):
        config = selector.hook_config(self.base, self.declared)
        with patch('subprocess.Popen', side_effect=AssertionError('spawned')):
            argv = selector.selector_argv(HOOK, config, self.declared)
        for stage in ('preview', 'recall'):
            with self.subTest(stage=stage):
                self.assertEqual(argv[stage][0], '/tmp/trial/selector/claude')
                self.assertEqual(argv[stage][argv[stage].index('--model') + 1], MODEL)
                self.assertEqual(argv[stage].count('--model'), 1)
                self.assertEqual(argv[stage][argv[stage].index('--tools') + 1], '')

    def test_argv_without_the_declared_model_refuses(self):
        for config in (dict(self.base, claude='/tmp/trial/selector/claude'),  # CLI default model
                       dict(self.base, claude='/tmp/trial/selector/claude', model='other-model'),
                       dict(self.base, claude='/elsewhere/claude', model=MODEL)):
            with self.subTest(config=config), self.assertRaisesRegex(ValueError, '^semantic_selector_argv$'):
                selector.selector_argv(HOOK, config, self.declared)
        with tempfile.TemporaryDirectory() as directory:
            broken = Path(directory) / 'memory.py'
            broken.write_text('def select_json(config):\n    return {}\n')
            with self.assertRaisesRegex(ValueError, '^semantic_selector_argv$'):
                selector.selector_argv(broken, selector.hook_config(self.base, self.declared), self.declared)

    def test_capture_consumer_of_config_model_has_zero_legacy_demand(self):
        self.assertEqual(selector.registered_events(LEGACY_HOOK_EVENTS),
                         dict(registered=list(LEGACY_HOOK_EVENTS), capture_events=[], capture_demand=0))
        for event in ('Stop', 'PreCompact', 'SessionEnd'):
            with self.subTest(event=event), self.assertRaisesRegex(ValueError, '^semantic_selector_capture_demand$'):
                selector.registered_events(LEGACY_HOOK_EVENTS + (event,))


class ObservationTest(unittest.TestCase):
    def test_rows_are_schema_checked_before_any_record_is_read(self):
        good = row(model_process=process())
        malformed = [
            # An arbitrary dict carrying recall.model_process is not an observation.
            dict(recall=dict(model_process=process())),
            dict(good, schema='cairn.task-hook-observation/2'),
            dict(good, event='Other'),
            dict(row('PostToolUse'), event='Other'),
            dict(row('PostToolUse'), recall_attempted=True, recall=dict(model_process=process())),
            dict(good, recall_attempted='yes'),
            {k: v for k, v in good.items() if k != 'recall'},
            dict(good, stdout_sha256='x'),
            dict(good, exit_code='0'),
            dict(row('PostToolUse'), recall=dict(model_process=process())),  # recall only runs on recall events
            dict(good, recall_attempted=False),
            dict(good, recall=dict(good['recall'], unexpected=1)),
            row(model_process='opaque'),
            row(model_process=dict(process(), extra=1)),
            row(model_process=process(model=['list'])),
            row(model_process=process(outcome='served')),
            row(model_process={k: v for k, v in process().items() if k != 'outcome'}),
            row(model_process=dict(process(), elapsed_ms=-1)),
        ]
        for bad in malformed:
            with self.subTest(row=bad), \
                    self.assertRaisesRegex(ValueError, '^semantic_selector_observation_malformed$'):
                selector.parse_observations(jsonl([row('SessionStart'), bad]))
        duplicate = b'{"event":"Stop",' + json.dumps(good).encode()[1:] + b'\n'  # a later key must not win
        for data in (b'not json\n', duplicate, b'\xff\n', '[]'):
            with self.subTest(data=data), \
                    self.assertRaisesRegex(ValueError, '^semantic_selector_observation_malformed$'):
                selector.parse_observations(data)

    def test_calls_are_extracted_in_order_and_an_unstarted_record_is_no_call(self):
        parsed = selector.parse_observations(jsonl([
            row('SessionStart', preview_process={}),
            row(preview_process=process(), model_process=process(outcome='timeout')),
            row(model_process=process(...))]))
        self.assertEqual(parsed['calls'], [
            dict(row=1, stage='preview', requested_model=MODEL, requested_model_observed=True, outcome='completed'),
            dict(row=1, stage='recall', requested_model=MODEL, requested_model_observed=True, outcome='timeout'),
            dict(row=2, stage='recall', requested_model=None, requested_model_observed=False, outcome='completed')])


@patch.object(selector, 'AUTHENTICATION_METHODS', frozenset({CHANNEL}))
class ClassifyTest(unittest.TestCase):
    declared = selector.typed(declaration())
    mounted = dict(source='/out/selector/claude', target='/tmp/trial/selector/claude', sha256=BINARY_SHA)

    def classify(self, rows, *, data=None, hooks=None, mounted=..., authentication=...):
        data = jsonl(rows) if data is None else data
        if authentication is ...:
            authentication = dict(method=CHANNEL, observations_sha256=hashlib.sha256(data).hexdigest())
        return selector.classify_attempt(self.declared, mounted=self.mounted if mounted is ... else mounted,
                                         observations=data, native_hooks=native(rows) if hooks is None else hooks,
                                         authentication=authentication)

    def test_matching_identity_is_matched_as_requested_not_served(self):
        result = self.classify(attempt_rows(preview_process=process(), model_process=process()))
        self.assertEqual((result['result'], result['model'], result['refusals']), ('matched', 'requested_matches', []))
        self.assertEqual(result['observed']['binary_sha256'], BINARY_SHA)
        self.assertEqual([c['stage'] for c in result['observed']['calls']], ['preview', 'recall'])
        self.assertEqual(result['not_observed'], ['served_model', 'provider_account'])
        self.assertEqual(result['declared'], self.declared)

    def test_complete_capture_with_zero_calls_is_not_invoked_never_matched(self):
        result = self.classify(attempt_rows(preview_process={}))
        self.assertEqual((result['result'], result['model'], result['refusals']), ('not_invoked', 'unobserved', []))
        self.assertEqual(result['observed']['calls'], [])

    def test_empty_or_incomplete_capture_refuses_instead_of_not_invoked(self):
        rows = attempt_rows()
        cases = [
            dict(rows=[], data=b''),  # an empty list alone is not a complete observation
            dict(rows=rows, hooks=[]),
            dict(rows=rows, hooks=native(rows)[:-1]),  # a native hook without its observer row
            dict(rows=rows, hooks=native(rows) + [dict(hook_id='extra', event='Stop', stdout_sha256='c' * 64)]),
            dict(rows=rows, hooks=[dict(h, stdout_sha256=None) for h in native(rows)]),  # unfinished hooks
            dict(rows=rows, hooks=[dict(h, hook_id='same') for h in native(rows)]),
            dict(rows=rows[2:], hooks=native(rows[2:])),  # no recall event observed at all
        ]
        for case in cases:
            with self.subTest(case=case):
                result = self.classify(case['rows'], data=case.get('data'), hooks=case.get('hooks'))
                self.assertEqual(result['result'], 'incomplete')
                self.assertIn('semantic_selector_capture_incomplete', result['refusals'])
        result = selector.classify_attempt(self.declared, mounted=self.mounted, observations=None,
                                           native_hooks=native(rows), authentication=None)
        self.assertEqual((result['result'], result['refusals']), ('incomplete', ['semantic_selector_capture_incomplete']))

    def test_unauthenticated_capture_refuses(self):
        rows = attempt_rows(model_process=process())
        data = jsonl(rows)
        for authentication in (None, dict(method='other', observations_sha256=hashlib.sha256(data).hexdigest()),
                               dict(method=CHANNEL, observations_sha256='0' * 64)):
            with self.subTest(authentication=authentication):
                result = self.classify(rows, authentication=authentication)
                self.assertEqual((result['result'], result['refusals']),
                                 ('incomplete', ['semantic_selector_capture_unauthenticated']))

    def test_current_source_accepts_no_authentication_channel(self):
        with patch.object(selector, 'AUTHENTICATION_METHODS', frozenset()):
            result = self.classify(attempt_rows(model_process=process()))
        self.assertEqual(result['refusals'], ['semantic_selector_capture_unauthenticated'])

    def test_mismatched_identity_is_rejected(self):
        cases = [
            (dict(rows=attempt_rows(model_process=process()), mounted=dict(self.mounted, sha256='d' * 64)),
             ['semantic_selector_binary_mismatch']),
            (dict(rows=attempt_rows(preview_process=process(), model_process=process('other-model'))),
             ['semantic_selector_model_mismatch']),
            (dict(rows=attempt_rows(model_process=process(None))), ['semantic_selector_model_mismatch']),
        ]
        for kwargs, refusals in cases:
            with self.subTest(refusals=refusals):
                result = self.classify(kwargs['rows'], mounted=kwargs.get('mounted', ...))
                self.assertEqual((result['result'], result['refusals']), ('mismatched', refusals))

    def test_missing_or_unobservable_identity_refuses(self):
        result = self.classify(attempt_rows(model_process=process()), mounted=None)
        self.assertEqual((result['result'], result['refusals']), ('incomplete', ['semantic_selector_binary_unobserved']))
        result = self.classify(attempt_rows(model_process=process(...)))  # a hook revision without requested_model
        self.assertEqual((result['result'], result['model'], result['refusals']),
                         ('incomplete', 'unobserved', ['semantic_selector_model_unobserved']))

    def test_malformed_observation_is_a_refusal_not_an_exception(self):
        for data in (b'{"recall": {"model_process": {}}}\n', b'\x00garbage', 'text'):
            with self.subTest(data=data):
                result = self.classify([], data=data, hooks=[],
                                       authentication=dict(method=CHANNEL, observations_sha256='0' * 64))
                self.assertEqual(result['result'], 'incomplete')
                self.assertIn('semantic_selector_observation_malformed', result['refusals'])
                self.assertIsNone(result['observed']['calls'])

    def test_capture_rows_refuse_as_unaccounted_selector_demand(self):
        rows = attempt_rows() + [row('Stop', b'stop')]
        result = self.classify(rows)
        self.assertEqual(result['refusals'], ['semantic_selector_capture_demand'])

    def test_each_attempt_is_classified_from_its_own_evidence(self):
        first = attempt_rows(model_process=process())
        second = [row('SessionStart', b'start-2'), row('UserPromptSubmit', b'prompt-2', preview_process={})]
        self.assertEqual(self.classify(first)['result'], 'matched')
        self.assertEqual(self.classify(second)['result'], 'not_invoked')
        # The first attempt's native hooks never complete the second attempt's capture.
        result = self.classify(second, hooks=native(first))
        self.assertEqual(result['refusals'], ['semantic_selector_capture_incomplete'])
        # The first attempt's authenticated digest never authenticates the second's bytes.
        result = self.classify(second, authentication=dict(method=CHANNEL,
                                                           observations_sha256=hashlib.sha256(jsonl(first)).hexdigest()))
        self.assertEqual(result['refusals'], ['semantic_selector_capture_unauthenticated'])


if __name__ == '__main__':
    unittest.main()
