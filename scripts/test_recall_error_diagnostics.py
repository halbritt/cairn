"""Failed recall keeps fixed failure codes, never provider text, through a real stub CLI."""
import json
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from test_claude_lifecycle import hook

PRIVATE = ('private owner prompt', 'private stderr sentinel', 'private provider message',
           'PRIVATE_UNLISTED_STATUS', 'PRIVATE_WARMING', 'private-local')

STUB = textwrap.dedent('''\
    #!{python}
    import json, sys, time
    from pathlib import Path
    here = Path({here!r})
    operation = next((arg for arg in sys.argv[1:] if arg in ('search', 'recall-observation')), None)
    if operation == 'recall-observation':
        with (here / 'observations.jsonl').open('a') as log:
            log.write(sys.stdin.read() + '\\n')
        print(json.dumps(dict(ok=True, data={{}})))
        sys.exit(0)
    mode = (here / 'mode').read_text()
    sys.stderr.write('private stderr sentinel\\n')
    def refuse(status, code):
        print(json.dumps(dict(ok=False, status=status, message='private provider message')))
        sys.exit(code)
    def answer(**data):
        print(json.dumps(dict(ok=True, data=data)))
    if mode == 'known_status':
        refuse('API_CONNECTION_FAILED', 7)
    elif mode == 'unknown_status':
        refuse('PRIVATE_UNLISTED_STATUS', 4)
    elif mode == 'reason_shaped_status':
        refuse('NOT_HOSTED', 4)
    elif mode == 'nonstring_status':
        print(json.dumps(dict(ok=False, status=dict(code='API_CONNECTION_FAILED'))))
        sys.exit(4)
    elif mode == 'malformed':
        print('private provider message, not JSON')
    elif mode == 'nonobject':
        print('["private provider message"]')
    elif mode == 'limit':
        sys.stdout.write('x' * ({limit} + 1))
    elif mode == 'unconfirmed':
        print(json.dumps(dict(ok=True, data=['private provider message'])))
    elif mode == 'bad_status':
        answer(status='PRIVATE_WARMING', destination=dict(name='hosted'), selected=[], index=[])
    elif mode == 'bad_destination':
        answer(status='READY', destination=dict(name='private-local'), selected=[], index=[])
    elif mode == 'sleep':
        time.sleep(5)
    else:
        answer(status='READY', destination=dict(name='hosted'), selected=[], index=[])
''')


class RecallErrorDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / '.git').mkdir()
        self.stub = self.root / 'cairn-stub'
        self.stub.write_text(STUB.format(python=sys.executable, here=str(self.root),
                                         limit=hook.COMMAND_OUTPUT_BYTES))
        self.stub.chmod(0o700)
        self.session = '5d0c1f4e-8a7b-4c2d-9e3f-0a1b2c3d4e5f'
        self.config = dict(cairn=str(self.stub), socket='fixture', token_file='fixture', repo='fixture',
                           state_dir=str(self.root / 'state'))
        self.event = dict(hook_event_name='UserPromptSubmit', session_id=self.session,
                          cwd=str(self.root), prompt='private owner prompt')
        self.state_path = self.root / 'state' / (self.session + '.json')

    def fail(self, mode, expected=hook.HookError):
        (self.root / 'mode').write_text(mode)
        with self.assertRaises(expected) as raised:
            hook.handle(self.config, self.event)
        return raised.exception, json.loads(self.state_path.read_text())['last_recall']

    def observations(self):
        path = self.root / 'observations.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def assert_private_free(self, status, error):
        diagnostics = json.dumps({key: status.get(key) for key in ('error_code', 'error_reason', 'error_type')})
        for text in (diagnostics, str(error), json.dumps(self.observations())):
            for private in PRIVATE:
                self.assertNotIn(private, text)

    def assert_failure(self, mode, code, reason):
        error, status = self.fail(mode)
        self.assertIs(type(error), hook.HookError)
        self.assertEqual(status['outcome'], 'failed')
        self.assertEqual(status['error_type'], 'HookError')
        self.assertEqual(status.get('error_code'), code)
        self.assertEqual(status.get('error_reason'), reason)
        if code is None:
            self.assertNotIn('error_code', status)
        if reason is None:
            self.assertNotIn('error_reason', status)
        observation = self.observations()[-1]
        self.assertEqual(observation['error_class'], 'HookError')
        self.assertNotIn('error_code', observation)
        self.assertNotIn('error_reason', observation)
        self.assert_private_free(status, error)
        return error, status, observation

    def test_listed_provider_status_and_local_reason_are_distinct_fields(self):
        error, _, observation = self.assert_failure('known_status', 'API_CONNECTION_FAILED', 'NONZERO_EXIT')
        self.assertEqual(str(error), 'memory command exited 7; operation not confirmed')
        self.assertEqual(observation['status'], 'error')
        self.assertEqual(observation['search_calls'], 1)

    def test_unlisted_reason_shaped_or_nonstring_status_is_never_a_code(self):
        for mode in ('unknown_status', 'reason_shaped_status', 'nonstring_status'):
            with self.subTest(mode=mode):
                self.assert_failure(mode, None, 'NONZERO_EXIT')

    def test_malformed_and_nonobject_json(self):
        for mode in ('malformed', 'nonobject'):
            with self.subTest(mode=mode):
                error, _, _ = self.assert_failure(mode, None, 'INVALID_JSON')
                self.assertEqual(str(error), 'memory command returned invalid JSON')

    def test_output_limit(self):
        error, _, _ = self.assert_failure('limit', None, 'OUTPUT_LIMIT')
        self.assertEqual(str(error), 'memory command output exceeded limit; operation not confirmed')

    def test_startup_failure_keeps_existing_code(self):
        self.config['cairn'] = str(self.root / 'missing-cairn')
        error, status = self.fail('success')
        self.assertEqual(str(error), 'could not start memory command')
        self.assertEqual((status['error_code'], status['error_reason']), ('API_UNAVAILABLE', 'START_FAILED'))

    def test_unconfirmed_result(self):
        error, _, _ = self.assert_failure('unconfirmed', None, 'NOT_CONFIRMED')
        self.assertEqual(str(error), 'Cairn did not confirm the operation')

    def test_unexpected_search_status_and_destination(self):
        error, _, _ = self.assert_failure('bad_status', None, 'SEARCH_STATUS')
        self.assertEqual(str(error), 'Cairn retrieval is not ready')
        error, _, _ = self.assert_failure('bad_destination', None, 'NOT_HOSTED')
        self.assertEqual(str(error), 'lifecycle hooks require a hosted-destination profile')

    def test_timeout_keeps_code_and_observation_status_without_reason(self):
        with patch.object(hook, 'RECALL_SECONDS', 0.6):
            error, status, observation = self.assert_failure('sleep', 'TIMEOUT', None)
        self.assertEqual(str(error), 'command timed out; memory operation not confirmed')
        self.assertEqual(observation['status'], 'timeout')

    def test_next_success_clears_failure_diagnostics(self):
        self.assert_failure('known_status', 'API_CONNECTION_FAILED', 'NONZERO_EXIT')
        (self.root / 'mode').write_text('success')
        self.assertEqual(hook.handle(self.config, self.event), {})
        status = json.loads(self.state_path.read_text())['last_recall']
        self.assertEqual(status['outcome'], 'empty')
        for key in ('error_type', 'error_code', 'error_reason'):
            self.assertNotIn(key, status)

    def test_persistence_revalidates_modified_attributes(self):
        def tampered(*args, **kwargs):
            error = hook.HookError('fixture failure', code='TIMEOUT')
            error.code, error.reason = 'private provider message', 'PRIVATE_WARMING'
            raise error
        with patch.object(hook.Memory, 'search', side_effect=tampered):
            error, status = self.fail('success')
        self.assertEqual(status['error_type'], 'HookError')
        self.assertNotIn('error_code', status)
        self.assertNotIn('error_reason', status)
        self.assert_private_free(status, error)

    def test_unknown_reason_is_dropped_at_construction(self):
        self.assertIsNone(hook.HookError('x', reason='PRIVATE_WARMING').reason)
        self.assertIsNone(hook.HookError('x', reason=['NOT_HOSTED']).reason)
        self.assertEqual(hook.HookError('x', reason='NOT_HOSTED').reason, 'NOT_HOSTED')


class RequiredConversionDiagnosticsTests(unittest.TestCase):
    """A known required failure converts as before; the original diagnostics persist."""

    def test_converted_required_failure_keeps_original_bounded_diagnostics(self):
        import test_claude_inbox_recall as claude_fixture
        case = claude_fixture.ClaudeInboxRecallTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.fixture['search']['selected'] = [dict(mandatory=True, record=dict(body='Required applicability guard.'))]
        case.freeze()
        self.assertEqual(case.invoke(main=True)['code'], 0)
        case.fixture['fail_search'] = 'API_UNAVAILABLE'; case.freeze()
        converted = []
        original = hook_module(case).codex_required_error

        def observe(config, event, error, **kwargs):
            result = original(config, event, error, **kwargs)
            converted.append((type(error).__name__, type(result).__name__))
            return result
        with patch.object(hook_module(case), 'codex_required_error', side_effect=observe):
            failed = case.memory_main(case.compact_event())
        self.assertEqual((failed['code'], failed['stdout']), (1, ''))
        self.assertIn(('HookError', 'RequiredContextRefused'), converted)
        ledger = case.ledger()
        self.assertTrue(ledger['grants'][ledger['active']]['required']['pending'])
        state_dir = Path(case.mc['state_dir'])
        status = json.loads((state_dir / (claude_fixture.SESSION + '.json')).read_text())['last_recall']
        self.assertEqual(status['error_type'], 'HookError')
        self.assertEqual((status['error_code'], status['error_reason']), ('API_UNAVAILABLE', 'NONZERO_EXIT'))


def hook_module(case):
    import test_inbox_recall_bridge
    return test_inbox_recall_bridge.memory


if __name__ == '__main__':
    unittest.main()
