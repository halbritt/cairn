"""Synthetic semantic-fallback selector identity: a fake binary file, fabricated hook observations.

No provider, model, store, case or oracle is used. The hook observation rows
mirror the shape trial_hook_observer retains (``cairn.task-hook-observation/1``
with ``recall.preview_process`` / ``recall.model_process``), and the process
dicts mirror what ``memory.select_json`` records (``requested_model``).
"""
import hashlib
from pathlib import Path
import tempfile
import unittest

import trial_semantic_selector as selector

BINARY_BYTES = b'#!/bin/sh\necho synthetic-selector\n'
BINARY_SHA = hashlib.sha256(BINARY_BYTES).hexdigest()


def declaration(**changes):
    document = dict(schema='cairn.semantic-selector-identity/1',
                    binary=dict(path='/opt/synthetic/claude', sha256=BINARY_SHA), model='synthetic-selector-model')
    document.update(changes)
    return document


def invocation(**stages):
    return dict(schema='cairn.task-hook-observation/1', event='UserPromptSubmit', recall_attempted=True,
                recall=dict(outcome='delivered', **stages))


def process(model='synthetic-selector-model', outcome='completed'):
    return dict(requested_model=model, outcome=outcome, elapsed_ms=12.5)


class DeclarationTest(unittest.TestCase):
    def test_typed_declaration_keeps_binary_pin_and_model(self):
        self.assertEqual(selector.typed(declaration()),
                         dict(binary=dict(path='/opt/synthetic/claude', sha256=BINARY_SHA),
                              model='synthetic-selector-model'))

    def test_malformed_declarations_raise_a_clean_value_error(self):
        malformed = [
            None, [], 'opaque', {},
            declaration(schema='cairn.semantic-selector-identity/2'),
            declaration(extra=True),
            {k: v for k, v in declaration().items() if k != 'model'},
            declaration(model=''), declaration(model='two words'), declaration(model=' padded'),
            declaration(model='x' * 257), declaration(model='ctl\x07'), declaration(model=7),
            declaration(binary=None), declaration(binary=dict(path='/opt/x')),
            declaration(binary=dict(path='relative/claude', sha256=BINARY_SHA)),
            declaration(binary=dict(path='/opt/\0claude', sha256=BINARY_SHA)),
            declaration(binary=dict(path='/opt/claude', sha256=BINARY_SHA.upper())),
            declaration(binary=dict(path='/opt/claude', sha256=BINARY_SHA[:63])),
            declaration(binary=dict(path='/opt/claude', sha256=BINARY_SHA, extra=1)),
            # Stage-specific selector models are not part of the closed declaration.
            declaration(recall_model='other'), declaration(preview_model='other'),
        ]
        for document in malformed:
            with self.subTest(document=document):
                with self.assertRaises(ValueError) as caught:
                    selector.typed(document)
                self.assertEqual(str(caught.exception), 'semantic_selector_declaration')


class ComparisonTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.binary = Path(directory.name) / 'claude'
        self.binary.write_bytes(BINARY_BYTES)
        self.declared = selector.typed(declaration())

    def compare(self, invocations, binary=None):
        return selector.compare(self.declared, binary_sha256=selector.observed_binary(binary or self.binary),
                                invocations=invocations)

    def test_matching_binary_and_requested_model_match(self):
        rows = [invocation(preview_process=process(), model_process=process()),
                invocation(model_process=process(outcome='timeout')),
                dict(schema='cairn.task-hook-observation/1', event='SessionStart', recall_attempted=False, recall=None)]
        result = self.compare(rows)
        self.assertEqual(result['result'], 'matched')
        self.assertEqual(result['refusals'], [])
        self.assertEqual(result['declared'], self.declared)
        self.assertEqual(result['observed']['binary_sha256'], BINARY_SHA)
        self.assertEqual(result['observed']['calls'], [
            dict(stage='preview', requested_model='synthetic-selector-model', requested_model_observed=True, outcome='completed'),
            dict(stage='recall', requested_model='synthetic-selector-model', requested_model_observed=True, outcome='completed'),
            dict(stage='recall', requested_model='synthetic-selector-model', requested_model_observed=True, outcome='timeout')])
        # The served model and account are never observed, so never claimed.
        self.assertEqual(result['not_observed'], ['served_model', 'provider_account'])

    def test_mismatched_binary_or_model_is_rejected(self):
        other = self.binary.with_name('other')
        other.write_bytes(BINARY_BYTES + b'# changed\n')
        cases = [
            (dict(binary=other, invocations=[invocation(model_process=process())]),
             ['semantic_selector_binary_mismatch']),
            (dict(invocations=[invocation(preview_process=process(), model_process=process('other-model'))]),
             ['semantic_selector_model_mismatch']),
            # A null request means the hook passed no --model: the CLI default ran, not the declared model.
            (dict(invocations=[invocation(model_process=process(None))]), ['semantic_selector_model_mismatch']),
            (dict(binary=other, invocations=[invocation(model_process=process('other-model'))]),
             ['semantic_selector_binary_mismatch', 'semantic_selector_model_mismatch']),
        ]
        for kwargs, refusals in cases:
            with self.subTest(refusals=refusals):
                result = self.compare(kwargs['invocations'], kwargs.get('binary'))
                self.assertEqual(result['result'], 'mismatched')
                self.assertEqual(result['refusals'], refusals)
                self.assertEqual(result['declared'], self.declared)

    def test_missing_or_unobservable_identity_refuses(self):
        without_request = dict(outcome='completed', elapsed_ms=3.0)  # a hook revision without requested_model
        cases = [
            (dict(binary=self.binary.with_name('absent'), invocations=[invocation(model_process=process())]),
             ['semantic_selector_binary_unobserved']),
            (dict(binary=self.binary.parent, invocations=[invocation(model_process=process())]),
             ['semantic_selector_binary_unobserved']),
            (dict(invocations=[]), ['semantic_selector_model_unobserved']),
            (dict(invocations=[invocation()]), ['semantic_selector_model_unobserved']),
            (dict(invocations=[invocation(model_process=process()), invocation(preview_process=without_request)]),
             ['semantic_selector_model_unobserved']),
        ]
        for kwargs, refusals in cases:
            with self.subTest(refusals=refusals):
                result = self.compare(kwargs['invocations'], kwargs.get('binary'))
                self.assertEqual(result['result'], 'unobserved')
                self.assertEqual(result['refusals'], refusals)
        result = self.compare([], self.binary.with_name('absent'))
        self.assertIsNone(result['observed']['binary_sha256'])
        self.assertEqual(result['refusals'], ['semantic_selector_binary_unobserved', 'semantic_selector_model_unobserved'])

    def test_malformed_observation_is_a_refusal_not_an_exception(self):
        for invocations in (None, {}, ['row'], [invocation(model_process='opaque')],
                            [dict(invocation(), recall=['x'])],
                            [invocation(model_process=process(model=['list']))]):
            with self.subTest(invocations=invocations):
                result = self.compare(invocations)
                self.assertEqual(result['result'], 'unobserved')
                self.assertEqual(result['refusals'], ['semantic_selector_observation_malformed'])
                self.assertIsNone(result['observed']['calls'])


if __name__ == '__main__':
    unittest.main()
