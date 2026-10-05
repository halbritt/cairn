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

# The 117d failure status keys; the change may add only error_code and error_reason.
FAILURE_KEYS = {'at', 'bytes', 'discovery', 'duration_ms', 'error_type', 'hook_event_name', 'inspected',
                'outcome', 'records', 'rejected'}


def spoof(like):
    """A str subclass that matches set membership while carrying private text."""
    class Spoof(str):
        def __eq__(self, other):
            return True
        __hash__ = lambda self: hash(like)
    return Spoof('private provider message')


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
    elif mode == 'budget_refused':
        refuse('BUDGET_REFUSED', 4)
    elif mode == 'invalid_utf8':
        sys.stdout.buffer.write(b'\\xff\\xfe\\xfa')
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
        # The whole synthetic state file, not only the new fields; the 117d baseline holds no sentinel here.
        self.assertLessEqual(set(status), FAILURE_KEYS | {'error_code', 'error_reason'})
        for text in (self.state_path.read_text(), str(error), json.dumps(self.observations())):
            for private in PRIVATE:
                self.assertNotIn(private, text)

    def assert_failure(self, mode, code, reason):
        error, status = self.fail(mode)
        self.assertIs(type(error), hook.HookError)
        self.assertEqual(status['outcome'], 'failed')
        self.assertEqual(status['error_type'], 'HookError')
        self.assertEqual(status.get('error_code'), code)
        self.assertEqual(status.get('error_reason'), reason)
        expected = FAILURE_KEYS | ({'error_code'} if code else set()) | ({'error_reason'} if reason else set())
        self.assertEqual(set(status), expected)
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
                error, _, _ = self.assert_failure(mode, None, 'NONZERO_EXIT')
                self.assertIsNone(error.code)

    def test_budget_refusal_keeps_its_type_without_code_or_reason(self):
        error, status = self.fail('budget_refused', hook.BudgetRefused)
        self.assertIs(type(error), hook.BudgetRefused)
        self.assertEqual(str(error), 'memory expansion budget exhausted')
        self.assertEqual((error.code, error.reason), (None, None))
        self.assertEqual(set(status), FAILURE_KEYS)
        self.assertEqual(status['error_type'], 'HookError')

    def test_invalid_utf8_output(self):
        error, _, _ = self.assert_failure('invalid_utf8', None, 'INVALID_JSON')
        self.assertEqual(str(error), 'memory command returned invalid JSON')

    def test_non_hook_failure_does_not_inherit_diagnostics(self):
        self.assert_failure('known_status', 'API_CONNECTION_FAILED', 'NONZERO_EXIT')
        with patch.object(hook.Memory, 'search', side_effect=ValueError('private provider message')):
            error, status = self.fail('success', ValueError)
        self.assertEqual(status['error_type'], 'ValueError')
        self.assertEqual(set(status), FAILURE_KEYS)
        # The raised ValueError keeps its own text; only persisted and reported data are bounded.
        for text in (self.state_path.read_text(), json.dumps(self.observations())):
            self.assertNotIn('private provider message', text)

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
        for code, reason in (('private provider message', 'PRIVATE_WARMING'),
                             (spoof('TIMEOUT'), spoof('NOT_HOSTED'))):
            def tampered(*args, **kwargs):
                error = hook.HookError('fixture failure', code='TIMEOUT')
                error.code, error.reason = code, reason
                raise error
            with self.subTest(code=type(code).__name__), patch.object(hook.Memory, 'search', side_effect=tampered):
                error, status = self.fail('success')
            self.assertEqual(status['error_type'], 'HookError')
            self.assertEqual(set(status), FAILURE_KEYS)
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


class BoundCodexConversionDiagnosticsTests(unittest.TestCase):
    """Root's bound Codex conversion probe, on the existing bridge fixture."""

    def test_bound_codex_required_conversion_preserves_original_diagnostics(self):
        import test_inbox_recall_bridge as bridge
        case = bridge.InboxRecallBridgeTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.fixture['search']['selected'] = [dict(mandatory=True, record=dict(body='Synthetic required guard.'))]
        case.freeze()
        case.invoke()
        case.fixture['fail_search'] = 'API_UNAVAILABLE'
        (case.root / 'fixture.json').write_text(json.dumps(case.fixture))
        conversions = []
        original = bridge.memory.codex_required_error

        def observe(config, event, error, **kwargs):
            result = original(config, event, error, **kwargs)
            conversions.append((type(error).__name__, type(result).__name__))
            return result
        with patch.object(bridge.memory, 'codex_required_error', side_effect=observe):
            result = case.memory_main(case.compact_event())
        self.assertEqual(result['code'], 0)
        self.assertIs(json.loads(result['stdout'])['continue'], False)
        self.assertIn(('HookError', 'RequiredContextRefused'), conversions)
        self.assertTrue(case.memory_ledger()['turns'][bridge.TURN]['required']['pending'])
        status = json.loads((Path(case.mc['state_dir']) / (bridge.SESSION + '.json')).read_text())['last_recall']
        self.assertEqual((status['error_type'], status['error_code'], status['error_reason']),
                         ('HookError', 'API_UNAVAILABLE', 'NONZERO_EXIT'))
        self.assertNotIn('Synthetic required guard.', json.dumps(status))


def hook_module(case):
    import test_inbox_recall_bridge
    return test_inbox_recall_bridge.memory


if __name__ == '__main__':
    unittest.main()
