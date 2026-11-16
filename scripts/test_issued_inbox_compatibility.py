"""Issued inbox tails through real Go/Python /3 frontends in all profile classes."""
import json
from pathlib import Path
import shlex
import sys
import tempfile
import unittest

from test_f04_activation import Fixture, compat, encoded, overwrite_fixture, sha
import test_f04_issued as issued
from test_f04_issued import RESULT, NOTE, SESSION, ok, LEASE, EVENT, DELIVERY
from test_r02_compatibility_install import InstalledFixture, InstalledTools
from stage_lifecycle_hooks import checked_go_launcher


class IssuedFixture(InstalledFixture):
    def __init__(self, *args, profile_class, **kwargs):
        self.profile_class = profile_class
        super().__init__(*args, coordination_only=profile_class == 'coordonly', **kwargs)

    def configuration_plan(self, **kwargs):
        plan = super().configuration_plan(**kwargs)
        if self.profile_class != 'bound':
            plan.update(profile_mode='unbound', enabled=False)
            for descriptor in [plan['memory_config'], *plan['coordination_configs']]:
                if descriptor == {'absent': True}:
                    continue
                path = Path(descriptor['path'])
                config = json.loads(path.read_bytes())
                config.pop('inbox_recall_binding', None)
                path.write_bytes(encoded(config))
                descriptor['sha256'] = sha(path.read_bytes())
            for config in (self.memory, self.coord):
                config.pop('inbox_recall_binding', None)
        return plan

    # Use the maintained literal producer so fault tests can repin a coherent
    # generation before issuing a new command, without reusing a stale launcher.
    launcher = Fixture.launcher

    def go_launcher(self, timeout=15):
        vector = compat.launch_vector(sys.executable, self.receipt_path, self.receipt_sha,
            'coordination', 'coordination-hook.py', self.coord_path, timeout, self.launch_root)
        self.launcher('coordination', timeout)
        return checked_go_launcher(self.stager, str(self.binary), 'coordination', vector)[0]


class IssuedInboxCompatibilityTests(InstalledTools, unittest.TestCase):
    prepare = issued.F04IssuedTests.prepare
    run_text = issued.F04IssuedTests.run_text

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='issued-inbox-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def fixture(self, root, **kwargs):
        return IssuedFixture(root, self.binary, stager=self.stager,
                             profile_class=self.profile_class, **kwargs)

    def rows(self):
        for profile_class in ('bound', 'unbound', 'coordonly'):
            self.profile_class = profile_class
            for runtime in ('python', 'go'):
                yield profile_class, runtime

    def execute(self, f, text, runtime, body=b'', extra_env=None, timeout=15):
        argv = shlex.split(text)
        self.assertEqual(argv[0], str(f.launcher('coordination', timeout)))
        # Keep the emitted tail byte-for-byte; select the independently checked
        # Go entry for the differential half, never call the routing helper.
        if runtime == 'go':
            argv[0] = f.go_launcher(timeout)
        result = self.run_text(f, shlex.join(argv), body, extra_env)
        print('ISSUED_MATRIX', self.profile_class, runtime, argv[1:], result.returncode, flush=True)
        return result

    def journal(self, f, context):
        return json.loads((Path(f.coord['state_dir']) / 'intents' / (context['attempt_id'] + '.json')).read_bytes())

    def test_every_context_command_commits_exact_journal_and_store_effect(self):
        for profile_class, runtime in self.rows():
            for operation in ('completion', 'acknowledgement', 'response', 'finish_reply'):
                with self.subTest(profile_class=profile_class, runtime=runtime, operation=operation):
                    f, context, _, _ = self.prepare(profile_class + runtime + operation,
                        'notice' if operation == 'acknowledgement' else 'request')
                    body = b'selected issued-inbox result\n'
                    text = context['shell_commands'][operation]
                    if operation == 'response':
                        completed = self.execute(f, context['shell_commands']['completion'], runtime, body)
                        self.assertEqual((completed.returncode, completed.stderr), (0, b''), completed.stdout)
                        text += ' --version 1 ' + RESULT['record_id']
                    result = self.execute(f, text, runtime, b'' if operation in ('acknowledgement', 'response') else body)
                    self.assertEqual((result.returncode, result.stderr), (0, b''), result.stdout)
                    calls = [row for row in f.api_calls() if row['operation'] in ('ack', 'complete', 'publish')]
                    expected = ['complete', 'publish'] if operation in ('response', 'finish_reply') else ['ack' if operation == 'acknowledgement' else 'complete']
                    self.assertEqual([row['operation'] for row in calls], expected)
                    for row in calls:
                        self.assertIn('--agent-id', row['argv'])
                        if row['operation'] == 'complete':
                            self.assertEqual(row['input'], body.decode())
                        if row['operation'] == 'publish':
                            self.assertEqual(row['argv'][-2:], ['1', RESULT['record_id']])
                            self.assertEqual(row['argv'][row['argv'].index('--causation-id') + 1], EVENT)
                    journal = self.journal(f, context)
                    self.assertEqual(journal['completion']['status'], 'committed')
                    if operation in ('response', 'finish_reply'):
                        self.assertEqual(journal['response']['status'], 'committed')
                    self.assertFalse(Path(f.memory['state_dir']).exists())

    def test_compact_read_inspect_settle_and_advance_preserve_separate_neighbor(self):
        for profile_class, runtime in self.rows():
            with self.subTest(profile_class=profile_class, runtime=runtime):
                f, context, path, public = self.prepare(profile_class + runtime, 'notice', True)
                prefix = shlex.join([str(f.launcher('coordination')), 'inbox', 'compact'])
                env = dict(CAIRN_INBOX_ROOT=str(f.root), CODEX_THREAD_ID=SESSION)
                for operation in ('read', 'inspect'):
                    result = self.execute(f, prefix + ' ' + operation + ' ' + context['compact_handle'], runtime, extra_env=env)
                    self.assertEqual((result.returncode, result.stderr), (0, b''), result.stdout)
                    data = json.loads(result.stdout)
                    self.assertTrue(data['ok'])
                    self.assertEqual(data['body'] if operation == 'read' else data['context']['attempt_id'],
                                     NOTE if operation == 'read' else context['attempt_id'])
                settle = prefix + ' settle ' + context['compact_handle'] + ' handled'
                self.assertIn(settle, json.loads(public.stdout)['hookSpecificOutput']['additionalContext'])
                result = self.execute(f, settle, runtime, extra_env=env)
                self.assertEqual((result.returncode, result.stderr), (0, b''), result.stdout)
                self.assertEqual(self.journal(f, context)['completion']['status'], 'committed')
                replies = json.loads(f.api.with_suffix('.replies').read_bytes())
                neighbor = dict(delivery_id='99999999-9999-4999-8999-999999999999', lease_id=LEASE,
                    state='leased', event=dict(event_id=EVENT, kind='notice', ref=RESULT, **{'from': 'agent/requester'}))
                attempt = dict(attempt_id='', session={}, delivery=neighbor, native_turn_id=context['native_turn_id'],
                    batch_root=context['attempt_id'], batch_index=2, batch_limit=3)
                replies.update({'session-inbox-advance': [ok(dict(attempt=attempt, remainder='empty'), advance_identity=True)],
                                'event-renew': [ok(neighbor)]})
                f.replies(replies)
                advance = prefix + ' advance ' + context['compact_handle']
                self.assertIn(advance, json.loads(public.stdout)['hookSpecificOutput']['additionalContext'])
                result = self.execute(f, advance, runtime, extra_env=env)
                self.assertEqual((result.returncode, result.stderr), (0, b''), result.stdout)
                self.assertEqual(json.loads(result.stdout)['batch_index'], 2)
                contexts = list(path.parent.glob('*.json'))
                self.assertEqual(len(contexts), 2)
                next_context = next(json.loads(p.read_bytes()) for p in contexts if p != path)
                self.assertEqual(next_context['delivery_id'], neighbor['delivery_id'])
                self.assertNotEqual(next_context['compact_handle'], context['compact_handle'])
                self.assertEqual([r['operation'] for r in f.api_calls()].count('ack'), 1)

    def test_drain_executes_issued_tool_cue_without_claiming_another_native_attempt(self):
        import re
        for profile_class, runtime in self.rows():
            with self.subTest(profile_class=profile_class, runtime=runtime):
                f, _, _, _ = self.prepare(profile_class + runtime, None)
                replies = json.loads(f.api.with_suffix('.replies').read_bytes())
                replies['session-inbox-pending'] = [ok(dict(requests=0, notices=1, responses=0, latest_position=1))]
                f.replies(replies)
                f.raw = encoded(dict(f.event, hook_event_name='PostToolUse'))
                cue = f.public('coordination', timeout=2)
                self.assertEqual((cue.returncode, cue.stderr), (0, b''), cue.stdout)
                text = re.search(r'with `([^`]+)`', json.loads(cue.stdout)['hookSpecificOutput']['additionalContext']).group(1)
                delivery = dict(delivery_id=DELIVERY, lease_id=LEASE, state='leased',
                    event=dict(event_id=EVENT, kind='notice', ref=RESULT, **{'from': 'agent/requester'}))
                replies = json.loads(f.api.with_suffix('.replies').read_bytes())
                replies['event-next'] = [ok(dict(delivery=delivery))]
                f.replies(replies)
                result = self.execute(f, text, runtime, timeout=2)
                self.assertEqual((result.returncode, result.stderr), (0, b''), result.stdout)
                self.assertEqual(json.loads(result.stdout)['delivery_id'], DELIVERY)
                self.assertEqual([r['operation'] for r in f.api_calls()].count('event-next'), 1)
                self.assertNotIn('ack', [r['operation'] for r in f.api_calls()])

    def test_wrong_context_root_and_profile_binding_refuse_before_store_effect(self):
        for profile_class, runtime in self.rows():
            with self.subTest(profile_class=profile_class, runtime=runtime):
                f, context, path, _ = self.prepare(profile_class + runtime, 'notice', True)
                before = len(f.api_calls())
                wrong = f.root / 'foreign.json'
                wrong.write_bytes(path.read_bytes())
                wrong.chmod(0o600)
                text = shlex.join([str(f.launcher('coordination')), 'inbox', '--context', str(wrong), 'ack'])
                result = self.execute(f, text, runtime)
                self.assertEqual((result.returncode, result.stdout), (1, b''))
                text = shlex.join([str(f.launcher('coordination')), 'inbox', 'compact', 'inspect', context['compact_handle']])
                result = self.execute(f, text, runtime, extra_env={'CAIRN_INBOX_ROOT': str(self.root / 'foreign')})
                self.assertEqual((result.returncode, result.stdout), (1, b''))
                index = Path(f.coord['state_dir']) / 'compact' / (context['compact_handle'] + '.json')
                entry = json.loads(index.read_bytes())
                entry['binding'] = 'another-binding'
                index.write_bytes(encoded(entry))
                result = self.execute(f, text, runtime, extra_env={'CAIRN_INBOX_ROOT': str(f.root)})
                self.assertEqual((result.returncode, result.stdout), (1, b''))
                original = path.read_bytes()
                for mutation in ('executable', 'credential'):
                    changed = json.loads(original)
                    command = changed['commands']['ack']
                    if mutation == 'executable':
                        command[0] = str(f.root / 'foreign-cairn')
                    else:
                        command[command.index('--token-file') + 1] = str(f.root / 'foreign-token')
                    path.write_bytes(encoded(changed))
                    result = self.execute(f, context['shell_commands']['acknowledgement'], runtime)
                    self.assertEqual((result.returncode, result.stdout), (1, b''))
                path.write_bytes(original)
                self.assertEqual(len(f.api_calls()), before)

    def test_disabled_unbound_manifest_cannot_downgrade_an_opted_in_sibling(self):
        for profile_class, role in (('unbound', 'memory'), ('unbound', 'coordination'),
                                    ('coordonly', 'coordination'), ('bound', 'coordination')):
            self.profile_class = profile_class
            f = self.fixture(self.root / (profile_class + role))
            descriptor = f.receipt['memory_config'] if role == 'memory' else f.receipt['coordination_configs'][0]
            path = Path(descriptor['path'])
            config = json.loads(path.read_bytes())
            if profile_class == 'bound':
                config.pop('inbox_recall_binding')
            else:
                config['inbox_recall_binding'] = str(f.binding)
            overwrite_fixture(path, encoded(config))
            descriptor['sha256'] = sha(path.read_bytes())
            manifest = json.loads(f.binding.read_bytes())
            manifest.update(enabled=False, memory_config=f.receipt['memory_config'],
                            coordination_configs=f.receipt['coordination_configs'])
            overwrite_fixture(f.binding, encoded(manifest))
            f.receipt['binding']['sha256'] = sha(f.binding.read_bytes())
            overwrite_fixture(f.receipt_path, encoded(f.receipt))
            f.receipt_sha = sha(f.receipt_path.read_bytes())
            for runtime in ('python', 'go'):
                for tail in (['inbox', '--context', str(Path(f.coord['state_dir']) / 'inbox' / 'absent.json'), 'ack'],
                             ['inbox', 'compact', 'inspect', 'abababababababab']):
                    with self.subTest(profile_class=profile_class, role=role, runtime=runtime, tail=tail):
                        text = shlex.join([str(f.launcher('coordination')), *tail])
                        result = self.execute(f, text, runtime, extra_env={'CAIRN_INBOX_ROOT': str(f.root)})
                        if profile_class == 'unbound' and role == 'coordination':
                            # Opting this profile in selects the unchanged bound
                            # validator, whose mixed-profile ValueError is the
                            # retained INVALID_HOST frontend refusal in both runtimes.
                            self.assertEqual(result.returncode, 7)
                            refused = json.loads(result.stdout)
                            self.assertEqual(refused['status'], 'INVALID_HOST')
                            self.assertEqual(refused['message'], 'INVALID_HOST: ValueError reading the native inbox context')
                        else:
                            self.assertEqual((result.returncode, result.stdout), (1, b''))
                            self.assertIn(b'compatibility_preflight', result.stderr)
            self.assertEqual(f.api_calls(), [])


if __name__ == '__main__':
    unittest.main()
