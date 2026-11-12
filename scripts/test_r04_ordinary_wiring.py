"""Staged ordinary hooks against retained Source and the actual scripted provider."""
import base64
import copy
import json
from pathlib import Path
import shlex
import unittest

import test_f02_ordinary_provider as provider
import test_r04_bound_wiring as bound


class R04OrdinaryWiringTests(provider.OrdinaryProviderTests):
    def stage_rows(self, harness, mode):
        metadata = bound.R04BoundWiringTests.stage_rows(
            self, harness, mode, enabled=False, remove_package=True)
        self.trace = self.f.memory_path.parent / 'invocation-trace' / 'trace.jsonl'
        self.trace.parent.mkdir(mode=0o700, exist_ok=True)
        ordinary = {'SessionStart', 'UserPromptSubmit'}
        if harness == 'claude':
            ordinary |= {'PostToolUse', 'PostToolUseFailure'}
        if harness == 'opencode' and mode == 'agent_tools':
            ordinary |= {'PreToolUse', 'PostToolUse', 'PostToolUseFailure'}
        actual = {name for name, row in self.rows.items()
                  if row['reason'].startswith('receipt-checked ordinary Go ')}
        self.assertEqual(actual, ordinary)
        for name in ordinary:
            row = self.rows[name]
            self.assertEqual(row['implementation'], 'go')
            self.assertEqual(row['hook']['timeout'], 13)
            self.assertNotEqual(row['checked_launcher'], row['retained_launcher'])
            vector = shlex.split(Path(row['argv'][0]).read_text().splitlines()[1])
            self.assertEqual(vector[:3], ['exec', str(self.f.binary), 'memory'])
            self.assertEqual(vector[vector.index('--role') + 1], 'memory')
            self.assertEqual(vector[vector.index('--hook-timeout') + 1], '13')
        if harness == 'opencode' and mode != 'agent_tools':
            for name in ('PostToolUse', 'PostToolUseFailure'):
                self.assertEqual(self.rows[name]['implementation'], 'python')
                self.assertEqual(self.rows[name]['checked_launcher'], self.rows[name]['retained_launcher'])
        print('R04_ORDINARY_SELECTION ' + json.dumps({
            'test': self.id(), 'harness': harness, 'mode': mode,
            'events': sorted(ordinary), 'go_sha256': self.f.receipt['binary']['sha256'],
            'receipt_sha256': self.f.receipt_sha,
        }, sort_keys=True), flush=True)
        return metadata

    def source(self):
        return self.invoke_provider_side(
            'source', lambda: bound.R04BoundWiringTests.staged_process(self, 'source'))

    def go(self):
        return self.invoke_provider_side(
            'go', lambda: bound.R04BoundWiringTests.staged_process(self, 'go'))

    def normalize(self, row, side):
        row = copy.deepcopy(row)
        if side == 'source':
            for _, _, headers in row['calls']:
                token = headers['Cairn-Client-Diagnostics']
                diagnostic = json.loads(base64.urlsafe_b64decode(token + '=' * (-len(token) % 4)))
                # This retained R02 launcher reports an unknown source identity.
                # Validate that raw declaration before using the shared fixture's
                # normalizer, whose standalone Python frame reports its alias hash.
                self.assertEqual(diagnostic['origin'], dict(component='lifecycle-memory', basis='reported'))
                diagnostic['origin']['implementation_id'] = self.f.receipt['members']['memory-hook.py']['sha256']
                headers['Cairn-Client-Diagnostics'] = base64.urlsafe_b64encode(
                    json.dumps(diagnostic).encode()).decode().rstrip('=')
        return super().normalize(row, side)

    def recall_pairs(self, harness, mode):
        self.configure_harness(harness, recall_mode=mode)
        self.required('Retain the checked lifecycle installation policy.')
        self.optional_note('Use PostgreSQL for durable lifecycle storage. 日本語 😀')
        self.stage_rows(harness, mode)
        session = 'ses_R04OrdinaryA1' if harness == 'opencode' else '22222222-2222-4222-8222-222222222222'
        for name in ('SessionStart', 'UserPromptSubmit'):
            with self.subTest(harness=harness, mode=mode, event=name):
                self.reset_state()
                self.event(name, session_id=session, source='startup',
                           turn_id='33333333-3333-4333-8333-333333333333', prompt='storage')
                result = self.pair()
                self.assertEqual((result.returncode, result.stderr), (0, b''))
                context = self.context(result)
                self.assertEqual(context['selected'][0]['record']['body'], self.selected[0]['record']['body'])
                if mode != 'agent_tools':
                    self.assertEqual(context['expanded']['selection']['record']['body'], self.optional[0]['body'])
                    self.assertEqual(len(self.provider_sides['go']), 1)
                    self.assertEqual(self.provider_sides['go'][0]['stage'], 'recall')
                    self.assertEqual(self.recall_status()['discovery'], 'verified')
                else:
                    self.assertEqual(self.provider_sides['go'], [])
                operations = [op for op, _, _ in self.calls]
                self.assertIn('index', operations)
                self.assertIn('recall-observation', operations)

    def test_staged_codex_agent_tools_recall(self):
        self.recall_pairs('codex', 'agent_tools')

    def test_staged_codex_provider_recall(self):
        self.recall_pairs('codex', 'ambient')

    def test_staged_claude_agent_tools_recall(self):
        self.recall_pairs('claude', 'agent_tools')

    def test_staged_claude_provider_recall(self):
        self.recall_pairs('claude', 'ambient')

    def test_staged_opencode_agent_tools_recall(self):
        self.recall_pairs('opencode', 'agent_tools')

    def test_staged_opencode_provider_recall(self):
        self.recall_pairs('opencode', 'ambient')

    def test_staged_opencode_default_provider_recall(self):
        self.recall_pairs('opencode', None)
        self.assertNotIn('recall_mode', self.f.memory)

    def tool_pairs(self, mode):
        self.configure_harness('claude', recall_mode=mode)
        (self.f.root / 'current.go').write_text('fixture\n')
        self.stage_rows('claude', mode)
        for name, fields in (
            ('PostToolUse', dict(tool_name='Read', tool_input=dict(file_path='current.go'))),
            ('PostToolUseFailure', dict(error='LeaseExpiredError')),
        ):
            with self.subTest(mode=mode, event=name):
                self.reset_state()
                self.event(name, session_id='22222222-2222-4222-8222-222222222222', **fields)
                result = self.pair()
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'{}\n', b''))
                self.assertEqual(self.calls, [])
                self.assertEqual(self.provider_sides['go'], [])
                hints = self.state()[self.state_key() + '.json']['hints']
                if name == 'PostToolUse':
                    self.assertIn('current.go', hints['files'])
                else:
                    self.assertEqual(hints['errors'], ['LeaseExpiredError'])

    def test_staged_claude_agent_tools_observation(self):
        self.tool_pairs('agent_tools')

    def test_staged_claude_provider_tool_observation(self):
        self.tool_pairs('ambient')


def load_tests(loader, tests, pattern):
    # Reuse the complete provider fixture; its original cases retain their own module.
    return unittest.TestSuite(R04OrdinaryWiringTests(name) for name in sorted(
        name for name in R04OrdinaryWiringTests.__dict__ if name.startswith('test_staged_')))


if __name__ == '__main__':
    unittest.main()
